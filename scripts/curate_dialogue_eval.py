"""Promote reviewed failures, keep immutable discovery reports and frozen v1.

Requires the discovery and confirmation runs; never fabricates model outcomes.
"""
from __future__ import annotations
import copy
from collections import Counter
import hashlib
import json
from pathlib import Path

from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.evaluation.runner import evaluate_check

ROOT=Path(__file__).resolve().parents[1]
EVALS=ROOT/'evals'
RUNS=ROOT/'.aster/evals/expansion-20260905'
SOURCES={
    'memory-discovery':'dialogue-memory-development.json',
    'product-discovery':'dialogue-product-development.json',
    'challenge-discovery':'dialogue-challenge-development.json',
    'long-discovery':'dialogue-long-history-development.json',
    'edges-discovery':'dialogue-edges-development.json',
    'input-discovery':'dialogue-input-development.json',
    'router-discovery':'dialogue-router-development.json',
}


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    all_cases={};reviewed=[];categories=Counter();metrics=[]
    for run,suite_file in SOURCES.items():
        suite=load_eval_suite(EVALS/suite_file)
        raw=read(EVALS/suite_file)
        specs={c.id:c for c in suite.cases}
        all_cases.update({c['id']:c for c in raw['cases']})
        report=read(RUNS/run/'report.json')
        metrics.append(report['summary']['metrics'])
        for result in report['cases']:
            spec=specs[result['id']];categories[spec.category]+=1
            attempts=[]
            for attempt in result['attempts']:
                checks=[]
                for check in spec.checks:
                    if check.type=='command':
                        # Oracle code did not change; use the original isolated
                        # grader outcome. No need to re-execute model-produced code.
                        matching=[c for c in attempt['checks'] if c['type']=='command' and c.get('options',{}).get('command')==check.options.get('command')]
                        if len(matching)!=1:raise ValueError(f'Changed command oracle: {spec.id}')
                        checks.append(matching[0])
                    else:
                        checks.append(evaluate_check(check,Path(attempt['workspace']),Path(attempt['workspace']).parent,
                            attempt['phases'],{},metrics=attempt.get('metrics'),workspace_changes=attempt.get('workspace_changes')))
                ok=not attempt.get('error') and all(c['passed'] for c in checks if c.get('required',True))
                attempts.append({'passed':ok,'checks':checks})
            reviewed.append({'id':spec.id,'category':spec.category,'original_passed':result['passed'],
                'reviewed_passed':all(a['passed'] for a in attempts),'attempts':attempts,
                'discovery_report':str((RUNS/run/'cases'/spec.id/'result.json').relative_to(ROOT)),
                'runner':spec.source.get('input_adapter','direct-CodingAssistant')})

    env=read(EVALS/'dialogue-memory-development.json')['environment']
    failing={c['id'] for c in reviewed if not c['reviewed_passed']}
    router_keep={'dialogue_group_identity_language','dialogue_group_identity_timezone',
                 'dialogue_group_identity_branch','dialogue_group_identity_contact'}
    selected=sorted((failing-{c for c in failing if c.startswith('dialogue_group_identity_')}) | router_keep)
    if not router_keep <= failing:raise ValueError('Only observed router failures may be selected')
    by_id={c['id']:c for c in reviewed}
    promoted=[]
    for cid in selected:
        case=copy.deepcopy(all_cases[cid])
        router=case['source'].get('input_adapter')=='feishu-router'
        confirmation='router-confirmation' if router else 'failure-confirmation'
        result=read(RUNS/confirmation/'cases'/cid/'result.json')
        if result['passed_attempts']==result['repetitions']:
            raise ValueError(f'Failure not reproduced: {cid}')
        failure_count=1+result['repetitions']-result['passed_attempts']
        case['source'].update({
            'track':'failure-regression',
            'discovery_report':by_id[cid]['discovery_report'],
            'confirmation_report':str((RUNS/confirmation/'cases'/cid/'result.json').relative_to(ROOT)),
            'observed_attempts':1+result['repetitions'],'observed_failures':failure_count,
            'failure_signature':('shared-thread-actor-binding' if router else
                'feishu-whitespace-normalization' if case['source'].get('input_adapter') else 'repository-instruction-overrides-readonly'),
            'status':'known-failure-unfixed',
        })
        case['repetitions']=3
        case['min_pass_rate']=1.0
        promoted.append(case)
    write(EVALS/'dialogue-failures.json',{'version':1,'name':'dialogue-failure-regression-v2',
        'environment':env,'cases':promoted})
    write(EVALS/'dialogue-failures-core.json',{'version':1,'name':'dialogue-failures-core',
        'environment':env,'cases':[c for c in promoted if c['source'].get('input_adapter')!='feishu-router']})
    write(EVALS/'dialogue-failures-router.json',{'version':1,'name':'dialogue-failures-router',
        'environment':env,'cases':[c for c in promoted if c['source'].get('input_adapter')=='feishu-router']})

    replacements={
        'memory_multihop_failure':'dialogue_chain_after_handover',
        'memory_preference_failure':'dialogue_preference_exception_scope',
        'procedure_sequence_failure':'dialogue_rollback_after_verify_failure',
        'dynamic_state_transition_failure':'dialogue_event_time_not_ingest',
        'static_evidence_failure':'dialogue_versioned_docs',
        'abstention_evidence_failure':'dialogue_conflicting_sources',
    }
    old=read(EVALS/'regression.json')
    replacement_cases=[]
    for original in old['cases']:
        if original['id'] in replacements:
            new=copy.deepcopy(all_cases[replacements[original['id']]])
            new['source']['replaces_case']=original['id']
            new['source']['replacement_reason']='Natural multi-turn task and stronger independent outcome oracle'
            replacement_cases.append(new)
        else:replacement_cases.append(original)
    write(EVALS/'regression-v2.json',{**old,'name':'failure-regression-v2','cases':replacement_cases})
    write(EVALS/'dialogue-replacements-validation.json',{'version':1,'name':'dialogue-replacement-validation',
        'environment':env,'cases':[c for c in replacement_cases if c['id'] in replacements.values()]})
    write(EVALS/'portfolio-v2.json',{'version':1,'name':'miniclaw-portfolio-v2',
        'includes':['smoke.json','regression-v2.json','resilience.json','dialogue-failures.json'],
        'coverage':{'dimensions':['outcome','process','efficiency','safety','reliability']}})
    write(EVALS/'dialogue-all-development.json',{'version':1,'name':'dialogue-development-112',
        'includes':list(SOURCES.values())})

    manifest={'version':2,'campaign':'dialogue-20260905','model':'gpt-5.6-luna',
        'source_policy':'Synthetic development conversations only; frozen public heldout untouched',
        'unique_scenarios':len(reviewed),'categories':dict(sorted(categories.items())),
        'reviewed_discovery_passed':sum(c['reviewed_passed'] for c in reviewed),
        'reviewed_discovery_failed':sum(not c['reviewed_passed'] for c in reviewed),
        'promoted_cases':selected,'promoted_count':len(promoted),
        'root_failure_families':sorted({c['source']['failure_signature'] for c in promoted}),
        'duplicates_left_in_development':sorted(failing-set(selected)),
        'replacements':replacements,'v1_policy':'Original full.json/regression.json/baselines/splits unchanged',
        'oracle_review':[
            {'case_id':'dialogue_versioned_docs','decision':'validation grader false positive',
             'reason':'Both 1.8 and wire-client==1.8 identify the correct pinned version; retain exact timeout parameter and bounded version check.'},
            {'case_id':'dialogue_chain_with_exception','decision':'grader false positive',
             'reason':'The user asked whom to contact; the correct team-qualified contact name is valid.'},
            {'case_id':'dialogue_interval_union','decision':'grader false positive',
             'reason':'Bytecode caches from validation are harmless generated files, not unauthorized source edits.'},
            {'case_id':'read-only dialogue cases','decision':'strengthened oracle',
             'reason':'allowed_paths=[] alone is unrestricted in current grader; explicitly require max_changed_files=0.'}],
        'reviewed_results':reviewed,
        'suite_hashes':{f:hashlib.sha256((EVALS/f).read_bytes()).hexdigest() for f in SOURCES.values()},
        'runtime_source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/'src/MiniClaw').rglob('*.py'))},
        'statistical_limits':'Scenario variants are not independent root causes; no unseen coding benchmark claim; aggregate latency percentiles from legacy runner are not used.'}
    write(EVALS/'dialogue-campaign-manifest.json',manifest)
    print(f"Reviewed {len(reviewed)} cases; promoted {len(promoted)} failures; replaced {len(replacements)} weak cases")


if __name__=='__main__':main()
