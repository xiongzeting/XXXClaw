"""Build the two completed 20-task reports, preserving program scores and assistant review."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from eval_partition_policy import reclassify

DIMS=('outcome','process','efficiency','safety','reliability')
BATCHES={'v1':'boundary-v1-jobs20-current-judge','v10':'boundary-full-v10-jobs20'}


def timing(raw):
    events={}
    for phase in raw['phases'].values():
        ids=set(phase.get('run_ids') or [])
        for line in Path(phase['trace_path']).read_text(encoding='utf-8').splitlines():
            e=json.loads(line)
            if not ids or e.get('run_id') in ids:events[e.get('event_id',line)]=e
    intervals=[];missing=0
    for e in events.values():
        if e.get('type')!='model.request':continue
        d=e['data']
        if not d.get('started_at') or not d.get('completed_at'):
            missing+=1;continue
        a,b=(datetime.fromisoformat(d[k].replace('Z','+00:00')).timestamp() for k in ('started_at','completed_at'))
        if b<a:missing+=1;continue
        intervals.append((a,b))
    end=None;wait=0
    for a,b in sorted(intervals):
        if end is None or a>end:wait+=b-a;end=b
        elif b>end:wait+=b-end;end=b
    wall=raw['duration_seconds']
    effective=None if missing or wait>wall+.1 else round(max(0,wall-wait),3)
    return {'wall_seconds':wall,'model_wait_seconds':round(wait,3),
            'effective_seconds':effective,'missing_intervals':missing}


def build_current(root,out):
    review_root=root/'.aster/evals/assistant-outcome-review-v1-v10'
    judge=json.loads((review_root/'verdicts.json').read_text(encoding='utf-8'))
    names={c['id']:c['name'] for c in json.loads((root/'docs/interview/eval no 2/原始成绩与复核标签.json').read_text(encoding='utf-8'))['cases']}
    batches=[]
    for version,folder in BATCHES.items():
        batch=root/'.aster/evals'/folder
        assert (batch/'completion.json').is_file(), 'Only publish after both batches finish'
        program=json.loads((batch/'run/program-report.json').read_text(encoding='utf-8'))
        scored={c['id']:c for c in program['cases']}
        decisions={c['id']:c for c in judge['batches'][version]}
        assert len(scored)==len(decisions)==20 and set(scored)==set(decisions)
        rows=[]
        for case_id,score in scored.items():
            path=batch/'run/cases'/case_id/'result.json'
            raw=json.loads(path.read_text(encoding='utf-8'));verdict=decisions[case_id]
            assert verdict['source_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
            assert type(verdict['passed']) is bool and verdict['reason']
            dims={d:(score['dimensions'][d]['score']==1 if score['dimensions'][d]['score'] is not None else None) for d in DIMS}
            dims['outcome']=verdict['passed']
            metrics=score.get('metrics',raw['metrics'])
            packet=json.loads((batch/'run/judge-packets'/(case_id+'.json')).read_text(encoding='utf-8'))
            rows.append(reclassify({'id':case_id,'name':names[case_id],'source':raw['source'],
                'category':raw['category'],'dimensions':dims,'passed':all(v is True for v in dims.values()),
                'metrics':metrics,'timing':timing(raw),'judge':verdict,
                'checks':score['checks'],'phases':raw['phases'],'requirements':packet['requirements'],
                'workspace':raw['workspace'],'source_path':str(path.relative_to(root)).replace('\\','/')},'round2'))
        totals={k:sum(c['metrics'].get(k,0) or 0 for c in rows) for k in
                ['total_tokens','input_tokens','output_tokens','cached_tokens','cost_usd','tool_calls','tool_errors',
                 'agent_model_requests','model_requests','paused_runs','compactions','cache_observed_input_tokens','cache_observed_cached_tokens']}
        totals['uncached_input']=totals['input_tokens']-totals['cached_tokens']
        totals['cache_ratio']=totals['cached_tokens']/totals['input_tokens'] if totals['input_tokens'] else None
        totals['effective_seconds']=sum(c['timing']['effective_seconds'] for c in rows) if all(c['timing']['effective_seconds'] is not None for c in rows) else None
        totals['model_wait_seconds']=sum(c['timing']['model_wait_seconds'] for c in rows)
        batches.append({'version':version,'label':'v1 原代码重跑' if version=='v1' else '新版 v10 · 简化接口',
            'jobs':20,'cases':rows,'totals':totals,'counts':{d:sum(c['dimensions'][d] is True for c in rows) for d in DIMS},
            'passed':sum(c['passed'] for c in rows),'source':str((batch/'run/program-report.json').relative_to(root)).replace('\\','/')})
    data={'schema':'assistant-reviewed-two-batches-v1','batches':batches,
          'judge_method':judge['method'],'limits':judge['limits'],'sources':[
          {'path':str((review_root/'verdicts.json').relative_to(root)).replace('\\','/'),
           'sha256':hashlib.sha256((review_root/'verdicts.json').read_bytes()).hexdigest()}]}
    review_path=root/'.aster/evals/effective-changes-review/conclusions.json'
    if review_path.is_file():
        data['improvement_review']=json.loads(review_path.read_text(encoding='utf-8'))
        data['sources'].append({'path':review_path.relative_to(root).as_posix(),
                                'sha256':hashlib.sha256(review_path.read_bytes()).hexdigest()})
    out.mkdir(parents=True,exist_ok=True)
    text=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    (out/'round2-data.js').write_text('window.EVAL_ROUND2 = '+text+';\n',encoding='utf-8')
    (out/'round2-data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return data
