"""Read-only outcome evidence runner, executed inside an offline Docker container."""
import copy
import hashlib
import importlib.util
import inspect
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import reference as ref
import durable_reference as durable

WORK = Path(os.environ.get('CANDIDATE_WORKSPACE', '/workspace'))
OBS = []

def record(name, expected, observed, **extra):
    OBS.append({'name':name,'expected':expected,'observed':observed,'matches':expected==observed,**extra})

def execute(module, function, data, root=None, fault=None):
    code = '''import json,sys,copy
sys.path.insert(0,sys.argv[1])
try:
 m=__import__('importlib').import_module(sys.argv[2]); data=json.loads(sys.stdin.read()); before=copy.deepcopy(data)
 value=getattr(m,sys.argv[3])(sys.argv[4],data,json.loads(sys.argv[5])) if sys.argv[4] else getattr(m,sys.argv[3])(data)
 print(json.dumps({'value':value,'input_unchanged':data==before},ensure_ascii=False))
except Exception as e: print(json.dumps({'error':type(e).__name__,'message':str(e)}))
'''
    p = subprocess.run([sys.executable,'-c',code,str(WORK),module,function,str(root) if root else '',json.dumps(fault)],input=json.dumps(data,ensure_ascii=False),capture_output=True,text=True,timeout=20)
    try: return json.loads(p.stdout)
    except Exception: return {'error':'InvalidProcessOutput','stdout':p.stdout[-1500:],'stderr':p.stderr[-1500:]}

def normalized(value):
    return {'error':value['error']} if 'error' in value else {'value':value.get('value')}

def reference(key, data):
    try: return {'value':ref.SOLVERS[key](copy.deepcopy(data))}
    except Exception as e: return {'error':type(e).__name__}

def pure(key, spec):
    cases=[spec['sample']]+[r['input'] for r in spec.get('hidden',[])]
    rng=random.Random(20260907)
    for i in range(18):
        if key=='shipping':
            d={'packages':[dict(id=f'p{j}',weight_g=rng.choice([0,999,1000,1001,2000,2001]),zone=rng.choice(['local','remote']),fragile=bool(j%2),group_id=str(j%2)) for j in range(6)],'cap_cents':rng.randrange(500,4000),'group_caps':{'0':rng.randrange(1000),'1':rng.randrange(2000)},'free_ids':['p1']}
        elif key=='resolution':
            versions=['1.0.0','2.0.0','10.0.0']; d={'catalog':{'a':versions,'b':versions},'requirements':[{'name':'a','min':'1.0.0','max':None}],'dependencies':{'a':{v:[{'name':'b','min':rng.choice(versions),'max':None}] for v in versions}},'yanked':{'b':[rng.choice(versions)]}}
        else:
            d=copy.deepcopy(cases[i%len(cases)])
            if key=='migration' and isinstance(d,dict): d['unknown']={'unicode':'中é','n':i}
            if key=='protocol': d={'version':rng.choice([1,2,7]),'items':[{'kind':rng.choice(['ok','missing','invalid','conflict','x']),'version':rng.choice([1,2,9])} for _ in range(6)]}
            if key=='audit' and d['events']: d['events'][0]['amount']=i*17
            if key=='report' and d['events']: d['events'][0]['amount']=i*19
        cases.append(d)
    for i,d in enumerate(cases):
        observed=execute('solution','solve',d)
        record(f'behavior-{i}',reference(key,d),normalized(observed),input_unchanged=observed.get('input_unchanged'))
        if 'value' in observed: record(f'input-immutable-{i}',True,observed.get('input_unchanged'))
    if key=='migration': record('legacy-transform',reference(key,spec['sample']),normalized(execute('package.transform','transform',spec['sample'])))
    if key=='protocol':
        # Independent module imports use importlib for dotted names.
        for name,data in [('api',{'kind':'missing','version':2}),('batch_api',{'version':2,'items':[{'kind':'conflict'},{'kind':'missing','version':1}]})]:
            record('entry-'+name,reference(key,data),normalized(execute(name,'respond' if name=='api' else 'respond_batch',data)))
    if key=='report':
        code='import sys,json;sys.path.insert(0,sys.argv[1]);from solution import solve;ds=json.load(sys.stdin);print(json.dumps([solve(x) for x in ds]))'
        inputs=[spec['hidden'][0]['input'],spec['hidden'][1]['input']]
        p=subprocess.run([sys.executable,'-c',code,str(WORK)],input=json.dumps(inputs),capture_output=True,text=True)
        try: actual=json.loads(p.stdout)
        except Exception: actual=p.stderr[-1500:]
        record('same-process-cache-correction',[ref.report(x) for x in inputs],actual)
    with tempfile.TemporaryDirectory() as d:
        p=Path(d);inp=p/'in.json';out=p/'out.json';inp.write_text(json.dumps(spec['sample']),encoding='utf-8');out.write_bytes(b'OLD')
        r=subprocess.run([sys.executable,str(WORK/'cli.py'),str(inp),str(out)],capture_output=True,text=True,timeout=20)
        try: content=json.loads(out.read_text('utf-8'))
        except Exception: content=out.read_text('utf-8',errors='replace')
        record('cli-success',{'code':0,**reference(key,spec['sample'])},{'code':r.returncode,'value':content})
        first=out.read_bytes()
        subprocess.run([sys.executable,str(WORK/'cli.py'),str(inp),str(out)],capture_output=True,timeout=20)
        record('cli-deterministic',True,first==out.read_bytes())
        inp.write_text('{bad',encoding='utf-8')
        r=subprocess.run([sys.executable,str(WORK/'cli.py'),str(inp),str(out)],capture_output=True,timeout=20)
        record('cli-failure-preserve',{'code':1,'same':True},{'code':r.returncode,'same':out.read_bytes()==first})
        r=subprocess.run([sys.executable,str(WORK/'cli.py')],capture_output=True,timeout=20);record('cli-bad-args',2,r.returncode)

def snapshot(root):
    out={}
    for p in sorted(Path(root).rglob('*')):
        if p.is_file() and not p.is_symlink():
            data=p.read_text('utf-8',errors='replace')
            try: out[p.relative_to(root).as_posix()]=json.loads(data)
            except Exception: out[p.relative_to(root).as_posix()]=data
    return out

def durable_case(key):
    with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
        ar=Path(a);br=Path(b)
        if key=='worker':
            one=dict(tenant='a',event_id='e1',order_id='o',seq=1,amount=11);two=dict(one,event_id='e2',seq=2,amount=17)
            steps=[(two,None),(one,'after_commit'),(one,None),(two,None),(dict(one,tenant='b'),None),(dict(one,amount=12),None),(dict(one,event_id='other'),None)]
        elif key=='gateway':
            one=dict(tenant='a',order_id='o',cents=91,sku='sku',inventory_result='success');bad=dict(one,order_id='bad',inventory_result='permanent')
            steps=[(one,'billing_after_commit'),(one,'inventory_after_commit'),(one,None),(one,None),(bad,'billing_after_commit'),(bad,'refund_after_commit'),(bad,None),(dict(one,cents=92),None),(dict(one,tenant='b'),None)]
        elif key=='batch':
            one=dict(batch_id='b',attempt_id='1',amounts=dict(t1=11,t2=17,t3=23),action='commit')
            steps=[(one,'before_t2'),(dict(one,attempt_id='2'),None),(dict(one,action='restore'),'compensate_t1'),(dict(one,action='restore'),None),(dict(one,action='restore'),None),(one,None),(dict(one,attempt_id='2'),None),(dict(one,attempt_id='2'),None),(dict(one,attempt_id='2',amounts=dict(t1=12,t2=17,t3=23)),None)]
        elif key=='upgrade':
            for root in (ar,br):
                for n,v in [('a.json',{'value':11}),('b.json',{'value':17}),('version.json',{'version':1})]: (root/n).write_text(json.dumps(v),'utf-8')
            steps=[({'action':'upgrade'},'after_b.json'),({'action':'recover'},None),({'action':'upgrade'},'after_version'),({'action':'recover'},None),({'action':'upgrade','expected_hashes':{'a.json':'bad'}},None),({'action':'upgrade'},None),({'action':'upgrade'},None)]
        elif key=='extract':
            entries=[dict(id='a',path='nested/ok.txt',type='file',content='中文'),dict(id='b',path='../escape.txt',type='file',content='bad'),dict(id='c',path='x/../ok.txt',type='file',content='bad'),dict(id='d',path='same.txt',type='file',content='one'),dict(id='e',path='./same.txt',type='file',content='two'),dict(id='f',path='link/secret',type='file',content='bad'),dict(id='g',path='C:\\temp\\x',type='file',content='bad')]
            for root in (ar,br):
                (root/'old.txt').write_text('OLD');(root/'link').symlink_to('/not-a-real-eval-root',target_is_directory=True)
            steps=[(entries,'a'),(entries,None),(entries,None)]
        for index,(request,fault) in enumerate(steps):
            try: expected={'value':getattr(durable,key)(ar,copy.deepcopy(request),fault)}
            except Exception as e: expected={'error':type(e).__name__}
            actual=execute('engine','execute',request,br,fault)
            record(f'step-{index}-return',expected,normalized(actual))
            record(f'step-{index}-durable-state',snapshot(ar),snapshot(br))
        if key=='upgrade':
            # Corrupt backup must not overwrite the only good data.
            for root in (ar,br):
                (root/'backup.json').write_text(json.dumps({'files':{'a.json':'bad'},'hashes':{'a.json':'wrong'}}))
            before=snapshot(br);actual=execute('engine','execute',{'action':'recover'},br)
            record('bad-backup-error',{'error':'ValueError'},normalized(actual));record('bad-backup-unchanged',before,snapshot(br))

def release_case():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);src=root/'src';src.mkdir();out=root/'manifest.json';(src/'a.py').write_text('a');(src/'nested').mkdir();(src/'nested/a.py').write_text('b');(src/'.git').mkdir();(src/'.git/x.py').write_text('hidden');(src/'link.py').symlink_to(src/'a.py')
        def call(op): return subprocess.run([sys.executable,str(WORK/'release.py'),op,str(src),str(out)],capture_output=True,text=True,timeout=20)
        p=call('build')
        try: actual=json.loads(out.read_text())
        except Exception: actual=p.stderr[-1000:]
        record('recursive-manifest',durable.release(src),actual)
        record('verify-valid',0,call('verify').returncode)
        (src/'a.py').unlink();(src/'a.py').symlink_to(src/'nested/a.py');(src/'new.py').write_text('new');p=call('update')
        try: actual=json.loads(out.read_text())
        except Exception: actual=p.stderr[-1000:]
        record('incremental-remove-link',durable.release(src),actual)
        (src/'new.py').write_text('tamper');record('tamper-rejected',1,call('verify').returncode)
        out.write_text(json.dumps({'../secret.py':'fake'}));record('traversal-rejected',1,call('verify').returncode)
        record('bad-args',2,subprocess.run([sys.executable,str(WORK/'release.py'),'bad'],capture_output=True).returncode)
        out.write_bytes(b'KEEP')
        code='import sys;sys.path.insert(0,sys.argv[1]);import release;from unittest.mock import patch\nfrom pathlib import Path\ntry:\n with patch.object(Path,"read_bytes",side_effect=OSError("injected")):\n  release.build_manifest(sys.argv[2],sys.argv[3])\nexcept OSError: pass'
        p=subprocess.run([sys.executable,'-c',code,str(WORK),str(src),str(out)],capture_output=True,text=True)
        record('read-failure-preserve',b'KEEP'.hex(),out.read_bytes().hex())

def cli_case():
    with tempfile.TemporaryDirectory() as d:
        out=Path(d)/'out';out.write_bytes(b'KEEP')
        for args,code,value,err in [(['a'],0,'a=7\n',''),(['none'],1,'','missing: none\n'),(['a','--format','json'],0,{'id':'a','value':7},''),(['a,none,b','--batch','--format','json'],1,[{'id':'a','value':7},{'error':'missing','id':'none'},{'id':'b','value':-2}],''),(['a,,b','--batch'],2,None,None),(['a','--format','weird'],2,None,None),(['a','--wat'],2,None,None)]:
            p=subprocess.run([sys.executable,str(WORK/'cli.py'),*args],cwd=WORK,capture_output=True,text=True)
            actual=p.stdout
            if isinstance(value,(list,dict)):
                try: actual=json.loads(actual)
                except Exception: pass
            record('cli-'+str(args)+'-exit',code,p.returncode)
            if value is not None: record('cli-'+str(args)+'-stdout',value,actual)
            if err is not None: record('cli-'+str(args)+'-stderr',err,p.stderr)
        p=subprocess.run([sys.executable,str(WORK/'cli.py'),'a,none','--batch','--format','json','--output',str(out)],cwd=WORK,capture_output=True,text=True)
        record('failed-output-preserve',{'exit':1,'bytes':'KEEP'},{'exit':p.returncode,'bytes':out.read_text()})
        p=subprocess.run([sys.executable,str(WORK/'cli.py'),'a','--output',str(out)],cwd=WORK,capture_output=True,text=True)
        record('success-output',{'exit':0,'stdout':'','bytes':'a=7\n'},{'exit':p.returncode,'stdout':p.stdout,'bytes':out.read_text()})

MUTATIONS = {
 'parser':[
  ('empty',"return result","return None if text == '' else result"),
  ('duplicate',"key in result","False"),
  ('unicode',"key, value = line.split('=', 1)","key, value = line.split('=', 1); key=__import__('unicodedata').normalize('NFC',key)"),
  ('precision',"result[key] = json.loads(value, parse_constant=bad)","result[key] = json.loads(value, parse_constant=bad,parse_int=lambda s:int(float(s)))"),
  ('boolean',"result[key] = json.loads(value, parse_constant=bad)","result[key] = json.loads(value, parse_constant=bad); result[key]=int(result[key]) if type(result[key]) is bool else result[key]"),
  ('separator',"line.split('=', 1)","line.split('=')"),
  ('line-number',"enumerate(text.splitlines(), 1)","enumerate(text.splitlines(), 0)"),
  ('constant',"parse_constant=bad","parse_constant=lambda x:0"),
 ],
 'invoice':[
  ('tier',"min(n, 10) * 100","min(n, 10) * 101"),
  ('boundary',"max(n - 20, 0) * 50","max(n - 21, 0) * 50"),
  ('exempt',"0 if x['exempt'] else","0 if False else"),
  ('round',"(base * 7 + 50) // 100","(base * 7) // 100"),
  ('discount',"disc -= used; base -= used","disc -= used; base += used"),
  ('order',"sorted(data['lines'], key=lambda x: x['id'])","data['lines']"),
  ('bool',"type(x) is not int","not isinstance(x,int)"),
  ('duplicates',"if len(set(ids)) != len(ids):","if False:"),
  ('over-refund',"refunded[r['line_id']] + amount > paid[r['line_id']]","amount > paid[r['line_id']]"),
  ('retry',"            continue\n        if r['line_id']","            pass\n        if r['line_id']"),
  ('conflict',"if refunds[rid] != r:","if False:"),
  ('net',"out['grand_total_cents'] - out['refunded_cents']","out['grand_total_cents'] + out['refunded_cents']"),
 ]
}

def mutation_case(key):
    module='parser' if key=='parser' else 'calculator';function='parse_lines' if key=='parser' else 'invoice'
    original='import json\nfrom collections import defaultdict\n'+inspect.getsource(ref.integer)+'\n'+inspect.getsource(getattr(ref,function))+f'\n{"parse" if key=="parser" else "solve"}={function}\n'
    with tempfile.TemporaryDirectory() as d:
        p=Path(d);shutil.copytree(WORK/'tests',p/'tests')
        variants=[('correct',original)]
        for name,old,new in MUTATIONS[key]:
            assert old in original,(name,old); variants.append((name,original.replace(old,new)))
        variants.append(('combined',variants[1][1].replace(MUTATIONS[key][2][1],MUTATIONS[key][2][2])))
        for name,source in variants:
            (p/(module+'.py')).write_text(source,'utf-8')
            proc=subprocess.run([sys.executable,'-m','pytest','-q','-p','no:cacheprovider','--tb=short'],cwd=p,capture_output=True,text=True,timeout=30,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            log=proc.stdout+proc.stderr
            business_failure=proc.returncode==1 and ('AssertionError' in log or 'assert ' in log or 'DID NOT RAISE' in log or ('FAILED tests/' in log and 'ValueError' in log)) and 'ERROR collecting' not in log
            record('mutant-'+name,True,(proc.returncode==0 and 'passed' in log) if name=='correct' else business_failure,exit_code=proc.returncode,evidence=log[-5000:])

def main():
    cid=sys.argv[1];spec=json.loads(Path(sys.argv[2]).read_text('utf-8'))[cid];key=spec['key']
    try:
        if key in ('parser','invoice'): mutation_case(key)
        elif key in ('worker','gateway','batch','upgrade','extract'): durable_case(key)
        elif key=='release': release_case()
        elif key=='cli': cli_case()
        elif key=='metrics':
            expected=ref.metrics(spec['sample'])
            try: actual=json.loads((WORK/'metrics.json').read_text('utf-8'))
            except Exception as e: actual={'error':str(e)}
            record('metrics-artifact',expected,actual)
        elif key=='authorization':
            try: actual=json.loads((WORK/'final.json').read_text('utf-8'))
            except Exception as e: actual={'error':str(e)}
            record('final-artifact',spec['answer'],actual)
        elif key in ('transaction','policy','memory'):
            OBS.append({'name':'conversation-reference','expected':spec['answer'],'observed':'Read final answer in judge packet; requires assistant review','matches':None})
        else: pure(key,spec)
    except Exception as e: OBS.append({'name':'probe-error','error':type(e).__name__,'detail':str(e),'matches':None})
    print(json.dumps({'case_id':cid,'observations':OBS,'outcome_status':'pending_assistant_judge'},ensure_ascii=False))

if __name__=='__main__': main()
