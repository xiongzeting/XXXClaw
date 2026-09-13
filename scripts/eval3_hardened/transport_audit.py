import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).parent))
from observe import read,write,records
OUT=Path(read(ROOT/'.aster/evals/eval3-hardened-r1/active-run.json')['directory'])
report=read(OUT/'report.json');events=[]
for case in report['cases']:
    for record in records(case['workspace']):
        d=record['data']
        if record['type']=='model.transport' and d.get('phase') in ('attempt_failed','retry_scheduled'):
            events.append({'case_id':case['id'],'event_id':record['event_id'],'timestamp':record['timestamp'],'details':d})
write(OUT/'transport-recovery-audit.json',{'events':events,'note':'Logical request usage includes final replies; unreported transport attempt usage is not assumed free.'})
print(json.dumps({'events':events},ensure_ascii=False))
