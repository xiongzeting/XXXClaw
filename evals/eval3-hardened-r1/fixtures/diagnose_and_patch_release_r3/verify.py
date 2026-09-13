import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
    root=Path(d);src=root/'src';src.mkdir();(src/'a.py').write_text('a')
    out=root/'manifest.json'
    p=subprocess.run([sys.executable,'release.py','build',str(src),str(out)],capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    assert json.loads(out.read_text())=={'a.py':hashlib.sha256(b'a').hexdigest()}
    assert subprocess.run([sys.executable,'release.py','verify',str(src),str(out)]).returncode==0
print('SMOKE_OK')
