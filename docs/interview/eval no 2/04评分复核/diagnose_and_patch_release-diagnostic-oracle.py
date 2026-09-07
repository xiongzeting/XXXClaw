import hashlib,json,subprocess,sys,tempfile,os
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
 root=Path(d); (root/'src').mkdir(); (root/'src/a.py').write_text('a'); (root/'src/b.py').write_text('b'); (root/'src/.cache.py').write_text('x'); out=root/'m.json'
 p=subprocess.run([sys.executable,'release.py','build',str(root/'src'),str(out)],capture_output=True,text=True); assert p.returncode==0
 m=json.loads(out.read_text()); assert list(m)==['a.py','b.py'] and m['a.py']==hashlib.sha256(b'a').hexdigest() and m['b.py']==hashlib.sha256(b'b').hexdigest()
 assert subprocess.run([sys.executable,'release.py','verify',str(root/'src'),str(out)]).returncode==0
 (root/'src/a.py').write_text('changed'); assert subprocess.run([sys.executable,'release.py','verify',str(root/'src'),str(out)]).returncode==1
 assert subprocess.run([sys.executable,'release.py','wat']).returncode==2
print('CHECK_OK')
