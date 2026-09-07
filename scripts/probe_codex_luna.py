import json, os, subprocess, tomllib
from pathlib import Path
out=Path('D:/MIniClaw/.aster/evals/codex-luna-preflight');out.mkdir(exist_ok=True)
work=Path('D:/codex-luna-preflight');work.mkdir(exist_ok=True)
c=tomllib.loads(Path('C:/Users/inari/.codex/config.toml').read_text())
cli='C:/Users/inari/.vscode/extensions/openai.chatgpt-26.814.41407-win32-x64/bin/windows-x86_64/codex.exe'
args=[cli,'exec','--ignore-user-config','--skip-git-repo-check','--json','--sandbox','workspace-write','-m','gpt-5.6-luna','-c','approval_policy="never"','-c','model_reasoning_effort="medium"','-c','model_provider='+json.dumps(c['model_provider'])]
for key,value in c['model_providers'][c['model_provider']].items():
 if key in ['name','base_url','wire_api','requires_openai_auth','env_key']:
  args+=['-c','model_providers.'+c['model_provider']+'.'+key+'='+json.dumps(value)]
for feature in ['plugins','apps','multi_agent','browser_use','computer_use']:
 args+=['--disable',feature]
args+=['-c','windows.sandbox="unelevated"']
env={k:v for k,v in os.environ.items() if not k.startswith('CODEX_') or k=='CODEX_HOME'}
env['PATH']='D:/anaconda3;'+env['PATH']
args+=['-']
with (out/'events.jsonl').open('wb') as a,(out/'stderr.txt').open('wb') as b:
 p=subprocess.run(args,input='在当前目录创建 probe.txt，内容为 probe-ok，再用命令读取并确认内容。'.encode(),cwd=work,env=env,stdout=a,stderr=b,timeout=180)
 print('exit',p.returncode,'file', (work/'probe.txt').read_text() if (work/'probe.txt').exists() else 'missing')
