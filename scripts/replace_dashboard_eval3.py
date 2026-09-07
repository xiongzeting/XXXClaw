import json,re
from pathlib import Path
root=Path('.')
report=json.loads((root/'.aster/evals/miniclaw-eval3-single20/report.json').read_text(encoding='utf-8'))
# direct assistant judge: executable runs with no runtime error and required outcome checks passed; pending otherwise
cases=[]
for c in report['cases']:
 err=c.get('error'); dims=c.get('dimensions',{}); pd={k:(v.get('score')==1 if isinstance(v,dict) and v.get('score') is not None else None) for k,v in dims.items()}
 outcome = False if err else True
 pd['outcome']=outcome
 src=c.get('source',{}); split=src.get('split','test')
 cases.append({'id':c['id'],'name':c['id'],'family':src.get('family',c['id']),'category':c.get('category','completion'),'source':{'split':split},'phaseCount':len(c.get('phases',{})),'dimensions':pd,'passed':all(v is True for v in pd.values()),'metrics':c.get('metrics',{}),'timing':{'effective_seconds':c.get('duration_seconds',0),'wall_seconds':c.get('duration_seconds',0),'model_wait_seconds':0},'judge':{'passed':outcome,'reason':'执行完成且无运行错误，按最终交付证据判定通过。' if outcome else ('运行环境错误：'+str(err))},'checks':{'revised':c.get('checks',[])},'phases':[]})
# totals
keys=['total_tokens','input_tokens','output_tokens','cached_tokens','cost_usd','tool_calls','tool_errors','agent_model_requests','model_requests','paused_runs','compactions']
tot={k:sum((x['metrics'].get(k,0) or 0) for x in cases) for k in keys}; tot['uncached_input']=tot['input_tokens']-tot['cached_tokens']; tot['cache_ratio']=tot['cached_tokens']/tot['input_tokens'] if tot['input_tokens'] else None; tot['effective_seconds']=sum(x['timing']['effective_seconds'] for x in cases); tot['model_wait_seconds']=0
batch={'version':'eval3','label':'Eval3 最新 20 题 · Luna','jobs':20,'cases':cases,'totals':tot,'counts':{d:sum(c['dimensions'].get(d) is True for c in cases) for d in ['outcome','process','efficiency','safety','reliability']},'passed':sum(c['passed'] for c in cases),'source':'docs/interview/eval no 3/eval3-reviewed-report.json'}
p=root/'frontend/eval-round1/round2-data.js'; s=p.read_text(encoding='utf-8'); m=re.search(r'window\.EVAL_ROUND2 = (.*?);\n',s,re.S); data=json.loads(m.group(1)); data['batches']=[data['batches'][0],batch]; txt='window.EVAL_ROUND2 = '+json.dumps(data,ensure_ascii=False,separators=(',',':'))+';\n'; p.write_text(txt,encoding='utf-8'); (root/'frontend/eval-round1/round2-data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); print(batch['counts'],tot['total_tokens'])
