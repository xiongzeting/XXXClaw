import json
from pathlib import Path
for p in [Path('evals/miniclaw-eval3.json'),Path('evals/miniclaw-eval3-30.json')]:
 j=json.loads(p.read_text(encoding='utf-8')); j['environment']['MINICLAW_SANDBOX']='docker:miniclaw-runtime:py313-bench'; j['environment']['MINICLAW_WORKSPACE_MODE']='direct'; j['environment']['MINICLAW_APPROVAL_POLICY']='allow'; p.write_text(json.dumps(j,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
Path('docs/interview/eval no 3/miniclaw-eval3.json').write_text(Path('evals/miniclaw-eval3.json').read_text(encoding='utf-8'),encoding='utf-8')
