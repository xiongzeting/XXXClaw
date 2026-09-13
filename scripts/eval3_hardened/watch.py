"""Windows filesystem event evidence, including writes followed by deletion."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import struct
import threading
import time

class DirectoryWatch:
    def __init__(self, root, output, active):
        self.root=Path(root);self.output=Path(output);self.active=active;self.errors=[];self.stopping=False
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        self.kernel.CreateFileW.restype=wintypes.HANDLE
        self.kernel.ReadDirectoryChangesW.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,wintypes.BOOL,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p,ctypes.c_void_p]
        self.kernel.ReadDirectoryChangesW.restype=wintypes.BOOL
        self.kernel.CancelIoEx.argtypes=[wintypes.HANDLE,ctypes.c_void_p]
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        self.handle=self.kernel.CreateFileW(str(self.root),1,7,None,3,0x02000000,None)
        if self.handle==wintypes.HANDLE(-1).value:raise OSError(ctypes.get_last_error(),'CreateFileW directory watcher')
        self.thread=threading.Thread(target=self.loop,daemon=True)
    def start(self):self.thread.start()
    def loop(self):
        buf=ctypes.create_string_buffer(65536);count=wintypes.DWORD()
        with self.output.open('a',encoding='utf-8') as out:
            while not self.stopping:
                ok=self.kernel.ReadDirectoryChangesW(self.handle,buf,len(buf),True,0x1|0x2|0x8|0x10,ctypes.byref(count),None,None)
                if not ok:
                    if not self.stopping:self.errors.append({'error':ctypes.get_last_error(),'time':time.time()})
                    break
                if count.value==0:
                    self.errors.append({'error':'buffer_overflow','time':time.time()});continue
                offset=0
                while offset<count.value:
                    nxt,action,size=struct.unpack_from('III',buf.raw,offset)
                    name=buf.raw[offset+12:offset+12+size].decode('utf-16-le').replace('\\','/')
                    parts=name.split('/')
                    if len(parts)>3 and parts[2]=='workspace' and '.aster' not in parts[3:]:
                        cid=parts[0];phase=self.active.get(cid)
                        if phase:
                            out.write(json.dumps({'timestamp':time.time(),'case_id':cid,'phase':phase,'action':action,'path':'/'.join(parts[3:])},ensure_ascii=False)+'\n');out.flush()
                    if not nxt:break
                    offset+=nxt
    def stop(self):
        self.stopping=True;self.kernel.CancelIoEx(self.handle,None);self.thread.join(timeout=3);self.kernel.CloseHandle(self.handle)

def self_test(root):
    import tempfile
    import subprocess
    with tempfile.TemporaryDirectory(dir=root) as d:
        p=Path(d);work=p/'case/attempt-001/workspace';work.mkdir(parents=True)
        active={'case':'turn1'};watch=DirectoryWatch(p,p/'audit.jsonl',active);watch.start();time.sleep(.1)
        f=work/'transient.txt';f.write_text('audit');f.unlink();time.sleep(.2);watch.stop()
        rows=[json.loads(x) for x in (p/'audit.jsonl').read_text('utf-8').splitlines()]
        assert any(r['action']==1 for r in rows) and any(r['action']==2 for r in rows),rows
        assert not watch.errors,watch.errors
        watch=DirectoryWatch(p,p/'docker-audit.jsonl',active);watch.start();time.sleep(.1)
        proc=subprocess.run(['docker','run','--rm','--network','none','--mount',f'type=bind,source={work.resolve()},target=/workspace','-w','/workspace','miniclaw-runtime:py313-bench','sh','-c','echo audit > docker-transient.txt; rm docker-transient.txt'],capture_output=True)
        time.sleep(.3);watch.stop()
        docker_rows=[json.loads(x) for x in (p/'docker-audit.jsonl').read_text('utf-8').splitlines()]
        assert proc.returncode==0 and any(r['action']==1 for r in docker_rows) and any(r['action']==2 for r in docker_rows),docker_rows
        assert not watch.errors,watch.errors
        return {'write_then_delete_observed':True,'events':len(rows),'docker_write_then_delete_observed':True,'docker_events':len(docker_rows)}
