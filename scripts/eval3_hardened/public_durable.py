"""Public reproducer. State lives in a temporary directory and survives subprocesses."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
CASE_KEY = "worker"

def invoke(root, request, fault=None):
    code = 'import json,sys;from engine import execute;print(json.dumps(execute(sys.argv[1],json.loads(sys.argv[2]),json.loads(sys.argv[3])),sort_keys=True))'
    return subprocess.run([sys.executable, '-c', code, str(root), json.dumps(request), json.dumps(fault)], capture_output=True, text=True)

def main():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        if CASE_KEY == 'worker':
            request = dict(tenant='a', event_id='e1', order_id='o', seq=1, amount=7)
            p = invoke(root, request, 'after_commit')
            assert p.returncode != 0, 'injected failure must surface'
            print('FAILURE_OBSERVED', CASE_KEY)
            p = invoke(root, request); assert p.returncode == 0, p.stderr
            assert json.loads(p.stdout) == {'seq': 1, 'total': 7}
            state = json.loads((root/'worker.json').read_text())
            assert state['effects'] == [['a','e1']]
        elif CASE_KEY == 'gateway':
            request = dict(tenant='a', order_id='o', cents=90, sku='x', inventory_result='success')
            p = invoke(root, request, 'billing_after_commit'); assert p.returncode != 0
            assert (root/'billing.json').exists()
            print('FAILURE_OBSERVED', CASE_KEY)
            p = invoke(root, request); assert p.returncode == 0, p.stderr
            assert json.loads(p.stdout) == 'completed'
            assert len(json.loads((root/'billing.json').read_text())) == 1
        elif CASE_KEY == 'batch':
            request = dict(batch_id='b', attempt_id='1', amounts=dict(t1=3,t2=5,t3=7), action='commit')
            p = invoke(root, request, 'before_t2'); assert p.returncode != 0
            assert json.loads((root/'t1.json').read_text()) == {'b/1/commit':3}
            print('FAILURE_OBSERVED', CASE_KEY)
            p = invoke(root, dict(request,action='restore')); assert p.returncode == 0, p.stderr
            assert json.loads(p.stdout) == 'all_pending'
            assert sum(json.loads((root/'t1.json').read_text()).values()) == 0
            p = invoke(root, dict(request,attempt_id='2')); assert p.returncode == 0, p.stderr
            assert json.loads(p.stdout) == 'committed'
        elif CASE_KEY == 'upgrade':
            for name,value in [('a.json',{'value':3}),('b.json',{'value':5}),('version.json',{'version':1})]:
                (root/name).write_text(json.dumps(value))
            p = invoke(root, {'action':'upgrade'}, 'after_b.json'); assert p.returncode != 0
            assert (root/'backup.json').exists()
            print('FAILURE_OBSERVED', CASE_KEY)
            p = invoke(root, {'action':'recover'}); assert p.returncode == 0, p.stderr
            assert json.loads(p.stdout) == 1
            assert json.loads((root/'a.json').read_text()) == {'value':3}
            p = invoke(root, {'action':'upgrade'}); assert p.returncode == 0, p.stderr
            assert json.loads(p.stdout) == 2
        elif CASE_KEY == 'extract':
            entries=[dict(id='a',path='good.txt',type='file',content='hello'),dict(id='b',path='../outside.txt',type='file',content='bad')]
            p = invoke(root, entries, 'a'); assert p.returncode != 0
            assert not (root/'good.txt').exists()
            print('FAILURE_OBSERVED', CASE_KEY)
            p = invoke(root, entries); assert p.returncode == 0, p.stderr
            assert json.loads(p.stdout) == {'accepted':['a'],'rejected':[{'id':'b','reason':'unsafe_path'}]}
            assert (root/'good.txt').read_text() == 'hello'
        print('RECOVERY_VERIFIED', CASE_KEY)
        print('SMOKE_OK')

if __name__ == '__main__': main()
