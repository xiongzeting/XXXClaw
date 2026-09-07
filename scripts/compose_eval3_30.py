import json
from pathlib import Path
r=Path('.')
base=json.loads((r/'evals/miniclaw-eval3.json').read_text(encoding='utf-8'))
extra=json.loads((r/'evals/miniclaw-eval3-extra.json').read_text(encoding='utf-8'))['cases']
allc=base['cases']+extra
for i,c in enumerate(allc):
 c.setdefault('source',{})['split']='development' if i<15 else 'test'
s={'version':1,'name':'miniclaw-eval3-30','environment':base['environment'],'cases':allc}
(r/'evals/miniclaw-eval3-30.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(r/'docs/interview/eval no 3/miniclaw-eval3-30.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(len(allc),sum(c['source']['split']=='development' for c in allc))
