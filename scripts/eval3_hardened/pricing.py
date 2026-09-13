"""Fetch public provider pricing metadata; never invent currency or conversion rules."""
import json
from pathlib import Path
import sys
import httpx
ROOT=Path(__file__).resolve().parents[2]
out=ROOT/'.aster/evals/eval3-hardened-r1/preflight'
out.mkdir(parents=True,exist_ok=True)
url='https://ai.zxcoding.top/api/pricing'
(out/'pricing-request.json').write_text(json.dumps({'url':url,'state':'started'}),'utf-8')
try:
    response=httpx.get(url,timeout=20)
    payload=response.json()
    rows=payload.get('data',[])
    selected=[r for r in rows if isinstance(r,dict) and 'luna' in str(r.get('model_name',r.get('id',''))).lower()] if isinstance(rows,list) else []
    result={'url':url,'status':response.status_code,'model_rows':selected,
            'metadata':{k:v for k,v in payload.items() if k not in ('data',)},
            'note':'Public pricing metadata. Ratios alone are not a verified USD token price.'}
except Exception as e:result={'url':url,'status':'unavailable','error_type':type(e).__name__}
(out/'provider-pricing.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(result,ensure_ascii=False))
