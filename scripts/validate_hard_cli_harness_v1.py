"""Run the CLI hidden harness on trusted references in isolated Docker."""
import json
import subprocess
from pathlib import Path
from build_hard_verified_v1 import LOCAL, DELIVERY, cli_oracle, function_oracle
from build_dialogue_campaign import hidden_test

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.aster/evals/hard-authoring/cli-reference-workspaces'
CLI='''import json,sys,os,tempfile
from pathlib import Path
from solution import solve
def main():
 src,dst=map(Path,sys.argv[1:])
 try:
  result=solve(json.loads(src.read_text(encoding='utf-8')))
 except ValueError:
  return 2
 text=json.dumps(result,ensure_ascii=False,sort_keys=True)
 with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=dst.parent,delete=False) as f:
  f.write(text); name=f.name
 os.replace(name,dst)
 return 0
if __name__=='__main__': raise SystemExit(main())
'''

def main():
 rows=[]
 for spec in LOCAL+DELIVERY:
  root=OUT/spec['key']; root.mkdir(parents=True,exist_ok=True)
  (root/'solution.py').write_text(spec['reference'],encoding='utf-8')
  (root/'cli.py').write_text(CLI,encoding='utf-8')
  command=hidden_test(function_oracle(spec)+'\n'+cli_oracle(spec))['command']
  result=subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=70)
  assert result.returncode==0,(spec['key'],result.stdout,result.stderr)
  rows.append({'id':spec['key'],'function_and_cli_harness_passed_in_docker':True})
 path=OUT.parent/'cli-reference-validation.json'
 path.write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8')
 print(f'{len(rows)} reference implementations passed function + real CLI checks in read-only Docker')

if __name__=='__main__':main()
