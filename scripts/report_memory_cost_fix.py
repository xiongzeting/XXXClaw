"""Report the completed candidate, retaining original checks and cost accounting."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / '.aster/evals/memory-cost-v2'
assert (RUN / 'completion.json').exists(), 'Wait for both cases before reviewing'
read = lambda p: json.loads(p.read_text(encoding='utf-8'))
baseline = {c['id']: c for c in read(ROOT / '.aster/evals/hard-campaign-v1/run/report.json')['cases']}
candidate = read(RUN / 'run/report.json')
CONFIRM = ROOT / '.aster/evals/memory-cost-v3'
comparison = []
for case in candidate['cases']:
    old = baseline[case['id']]['attempts'][0]
    new = case['attempts'][0]
    dims = {d: all(x['passed'] for x in new['checks'] if x.get('required', True) and x['dimension'] == d)
            for d in ('outcome', 'process', 'efficiency', 'safety', 'reliability')}
    row = {'id': case['id'], 'baseline': old['metrics'], 'candidate': new['metrics'],
           'dimensions': dims, 'all_passed': case['passed'],
           'failed_checks': [x for x in new['checks'] if x.get('required', True) and not x['passed']],
           'token_change_percent': round((new['metrics']['total_tokens']/old['metrics']['total_tokens'] - 1)*100, 2)}
    comparison.append(row)
summary = {'cases': comparison, 'policy': 'One observed candidate run on exposed cases; mixed source changes vs old baseline, not causal or statistical proof.'}
(RUN / 'comparison.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
table = '\n'.join(f"| `{c['id'].removeprefix('hard_v1_')}` | {c['baseline']['total_tokens']:,} | {c['candidate']['total_tokens']:,} | {c['token_change_percent']:+.2f}% | {c['baseline']['tool_calls']} → {c['candidate']['tool_calls']} | {c['baseline']['compactions']} → {c['candidate']['compactions']} | {'通过' if c['dimensions']['outcome'] else '未通过'} |" for c in comparison)
details = '\n'.join(f"- `{c['id']}`：全维{'通过' if c['all_passed'] else '未通过'}；" + ('无未通过项。' if not c['failed_checks'] else '；'.join(x['dimension']+': '+x['detail'][:400] for x in c['failed_checks'])) for c in comparison)
confirmation_text = ''
if (CONFIRM / 'completion.json').exists():
    assert read(RUN / 'freeze.json')['source_hashes'] == read(CONFIRM / 'freeze.json')['source_hashes']
    confirmed = read(CONFIRM / 'run/report.json')['cases'][0]
    a = confirmed['attempts'][0]
    b = baseline[confirmed['id']]['attempts'][0]
    failed = [x for x in a['checks'] if x.get('required', True) and not x['passed']]
    confirmation = {'id': confirmed['id'], 'metrics': a['metrics'], 'failed_checks': failed,
                    'passed': confirmed['passed'], 'same_candidate_source': True,
                    'token_change_percent': round((a['metrics']['total_tokens']/b['metrics']['total_tokens']-1)*100, 2)}
    (CONFIRM / 'comparison.json').write_text(json.dumps(confirmation, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    confirmation_text = f'''### 压缩题独立复核（相同代码，另行保留）

首轮压缩题 turn7 与 turn8 都出现 ConnectError，只有 6/8 轮被记为成功，因此 **首轮 -57.92% 不作为完整执行成本的改善证明**。未改代码、预算或题目，另以独立目录执行一次完整任务，不覆盖首轮结果。

复核 `{confirmed['id']}`：总 token **{a['metrics']['total_tokens']:,}**，相对旧基线观察变化 **{confirmation['token_change_percent']:+.2f}%**；工具调用 **{a['metrics']['tool_calls']}**，成功压缩 **{a['metrics']['compactions']}**；成功阶段 **{a['metrics']['successful_runs']}/8**，model_errors **{a['metrics']['model_errors']}**。全维{'通过' if confirmed['passed'] else '未通过'}。

{'未通过项：' + '；'.join(x['dimension']+': '+x['detail'][:300] for x in failed) if failed else '全部原始验收条件通过。'}

证据 `.aster/evals/memory-cost-v3/run/report.json` 与 `comparison.json`。复核为单独执行的一条任务，时延不与两路批次直接作等条件比较；不得只挑两次中较低的 token 当成绩。
'''
    display = [dict(c) for c in comparison]
    for c in display:
        if c['id'] == confirmed['id']:
            c['candidate'] = a['metrics']
            c['token_change_percent'] = confirmation['token_change_percent']
            c['dimensions'] = {d: all(x['passed'] for x in a['checks'] if x.get('required', True) and x['dimension'] == d)
                               for d in ('outcome','process','efficiency','safety','reliability')}
    table = '\n'.join(f"| `{c['id'].removeprefix('hard_v1_')}` | {c['baseline']['total_tokens']:,} | {c['candidate']['total_tokens']:,} | {c['token_change_percent']:+.2f}% | {c['baseline']['tool_calls']} → {c['candidate']['tool_calls']} | {c['baseline']['compactions']} → {c['candidate']['compactions']} | {'通过' if c['dimensions']['outcome'] else '未通过'} |" for c in display)
text = f'''# 压缩与召回成本修复

本轮修改了实际实现，没有调整 Eval 的题目、结果预期或效率阈值。首轮两条已曝光高耗任务以 gpt-5.6-luna、原 Python 3.11 环境共享两路复跑，各运行一次，全部结束后统一审查；随后对连接错误影响的压缩题进行一次独立复核。

## 实际复跑

下表召回题取首轮 v2，压缩题取完整执行的独立复核 v3，并非挑选较低成本。两题的结果检查均通过；压缩题完整执行仍超过 240,000-token 预算 15,445 tokens，约 6.4%，不记为全维通过。

| 案例 | 原总 token | 本次总 token | 变化 | 工具调用 | 成功压缩 | 结果验收 |
|---|---:|---:|---:|---|---|---|
{table}

首轮 v2 的原始状态（含连接错误，保留不覆盖）：

{details}

{confirmation_text}

原始报告 `.aster/evals/memory-cost-v2/run/report.json`、逐项对比 `comparison.json`、token 来源 `candidate-accounting.json` 均保留。`total_tokens` 是本次 provider 返回使用量的累计；输入、输出、缓存命中与摘要调用开销可以在对比文件逐项查看。来源拆分使用字节估算，只用于定位，不能充当精确计费 token。

## 实现变化

1. **阻止记忆重新撑大同一会话。** 自动召回跳过当前会话已由活动历史/检查点承载的 archive 与 episode；保留跨会话召回、semantic/procedure 和显式 archive_search。归档 metadata 增加 session_id，方便核验来源。
2. **去掉重复的当前请求。** 若最新用户请求仍在保留消息中，摘要只引用它；在单轮内部切分、当前请求很长时，通过摘要与可恢复归档承载，避免整段原文再次复制进摘要。摘要提示要求合并新增信息、去重与处理明确覆盖，并提供目标文本预算。
3. **先决定压缩是否值得，再写归档。** artifact 先 prepare，只有接受的压缩才 persist、索引和提取事实。软阈值暂缓不产生额外归档。压缩后必须实际缩小本地历史至少 `max(1, min(256, history_tokens // 20))` 个估算 token；无收益的相同前缀不反复调用模型摘要。
4. **修复刷新游标。** 压缩后按移除前缀映射游标，保留未读的工具证据；未压缩时不推进游标。动态查询去重并限制新增工具证据长度，同 ID 内容更新可以替换旧对象。精确重复且时间/作用域等元数据一致的渲染正文合并，保留来源。
5. **补齐成本观测。** 每次模型请求区分工作对话、记忆注入、检查点、工具结果、工具调用参数、系统提示及工具定义。provider 实际 input/output/cached 独立保存，purpose 区分主模型和压缩等辅助调用。正常暂缓记为 compaction.deferred，取消和真实失败分别统计；节省量采用同口径的本地历史估算，避免把不可压缩的系统/工具开销算成压缩收益。

## 验证与边界

- Python 3.11 中相关 **138 项测试通过**，覆盖压缩恢复、工具 call/result 配对、无收益回退、无归档副作用、跨会话召回、同 ID 更新、未读工具证据、token 统计和取消路径。
- 固定输入/固定摘要的离线双进程对比：长请求压缩后估算从 **3264 → 1714 tokens**，重复请求从 2 份 → 1 份，保留原文与重新加载结果均一致；连续三次软暂缓的额外归档从 3 份 → 0 份。该数据是机制验证，不是端到端降幅。
- 第一版候选在代码审查发现刷新游标回归后被主动停止，未阅读中间答案，不作为成绩；记录位于 `.aster/evals/memory-cost-v1/abandoned.json`。修正并补测后以独立 v2 快照运行。
- 当前快照相对早期基线还包含会话前的其他代码差异，清单见 `freeze.json`；上表只能作为这两个版本的观察结果，不能把全部差异因果归于本轮补丁。每条只有一个样本，也不能证明普遍成本降幅或时延分布改善。
- 未更换 BM25/BGE/RRF/重排方案，未缩小固定 Eval 预算；记忆召回质量仍以独立结果检查为准。Python 3.13 环境曾出现 5 项 FAISS 后端测试失败；相同测试在实际评测的 Python 3.11 环境通过，本轮没有修改这些后端测试或安装依赖来掩盖差异。

## 后续验收

本轮两题完整执行的结果均正确，成本均低于早期基线，但压缩题尚未满足固定效率预算。后续应在固定版本上扩大其他任务族并做多次配对对比，同时报告主模型/摘要成本、工具次数、结果正确率及故障恢复；不通过微调阈值把本轮变成通过。需要进一步做“仅关闭同会话自动回灌”“仅改变压缩策略”的消融，才能明确各项优化的贡献。已曝光题仅用于开发回归，不能作为未见测试集成绩。
'''
path = ROOT / 'docs/压缩与召回成本修复.md'
path.write_text(text, encoding='utf-8')
print(json.dumps({'results': [{k: c[k] for k in ('id','dimensions','all_passed','token_change_percent')} for c in comparison]}, ensure_ascii=True))
