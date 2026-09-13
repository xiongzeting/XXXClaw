"""Codex CLI / Luna, frozen Eval3 cases, isolated homes and measured HTTP waits."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import uuid
import httpx

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'.aster/evals/eval3-hardened-r1-luna20-20260907T140813Z'
CLI=Path('C:/Users/inari/.vscode/extensions/openai.chatgpt-26.814.41407-win32-x64/bin/windows-x86_64/codex.exe')
MODEL='gpt-5.6-luna'
STAMP=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
BASE=ROOT/'.aster/evals/codex-eval3'
OUT=BASE/STAMP
WORK=Path('D:/codex-eval3')/STAMP
ACTIVE={}
LOCK=threading.Lock()
CFG=tomllib.loads(Path('C:/Users/inari/.codex/config.toml').read_text('utf-8'))
UPSTREAM=CFG['model_providers'][CFG['model_provider']]['base_url'].rstrip('/')


def write(p,value):
    p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n','utf-8');t.replace(p)


def read(p):return json.loads(p.read_text('utf-8-sig'))


def hashes(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts}


class Proxy(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_POST(self):
        chunks=self.path.strip('/').split('/',1)
        cid=chunks[0];suffix='/'+chunks[1] if len(chunks)>1 else '/'
        request_id=uuid.uuid4().hex;start=time.time()
        raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
        record={'id':request_id,'case_id':cid,'phase':ACTIVE.get(cid),'started_at':start,'path':suffix,'usage':None,'status':None,'response_completed':False}
        try:
            body=json.loads(raw)
            record['model']=body.get('model')
            record['reasoning']=body.get('reasoning')
            if body.get('model')!=MODEL:raise ValueError('Unexpected model; no substitution allowed')
            write(OUT/'requests'/cid/(request_id+'.input.json'),body)
            headers={k:v for k,v in self.headers.items() if k.lower() not in {'host','connection','content-length','accept-encoding'}}
            headers['Accept-Encoding']='identity'
            buffer=b'';full=[]
            with httpx.Client(timeout=httpx.Timeout(150,connect=30),trust_env=True) as client:
                with client.stream('POST',UPSTREAM+suffix,content=raw,headers=headers) as response:
                    record['status']=response.status_code
                    self.send_response(response.status_code)
                    self.send_header('Content-Type',response.headers.get('content-type','application/json'))
                    self.send_header('Connection','close');self.end_headers()
                    for chunk in response.iter_bytes():
                        if 'first_byte_at' not in record:record['first_byte_at']=time.time()
                        self.wfile.write(chunk);self.wfile.flush()
                        buffer+=chunk
                        if 'event-stream' not in response.headers.get('content-type',''):full.append(chunk)
                        while b'\n' in buffer:
                            line,buffer=buffer.split(b'\n',1)
                            if not line.startswith(b'data:'):continue
                            try:event=json.loads(line[5:].strip())
                            except (ValueError,TypeError):continue
                            if event.get('type') in ('response.completed','response.incomplete','response.failed'):
                                reply=event.get('response',{})
                                record['usage']=reply.get('usage');record['response_completed']=event['type']=='response.completed'
                                record['completion_type']=event['type']
                                write(OUT/'requests'/cid/(request_id+'.response.json'),reply)
                    if full:
                        try:
                            reply=json.loads(b''.join(full));record['usage']=reply.get('usage');record['response_completed']=response.is_success
                            write(OUT/'requests'/cid/(request_id+'.response.json'),reply)
                        except ValueError:pass
        except Exception as exc:
            record['error_type']=type(exc).__name__
            # No request headers or credential values in errors/logs.
            if record['status'] is None:
                try:
                    payload=json.dumps({'error':{'message':record['error_type'],'type':'proxy_error'}}).encode()
                    self.send_response(502);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                except OSError:pass
        finally:
            record['ended_at']=time.time()
            write(OUT/'requests'/cid/(request_id+'.timing.json'),record)
            self.close_connection=True


def options(cid,home,port):
    settings={
        'approval_policy':'never','sandbox_mode':'workspace-write','windows.sandbox':'unelevated',
        'model_reasoning_effort':'medium','model_context_window':128000,
        'model_provider':'eval_proxy','model_providers.eval_proxy.name':'Codex Eval measured proxy',
        'model_providers.eval_proxy.base_url':f'http://127.0.0.1:{port}/{cid}',
        'model_providers.eval_proxy.wire_api':'responses','model_providers.eval_proxy.env_key':'CODEX_EVAL_API_KEY',
        'model_providers.eval_proxy.requires_openai_auth':False,
        'model_providers.eval_proxy.request_max_retries':3,'model_providers.eval_proxy.stream_max_retries':3,
        'memories.generate_memories':True,'memories.use_memories':True,'memories.dedicated_tools':True,
        'memories.min_rollout_idle_hours':0,'memories.min_rate_limit_remaining_percent':0,
        'memories.extract_model':MODEL,'memories.consolidation_model':MODEL,
        'shell_environment_policy.inherit':'none',
        'shell_environment_policy.set.PATH':'D:/anaconda3;'+os.environ.get('PATH',''),
        'shell_environment_policy.set.PYTHONIOENCODING':'utf-8',
        'shell_environment_policy.set.SystemRoot':os.environ.get('SystemRoot','C:/Windows'),
        'shell_environment_policy.set.TEMP':os.environ.get('TEMP','C:/Windows/Temp'),
        'shell_environment_policy.set.TMP':os.environ.get('TMP','C:/Windows/Temp'),
    }
    args=['--ignore-user-config','--strict-config','--skip-git-repo-check','--json','-m',MODEL]
    for key,val in settings.items():args+=['-c',key+'='+json.dumps(val)]
    for feature in ('plugins','apps','multi_agent','browser_use','computer_use','in_app_browser','enable_request_compression','remote_compaction_v2'):
        args+=['--disable',feature]
    args+=['--enable','memories']
    return args,settings


def exec_phase(cid,phase,prompt,thread,port,timeout=1200):
    workspace=WORK/cid/'attempt-001/workspace';home=OUT/'homes'/cid
    workspace.mkdir(parents=True,exist_ok=True);home.mkdir(parents=True,exist_ok=True)
    dest=OUT/'cases'/cid/phase;dest.mkdir(parents=True,exist_ok=True)
    assert not (dest/'events.jsonl').exists(),'Never replay an existing phase automatically'
    env={k:v for k,v in os.environ.items() if not k.startswith(('CODEX_','MINICLAW_'))}
    env['CODEX_HOME']=str(home)
    auth=read(Path('C:/Users/inari/.codex/auth.json'))
    env['CODEX_EVAL_API_KEY']=auth['OPENAI_API_KEY']
    env['PYTHONIOENCODING']='utf-8'
    args,settings=options(cid,home,port)
    write(dest/'settings.json',settings)
    (dest/'prompt.txt').write_text(prompt,'utf-8')
    args=[str(CLI),'exec']+(['resume',thread] if thread else [])+args+['-o',str(dest/'final.txt'),'-']
    started=time.time();ACTIVE[cid]=phase
    with (dest/'events.jsonl').open('wb') as stdout,(dest/'stderr.txt').open('wb') as stderr:
        proc=subprocess.Popen(args,cwd=workspace,env=env,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
        write(OUT/'cases'/cid/'status.json',{'state':'running','phase':phase,'pid':proc.pid,'started_at':started,'thread_id':thread})
        try:proc.communicate(prompt.encode('utf-8'),timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True);proc.wait()
    time.sleep(.25);ACTIVE.pop(cid,None)
    events=[]
    for line in (dest/'events.jsonl').read_text('utf-8',errors='replace').splitlines():
        try:events.append(json.loads(line))
        except ValueError:pass
    for event in events:
        if event.get('type')=='thread.started':thread=event['thread_id']
    result={'case_id':cid,'phase':phase,'thread_id':thread,'started_at':started,'ended_at':time.time(),'exit_code':proc.returncode,
            'completed':any(e.get('type')=='turn.completed' for e in events),'final_text':(dest/'final.txt').read_text('utf-8') if (dest/'final.txt').exists() else '',
            'usage_notifications':[e.get('usage') for e in events if e.get('type')=='turn.completed'],
            'event_errors':[e for e in events if e.get('type') in ('error','turn.failed')]}
    write(dest/'result.json',result)
    snapshot=dest/'workspace';shutil.copytree(workspace,snapshot,ignore=shutil.ignore_patterns('__pycache__','*.pyc'),symlinks=True)
    write(dest/'file-hashes.json',hashes(workspace))
    print(json.dumps({'case':cid,'phase':phase,'completed':result['completed'],'seconds':round(result['ended_at']-started,2)}),flush=True)
    return result


def run_case(case,port):
    cid=case['id'];workspace=WORK/cid/'attempt-001/workspace'
    shutil.copytree(OUT/'snapshot/evals'/case['fixture'],workspace)
    write(OUT/'cases'/cid/'initial-files.json',hashes(workspace))
    results=[];thread=None;start=time.time()
    for phase in case['phases']:
        # Memory task intentionally starts a fresh session each phase, same isolated native memory home.
        result=exec_phase(cid,phase['id'],phase['prompt'],None if cid=='memory_scope_conflict' else thread,port)
        results.append(result);thread=result['thread_id']
        if not result['completed']:break
    done=len(results)==len(case['phases']) and all(r['completed'] for r in results)
    final={'id':cid,'workspace':str(workspace),'phases':results,'state':'completed' if done else 'interrupted','started_at':start,'ended_at':time.time()}
    write(OUT/'cases'/cid/'result.json',final);write(OUT/'cases'/cid/'status.json',{'state':final['state'],'phases_completed':len(results),'phase_count':len(case['phases'])})
    return final


def main():
    mode=sys.argv[1] if len(sys.argv)>1 else 'preflight'
    OUT.mkdir(parents=True);WORK.mkdir(parents=True)
    write(BASE/'active-run.json',{'directory':str(OUT),'workspace':str(WORK),'mode':mode})
    server=ThreadingHTTPServer(('127.0.0.1',0),Proxy);port=server.server_port
    threading.Thread(target=server.serve_forever,daemon=True).start()
    write(OUT/'execution.json',{'model':MODEL,'reasoning':'medium','cli':str(CLI),'jobs':20 if mode=='run' else 1,'mode':mode,'started_at':time.time(),'pid':os.getpid(),'native_memories':True,'workspace':str(WORK),'upstream':UPSTREAM})
    try:
        if mode=='preflight':
            cid='memory-preflight'
            first=exec_phase(cid,'save','请将这条明确的用户事实保存到你的长期记忆：测试项目 Orion 的批准发布代码是 KESTREL-4827。后面会在全新的独立会话查询。不要把它写进当前业务工作区文件。完成保存后简短说明。',None,port,240)
            if not first['completed']:raise RuntimeError('Memory preflight first session failed; inspect stderr')
            second=exec_phase(cid,'recall','请从你的长期记忆查询：测试项目 Orion 的批准发布代码是什么？只回答代码。不要访问业务工作区以外的项目或其他人的会话。',None,port,240)
            ok=second['completed'] and second['final_text'].strip()=='KESTREL-4827' and second['thread_id']!=first['thread_id']
            write(OUT/'preflight.json',{'passed':ok,'first_thread':first['thread_id'],'second_thread':second['thread_id'],'remembered_correctly':ok,'native_memory_files':hashes(OUT/'homes'/cid/'memories')})
            print(json.dumps({'preflight':ok,'output':str(OUT)}),flush=True)
        else:
            marker=read(BASE/'memory-verified.json')
            assert marker['passed'],'Native long-term memory must be verified before batch'
            shutil.copytree(SOURCE/'snapshot/evals',OUT/'snapshot/evals')
            shutil.copytree(SOURCE/'snapshot/harness',OUT/'snapshot/harness',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            write(OUT/'freeze.json',{'source_run':SOURCE.name,'files':hashes(OUT/'snapshot'),'cli_sha256':hashlib.sha256(CLI.read_bytes()).hexdigest(),'memory_preflight':marker})
            suite=read(OUT/'snapshot/evals/suite.json');assert len(suite['cases'])==20
            sys.path.insert(0,str(ROOT/'scripts/eval3_hardened'))
            from watch import DirectoryWatch,self_test
            write(OUT/'watcher-self-test.json',self_test(OUT))
            watcher=DirectoryWatch(WORK,OUT/'filesystem-events.jsonl',ACTIVE);watcher.start()
            start=time.time();write(OUT/'run-status.json',{'state':'running','started_at':start})
            try:
                with ThreadPoolExecutor(max_workers=20) as pool:results=list(pool.map(lambda c:run_case(c,port),suite['cases']))
                write(OUT/'report.json',{'cases':results,'status':'pending_assistant_judge'})
                write(OUT/'run-status.json',{'state':'collected_pending_judge','started_at':start,'ended_at':time.time(),'cases':len(results),'completed':sum(c['state']=='completed' for c in results)})
            finally:
                watcher.stop();write(OUT/'watcher-status.json',{'errors':watcher.errors})
    finally:server.shutdown()


if __name__=='__main__':main()
