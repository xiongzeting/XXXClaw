"""User-requested interim review; never changes scores, suites or workspaces."""
import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from run_boundary_campaign_v1 import docker_oracle

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'.aster/evals/boundary-campaign-v1'

def grader_code(case):
    tree=ast.parse(case['checks'][0]['command'][-1])
    assignment=next(node for node in tree.body if isinstance(node,ast.Assign)
                    and any(isinstance(t,ast.Name) and t.id=='args' for t in node.targets))
    return ast.literal_eval(assignment.value.elts[-1])

def main():
    suite=json.loads((BASE/'snapshot/evals/boundary-campaign-v1.json').read_text(encoding='utf-8'))
    cases={c['id']:c for c in suite['cases']}
    changes={
        'boundary_v1_config_migration_cli_contract':[
            ("with patch('pathlib.Path.replace'", "with patch('package.storage.os.replace'"),
            ("save(o, {'should_not_commit':True})", "save({'should_not_commit':True}, o)")],
        'boundary_v1_idempotent_charge_recovery':[
            ("charge_mod.charge({},'old',90)=={'order_id':'old','cents':90,'status':'charged'}",
             "charge_mod.charge({},'old',90)=={'order_id':'old','cents':90}")],
        'boundary_v1_queue_rebuild_and_reconcile':[
            ("json.loads(s.read_text())=={'active':['b']}","json.loads(s.read_text())==['b']")],
    }
    issues={
        'boundary_v1_config_migration_cli_contract':'Public contract does not specify private save argument order or require pathlib.Path.replace instead of os.replace. Original failure is not evidence of broken public CLI or atomic output.',
        'boundary_v1_idempotent_charge_recovery':'Existing receipt in fixture has order_id and cents only; prompt says return original result. Hidden oracle additionally demands status=charged, which was not specified.',
        'boundary_v1_queue_rebuild_and_reconcile':'Prompt names state.json and ordered tasks but does not specify an active wrapper key. An ordered JSON array is a plausible implementation; hidden exact wrapper check over-constrains output.',
    }
    audit=[]
    for case_id,replacements in changes.items():
        result_path=BASE/'run/cases'/case_id/'result.json'
        assert result_path.exists(), 'Only inspect completed cases'
        code=grader_code(cases[case_id])
        for old,new in replacements:
            assert old in code,(case_id,old)
            code=code.replace(old,new)
        output=docker_oracle(BASE/'run/cases'/case_id/'attempt-001/workspace',code)
        audit.append({'id':case_id,'review_label':'oracle_contract_mismatch',
                      'reason':issues[case_id],'diagnostic_changes':replacements,
                      'diagnostic_exit_code':output.returncode,
                      'output':(output.stdout+output.stderr)[-3500:],
                      'score_changed':False,
                      'limitation':'Post-hoc diagnostic only; passing does not establish all task requirements or license changing raw score to passed.'})
    path=BASE/'interim-review.json'
    path.write_text(json.dumps({'reviewed_at':datetime.now(timezone.utc).isoformat(),
        'authorization':'User explicitly asked to inspect second-round status while the batch was running.',
        'cases':audit},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for row in audit:print(row['id'],row['diagnostic_exit_code'],row['output'])

if __name__=='__main__':main()
