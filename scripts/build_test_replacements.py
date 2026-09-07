"""New frozen compression probes replacing underspecified type oracles."""
import json,hashlib
from build_dialogue_campaign import CASES,EVALS,add,hidden_test,edits_only,write_suite

def noise(tag):return '\n'.join(f'{tag}-{i:03d}: worker=Shift{i}; region=auxiliary; note=unrelated dispatch archive, no change to active implementation; batch={i+15}' for i in range(120))
add('test_compressed_typed_parser','compression',[
    '稍后实现 toggle.py 的 parse_toggle(raw)，本次对话先记住要求，暂不写代码。raw 是字符串：去两端空白并忽略大小写后，on 返回 Python True，off 返回 Python False，unset 返回 Python None，其他字符串抛 ValueError。',
    '补充：空字符串也是非法值，不能把它当 unset；返回的必须是上述 Python 对象，不能返回同名字符串。现在仍不改代码。',
    '先看其他部门记录，简短确认即可，不用进长期记忆：\n'+noise('Ops'),
    '还有一批无关轮值登记，简短确认即可：\n'+noise('Finance'),
    '现在继续最早的 parse_toggle 任务，实现全部已确认要求，只改 toggle.py，并实际运行测试。'
],files={'toggle.py':'def parse_toggle(raw): return raw\n'},shared=True,
    checks=[hidden_test('from toggle import parse_toggle as f\nassert f(" ON ") is True\nassert f("oFf") is False\nassert f("unset") is None\nfor raw in ["", "yes", "NONE"]:\n try:f(raw)\n except ValueError:pass\n else:raise AssertionError(raw)'),edits_only('toggle.py')])
add('test_compressed_release_gate','compression',[
    '先跟踪这次发布，不要实际发布或写文件：必须构建成功、单元测试成功、contract-test 成功才能发布。当前构建与单元测试成功，contract-test 仍未运行。最终决策文件字段 may_release 必须为 JSON 布尔值，pending 是未满足前置条件名的列表。',
    'contract-test 执行失败。现阶段没有豁免，也没有重跑成功记录；请保留失败状态，不要把执行过当作成功。',
    '插入另一产品的运行登记，和我们发布无关：\n'+noise('OtherRelease'),
    '还有历史值班备注，只确认收到，别覆盖当前发布：\n'+noise('OldShift'),
    '继续最早的发布判断，用最后有效状态写 release-decision.json。只创建这个文件；may_release 用 JSON true/false，pending 按前置条件名称排序。不要执行发布。'
],files={'README.md':'Current release state only exists in the conversation.\n'},shared=True,
    checks=[hidden_test('import json\nfrom pathlib import Path\nv=json.loads(Path("release-decision.json").read_text())\nassert v=={"may_release":False,"pending":["contract-test"]}\nassert v["may_release"] is False'),edits_only('release-decision.json')])
for c in CASES:
    c['environment']={'MINICLAW_COMPACTION_ENABLED':'true','MINICLAW_PROGRESSIVE_COMPACTION_ENABLED':'true',
        'MINICLAW_COMPACTION_SOFT_TRIGGER_TOKENS':'4500','MINICLAW_COMPACTION_HARD_TRIGGER_TOKENS':'6500',
        'MINICLAW_COMPACTION_TARGET_TOKENS':'2500','MINICLAW_COMPACTION_KEEP_RECENT_TOKENS':'900',
        'MINICLAW_MEMORY_CONSOLIDATION_ENABLED':'false'}
    c['checks'] += [
        {'type':'metric','name':'compactions','min':1,'dimension':'process'},
        {'type':'metric','name':'history_archives','min':1,'dimension':'process'},
        {'type':'metric','name':'total_tokens','max':180000,'dimension':'efficiency'},
        {'type':'metric','name':'tool_calls','max':50,'dimension':'efficiency'},
        {'type':'metric','name':'successful_runs','min':5,'dimension':'reliability'},
        {'type':'metric','name':'model_errors','equals':0,'dimension':'reliability'},
        {'type':'final_regex','pattern':r'\S','dimension':'reliability'}]
    c['timeout_seconds']=720
    c['source'].update(split='test-replacement',family=c['id'],track='frozen-before-first-run',
        reason='Prior boolean-vs-string contract unspecified; original test cases kept immutable and marked invalid. This is an explicitly amended synthetic evaluation, not an untouched unseen benchmark.')
write_suite('fresh-test-replacements-v1',CASES)
p=EVALS/'fresh-test-replacements-v1.json'
m={'suite':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'excluded_original_ids':['fresh_test_feature_matrix','fresh_test_nested_contract']}
freeze=EVALS/'fresh-test-replacements-freeze.json'
if freeze.exists():assert json.loads(freeze.read_text(encoding='utf-8'))==m
else:freeze.write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Two explicit compression replacement cases frozen before execution')
