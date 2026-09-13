import subprocess
import sys
import json
p=subprocess.run([sys.executable,'cli.py','a'],capture_output=True,text=True)
assert p.returncode==0 and p.stdout=='a=7\n' and p.stderr==''
p=subprocess.run([sys.executable,'cli.py','a','--format','json'],capture_output=True,text=True)
assert p.returncode==0 and json.loads(p.stdout)=={'id':'a','value':7}
print('SMOKE_OK')
