"""Current display/execution partitions, independent of immutable run archives."""
import copy
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
POLICY=ROOT/'evals/current-round-partitions.json'

def reclassify(case, round_name):
    row=copy.deepcopy(case)
    mapping=json.loads(POLICY.read_text(encoding='utf-8'))['rounds'][round_name]['assignments']
    source=row.setdefault('source',{})
    prior=source.get('split',row.get('split'))
    target=mapping[row['id']]
    source.setdefault('historical_split',prior)
    source['split']=target
    source['partition_policy']='current-round-partitions-v1'
    source['exposure']='Previously executed/reviewed; exposed development or internal test, not unseen holdout.'
    if 'split' in row:
        row.setdefault('historicalSplit',row['split']);row['split']=target
    return row

def metadata(round_name):
    return json.loads(POLICY.read_text(encoding='utf-8'))['rounds'][round_name]
