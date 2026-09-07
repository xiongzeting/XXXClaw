"""Collect supporting checks for assistant review; this script never assigns judge verdicts."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.evaluation.runner import evaluate_check
from MiniClaw.evaluation.oracle import evaluate_oracle
OUT=ROOT/'.aster/evals/assistant-outcome-review-v1-v10'
BATCHES={'v1':'boundary-v1-jobs20-current-judge','v10':'boundary-full-v10-jobs20'}


def main(version):
    OUT.mkdir(exist_ok=True)
    suite=load_eval_suite(ROOT/'.aster/evals/boundary-full-v10-jobs20/snapshot/evals/boundary-submissions-v10.json')
    oracle_path=ROOT/'.aster/evals/boundary-full-v9-review/review_oracle.py'
    spec=importlib.util.spec_from_file_location('review_oracle',oracle_path)
    oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
    def run(case):
        path=ROOT/'.aster/evals'/BATCHES[version]/'run/cases'/case.id/'result.json'
        dest=OUT/version/(case.id+'.json')
        if not path.exists() or dest.exists(): return
        raw=json.loads(path.read_text(encoding='utf-8'))
        key=case.id.removeprefix('boundary_v1_')
        checks=[]
        if key in oracle.REQUIREMENTS:
            checks.append(evaluate_oracle({'script':str(oracle_path),
                'sha256':hashlib.sha256(oracle_path.read_bytes()).hexdigest(),
                'entry':key,'requirements':oracle.REQUIREMENTS[key]},Path(raw['workspace'])))
        else:
            for check in case.checks:
                if check.dimension=='outcome':
                    checks.append(evaluate_check(check,Path(raw['workspace']),path.parent/'attempt-001',
                        raw['phases'],{},metrics=raw['metrics'],workspace_changes=raw['workspace_changes']))
        dest.parent.mkdir(exist_ok=True)
        dest.write_text(json.dumps({'version':version,'id':case.id,'judge_status':'awaiting_assistant_review',
            'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'checks':checks},
            ensure_ascii=False,indent=2),encoding='utf-8')
        print(version,case.id,[c['passed'] for c in checks],flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(run,suite.cases))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('version',choices=BATCHES);main(p.parse_args().version)
