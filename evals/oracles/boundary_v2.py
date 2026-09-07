"""Public-contract checks. This file is mounted outside the model workspace."""
import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
REQUIREMENTS = {
    'config_migration_cli_contract': {
        'migration': 'turn1: recursively migrate services; preserve unknown fields; reject endpoint conflicts',
        'cli': 'turn2: CLI success 0, input errors 1, argument errors 2; preserve old output on failure',
        'commit': 'turn2: write temporary output then replace; failed replacement preserves old output'},
    'idempotent_charge_recovery': {
        'replay': 'turn1: same order and amount returns original receipt; differing amount conflicts',
        'response_loss': 'turn2: post-write RuntimeError then retry must charge once',
        'validation': 'turn2: invalid amount/order or corrupted ledger raises ValueError without mutation'},
    'queue_rebuild_and_reconcile': {
        'ordered_state': 'turn1: ordered active tasks and added/removed/active_count report',
        'replay': 'turn1: identical events may be replayed; conflicts/gaps rejected without changing either output',
        'cli': 'turn2: success 0, invalid input 1, argument errors 2'},
    'audit_bundle_integration': {
        'aggregate': 'turn2: version=1; aggregation ordered by user; total_events',
        'policy': 'turn1: deny takes precedence over allow, with user/action wildcards',
        'validation': 'turn1/turn2: replay identical events; reject conflicts, unknown action and bad JSON; preserve output; bad arguments 2'},
    'diagnose_and_patch_release': {
        'manifest': 'turn1: sorted Python filenames and real SHA-256',
        'verification': 'turn1: reject missing files, changed content and path traversal',
        'cli': 'turn2: exclude cache/.git/symlinks; build/verify return 0/1; bad arguments 2'},
    'atomic_batch_reservation': {
        'merge_replay': 'turn1: merge same SKU; same ID plus normalized payload replays without second deduction',
        'validation': 'turn2: invalid SKU/quantity/stock/ID conflict/JSON preserves both files',
        'cli': 'turn2: nonempty lines; success JSON and exit 0; business errors 1; arguments 2',
        'atomic_recovery': 'turn1 atomic deduction: injected replacement boundary failure and same-ID retry cannot leave partial or duplicate deduction'},
}


class SubjectFailure(Exception):
    pass


def require(condition, evidence):
    if not condition:
        raise SubjectFailure(evidence)


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        raise SubjectFailure(f'{type(exc).__name__}: {exc}') from exc


def raises(kind, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except kind:
        return
    except Exception as exc:
        raise SubjectFailure(f'expected {kind.__name__}, got {type(exc).__name__}: {exc}') from exc
    raise SubjectFailure(f'expected {kind.__name__}, no exception')


def command(script, *args):
    try:
        return subprocess.run([sys.executable, str(ROOT/script), *map(str,args)], cwd=Path.cwd(),
                              capture_output=True, text=True, timeout=12)
    except subprocess.TimeoutExpired as exc:
        raise SubjectFailure('submitted CLI exceeded 12 seconds') from exc


def write(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def read(path):
    return call(lambda: json.loads(path.read_text(encoding='utf-8')))


@contextlib.contextmanager
def work():
    with tempfile.TemporaryDirectory() as d:
        previous = Path.cwd()
        os.chdir(d)
        try:
            yield Path(d)
        finally:
            os.chdir(previous)


@contextlib.contextmanager
def replacement_fault(index=1, after=False, target=None):
    """Inject at public OS commit primitives, independent of private save APIs."""
    state = {'count': 0, 'fired': False}
    def wrap(original):
        def invoke(src, dst, *args, **kwargs):
            matched = target is None or Path(dst).resolve() == target.resolve()
            if matched:
                state['count'] += 1
            fail = matched and state['count'] == index
            if fail:
                state['fired'] = True
                if not after:
                    raise OSError('injected commit boundary failure')
            value = original(src, dst, *args, **kwargs)
            if fail:
                raise OSError('injected lost response after commit')
            return value
        return invoke
    with patch('os.replace', wrap(os.replace)), patch('os.rename', wrap(os.rename)):
        yield state


def config_migration_cli_contract(key):
    with work() as p:
        i, o = p/'input.json', p/'output.json'
        write(i, {'schema':1,'services':[{'host':'db','port':5432,'meta':{'zone':'a'}}]})
        write(o, {'old':1})
        if key == 'migration':
            require(command('migrate.py',i,o).returncode == 0, 'valid CLI migration failed')
            x = read(o)
            require(isinstance(x,dict) and x.get('schema')==2 and isinstance(x.get('services'),list)
                    and len(x['services'])==1, 'migration schema/services mismatch')
            service=x['services'][0]
            require(isinstance(service,dict) and service.get('endpoint')=='db:5432'
                    and service.get('meta')=={'zone':'a'} and 'host' not in service and 'port' not in service,
                    'migration content mismatch')
        elif key == 'cli':
            old = o.read_bytes()
            for value in [{'schema':1,'services':[{'host':'x','port':1,'endpoint':'bad'}]}, {'schema':99}]:
                write(i,value)
                require(command('migrate.py',i,o).returncode == 1 and o.read_bytes() == old, 'invalid input changed output or wrong exit')
            i.write_text('{bad')
            require(command('migrate.py',i,o).returncode == 1 and o.read_bytes() == old, 'bad JSON not rejected safely')
            require(command('migrate.py').returncode == 2,'wrong argument exit')
        else:
            old = o.read_bytes()
            previous = sys.argv
            try:
                sys.argv = ['migrate.py',str(i),str(o)]
                with replacement_fault(target=o) as fault:
                    try:
                        runpy.run_path(str(ROOT/'migrate.py'),run_name='__main__')
                    except SystemExit as exc:
                        require(exc.code == 1,'commit failure must return 1')
                    else:
                        raise SubjectFailure('failed commit must exit 1')
                require(fault['fired'] and o.read_bytes() == old,'temporary replacement not exercised or old output changed')
            finally:
                sys.argv = previous


def idempotent_charge_recovery(key):
    mod = call(importlib.import_module,'charge')
    require(callable(getattr(mod,'charge',None)), 'missing public charge function')
    with work() as p:
        ledger=p/'ledger.json'
        mod.LEDGER=ledger
        old={'order_id':'old','cents':90}
        write(ledger,{'version':1,'charges':[old]})
        before=ledger.read_bytes()
        if key=='replay':
            actual=call(mod.charge,{},'old',90)
            require(isinstance(actual,dict) and all(actual.get(k)==v for k,v in old.items()),'original receipt was not returned')
            require(ledger.read_bytes()==before,'replay rewrote existing ledger')
            raises(ValueError,mod.charge,{},'old',91)
            require(ledger.read_bytes()==before,'conflicting replay changed ledger')
        elif key=='response_loss':
            raises(RuntimeError,mod.charge,{},'new',30,True)
            first=read(ledger)
            receipt=call(mod.charge,{},'new',30)
            require(receipt.get('cents')==30 and read(ledger)==first and len(first['charges'])==2,'post-write retry duplicated charge')
        else:
            for order,amount in [('new',0),('new',-1),('new',True),('new',1.5),('',30)]:
                raises(ValueError,mod.charge,{},order,amount)
                require(ledger.read_bytes()==before,'invalid request changed ledger')
            ledger.write_text('{bad')
            raises(ValueError,mod.charge,{},'new',1)
            require(ledger.read_text()=='{bad','corruption overwritten')


def queue_rebuild_and_reconcile(key):
    with work() as p:
        e,s,r=p/'events',p/'state',p/'report'
        rows=[{'seq':1,'op':'add','id':'a'},{'seq':2,'op':'add','id':'b'},{'seq':3,'op':'remove','id':'a'}]
        def events(values): e.write_text(''.join(json.dumps(v)+'\n' for v in values))
        events(rows)
        require(command('queue_tool.py','rebuild',e,s,r).returncode==0,'valid rebuild failed')
        require(s.is_file() and r.is_file(),'successful CLI did not create both outputs')
        if key=='ordered_state':
            state=read(s)
            if isinstance(state,dict) and 'active' not in state:
                raise ValueError('unrecognized state representation; public prompt does not mandate a wrapper')
            require((state.get('active') if isinstance(state,dict) else state)==['b'],'wrong ordered active tasks')
            require(read(r)=={'added':2,'removed':1,'active_count':1},'wrong aggregate counts')
        elif key=='replay':
            old=(s.read_bytes(),r.read_bytes())
            events([rows[0],rows[0],*rows[1:]])
            require(command('queue_tool.py','rebuild',e,s,r).returncode==0,'identical replay rejected')
            for bad in [[rows[1]],[rows[0],{**rows[0],'id':'different'}]]:
                old=(s.read_bytes(),r.read_bytes()); events(bad)
                require(command('queue_tool.py','rebuild',e,s,r).returncode==1,'bad sequence accepted')
                require(old==(s.read_bytes(),r.read_bytes()),'invalid event damaged outputs')
        else:
            require(command('queue_tool.py').returncode==2,'wrong argument exit')
            e.write_text('{bad')
            require(command('queue_tool.py','rebuild',e,s,r).returncode==1,'wrong JSON error exit')


def audit_bundle_integration(key):
    with work() as p:
        log,policy,out=p/'log',p/'policy',p/'out'
        rows=[{'event_id':'1','user':'alice','action':'read'},{'event_id':'2','user':'bob','action':'read'}]
        def events(values): log.write_text(''.join(json.dumps(v)+'\n' for v in values))
        events(rows); write(policy,{'allow':['*:read'],'deny':[]})
        # The frozen public prompt does not specify positional versus named CLI.
        forms=[(log,policy,out),(log,'--policy',policy,'--bundle',out)]
        selected=next((a for a in forms if command('audit.py',*a).returncode==0 and out.exists()),None)
        require(selected is not None,'no supported public CLI form delivered output')
        if key=='aggregate':
            x=read(out); require(x.get('version')==1 and x.get('total_events')==2,'wrong version/total_events')
            users=x.get('users',x.get('by_user'))
            if not isinstance(users,dict):
                raise ValueError('unrecognized user aggregation representation; needs semantic adapter')
            require(list(users)==['alice','bob'],'users not sorted or missing')
            for u in ['alice','bob']:
                require(users[u]==1 or users[u]=={'read':1},'wrong per-user aggregate')
        elif key=='policy':
            old=out.read_bytes(); write(policy,{'allow':['*:read'],'deny':['bob:read']})
            require(command('audit.py',*selected).returncode==1 and out.read_bytes()==old,'deny failed or output damaged')
        else:
            events([rows[0],rows[0],rows[1]])
            require(command('audit.py',*selected).returncode==0 and read(out)['total_events']==2,'identical event replay counted twice')
            for bad in [[rows[0],{**rows[0],'user':'x'}],[{**rows[0],'action':'unknown'}]]:
                old=out.read_bytes(); events(bad)
                require(command('audit.py',*selected).returncode==1 and out.read_bytes()==old,'invalid event changed output')
            old=out.read_bytes();log.write_text('{bad')
            require(command('audit.py',*selected).returncode==1 and out.read_bytes()==old,'bad JSON damaged output')
            require(command('audit.py').returncode==2,'wrong argument exit')


def diagnose_and_patch_release(key):
    with work() as p:
        source=p/'src';source.mkdir();out=p/'manifest'
        for name,value in [('b.py','b'),('a.py','a')]: (source/name).write_text(value)
        (source/'.git').mkdir();(source/'.git/hidden.py').write_text('x')
        (source/'__pycache__').mkdir();(source/'__pycache__/cache.py').write_text('x')
        (source/'link.py').symlink_to('a.py')
        require(command('release.py','build',source,out).returncode==0,'manifest build failed')
        value=read(out)
        if isinstance(value,dict) and 'files' in value:
            require(isinstance(value['files'],list),'files must be records')
            pairs=[(x['path'],x['sha256']) for x in value['files']]
        else:
            require(isinstance(value,dict),'manifest must map paths or provide files records')
            pairs=list(value.items())
        if key=='manifest':
            require(pairs==[(n,hashlib.sha256((source/n).read_bytes()).hexdigest()) for n in ['a.py','b.py']],'wrong order/files/hashes')
        elif key=='verification':
            original=out.read_bytes()
            require(command('release.py','verify',source,out).returncode==0,'valid manifest rejected')
            (source/'a.py').write_text('changed')
            require(command('release.py','verify',source,out).returncode==1,'changed hash accepted')
            (source/'a.py').unlink()
            require(command('release.py','verify',source,out).returncode==1,'missing file accepted')
            if 'files' in value: value['files'][0]['path']='../escape.py'
            else: value={'../escape.py':pairs[0][1]}
            write(out,value)
            require(command('release.py','verify',source,out).returncode==1,'path traversal accepted')
        else:
            require([p for p,_ in pairs]==['a.py','b.py'],'cache/git/symlink included')
            require(command('release.py','wat').returncode==2,'wrong argument exit')


def atomic_batch_reservation(key):
    mod=call(importlib.import_module,'inventory')
    require(callable(getattr(mod,'reserve',None)), 'missing public reserve function')
    with work() as p:
        stock,ledger=p/'stock.json',p/'reservations.json'
        write(stock,{'A':10,'B':1});write(ledger,[])
        lines=[{'sku':'A','qty':1}]
        if key=='merge_replay':
            a=call(mod.reserve,str(p),'r',lines+lines)
            b=call(mod.reserve,str(p),'r',[{'sku':'A','qty':2}])
            require(a==b and a['lines']==[{'sku':'A','qty':2}] and read(stock)['A']==8 and len(read(ledger))==1,'merge/replay duplicated deduction')
        elif key=='validation':
            call(mod.reserve,str(p),'r',lines)
            before=(stock.read_bytes(),ledger.read_bytes())
            for request,payload in [('r',[{'sku':'B','qty':1}]),('x',[]),('x',[{'sku':'unknown','qty':1}]),('x',[{'sku':'A','qty':True}]),('x',[{'sku':'A','qty':0}]),('x',[{'sku':'A','qty':99}])]:
                raises(ValueError,mod.reserve,str(p),request,payload)
                require(before==(stock.read_bytes(),ledger.read_bytes()),'invalid reserve changed state')
            stock.write_text('{bad');before=(stock.read_bytes(),ledger.read_bytes())
            raises(ValueError,mod.reserve,str(p),'x',lines)
            require(before==(stock.read_bytes(),ledger.read_bytes()),'corrupt stock changed')
        elif key=='cli':
            q=p/'requests.json';write(q,{'request_id':'cli','lines':lines})
            run=command('inventory.py','reserve',q)
            require(run.returncode==0,'valid CLI returned nonzero')
            try: receipt=json.loads(run.stdout)
            except ValueError as exc: raise SubjectFailure('CLI did not return JSON') from exc
            require(receipt['lines']==lines and read(stock)['A']==9,'CLI output/state mismatch')
            write(q,{'request_id':'empty','lines':[]})
            require(command('inventory.py','reserve',q).returncode==1,'invalid CLI accepted')
            require(command('inventory.py').returncode==2,'wrong argument exit')
        else:
            call(mod.reserve,str(p),'preflight',lines)
            require(read(stock)['A']==9 and len(read(ledger))==1,'normal reservation did not commit exactly once')
            for index in [1,2]:
                for after in [False,True]:
                    # Isolate every probe, including implementation-owned recovery journals.
                    with work() as fresh:
                        sf,rf=fresh/'stock.json',fresh/'reservations.json'
                        write(sf,{'A':10});write(rf,[])
                        with replacement_fault(index,after) as fault:
                            try: mod.reserve(str(fresh),'fault',lines)
                            except Exception: pass
                        if not fault['fired']:
                            raise ValueError('commit strategy not exercised by OS replacement probe; needs an oracle adapter')
                        state=(read(sf)['A'],len(read(rf)))
                        require(state in [(10,0),(9,1)],f'partial commit at boundary {index}/{after}: {state}')
                        call(mod.reserve,str(fresh),'fault',lines)
                        require(read(sf)['A']==9 and len(read(rf))==1,f'retry duplicated deduction at {index}/{after}')


def main(entry):
    checks=[]
    captured=io.StringIO()
    try:
        with contextlib.redirect_stdout(captured),contextlib.redirect_stderr(captured):
            for key in REQUIREMENTS[entry]:
                try:
                    globals()[entry](key)
                    checks.append({'requirement_id':key,'passed':True,'evidence':REQUIREMENTS[entry][key]})
                except SubjectFailure as exc:
                    checks.append({'requirement_id':key,'passed':False,'evidence':str(exc)})
        passed=all(x['passed'] for x in checks)
        result={'protocol':'miniclaw-oracle-v1','status':'passed' if passed else 'capability_failure','checks':checks}
        code=0 if passed else 10
    except Exception as exc:
        result={'protocol':'miniclaw-oracle-v1','status':'oracle_invalid','checks':checks,'error':f'{type(exc).__name__}: {exc}'}
        code=20
    print(json.dumps(result,ensure_ascii=False))
    return code


if __name__=='__main__':
    raise SystemExit(main(sys.argv[1]))
