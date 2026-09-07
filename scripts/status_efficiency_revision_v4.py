"""Read-only progress snapshot; incomplete attempts are never graded here."""
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]/'.aster/evals/efficiency-revision-v4'
suite=json.loads((ROOT/'snapshot/evals/boundary-efficiency-v4.json').read_text(encoding='utf-8'))
cases={c['id']:c for c in suite['cases']}
rows=[]
for directory in sorted((ROOT/'run/cases').iterdir()):
    events={}
    for path in directory.rglob('trace.jsonl'):
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                event=json.loads(line);events[event['event_id']]=event
            except (ValueError,KeyError):continue
    ordered=sorted(events.values(),key=lambda e:e['timestamp'])
    starts=[e for e in ordered if e['type']=='run.started']
    models=[e for e in ordered if e['type']=='model.request']
    tools=[e for e in ordered if e['type']=='tool.call']
    current=starts[-1]['data'].get('request') if starts else None
    phase=next((p['id'] for p in cases[directory.name]['phases'] if p['prompt']==current),'unknown')
    successful=[e for e in models if e['data'].get('status')=='success']
    main=[e for e in successful if e['data'].get('purpose')=='agent']
    last=ordered[-1] if ordered else {}
    rows.append({'case':directory.name,'final_result_available':(directory/'result.json').exists(),
                 'current_phase':phase,'total_phases':len(cases[directory.name]['phases']),
                 'successful_main_requests':len(main),'tokens_so_far':sum(e['data'].get('usage',{}).get('total_tokens',0) for e in successful),
                 'tools':len(tools),'tool_failures':sum(e['data'].get('status') not in {'success'} for e in tools),
                 'model_error_records':sum(e['data'].get('status')!='success' for e in models),
                 'latest_event_type':last.get('type'),'latest_event_time':last.get('timestamp')})
print(json.dumps({'as_of':datetime.now(timezone.utc).isoformat(),'final_report_available':(ROOT/'run/report.json').exists(),'cases':rows},ensure_ascii=False,indent=2))
