"""Frozen sequential-phase Codex CLI comparison; hidden checks stay outside workspaces."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import tomllib

ROOT = Path('D:/MIniClaw')
SOURCE = ROOT / '.aster/evals/efficiency-revision-v2/snapshot/evals'
OUT = ROOT / '.aster/evals/codex-luna-comparison-v2'
WORK = Path('D:/codex-luna-comparison-v2')
CLI = 'C:/Users/inari/.vscode/extensions/openai.chatgpt-26.814.41407-win32-x64/bin/windows-x86_64/codex.exe'
IDS = ['boundary_v1_config_migration_cli_contract', 'hard_v1_tiered_invoice', 'hard_v1_redaction_priority']

def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

def hashes(path):
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.rglob('*')) if p.is_file()}

def run_case(case):
    cid = case['id']; dest = OUT / cid; workspace = WORK / cid
    dest.mkdir(parents=True, exist_ok=True)
    if not workspace.exists():
        shutil.copytree(SOURCE / case['fixture'], workspace)
        dump(dest / 'initial-files.json', hashes(workspace))
    if (dest/'status.json').exists() and json.loads((dest/'status.json').read_text())['state']=='completed':
        return
    cfg = tomllib.loads(Path('C:/Users/inari/.codex/config.toml').read_text(encoding='utf-8'))
    provider = cfg['model_provider']
    options = ['--ignore-user-config', '-m', 'gpt-5.6-luna', '-c', 'model_reasoning_effort="medium"',
               '-c', 'approval_policy="never"', '-c', 'sandbox_mode="workspace-write"', '-c', 'windows.sandbox="unelevated"',
               '-c', 'model_provider=' + json.dumps(provider), '--skip-git-repo-check', '--json']
    for key, value in cfg['model_providers'][provider].items():
        if key in ('name', 'base_url', 'wire_api', 'requires_openai_auth', 'env_key'):
            options += ['-c', 'model_providers.' + provider + '.' + key + '=' + json.dumps(value)]
    for feature in ['plugins','apps','multi_agent','browser_use','computer_use']:
        options += ['--disable',feature]
    results = []; thread = None; started = time.time()
    env = {k:v for k,v in os.environ.items() if not k.startswith('CODEX_') or k=='CODEX_HOME'}
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PATH'] = 'D:/anaconda3;' + env.get('PATH', '')
    for i, phase in enumerate(case['phases'], 1):
        prefix = dest / f'phase-{i:02}'
        previous_events = Path(str(prefix)+'.events.jsonl')
        if previous_events.exists():
            previous = [json.loads(x) for x in previous_events.read_text(encoding='utf-8').splitlines() if x.startswith('{')]
            for event in previous:
                if event.get('type')=='thread.started': thread=event['thread_id']
            assert any(e.get('type')=='turn.completed' for e in previous), 'Incomplete phase must be investigated before recovery'
            saved = json.loads((dest/'progress.json').read_text()) if (dest/'progress.json').exists() else []
            prior = next((r for r in saved if r['phase']==i),None)
            results.append(prior or {'phase':i,'exit_code':None,'completed':True,
                'seconds':round(previous_events.stat().st_mtime-Path(str(prefix)+'.prompt.txt').stat().st_mtime,3),
                'timing_source':'event-file mtime minus prompt-file mtime; controller killed',
                'usage':[e['usage'] for e in previous if e.get('type')=='turn.completed']})
            continue
        Path(str(prefix) + '.prompt.txt').write_text(phase['prompt'], encoding='utf-8')
        args = [CLI, 'exec'] + (['resume', thread] if thread else []) + options + ['-o', str(prefix) + '.final.txt', '-']
        t = time.time()
        with open(str(prefix) + '.events.jsonl', 'wb') as stdout, open(str(prefix) + '.stderr.txt', 'wb') as stderr:
            proc = subprocess.Popen(args, cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
            dump(dest / 'status.json', {'phase': i, 'phases': len(case['phases']), 'pid': proc.pid, 'state': 'running', 'thread_id': thread})
            try:
                proc.communicate(phase['prompt'].encode('utf-8'), timeout=case['timeout_seconds'])
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True)
                proc.wait()
        events = []
        for line in Path(str(prefix) + '.events.jsonl').read_text(encoding='utf-8').splitlines():
            try: events.append(json.loads(line))
            except ValueError: pass
        for event in events:
            if event.get('type') == 'thread.started': thread = event['thread_id']
        complete = any(e.get('type') == 'turn.completed' for e in events)
        result = {'phase': i, 'exit_code': proc.returncode, 'completed': complete, 'seconds': round(time.time()-t, 3),
                  'usage': [e.get('usage') for e in events if e.get('type') == 'turn.completed']}
        results.append(result)
        dump(dest / 'progress.json', results)
        print(cid, i, 'completed' if complete else 'FAILED', result['seconds'], flush=True)
        if not complete: break
    dump(dest / 'final-files.json', hashes(workspace))
    dump(dest / 'status.json', {'state': 'completed' if len(results)==len(case['phases']) and all(r['completed'] for r in results) else 'interrupted',
                              'thread_id': thread, 'seconds': round(sum(r['seconds'] for r in results),3), 'phases': results})

if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    suite = json.loads((SOURCE / 'boundary-efficiency-v2.json').read_text(encoding='utf-8'))
    cases = [next(c for c in suite['cases'] if c['id']==cid) for cid in IDS]
    dump(OUT / 'frozen-cases.json', cases)
    dump(OUT / ('recovery-execution.json' if (OUT/'execution.json').exists() else 'execution.json'), {'model':'gpt-5.6-luna', 'reasoning':'medium', 'cli':CLI, 'jobs':2,
        'started':time.time(), 'pid':os.getpid(), 'workspace_root':str(WORK), 'suite_sha256':hashlib.sha256((SOURCE/'boundary-efficiency-v2.json').read_bytes()).hexdigest(),
        'differences':['Codex host workspace-write versus MiniClaw Docker tools', 'Codex native system/tools/context management',
                       'Original post-run budgets retained; Codex has no matched 32-step control', 'One trial each; exposed regression']})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run_case, cases))
