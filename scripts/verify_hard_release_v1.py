"""Offline integrity checks for the hard suite and active portfolio."""
from collections import Counter
import ast
import json
from pathlib import Path
from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.evaluation.runner import ADDITIVE_METRICS
from run_hard_campaign_v1 import hashes

ROOT=Path(__file__).resolve().parents[1]; E=ROOT/'evals'; R=ROOT/'.aster/evals/hard-campaign-v1'

def main():
 frozen=json.loads((R/'freeze.json').read_text(encoding='utf-8'))
 assert hashes(R/'snapshot/src')==frozen['source_hashes']
 assert hashes(R/'snapshot/evals')==frozen['eval_hashes']
 ids=set(); matrix={}; turns=0
 for split in ('development','retained','test'):
  suite=load_eval_suite(E/f'{split}-challenge-v3.json')
  assert (E/f'{split}-challenge-v3.json').read_bytes()==(R/f'snapshot/evals/{split}-challenge-v3.json').read_bytes()
  matrix[split]=dict(Counter(c.category for c in suite.cases))
  assert matrix[split]==dict.fromkeys(('recall','compression','tools','safety','completion'),2)
  for c in suite.cases:
   assert c.id not in ids; ids.add(c.id); turns+=len(c.phases)
   assert {x.dimension for x in c.checks if x.required}>={'outcome','process','efficiency','safety','reliability'}
   assert (c.fixture_root/c.fixture).is_dir()
   assert hashes(c.fixture_root/c.fixture)==hashes(R/'snapshot/evals'/c.fixture), c.id
   for x in c.checks:
    if x.type=='metric': assert x.options['name'] in ADDITIVE_METRICS,(c.id,x.options['name'])
    if x.type=='command':
     command=x.options['command']
     if command[:2]==['python','-c']: ast.parse(command[2])
   assert any(x.type=='metric' and x.options.get('name')=='successful_runs' and x.options.get('min')==len(c.phases) for x in c.checks)
 for file in ('smoke-basic-v3.json',): load_eval_suite(E/file)
 for path in (E).glob('hard-failures-*-v1.json'): load_eval_suite(path)
 reference=json.loads((ROOT/'.aster/evals/hard-authoring/cli-reference-validation.json').read_text(encoding='utf-8'))
 assert len(reference)==18 and all(r['function_and_cli_harness_passed_in_docker'] for r in reference)
 result={'valid_cases':len(ids),'dialogue_turns':turns,'matrix':matrix,'source_snapshot_intact':True,
         'input_snapshot_intact':True,'reference_function_and_cli_validations':len(reference)}
 (R/'static-verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(result))

if __name__=='__main__':main()
