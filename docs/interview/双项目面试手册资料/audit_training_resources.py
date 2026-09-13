from __future__ import annotations
import csv
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

SHOP = Path('D:/shopping-grpo-longhorizon-main2-reward-v4')
OUT = Path(__file__).resolve().parent
FIELDS = ['timing_s/step', 'timing_s/gen', 'timing_s/update_actor', 'timing_s/update_weights',
          'actor/perf/max_memory_allocated_gb', 'actor/perf/max_memory_reserved_gb']

def number(value):
    if value is None or value == '':
        return None
    value = re.sub(r'^np\.[^(]+\((.+)\)$', r'\1', str(value).strip())
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except ValueError:
        return None

def summarize(rows):
    result = {'rows': len(rows), 'actor_updates': sum(number(r.get('actor/loss')) is not None for r in rows)}
    for key in FIELDS:
        values = [number(r.get(key)) for r in rows]
        present = [v for v in values if v is not None]
        result[key] = {'count':len(present), 'missing_steps':[int(float(r['step'])) for r,v in zip(rows,values) if v is None],
                       'max':max(present) if present else None}
        if key.startswith('timing_'):
            result[key]['sum'] = sum(present)
    return result

def read_csv(relative):
    path = SHOP / relative
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    return path, rows

sources = {}
def source(path):
    sources[str(path.relative_to(SHOP))] = hashlib.sha256(path.read_bytes()).hexdigest()

sft_path = SHOP / 'outputs/runs/sft/fresh-sft-convergence-repair-v5-30k/attempts/20260809T160700Z-noswanlab-direct/train_summary.json'
sft = json.loads(sft_path.read_text(encoding='utf-8'))
source(sft_path)
report = {'sft': {k: sft[k] for k in ['status','started_at','finished_at','optimizer_steps','peak_gpu_memory_gib','total_time_minutes']}}
report['sft']['trainer_runtime_seconds'] = sft['metrics']['train_runtime']
report['sft']['wall_seconds'] = (datetime.fromisoformat(sft['finished_at']) - datetime.fromisoformat(sft['started_at'])).total_seconds()
report['sft']['devices'] = sft['runtime']['cuda']['devices']
report['sft']['recipe'] = sft['recipe']['effective']

rows_by_name = {}
for name, relative in [
    ('grpo100','重点/训练过程曲线/GRPO-step100/grpo-step100-actor-updates.csv'),
    ('grpo230','重点/训练过程曲线/GRPO-step230/grpo-step230-actor-updates.csv'),
]:
    path, rows = read_csv(relative)
    source(path)
    rows_by_name[name] = rows
    report[name] = summarize(rows)
    report[name]['segments'] = {log: summarize([r for r in rows if r['source_log'] == log]) for log in dict.fromkeys(r['source_log'] for r in rows)}

ansi = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
step_pattern = re.compile(r'\bstep:(\d+)\s+-\s+')
raw_reports = {}
raw_paths = [SHOP/'重点/训练过程曲线/GRPO-step230/raw-remote-20260822'/name for name in [
    'grpo-step1-to90-training.log','grpo-step90-to150-training.log','grpo-step150-to230-supervisor.log']]
raw_paths += list((SHOP/'重点/训练过程曲线/GRPO-step100/raw-remote-20260816/outputs/runs/grpo').glob('*/training.log'))
for path in raw_paths:
    source(path)
    rows = []
    for line_number, line in enumerate(path.open(encoding='utf-8', errors='replace'), 1):
        line = ansi.sub('',line)
        match = step_pattern.search(line)
        if not match:
            continue
        row = {'step': int(match[1]), 'line':line_number}
        for entry in line[match.end():].strip().split(' - '):
            if ':' in entry:
                key, value = entry.split(':',1)
                row[key.strip()] = value.strip()
        rows.append(row)
    steps = Counter(r['step'] for r in rows)
    summary = summarize(rows)
    summary['first_step'] = min(steps) if steps else None
    summary['last_step'] = max(steps) if steps else None
    summary['repeated_steps'] = {k:v for k,v in steps.items() if v>1}
    summary['step_records'] = [{k:r.get(k) for k in ['step','line','sampling/skipped_actor_update','timing_s/step','actor/perf/max_memory_allocated_gb','actor/perf/max_memory_reserved_gb']} for r in rows]
    raw_reports[str(path.relative_to(SHOP))] = summary

report['raw_logs'] = raw_reports
report['sources_sha256'] = sources
report['scope'] = 'Read-only historical source audit. Timing fields overlap; component times must not be added to timing_s/step. Missing timings are not zero. No billing record established.'
(OUT/'training_resources_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
def compact(value):
    return {'rows':value['rows'], 'updates':value['actor_updates'],
            'timed_rows':value['timing_s/step']['count'], 'seconds':value['timing_s/step']['sum'],
            'allocated_peak':value['actor/perf/max_memory_allocated_gb']['max'],
            'reserved_peak':value['actor/perf/max_memory_reserved_gb']['max']}
brief = {key:compact(report[key]) for key in ['grpo100','grpo230']}
for key in ['grpo100','grpo230']:
    brief[key]['segments'] = {log:compact(val) for log,val in report[key]['segments'].items()}
brief['raw_logs'] = {str(Path(key).parent.name)+'/'+Path(key).name: {**compact(val), 'first':val['first_step'], 'last':val['last_step'], 'duplicates':val['repeated_steps']} for key,val in raw_reports.items()}
print(json.dumps(brief,ensure_ascii=True,indent=2))
