"""Six frozen boundary-delivery eval specifications.

The fixtures are intentionally small repositories.  ``reference_files`` and
``mutants`` are used only by the authoring self-checker and are never copied
to a model workspace.
"""

from __future__ import annotations


def _oracle(code: str) -> str:
    return code.strip() + "\n"


SPECS = [
    {
        "key": "idempotent_charge_recovery",
        "category": "tools",
        "split": "development",
        "phases": [
            "请检查这个小型支付仓库。实现 charge.py 的 charge(store, order_id, cents, fail_after_reserve=False)；它模拟扣款，必须先校验正整数金额和唯一订单，再原子地写入 ledger.json。扣款写入后若 fail_after_reserve=True 要抛出 RuntimeError，重试同一订单只能返回原扣款结果，不能重复扣款。",
            "补充契约：只允许修改 charge.py 和 ledger.json；ledger 缺失按空账本处理，JSON 损坏或金额非法必须 ValueError 且文件不变。请实际运行 python verify.py 验收，不能改验收文件。",
        ],
        "files": {
            "charge.py": "import json\nfrom pathlib import Path\nLEDGER=Path('ledger.json')\ndef charge(store, order_id, cents, fail_after_reserve=False):\n    raise NotImplementedError\n",
            "ledger.json": '{"version": 1, "charges": []}\n',
            "README.md": "The caller may retry after an unknown outcome. Preserve prior charges.\n",
            "verify.py": "# hidden oracle is run externally\n",
        },
        "allowed": ["charge.py", "ledger.json"],
        "oracle": _oracle("""
import charge as charge_mod
from charge import charge
import json, tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as d:
    import os; os.chdir(d); charge_mod.LEDGER = Path('ledger.json')
    Path('ledger.json').write_text('{"version":1,"charges":[]}', encoding='utf-8')
    assert charge({}, 'o1', 125) == {'order_id':'o1','cents':125,'status':'charged'}
    before = Path('ledger.json').read_text()
    try: charge({}, 'o2', 50, True)
    except RuntimeError: pass
    else: raise AssertionError('failure flag must surface')
    assert charge({}, 'o2', 50) == {'order_id':'o2','cents':50,'status':'charged'}
    rows=json.loads(Path('ledger.json').read_text())['charges']
    assert rows == [{'order_id':'o1','cents':125},{'order_id':'o2','cents':50}]
    assert charge({}, 'o1', 999) == {'order_id':'o1','cents':125,'status':'charged'}
    for bad in [0, -1, 1.5, '2']:
        old=Path('ledger.json').read_text()
        try: charge({}, 'bad', bad)
        except ValueError: pass
        else: raise AssertionError('bad amount accepted')
        assert Path('ledger.json').read_text()==old
print('CHECK_OK')
"""),
        "reference_files": {
            "charge.py": "import json\nfrom pathlib import Path\nLEDGER=Path('ledger.json')\ndef charge(store, order_id, cents, fail_after_reserve=False):\n    if not isinstance(order_id,str) or not order_id or type(cents) is not int or cents<=0: raise ValueError('order_id and positive integer cents required')\n    try: data=json.loads(LEDGER.read_text(encoding='utf-8')) if LEDGER.exists() else {'version':1,'charges':[]}\n    except Exception as e: raise ValueError('invalid ledger') from e\n    if data.get('version')!=1 or not isinstance(data.get('charges'),list): raise ValueError('invalid ledger')\n    for row in data['charges']:\n        if row.get('order_id')==order_id:\n            if row.get('cents') != cents: raise ValueError('conflicting retry')\n            return {'order_id':order_id,'cents':row['cents'],'status':'charged'}\n    data['charges'].append({'order_id':order_id,'cents':cents})\n    tmp=LEDGER.with_suffix('.tmp'); tmp.write_text(json.dumps(data,separators=(',',':')),encoding='utf-8'); tmp.replace(LEDGER)\n    if fail_after_reserve: raise RuntimeError('unknown outcome after charge')\n    return {'order_id':order_id,'cents':cents,'status':'charged'}\n",
        },
        "mutants": [
            {"charge.py": "import json\nfrom pathlib import Path\nLEDGER=Path('ledger.json')\ndef charge(store,order_id,cents,fail_after_reserve=False):\n d=json.loads(LEDGER.read_text()); d['charges'].append({'order_id':order_id,'cents':cents}); LEDGER.write_text(json.dumps(d)); return {'order_id':order_id,'cents':cents,'status':'charged'}\n"},
            {"charge.py": "import json\nfrom pathlib import Path\nLEDGER=Path('ledger.json')\ndef charge(store,order_id,cents,fail_after_reserve=False):\n if type(cents) is not int or cents<=0: raise ValueError()\n d=json.loads(LEDGER.read_text()); d['charges'].append({'order_id':order_id,'cents':cents}); LEDGER.write_text(json.dumps(d));\n if fail_after_reserve: raise RuntimeError()\n return {'order_id':order_id,'cents':cents,'status':'charged'}\n"},
        ],
        "rationale": "以异常后重试为触发器，验收持久化原子性、幂等键和非法输入契约；故障只发生在写入之后，模型必须从仓库状态推断恢复策略。",
    },
    {
        "key": "atomic_batch_reservation",
        "category": "tools",
        "split": "retained",
        "phases": [
            "修复 inventory.py 的 reserve(path, request_id, lines) 和 CLI `python inventory.py reserve requests.json`。一个 request_id 可重试且返回同一结果；整批校验 SKU、正整数数量和库存后才扣减，任何失败都不能留下部分扣减。成功要写 stock.json 与 reservations.json，输出 JSON。",
            "请保留现有 JSON 字段和文件格式，拒绝重复 request_id、未知 SKU、库存不足和坏 JSON；只修改 inventory.py、stock.json、reservations.json。请运行 python verify.py 检查 CLI 和函数两条路径。",
        ],
        "files": {
            "inventory.py": "# broken implementation\ndef reserve(path, request_id, lines):\n    raise NotImplementedError\n",
            "stock.json": '{"A": 3, "B": 1}\n', "reservations.json": '[]\n',
            "requests.json": '{"request_id":"r1","lines":[{"sku":"A","qty":2},{"sku":"B","qty":1}]}\n',
            "verify.py": "# hidden oracle\n", "README.md": "Reservations are retried by request id after process crashes.\n",
        },
        "allowed": ["inventory.py", "stock.json", "reservations.json"],
        "oracle": _oracle("""
import json, subprocess, sys, tempfile, os
from pathlib import Path
from inventory import reserve
with tempfile.TemporaryDirectory() as d:
 os.chdir(d); Path('stock.json').write_text('{"A":3,"B":1}'); Path('reservations.json').write_text('[]')
 assert reserve('.', 'r1', [{'sku':'A','qty':2},{'sku':'B','qty':1}]) == {'request_id':'r1','lines':[{'sku':'A','qty':2},{'sku':'B','qty':1}]}
 assert json.loads(Path('stock.json').read_text()) == {'A':1,'B':0}
 assert reserve('.', 'r1', [{'sku':'A','qty':99}])['lines'][0]['qty']==2
 old=Path('stock.json').read_text(); oldr=Path('reservations.json').read_text()
 try: reserve('.', 'r2', [{'sku':'A','qty':2},{'sku':'Z','qty':1}])
 except ValueError: pass
 else: raise AssertionError()
 assert Path('stock.json').read_text()==old and Path('reservations.json').read_text()==oldr
print('CHECK_OK')
"""),
        "reference_files": {"inventory.py": "import json, os, tempfile\nfrom pathlib import Path\ndef reserve(path, request_id, lines):\n p=Path(path); stockf=p/'stock.json'; resf=p/'reservations.json'\n if not isinstance(request_id,str) or not request_id or not isinstance(lines,list): raise ValueError('bad request')\n try: stock=json.loads(stockf.read_text()); reservations=json.loads(resf.read_text())\n except Exception as e: raise ValueError('bad json') from e\n for r in reservations:\n  if r.get('request_id')==request_id: return r\n if any(r.get('request_id')==request_id for r in reservations): raise ValueError('duplicate')\n if any(not isinstance(x,dict) or x.get('sku') not in stock or type(x.get('qty')) is not int or x['qty']<=0 for x in lines): raise ValueError('invalid line')\n need={}; [need.__setitem__(x['sku'],need.get(x['sku'],0)+x['qty']) for x in lines]\n if any(stock[k]<v for k,v in need.items()): raise ValueError('insufficient')\n result={'request_id':request_id,'lines':[{'sku':x['sku'],'qty':x['qty']} for x in lines]}\n newstock=dict(stock); [newstock.__setitem__(k,newstock[k]-v) for k,v in need.items()]\n def atomic(f,obj):\n  t=f.with_suffix('.tmp'); t.write_text(json.dumps(obj,separators=(',',':'))); t.replace(f)\n atomic(stockf,newstock); atomic(resf,reservations+[result]); return result\nif __name__=='__main__':\n import sys\n if len(sys.argv)!=3 or sys.argv[1]!='reserve': raise SystemExit(2)\n q=json.loads(Path(sys.argv[2]).read_text()); print(json.dumps(reserve('.',q['request_id'],q['lines']),separators=(',',':')))\n"""},
        "mutants": [{"inventory.py": "def reserve(path,request_id,lines):\n import json\n from pathlib import Path\n s=json.loads(Path(path,'stock.json').read_text()); x=lines[0]; s[x['sku']]-=x['qty']; Path(path,'stock.json').write_text(json.dumps(s)); return {'request_id':request_id,'lines':lines}\n"}, {"inventory.py": "def reserve(path,request_id,lines):\n import json\n from pathlib import Path\n s=json.loads(Path(path,'stock.json').read_text()); r=json.loads(Path(path,'reservations.json').read_text());\n if any(x.get('request_id')==request_id for x in r): raise ValueError('duplicate')\n return {'request_id':request_id,'lines':lines}\n"}],
        "rationale": "跨两个状态文件验证完整批次校验、幂等重试和 CLI 集成，能区分只更新库存、只做内存检查等常见伪修复。",
    },
    {
        "key": "diagnose_and_patch_release",
        "category": "tools",
        "split": "test",
        "phases": [
            "这是一个多文件发布工具仓库。定位并修复 `release.py`：`build_manifest(source_dir, out_file)` 必须按文件名排序收集 .py 文件的 sha256；`verify_manifest` 必须拒绝缺文件、哈希不符和 manifest 中的路径穿越。修复相关测试或配置之外的代码，并实际运行 pytest。",
            "集成契约：CLI `python release.py build SRC OUT` 与 `verify SRC OUT` 返回 0/1，坏参数返回 2；不得把缓存、.git 或符号链接纳入清单。仅可修改 release.py、tests/test_release.py。",
        ],
        "files": {
            "release.py": "import hashlib, json, sys\ndef build_manifest(source_dir,out_file):\n    raise NotImplementedError\ndef verify_manifest(source_dir,out_file):\n    return True\nif __name__=='__main__': sys.exit(2)\n",
            "src/a.py": "print('a')\n", "src/b.py": "print('b')\n", "src/.cache.py": "bad\n", "manifest.json": "{}\n",
            "tests/test_release.py": "# hidden integration tests\n", "README.md": "Build a deterministic release manifest.\n",
        },
        "allowed": ["release.py", "tests/test_release.py"],
        "oracle": _oracle("""
import hashlib,json,subprocess,sys,tempfile,os
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
 root=Path(d); (root/'src').mkdir(); (root/'src/a.py').write_text('a'); (root/'src/b.py').write_text('b'); (root/'src/.cache.py').write_text('x'); out=root/'m.json'
 p=subprocess.run([sys.executable,'release.py','build',str(root/'src'),str(out)],capture_output=True,text=True); assert p.returncode==0
 m=json.loads(out.read_text()); assert [x['path'] for x in m['files']]==['a.py','b.py']
 assert subprocess.run([sys.executable,'release.py','verify',str(root/'src'),str(out)]).returncode==0
 (root/'src/a.py').write_text('changed'); assert subprocess.run([sys.executable,'release.py','verify',str(root/'src'),str(out)]).returncode==1
 assert subprocess.run([sys.executable,'release.py','wat']).returncode==2
print('CHECK_OK')
"""),
        "reference_files": {"release.py": "import hashlib,json,sys\nfrom pathlib import Path\ndef build_manifest(source_dir,out_file):\n root=Path(source_dir).resolve(); rows=[]\n for p in sorted(root.rglob('*.py')):\n  if p.name.startswith('.') or p.is_symlink() or any(x in {'.git','.cache','__pycache__'} for x in p.parts): continue\n  rel=p.relative_to(root).as_posix(); rows.append({'path':rel,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})\n Path(out_file).write_text(json.dumps({'version':1,'files':rows},sort_keys=True,separators=(',',':')))\ndef verify_manifest(source_dir,out_file):\n try: m=json.loads(Path(out_file).read_text()); root=Path(source_dir).resolve(); expected={x['path']:x['sha256'] for x in m['files']}\n except Exception: return False\n if any(Path(k).is_absolute() or '..' in Path(k).parts for k in expected): return False\n actual={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.py') if not p.name.startswith('.') and not p.is_symlink() and '.git' not in p.parts and '.cache' not in p.parts and '__pycache__' not in p.parts}\n return actual==expected\nif __name__=='__main__':\n if len(sys.argv)!=4 or sys.argv[1] not in {'build','verify'}: raise SystemExit(2)\n if sys.argv[1]=='build': build_manifest(sys.argv[2],sys.argv[3]); raise SystemExit(0)\n raise SystemExit(0 if verify_manifest(sys.argv[2],sys.argv[3]) else 1)\n"""},
        "mutants": [{"release.py": "def build_manifest(source_dir,out_file):\n import json\n from pathlib import Path\n Path(out_file).write_text(json.dumps({'version':1,'files':[]}))\ndef verify_manifest(source_dir,out_file): return True\n"}, {"release.py": "def build_manifest(source_dir,out_file):\n import json\n from pathlib import Path\n Path(out_file).write_text(json.dumps({'version':1,'files':[{'path':'../secret','sha256':'x'}]}))\ndef verify_manifest(source_dir,out_file): return True\n"}],
        "rationale": "要求真正阅读多文件仓库并完成函数与 CLI 集成验收，隐藏测试覆盖确定性、忽略项、篡改检测和退出码。",
    },
    {
        "key": "config_migration_cli_contract",
        "category": "completion",
        "split": "development",
        "phases": [
            "完成这个配置迁移交付：`migrate.py` 的 `migrate(input_path, output_path)` 将 schema=1 的服务配置迁移为 schema=2，把 `host`+`port` 合并为 `endpoint`，保留 timeout 和未知字段；输出按 JSON 稳定写入。",
            "CLI `python migrate.py IN OUT` 成功返回 0，输入不存在、坏 JSON、schema 非 1 或缺 host/port 返回 1，不能创建半成品输出；只允许修改 migrate.py 和 README.md。请运行 python verify.py。",
        ],
        "files": {"migrate.py": "def migrate(input_path,output_path):\n    raise NotImplementedError\n", "config-v1.json": '{"schema":1,"host":"db","port":5432,"timeout":3,"tag":"x"}\n', "README.md": "Migration must be atomic.\n", "verify.py": "# hidden\n"},
        "allowed": ["migrate.py", "README.md"],
        "oracle": _oracle("""
import json,subprocess,sys,tempfile
from pathlib import Path
from migrate import migrate
with tempfile.TemporaryDirectory() as d:
 p=Path(d); i=p/'i.json'; o=p/'o.json'; i.write_text('{"schema":1,"host":"db","port":5432,"timeout":3,"tag":"x"}')
 migrate(i,o); assert json.loads(o.read_text())=={'schema':2,'endpoint':'db:5432','timeout':3,'tag':'x'}
 old=o.read_text(); i.write_text('{"schema":2}');
 try: migrate(i,o)
 except ValueError: pass
 else: raise AssertionError()
 assert o.read_text()==old
 assert subprocess.run([sys.executable,'migrate.py',str(i),str(o)]).returncode==1
print('CHECK_OK')
"""),
        "reference_files": {"migrate.py": "import json,sys\nfrom pathlib import Path\ndef migrate(input_path,output_path):\n try: d=json.loads(Path(input_path).read_text(encoding='utf-8'))\n except Exception as e: raise ValueError('invalid input') from e\n if d.get('schema')!=1 or not isinstance(d.get('host'),str) or type(d.get('port')) is not int: raise ValueError('invalid schema')\n out={k:v for k,v in d.items() if k not in {'host','port'}}; out['schema']=2; out['endpoint']=f\"{d['host']}:{d['port']}\"\n t=Path(output_path).with_suffix('.tmp'); t.write_text(json.dumps(out,sort_keys=True,separators=(',',':'))); t.replace(output_path)\nif __name__=='__main__':\n if len(sys.argv)!=3: raise SystemExit(2)\n try: migrate(sys.argv[1],sys.argv[2])\n except (ValueError,OSError): raise SystemExit(1)\n"""},
        "mutants": [{"migrate.py": "def migrate(input_path,output_path):\n import shutil; shutil.copyfile(input_path,output_path)\n"}, {"migrate.py": "def migrate(input_path,output_path):\n import json\n from pathlib import Path\n d=json.loads(Path(input_path).read_text()); d['schema']=2; d['endpoint']=d['host']+':'+str(d['port']); Path(output_path).write_text(json.dumps(d))\n"}],
        "rationale": "小型交付题强调明确 CLI 错误码、原子输出和未知字段保留，避免只检查转换后 JSON 形状。",
    },
    {
        "key": "queue_rebuild_and_reconcile",
        "category": "completion",
        "split": "retained",
        "phases": [
            "完成 queue_tool.py：读取 events.jsonl 中唯一递增 seq 的 add/remove 事件，生成 state.json（当前任务按首次出现顺序）和 report.json（added、removed、active_count）。重复完全相同事件可重放，冲突或 seq 缺口必须失败且不改输出。",
            "CLI `python queue_tool.py rebuild events.jsonl state.json report.json` 成功 0，非法输入 1，参数错误 2；先完整校验再写两个输出，只允许修改 queue_tool.py。请实际运行 python verify.py。",
        ],
        "files": {"queue_tool.py": "# TODO\n", "events.jsonl": '{"seq":1,"op":"add","id":"a"}\n{"seq":2,"op":"remove","id":"a"}\n', "state.json": "{}\n", "report.json": "{}\n", "verify.py": "# hidden\n"},
        "allowed": ["queue_tool.py"],
        "oracle": _oracle("""
import json,subprocess,sys,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
 p=Path(d); e=p/'e'; s=p/'s'; r=p/'r'; e.write_text('{"seq":1,"op":"add","id":"a"}\\n{"seq":2,"op":"add","id":"b"}\\n{"seq":3,"op":"remove","id":"a"}\\n')
 assert subprocess.run([sys.executable,'queue_tool.py','rebuild',str(e),str(s),str(r)]).returncode==0
 assert json.loads(s.read_text())=={'active':['b']}; assert json.loads(r.read_text())=={'added':2,'removed':1,'active_count':1}
 old=s.read_text(); e.write_text('{"seq":2,"op":"add","id":"x"}\\n'); assert subprocess.run([sys.executable,'queue_tool.py','rebuild',str(e),str(s),str(r)]).returncode==1; assert s.read_text()==old
print('CHECK_OK')
"""),
        "reference_files": {"queue_tool.py": "import json,sys\nfrom pathlib import Path\ndef rebuild(event_path,state_path,report_path):\n rows=[]\n try:\n  for line in Path(event_path).read_text().splitlines(): rows.append(json.loads(line))\n except Exception as e: raise ValueError('bad events') from e\n active=[]; seen={}; added=removed=0\n for i,x in enumerate(rows,1):\n  if x.get('seq')!=i or x.get('op') not in {'add','remove'} or not isinstance(x.get('id'),str): raise ValueError('invalid sequence')\n  sig=json.dumps(x,sort_keys=True)\n  if x['seq'] in seen:\n   if seen[x['seq']]!=sig: raise ValueError('conflict')\n   continue\n  seen[x['seq']]=sig\n  if x['op']=='add':\n   if x['id'] not in active: active.append(x['id'])\n   added+=1\n  elif x['id'] in active: active.remove(x['id']); removed+=1\n st={'active':active}; rp={'added':added,'removed':removed,'active_count':len(active)}\n for f,obj in ((Path(state_path),st),(Path(report_path),rp)):\n  t=f.with_suffix('.tmp'); t.write_text(json.dumps(obj,separators=(',',':'))); t.replace(f)\nif __name__=='__main__':\n if len(sys.argv)!=5 or sys.argv[1]!='rebuild': raise SystemExit(2)\n try: rebuild(*sys.argv[2:])\n except (ValueError,OSError): raise SystemExit(1)\n"""},
        "mutants": [{"queue_tool.py": "import json,sys\nfrom pathlib import Path\ndef rebuild(e,s,r):\n rows=[json.loads(x) for x in Path(e).read_text().splitlines()]; Path(s).write_text(json.dumps({'active':[x['id'] for x in rows if x['op']=='add']})); Path(r).write_text('{}')\n"}, {"queue_tool.py": "import json\ndef rebuild(e,s,r):\n from pathlib import Path\n Path(s).write_text('{\"active\":[]}'); Path(r).write_text('{\"added\":0,\"removed\":0,\"active_count\":0}')\n"}],
        "rationale": "多文件输出的事件重建要求先校验后提交，并将状态与统计报告保持一致；负例覆盖忽略序列和空实现。",
    },
    {
        "key": "audit_bundle_integration",
        "category": "completion",
        "split": "test",
        "phases": [
            "完成 audit.py 的 `make_bundle(log_path, policy_path, out_path)`：解析 JSONL 审计日志，按 user 聚合 action 次数，仅计 policy.json 允许的 action；输出 bundle.json 包含 version=1、users（按 user 排序）和 total_events。未知 action、重复 event_id、坏 JSON 必须失败。",
            "CLI `python audit.py LOG POLICY OUT` 成功 0，业务输入错误 1，参数错误 2；写出必须原子，不能篡改输入，只改 audit.py。请运行 python verify.py 验收实际文件内容。",
        ],
        "files": {"audit.py": "def make_bundle(log_path,policy_path,out_path):\n    raise NotImplementedError\n", "audit.jsonl": '{"event_id":"1","user":"alice","action":"read"}\n', "policy.json": '{"allowed":["read"]}\n', "bundle.json": "{}\n", "verify.py": "# hidden\n", "README.md": "Audit export feeds billing review.\n"},
        "allowed": ["audit.py"],
        "oracle": _oracle("""
import json,subprocess,sys,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
 p=Path(d); l=p/'l'; pol=p/'p'; out=p/'o'; l.write_text('{"event_id":"2","user":"bob","action":"read"}\\n{"event_id":"1","user":"alice","action":"read"}\\n{"event_id":"3","user":"bob","action":"read"}\\n'); pol.write_text('{"allowed":["read"]}')
 assert subprocess.run([sys.executable,'audit.py',str(l),str(pol),str(out)]).returncode==0
 assert json.loads(out.read_text())=={'version':1,'users':{'alice':{'read':1},'bob':{'read':2}},'total_events':3}
 old=out.read_text(); l.write_text('{"event_id":"1","user":"x","action":"read"}\\n{"event_id":"1","user":"y","action":"read"}\\n'); assert subprocess.run([sys.executable,'audit.py',str(l),str(pol),str(out)]).returncode==1; assert out.read_text()==old
print('CHECK_OK')
"""),
        "reference_files": {"audit.py": "import json,sys\nfrom pathlib import Path\ndef make_bundle(log_path,policy_path,out_path):\n try: allowed=set(json.loads(Path(policy_path).read_text())['allowed']); rows=[json.loads(x) for x in Path(log_path).read_text().splitlines()]\n except Exception as e: raise ValueError('bad input') from e\n ids=set(); users={};\n for x in rows:\n  if not all(isinstance(x.get(k),str) and x[k] for k in ('event_id','user','action')) or x['event_id'] in ids or x['action'] not in allowed: raise ValueError('invalid event')\n  ids.add(x['event_id']); users.setdefault(x['user'],{}); users[x['user']][x['action']]=users[x['user']].get(x['action'],0)+1\n result={'version':1,'users':{u:users[u] for u in sorted(users)},'total_events':len(rows)}\n t=Path(out_path).with_suffix('.tmp'); t.write_text(json.dumps(result,sort_keys=True,separators=(',',':'))); t.replace(out_path)\nif __name__=='__main__':\n if len(sys.argv)!=4: raise SystemExit(2)\n try: make_bundle(*sys.argv[1:])\n except (ValueError,OSError): raise SystemExit(1)\n"""},
        "mutants": [{"audit.py": "def make_bundle(log_path,policy_path,out_path):\n import shutil; shutil.copyfile(log_path,out_path)\n"}, {"audit.py": "def make_bundle(log_path,policy_path,out_path):\n import json\n from pathlib import Path\n Path(out_path).write_text(json.dumps({'version':1,'users':{},'total_events':0}))\n"}],
        "rationale": "最终题检验真实日志、策略和输出三文件之间的集成语义，要求重复事件与策略拒绝可观测且失败不覆盖旧交付。",
    },
]


assert len(SPECS) == 6
assert {(s['category'], s['split']) for s in SPECS} == {
    ('tools','development'), ('tools','retained'), ('tools','test'),
    ('completion','development'), ('completion','retained'), ('completion','test'),
}
assert all(set(s) >= {'key','category','split','phases','files','allowed','oracle','reference_files','mutants','rationale'} for s in SPECS)
assert all(len(s['mutants']) >= 2 for s in SPECS)


# Hardened authoring revisions.  Kept here as data transformations so the
# original case identities remain stable for downstream split assembly.
def _set(key, **changes):
    next(s for s in SPECS if s['key'] == key).update(changes)


_set('idempotent_charge_recovery',
    phases=[
        "仓库已留下未知结果：ledger.json 中 order_id=old、cents=90 已扣款，但上次进程在返回前异常。先恢复并实现 charge.py 的 charge(store, order_id, cents, fail_after_reserve=False)，同订单同金额重试返回原结果且不重复扣款；同订单不同金额 ValueError。",
        "写入后 fail_after_reserve=True 必须抛 RuntimeError，随后重试仍只能得到一次扣款。非法金额、损坏账本和非法订单必须 ValueError 且文件不变；只改 charge.py 与 ledger.json。verify.py 只是公开 smoke，仍须实际运行。",
    ],
    files={**SPECS[0]['files'], 'ledger.json':'{"version":1,"charges":[{"order_id":"old","cents":90}]}\n',
           'verify.py':"from charge import charge\nassert charge({}, 'old', 90)['cents'] == 90\nprint('SMOKE_OK')\n"},
    oracle=_oracle("""
import charge as charge_mod
from pathlib import Path
import json, tempfile, os
with tempfile.TemporaryDirectory() as d:
 os.chdir(d); charge_mod.LEDGER=Path('ledger.json'); Path('ledger.json').write_text('{"version":1,"charges":[{"order_id":"old","cents":90}]}')
 assert charge_mod.charge({},'old',90)=={'order_id':'old','cents':90,'status':'charged'}
 try: charge_mod.charge({},'old',91)
 except ValueError: pass
 else: raise AssertionError('conflicting retry accepted')
 try: charge_mod.charge({},'new',30,True)
 except RuntimeError: pass
 assert charge_mod.charge({},'new',30)['cents']==30
 assert len(json.loads(Path('ledger.json').read_text())['charges'])==2
 os.chdir(Path(__file__).parent)
print('CHECK_OK')
"""))

_set('atomic_batch_reservation',
    phases=[
        "修复 inventory.py 的 reserve(path, request_id, lines) 和 CLI `python inventory.py reserve requests.json`。同 request_id 携带完全相同的合并后 payload 时返回已保存结果；同 ID 的不同 SKU/数量是冲突并 ValueError。相同 SKU 行先合并，再完整校验后原子扣减。",
        "未知 SKU、非正整数、库存不足、重复 ID 冲突或坏 JSON 均 ValueError 且两个文件字节不变；CLI 请求必须是非空 lines，成功输出 JSON、业务错误返回 1、参数错误返回 2。只修改 inventory.py、stock.json、reservations.json；verify.py 是公开 smoke。",
    ],
    files={**SPECS[1]['files'], 'verify.py':"from inventory import reserve\nassert reserve('.', 'smoke', [{'sku':'A','qty':1}])['request_id']=='smoke'\nprint('SMOKE_OK')\n"},
    reference_files={'inventory.py': """import json,sys
from pathlib import Path
def reserve(path,request_id,lines):
 p=Path(path); sf=p/'stock.json'; rf=p/'reservations.json'
 if not isinstance(request_id,str) or not request_id or not isinstance(lines,list) or not lines: raise ValueError('bad request')
 try: stock=json.loads(sf.read_text()); rs=json.loads(rf.read_text())
 except Exception as e: raise ValueError('bad json') from e
 merged={}
 for x in lines:
  if not isinstance(x,dict) or x.get('sku') not in stock or type(x.get('qty')) is not int or x['qty']<=0: raise ValueError('invalid line')
  merged[x['sku']]=merged.get(x['sku'],0)+x['qty']
 normalized=[{'sku':k,'qty':merged[k]} for k in sorted(merged)]
 for old in rs:
  if old.get('request_id')==request_id:
   if old.get('lines')!=normalized: raise ValueError('conflicting request')
   return old
 if any(stock[k]<v for k,v in merged.items()): raise ValueError('insufficient')
 result={'request_id':request_id,'lines':normalized}; ns=dict(stock)
 for k,v in merged.items(): ns[k]-=v
 for f,obj in ((sf,ns),(rf,rs+[result])):
  t=f.with_suffix('.tmp'); t.write_text(json.dumps(obj,separators=(',',':'))); t.replace(f)
 return result
if __name__=='__main__':
 if len(sys.argv)!=3 or sys.argv[1]!='reserve': raise SystemExit(2)
 try:
  q=json.loads(Path(sys.argv[2]).read_text()); print(json.dumps(reserve('.',q['request_id'],q['lines']),separators=(',',':')))
 except (ValueError,OSError,KeyError,json.JSONDecodeError): raise SystemExit(1)
"""},
    oracle=_oracle("""
import json,os,tempfile,subprocess,sys
from pathlib import Path
from inventory import reserve
with tempfile.TemporaryDirectory() as d:
 os.chdir(d); Path('stock.json').write_text('{"A":3,"B":1}'); Path('reservations.json').write_text('[]')
 assert reserve('.', 'r', [{'sku':'A','qty':1},{'sku':'A','qty':1}])['lines']==[{'sku':'A','qty':2}]
 assert reserve('.', 'r', [{'sku':'A','qty':2}])['lines']==[{'sku':'A','qty':2}]
 old=(Path('stock.json').read_bytes(),Path('reservations.json').read_bytes())
 try: reserve('.', 'r', [{'sku':'A','qty':1}])
 except ValueError: pass
 else: raise AssertionError()
 assert old==(Path('stock.json').read_bytes(),Path('reservations.json').read_bytes())
 q=Path('q.json'); q.write_text('{"request_id":"cli","lines":[{"sku":"B","qty":1}]}')
 assert subprocess.run([sys.executable,str(Path(__file__).parent/'inventory.py'),'reserve',str(q)]).returncode in (0,1)
 os.chdir(Path(__file__).parent)
print('CHECK_OK')
"""))

_set('config_migration_cli_contract',
    phases=[
        "这是一个多文件迁移仓库。修复 package/parser.py、package/transform.py、package/storage.py 和根入口 migrate.py 的 schema=1 到 schema=2 迁移：递归处理 services 数组，将 host+port 变为 endpoint，保留嵌套未知字段；若输入同时已有 endpoint 或 host/port 冲突必须拒绝。",
        "CLI `python migrate.py IN OUT` 成功 0，输入/JSON/schema/冲突错误 1，参数错误 2；先完整校验，输出临时文件成功后替换，失败保留旧输出。只改 package/*.py、migrate.py、README.md；verify.py 仅公开 smoke。",
    ],
    files={'migrate.py':'from package.cli import main\nif __name__=="__main__": main()\n', 'package/__init__.py':'', 'package/parser.py':'# BUG: parser stub\n', 'package/transform.py':'# BUG: transform stub\n', 'package/storage.py':'# BUG: storage stub\n', 'package/cli.py':'# BUG: cli stub\n', 'config-v1.json':'{"schema":1,"services":[{"name":"db","host":"db","port":5432,"meta":{"zone":"a"}}]}\n', 'README.md':'Migrate nested service configuration atomically.\n', 'verify.py':"import json,tempfile\nfrom pathlib import Path\nfrom package.transform import transform\nassert transform({'schema':1,'services':[{'host':'x','port':1}]})['schema']==2\nprint('SMOKE_OK')\n"},
    allowed=['migrate.py','package/__init__.py','package/parser.py','package/transform.py','package/storage.py','package/cli.py','README.md'],
    reference_files={
      'migrate.py':'from package.cli import main\nif __name__=="__main__": main()\n',
      'package/__init__.py':'',
      'package/parser.py':"import json\ndef parse(path):\n with open(path,encoding='utf8') as f: return json.load(f)\n",
      'package/transform.py':"def transform(d):\n if d.get('schema')!=1: raise ValueError('schema')\n def walk(x):\n  if isinstance(x,list): return [walk(v) for v in x]\n  if not isinstance(x,dict): return x\n  if ('host' in x) != ('port' in x) or 'endpoint' in x and ('host' in x or 'port' in x): raise ValueError('conflict')\n  out={k:walk(v) for k,v in x.items() if k not in ('host','port')}\n  if 'host' in x:\n   if not isinstance(x['host'],str) or type(x['port']) is not int: raise ValueError('conflict')\n   out['endpoint']=x['host']+':'+str(x['port'])\n  return out\n out=walk(d); out['schema']=2; return out\n",
      'package/storage.py':"import json\nfrom pathlib import Path\ndef save(path,obj):\n t=Path(path).with_suffix('.tmp'); t.write_text(json.dumps(obj,sort_keys=True,separators=(',',':'))); t.replace(path)\n",
      'package/cli.py':"import sys\nfrom .parser import parse\nfrom .transform import transform\nfrom .storage import save\ndef main():\n if len(sys.argv)!=3: return 2\n try: save(sys.argv[2],transform(parse(sys.argv[1]))); return 0\n except (ValueError,OSError,KeyError,TypeError): return 1\nif __name__=='__main__': raise SystemExit(main())\n",
    },
    mutants=[{'package/transform.py':'def transform(d):\n d=dict(d); d["schema"]=2; return d\n'}, {'package/storage.py':'def save(path,obj):\n import json\n open(path,"w").write(json.dumps(obj))\n'}],
    oracle=_oracle("""import json,tempfile,subprocess,sys,os
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
 p=Path(d); i=p/'i'; o=p/'o'; i.write_text('{"schema":1,"services":[{"name":"db","host":"db","port":5432,"meta":{"zone":"a"}}]}'); o.write_text('{"old":1}')
 assert subprocess.run([sys.executable,'migrate.py',str(i),str(o)]).returncode==0
 x=json.loads(o.read_text()); assert x['schema']==2 and x['services'][0]['endpoint']=='db:5432' and x['services'][0]['meta']=={'zone':'a'} and 'host' not in x['services'][0] and 'port' not in x['services'][0]
 from unittest.mock import patch
 from package.storage import save
 before_commit=o.read_bytes()
 with patch('pathlib.Path.replace', side_effect=OSError('simulated commit failure')):
  try: save(o, {'should_not_commit':True})
  except OSError: pass
  else: raise AssertionError('storage did not use the required temporary-file replacement')
 assert o.read_bytes()==before_commit, 'failed commit damaged previous output'
 old=o.read_bytes(); i.write_text('{"schema":1,"services":[{"host":"x","port":1,"endpoint":"bad"}]}'); assert subprocess.run([sys.executable,'migrate.py',str(i),str(o)]).returncode==1 and o.read_bytes()==old
print('CHECK_OK')
"""))

_set('audit_bundle_integration',
    phases=[
        "这是一个多模块审计仓库。修复 parser.py、policy.py、aggregate.py 与根入口 audit.py：解析 JSONL 事件，策略支持 action 精确值或 `*`、以及 user 作用域（`alice:read`、`*:read`）；拒绝规则优先于允许规则。相同 event_id 的完全相同事件可重放，冲突必须失败。",
        "成功生成 bundle.json（version=1、按 user 排序聚合、total_events）；坏 JSON、未知 action、策略拒绝、重复冲突均返回 1 且保留旧输出，参数错误返回 2。只能修改上述模块；verify.py 是公开 smoke，需实际运行 CLI。",
    ],
    files={'audit.py':'from cli import main\nif __name__=="__main__": main()\n','parser.py':'# BUG parser\n','policy.py':'# BUG policy\n','aggregate.py':'# BUG aggregate\n','cli.py':'# BUG cli\n','audit.jsonl':'{"event_id":"1","user":"alice","action":"read"}\n','policy.json':'{"allow":["*:read"],"deny":[]}\n','bundle.json':'{}\n','verify.py':"from policy import allowed\nassert allowed({'allow':['*:read'],'deny':[]},'alice','read')\nprint('SMOKE_OK')\n"},
    allowed=['audit.py','parser.py','policy.py','aggregate.py','cli.py'],
    reference_files={
      'audit.py':'from cli import main\nif __name__=="__main__": main()\n',
      'parser.py':"import json\ndef parse(path):\n rows=[]\n for line in open(path,encoding='utf8'):\n  if line.strip(): rows.append(json.loads(line))\n return rows\n",
      'policy.py':"def allowed(policy,user,action):\n def hit(x): return x in (action,user+':'+action,'*:'+action,'*')\n return any(hit(x) for x in policy.get('allow',[])) and not any(hit(x) for x in policy.get('deny',[]))\n",
      'aggregate.py':"def aggregate(rows,policy):\n from policy import allowed\n ids={}; users={}\n for x in rows:\n  if not all(isinstance(x.get(k),str) and x[k] for k in ('event_id','user','action')): raise ValueError('event')\n  sig=repr(sorted(x.items()))\n  if x['event_id'] in ids:\n   if ids[x['event_id']]!=sig: raise ValueError('conflict')\n   continue\n  ids[x['event_id']]=sig\n  if not allowed(policy,x['user'],x['action']): raise ValueError('denied')\n  u=users.setdefault(x['user'],{}); u[x['action']]=u.get(x['action'],0)+1\n return {'version':1,'users':{k:users[k] for k in sorted(users)},'total_events':len(ids)}\n",
      'cli.py':"import sys\nfrom parser import parse\nfrom aggregate import aggregate\nimport json\nfrom pathlib import Path\ndef main():\n if len(sys.argv)!=4: return 2\n try:\n  result=aggregate(parse(sys.argv[1]),json.loads(Path(sys.argv[2]).read_text()))\n  t=Path(sys.argv[3]).with_suffix('.tmp'); t.write_text(json.dumps(result,sort_keys=True,separators=(',',':'))); t.replace(sys.argv[3]); return 0\n except (ValueError,OSError,KeyError,json.JSONDecodeError): return 1\nif __name__=='__main__': raise SystemExit(main())\n",
    },
    mutants=[{'policy.py':"def allowed(policy,user,action): return any(x in (action,user+':'+action,'*:'+action,'*') for x in policy.get('allow',[]))\n"}, {'aggregate.py':"def aggregate(rows,policy):\n return {'version':1,'users':{},'total_events':len(rows)}\n"}],
    oracle=_oracle("""import json,subprocess,sys,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
 p=Path(d); l=p/'l'; q=p/'q'; o=p/'o'; l.write_text('{"event_id":"1","user":"alice","action":"read"}\\n{"event_id":"2","user":"bob","action":"read"}\\n'); q.write_text('{"allow":["*:read"],"deny":["bob:read"]}'); o.write_text('{"old":1}')
 old=o.read_bytes(); assert subprocess.run([sys.executable,'audit.py',str(l),str(q),str(o)]).returncode==1 and o.read_bytes()==old
 q.write_text('{"allow":["*:read"],"deny":[]}'); assert subprocess.run([sys.executable,'audit.py',str(l),str(q),str(o)]).returncode==0
 assert json.loads(o.read_text())['users']['alice']['read']==1
 l.write_text('{"event_id":"1","user":"x","action":"read"}\\n{"event_id":"1","user":"y","action":"read"}\\n'); assert subprocess.run([sys.executable,'audit.py',str(l),str(q),str(o)]).returncode==1
print('CHECK_OK')
"""))

# Public smoke files are shallow examples. Actual hidden Docker checks grade
# the complete contract; public_smoke is not a supported Eval check type.

# Root entry points must propagate the CLI status to the operating system.
for _s in SPECS:
    if _s['key'] == 'config_migration_cli_contract':
        for _bucket in (_s['files'], _s['reference_files']):
            _bucket['migrate.py'] = _bucket['migrate.py'].replace('if __name__=="__main__": main()', 'if __name__=="__main__": raise SystemExit(main())')
    if _s['key'] == 'audit_bundle_integration':
        for _bucket in (_s['files'], _s['reference_files']):
            _bucket['audit.py'] = _bucket['audit.py'].replace('if __name__=="__main__": main()', 'if __name__=="__main__": raise SystemExit(main())')
