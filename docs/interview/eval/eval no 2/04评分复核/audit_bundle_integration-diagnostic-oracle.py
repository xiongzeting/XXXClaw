import json,subprocess,sys,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
 p=Path(d); l=p/'l'; q=p/'q'; o=p/'o'; l.write_text('{"event_id":"1","user":"alice","action":"read"}\n{"event_id":"2","user":"bob","action":"read"}\n'); q.write_text('{"allow":["*:read"],"deny":["bob:read"]}'); o.write_text('{"old":1}')
 old=o.read_bytes(); assert subprocess.run([sys.executable,'audit.py',str(l),'--policy',str(q),'--bundle',str(o)]).returncode==1 and o.read_bytes()==old
 q.write_text('{"allow":["*:read"],"deny":[]}'); assert subprocess.run([sys.executable,'audit.py',str(l),'--policy',str(q),'--bundle',str(o)]).returncode==0
 assert json.loads(o.read_text())['by_user']['alice']==1
 l.write_text('{"event_id":"1","user":"x","action":"read"}\n{"event_id":"1","user":"y","action":"read"}\n'); assert subprocess.run([sys.executable,'audit.py',str(l),'--policy',str(q),'--bundle',str(o)]).returncode==1
print('CHECK_OK')
