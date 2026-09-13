import json
import hashlib
import re
from pathlib import Path

root = Path('D:/shopping-grpo-longhorizon-main2-reward-v4/重点/训练过程曲线/GRPO-step230/raw-remote-20260822')
pattern = re.compile(r'Training Progress:.*?\|\s*(\d+)/(\d+)\s*\[([\d:]+)[<,]')
report = {}
for name in ['grpo-step1-to90-training.log','grpo-step90-to150-training.log','grpo-step150-to230-supervisor.log']:
    sequences = []
    current = []
    previous = -1
    for lineno,line in enumerate((root/name).open(encoding='utf-8',errors='replace'),1):
        match=pattern.search(line)
        if not match:
            continue
        elapsed=0
        for part in match[3].split(':'):
            elapsed=elapsed*60+int(part)
        if current and elapsed < previous:
            sequences.append(current)
            current=[]
        current.append({'line':lineno,'step':int(match[1]),'elapsed_s':elapsed})
        previous=elapsed
    if current:
        sequences.append(current)
    entry={'log':name,'sha256':hashlib.sha256((root/name).read_bytes()).hexdigest(),
           'sessions':[{'first':rows[0],'last':rows[-1], 'selected':[row for row in rows if row['step'] in [90,92,150,190,195,230,231]][-10:]} for rows in sequences]}
    report[name]=entry
    print(json.dumps(entry,ensure_ascii=True))
first = report['grpo-step1-to90-training.log']['sessions']
second = report['grpo-step90-to150-training.log']['sessions']
last = report['grpo-step150-to230-supervisor.log']['sessions']
assert len(first) == 1 and len(second) == 1 and len(last) == 2
prefix = first[0]['last']['elapsed_s'] + second[0]['last']['elapsed_s'] + last[0]['last']['elapsed_s']
through230 = next(item['elapsed_s'] for item in last[1]['selected'] if item['step'] == 230)
report['observed_loop_seconds_through_step230_including_replayed_work'] = prefix + through230
report['observed_loop_seconds_through_last_logged_step231'] = prefix + last[1]['last']['elapsed_s']
assert report['observed_loop_seconds_through_step230_including_replayed_work'] == 11715
assert report['observed_loop_seconds_through_last_logged_step231'] == 11743
report['scope'] = 'Sum of distinct progress-bar sessions; excludes setup outside these timers, inter-run downtime, unrelated probes/evaluations and other experiments. Integer-second log resolution.'
(Path(__file__).resolve().parent/'training_loop_timers.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
