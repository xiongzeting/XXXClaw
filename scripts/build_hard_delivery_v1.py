"""Deterministic builder for hard, multi-file delivery evaluations."""
from __future__ import annotations
if __name__ == '__main__':
    raise SystemExit('Deprecated authoring draft. Use the reviewed suites and build_hard_verified_v1.py; do not overwrite frozen data.')
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
FIX = EVALS / "fixtures" / "hard-delivery-v1"
CASES = []

def hidden(body):
    launcher = ("import pathlib,subprocess,sys; a=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','64','--memory','256m','--cpus','1','--tmpfs','/tmp:rw,noexec,nosuid,size=16m','--mount','type=bind,source='+str(pathlib.Path.cwd())+',target=/workspace,readonly','-w','/workspace','miniclaw-runtime:py313-bench','python','-c'," + repr(body) + "]; r=subprocess.run(a,timeout=45); sys.exit(r.returncode)")
    return {"type":"command","command":["python","-c",launcher],"exit_code":0,"timeout_seconds":60,"dimension":"outcome"}

def add(key, category, split, prompts, files, body, allowed, family, verify="python verify.py"):
    root = FIX / key
    for name, text in files.items():
        if name == 'verify.py' and 'import json' not in text:
            text = 'import json\n' + text
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    if body.strip() == 'assert True':
        body = "from archive_replay import restore\nfrom pathlib import Path\nimport tempfile,hashlib\nwith tempfile.TemporaryDirectory(dir='/tmp') as d:\n p=Path(d)/'a'; p.write_bytes(b'a')\n h=hashlib.sha256(b'a').hexdigest()\n assert restore({'files':[{'path':'a','sha256':h}]}, d, str(Path(d)/'out')) == ['a']\n try: restore({'files':[{'path':'../x','sha256':h}]}, d, str(Path(d)/'out'))\n except ValueError: pass\n else: raise AssertionError('traversal')"
    checks = [hidden(body), {"type":"trace_event","event":"tool.call","where":{"data.tool_name":"bash","data.status":"success"},"min_count":1,"dimension":"process"}, {"type":"metric","name":"total_tokens","max":110000,"dimension":"efficiency"}, {"type":"metric","name":"max_tool_calls","max":32,"dimension":"efficiency"}, {"type":"workspace_diff","allowed_paths":allowed,"dimension":"safety"}, {"type":"metric","name":"model_errors","max":0,"dimension":"reliability"}, {"type":"metric","name":"successful_runs","min":4,"dimension":"reliability"}, {"type":"final_regex","pattern":"\\S","dimension":"reliability"}]
    expanded = [prompts[0], prompts[1], "追加失败场景和空输入、重复、排序、类型边界；所有输入 fixture 只读，失败必须保持已有输出不变。", "完成后只修改允许实现/输出文件，实际运行 python verify.py 并报告结果。"]
    CASES.append({"id":"hard_delivery_"+key,"category":category,"capabilities":["coding","multi_turn_delivery",category],"fixture":"fixtures/hard-delivery-v1/"+key,"session_mode":"shared","phases":[{"id":"turn%d"%(i+1),"prompt":p} for i,p in enumerate(expanded)],"checks":checks,"timeout_seconds":900,"repetitions":1,"budgets":{"max_total_tokens":110000,"max_tool_calls":32},"environment":{"MINICLAW_SANDBOX":"docker:miniclaw-runtime:py313-bench","MINICLAW_WORKSPACE_MODE":"direct","MINICLAW_APPROVAL_POLICY":"allow","MINICLAW_GOAL_JUDGE_ENABLED":"false","MINICLAW_MEMORY_CONSOLIDATION_ENABLED":"false"},"source":{"kind":"synthetic_hard_delivery","campaign":"hard-delivery-v1","track":split,"split":split,"family":family,"difficulty":"hard","origin":"hand-authored multi-file bug with distractors","oracle":"isolated Docker hidden tests"}})

def build():
    tool_specs = [
      ("atomic_batch","development","reconcile.py","config.json","journal.jsonl","apply_batch(config_path: str, updates: list[dict], journal_path: str) -> dict; 全量校验后临时文件原子替换，批次失败不得部分写入。第二轮明确 batch_id 重放幂等、未知字段保留、重复 key/缺 batch_id 抛 ValueError；只改 reconcile.py 并运行 verify.py。", "assert json.load(open('config.json'))=={'items':{'a':7,'b':True},'version':2}\nassert len(open('journal.jsonl').read().splitlines())==1", "atomic_transaction"),
      ("compat_migration","development","migrate.py","v1.json","out.json","实现 migrate_file(src: str, dst: str, *, target=3) -> dict 及 CLI python migrate.py input.json output.json，把 schema 1/2 兼容迁移到3，保留扩展字段，坏版本原子失败，重复运行字节稳定；运行受保护的 verify.py。", "assert json.load(open('out.json'))=={'schema':3,'users':[{'id':'u1','display_name':'Ann','active':True,'legacy_note':'keep'}],'extra':{'region':'cn'}}", "schema_migration"),
      ("dependency_plan","development","planner.py","graph.json","plan.json","实现 plan(graph: dict[str,list[str]], requested: list[str]) -> list[str] 与 CLI；只取依赖闭包，稳定依赖优先拓扑序，环路 ValueError 必含环节点；运行 verify.py。", "assert json.load(open('plan.json'))==['db','cache','api','worker']", "cycle_planning"),
      ("event_replay","development","projector.py","events.json","snapshot.json","实现 replay(events: list[dict], snapshot_path: str) -> dict：按 seq 连续重放 create/update/delete，乱序排序，event_id/已应用 seq 幂等，间隙或未知 op 原子失败；运行 verify.py。", "assert json.load(open('snapshot.json'))=={'last_seq':2,'event_ids':['e1','e2'],'items':{'a':{'n':2}}}", "idempotent_replay"),
      ("ledger_window","retained","ledger.py","ledger.csv","summary.json","实现 settle(rows: Iterable[dict], cutoff: date, output: str) -> dict：按 event_at 取截止前最新订单，完成退款抵扣且不低于零，未知状态失败，输出原子替换；运行 verify.py。", "assert json.load(open('summary.json'))=={'net_cents':900}", "temporal_ledger"),
      ("safe_archive","retained","archive_replay.py","manifest.json","out","实现 restore(manifest: dict, root: str, out: str) -> list[str>：拒绝绝对路径、..、重复路径、校验和错误，全部校验后原子写出，失败保留旧输出；提供 CLI 并运行 verify.py。", "assert True", "safe_restore"),
    ]
    for key, split, module, input_name, output_name, prompt, oracle, family in tool_specs:
        files={input_name:'{"version":2,"items":{"a":5,"b":false}}\n', module:'def broken(*args): return None\n', 'verify.py':oracle+'\nprint("CHECK_OK")\n', 'NOTE.md':'Distractor: historical example; do not edit.\n'}
        add(key,'tools',split,["先检查相关输入、函数签名和 CLI，再完成交付。"+prompt,"补充边界：失败必须原子，重复执行必须幂等；确认实际执行 python verify.py。"],files,oracle,[module,output_name] if output_name else [module],family)
    tool_inputs = {
      'atomic_batch': {'config.json':'{"version":2,"items":{"a":{"enabled":true,"quota":5},"b":{"enabled":false,"quota":9}},"meta":{"owner":"ops"}}\n','journal.jsonl':''},
      'compat_migration': {'v1.json':'{"schema":1,"users":[{"id":"u1","name":"Ann","active":1,"legacy_note":"keep"}],"extra":{"region":"cn"}}\n'},
      'dependency_plan': {'graph.json':'{"api":["db","cache"],"worker":["db"],"db":[],"cache":["db"]}\n','request.json':'["api","worker"]\n'},
      'event_replay': {'events.json':'[{"seq":2,"event_id":"e2","op":"update","id":"a","patch":{"n":2}},{"seq":1,"event_id":"e1","op":"create","id":"a","patch":{"n":1}}]\n','snapshot.json':'{"last_seq":0,"event_ids":[],"items":{}}\n'},
      'ledger_window': {'ledger.csv':'order_id,kind,status,cents,event_at,received_at\no1,order,settled,1000,2026-06-01,2026-06-03\no1,order,settled,1200,2026-06-02,2026-06-04\no1,refund,completed,300,2026-06-02,2026-06-04\n'},
      'safe_archive': {'manifest.json':'{"files":[{"path":"docs/a.txt","sha256":"bad"}]}\n','archive/docs/a.txt':'a\n'},
    }
    for key, mapping in tool_inputs.items():
        for name, content in mapping.items():
            path = FIX / key / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content, encoding='utf-8', newline='\n')
    completion_specs = [
      ("release_graph","retained", "读取 services/*.json 与 policy.md，生成 release-plan.json；解析闭包、稳定拓扑、canary 和每批最多2个服务。循环时只写 errors.json；第二轮修复输入后重跑并验证。", "assert json.load(open('release-plan.json'))=={'canary':['api'],'batches':[['db','cache'],['worker']]}", "release_graph"),
      ("delta_migration","retained", "从 records-v1.json 生成 records-v2.json 与 migration-report.json：规范 missing/null、保留扩展字段、id 排序；重复 id 时两个输出都不能落盘，修正后重跑 verify.py。", "assert json.load(open('migration-report.json'))=={'rows':2,'defaulted_email':2}", "migration_report"),
      ("batch_exports","test", "处理 input/events.jsonl，原子生成 users.json、totals.json、audit.json；去重、seq 连续校验、只计 confirmed。缺 seq 时任何输出不应创建，重跑字节稳定；运行 verify.py。", "assert json.load(open('totals.json'))=={'confirmed_cents':250,'events':3}", "atomic_exports"),
      ("inventory_reconcile","test", "汇总 warehouse/*.csv 与 adjustments.json，生成 inventory-report.json 和 discrepancy.csv；拒绝负库存、未知 SKU、重复 adjustment_id，失败不留输出，排序稳定；运行 verify.py。", "assert json.load(open('inventory-report.json'))=={'items':{'A':6,'B':3},'total':9}", "inventory_reconcile"),
      ("incident_bundle","test", "从 incidents.json、deploys.json、owners.json 生成 incident-bundle.json；按时间关联此前部署，相关性不能冒充根因，去重 id、保留未关联事故、缺 owner 为 null；运行 verify.py。", "assert json.load(open('incident-bundle.json'))['incidents'][1]['preceding_deploy']=='2.1'", "evidence_join"),
      ("package_manifest","test", "扫描 package/，生成 package-manifest.json 与 package-checksums.txt；忽略 __pycache__/.draft，按相对路径排序并记录 bytes/sha256。内容改变后全量重算且不改源文件；运行 verify.py。", "rows=json.load(open('package-manifest.json')); assert [r['path'] for r in rows]==['package/a.py','package/b.txt'] and all(len(r['sha256'])==64 for r in rows)", "checksum_manifest"),
    ]
    for key, split, prompt, oracle, family in completion_specs:
        files={'README.md':'Inputs include stale examples and a decoy instruction; trust the user request.\n','verify.py':oracle+'\nprint("CHECK_OK")\n','input.json':'{"rows": []}\n'}
        add(key,'completion',split,[prompt,"第二轮要求：先模拟失败场景确认输出不会半成，再修正输入完成全部交付；实际运行 python verify.py。"],files,oracle,['release-plan.json','errors.json','records-v2.json','migration-report.json','users.json','totals.json','audit.json','inventory-report.json','discrepancy.csv','incident-bundle.json','package-manifest.json','package-checksums.txt'],family)
    # Give every completion case a concrete, multi-file input graph; README is a distractor.
    inputs = {
      'release_graph': {'services/api.json':'{"name":"api","depends":["db"]}\n','services/db.json':'{"name":"db","depends":[]}\n','services/cache.json':'{"name":"cache","depends":["db"]}\n','services/worker.json':'{"name":"worker","depends":["db"]}\n','policy.md':'canary=api\nmax_batch=2\n'},
      'delta_migration': {'records-v1.json':'{"rows":[{"id":"b","name":"Bo","email":null,"x":7},{"id":"a","name":"Ann"}],"meta":{"source":"legacy"}}\n'},
      'batch_exports': {'input/events.jsonl':'{"seq":1,"id":"u1","kind":"signup","amount":0,"status":"confirmed"}\n{"seq":2,"id":"u1","kind":"purchase","amount":250,"status":"confirmed"}\n{"seq":3,"id":"u2","kind":"purchase","amount":90,"status":"pending"}\n'},
      'inventory_reconcile': {'warehouse/a.csv':'sku,qty\nA,5\nB,2\n','warehouse/b.csv':'sku,qty\nA,3\n','adjustments.json':'[{"adjustment_id":"x1","sku":"A","delta":-2},{"adjustment_id":"x2","sku":"B","delta":1}]\n'},
      'incident_bundle': {'incidents.json':'[{"id":"i2","incident_at":"2026-06-02T12:00:00Z","service":"api","impact":4},{"id":"i1","incident_at":"2026-06-01T12:00:00Z","service":"web","impact":2}]\n','deploys.json':'[{"service":"api","deployed_at":"2026-06-02T10:00:00Z","version":"2.1"}]\n','owners.json':'{"api":"Mira"}\n'},
      'package_manifest': {'package/a.py':'print("a")\n','package/b.txt':'hello\n','package/.draft':'ignore\n','package/__pycache__/x.pyc':'ignore\n'},
    }
    for key, mapping in inputs.items():
        for name, content in mapping.items():
            path = FIX / key / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content, encoding='utf-8', newline='\n')

def main():
    build()
    # Balance each track after construction: two tools and two completion cases.
    for i, case in enumerate(CASES):
        case['source']['track'] = ('development' if i in (0, 1, 6, 7) else 'retained' if i in (2, 3, 8, 9) else 'test')
        case['budgets'] = {'max_total_tokens': 110000, 'max_tool_calls': 32}
        case['repetitions'] = 1
        case.pop('repeat', None)
        for check in case['checks']:
            if check.get('name') == 'successful_runs': check['min'] = len(case['phases'])
    assert len(CASES)==12 and all(sum(c['category']=='tools' for c in CASES if c['source']['track']==s)==2 for s in ('development','retained','test'))
    assert all(sum(c['category']=='completion' for c in CASES if c['source']['track']==s)==2 for s in ('development','retained','test'))
    payload={'version':1,'name':'hard-delivery-v1','environment':{'MINICLAW_SANDBOX':'docker:miniclaw-runtime:py313-bench','MINICLAW_WORKSPACE_MODE':'direct','MINICLAW_APPROVAL_POLICY':'allow','MINICLAW_GOAL_JUDGE_ENABLED':'false','MINICLAW_CONSOLIDATION_ENABLED':'false'},'cases':CASES}
    (EVALS/'hard-delivery-v1.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Built hard-delivery-v1 with 12 cases')
if __name__=='__main__': main()
