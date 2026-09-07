"""Write a reviewed v2/v5 comparison; never rewrite benchmark scores."""
import json
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.aster/evals/efficiency-revision-v5'
DOC = ROOT / 'docs/interview/eval no 2/六路Luna-v5与v2完整对比.md'
data = json.loads((OUT / 'final-comparison.json').read_text(encoding='utf-8'))
for version, expected in data['original_report_sha256'].items():
    original_path = ROOT / f'.aster/evals/efficiency-revision-{version}/run/report.json'
    assert hashlib.sha256(original_path.read_bytes()).hexdigest() == expected
audit = json.loads((OUT / 'cause-audit.json').read_text(encoding='utf-8'))
a, b = data['totals']['v2'], data['totals']['v5']
def pct(new, old): return f'{(new / old - 1) * 100:+.1f}%'
def yes(value): return '通过' if value else '未通过'

lines = ['# 六路 Luna v5 与 v2 完整对比', '',
'2026-09-06。v5 于北京时间 18:17–18:32 完成六题，每题一次、六路并行，模型 gpt-5.6-luna。整批约 14.94 分钟，无模型错误、无网络重试、无进程中断。冻结源码和题目在结束时通过哈希核对。', '',
'## 结论', '',
'**缓存复用和部分题目的成本有改善，但整体效率目标没有达成。** 相比 v2，未缓存输入下降 22.2%，总 tokens 却增加 27.3%，输出增加 49.1%；结果通过从 2/6 提高到 4/6，效率仍只有 1/6 通过，五维全通过仍为 0/6。', '',
'六题均已结束评分，不等于六题均正常完成任务。发票、脱敏、运费各暂停一个阶段；发票和脱敏在题目原有的第八轮继续后收尾，运费最终一轮仍暂停。六题中有五题最后一轮状态为 success，但原子预留和依赖锁的结果验收没有通过。', '',
'**本次优先修验收接口的易错设计与失败后的处理方式。保留历史前缀稳定方向，不因总 tokens 上涨就整体回退；也不能凭缓存率上升宣布已经优化成功。** 本报告只分析已完成的实跑，未继续修改 Agent 实现或追加模型重跑。', '',
'## 汇总对比', '', '| 指标 | v2 | v5 | 变化 |', '|---|---:|---:|---:|']
for label, key in [('总 tokens', 'total_tokens'), ('未缓存输入 tokens', 'uncached_input'),
                   ('缓存输入 tokens', 'cached_tokens'), ('输出 tokens', 'output_tokens'),
                   ('主模型请求数', 'agent_model_requests'), ('工具调用数', 'tool_calls'),
                   ('原始工具错误标记（口径有变化）', 'tool_errors'),
                   ('工具报错或验收报错事件（同一事件不重复计）', 'tool_or_verification_error_events'),
                   ('验收错误反馈次数', 'verification_error_count'),
                   ('成功压缩次数', 'compactions'), ('压缩模型 tokens', 'compaction_tokens')]:
    lines.append(f'| {label} | {a[key]:,} | {b[key]:,} | {pct(b[key], a[key])} |')
lines += [f"| 输入缓存占比 | {a['cache_rate_pct']}% | {b['cache_rate_pct']}% | +26.36 个百分点 |",
          '| 阶段暂停次数 | 0 | 3 | 两次步数耗尽，一次协议卡住保护 |', '',
'52 次是携带当前验收错误状态的工具反馈次数，含协议、验证命令失败及之前尚未消除的错误；不等于 52 个独立业务错误。新结构化接口实际执行并记录计划 65 次，另有 4 次计划在命令执行前被拒绝；这 4 次也包含在错误反馈中，不重复相加。', '',
'工具错误标记的 13→61 不能直接解释成执行器故障增长：v2 只把验收协议问题附在反馈中，v5 会同时设置工具 error 状态。上表另列两版统一按“工具状态 error 或携带验收错误”统计的事件数，消除仅标记方式不同带来的影响。', '',
'| 五维（该题该维度所有必需检查均通过） | v2 | v5 |', '|---|---:|---:|']
for d, name in [('outcome', '结果'), ('process', '过程'), ('efficiency', '效率'), ('safety', '安全'), ('reliability', '可用性')]:
    lines.append(f"| {name} | {a['dimension_pass'][d]}/6 | {b['dimension_pass'][d]}/6 |")
lines += ['| 五维全部通过 | 0/6 | 0/6 |', '',
'过程 6/6 仅代表本轮冻结检查通过，不代表工具没有报错。这六题沿用原来没有新增工具错误上限、时间上限的配置，不能追溯添加门槛。安全 6/6 也仅代表已配置的安全检查，不扩大成所有攻击与瞬时写入均有保障。', '',
'v5 没有网络故障，因此可用性 3/6 的下降来自上述真实暂停。v2 记录了 14 次请求重试及 1 条模型错误，恢复后的成绩保持原报告口径，不把网络波动当能力失败。', '',
'## 逐题对比', '', '| 题目 | v2 tokens | v5 tokens | 总量变化 | 未缓存输入变化 | 结果 v2→v5 | v5 token 预算 |', '|---|---:|---:|---:|---:|---|---:|']
for r in data['cases']:
    x, y = r['v2'], r['v5']
    budget = 130000 if ('config_migration' in r['id'] or 'atomic_batch' in r['id']) else 240000
    lines.append(f"| {r['name']} | {x['total_tokens']:,} | {y['total_tokens']:,} | {pct(y['total_tokens'], x['total_tokens'])} | {pct(y['uncached_input'], x['uncached_input'])} | {yes(x['dimension_pass']['outcome'])}→{yes(y['dimension_pass']['outcome'])} | {budget:,} |")
lines += ['',
'- **配置迁移是本轮最清楚的正向证据**：保持结果和正常收尾，tokens 下降 30.4%，未缓存输入下降 59.2%，主请求 29→25，工具 41→37；仍未达到 130,000 的挑战预算。',
'- **发票和运费的业务结果改善，代价仍高**：v2 发票曾把应为 54 分的案例算成 53 分；运费漏了易碎附加费，且 CLI 失败可能覆盖旧输出。这些冻结外部检查在 v5 均通过，但验收返工推高了请求数与输出。',
'- **脱敏的总 tokens 只降 7.3%**，未缓存输入降 42.2%；它经历协议保护暂停和第八轮恢复，不能只用下降数字宣称长任务体验改善。',
'- **原子预留未解决事务失败**，消耗反而增加 142.0%；依赖锁虽然更便宜，仍没按要求输出提案编号，不能算正确性保持下的成功优化。', '',
'## 根因与优先级', '',
'### P0：先消除验收 JSON 的双重约定，避免反复跑已经跑过的验证', '',
'原来的改动把版本、比较和文件绑定交给 Runtime，这个方向合理。但模型仍要同时拼两样东西：命令输出的 JSON 外壳，以及另一个工具参数里的字段路径。本轮它经常只修好其中一个。', '',
'真实例子（发票 Trace 第 269 行，脱敏第 240 行，运费第 192 行）：', '',
'```text',
'命令输出：{"observations":{"public":true}}',
'模型绑定：/observations/public',
'程序实际：先取出 observations 对象，再按路径取值',
'正确路径：/public',
'```', '',
'随后模型又把外层 observations 去掉，同时把路径改为 /public，于是程序转而报“找不到 observations JSON”。它就在两种错误格式之间来回切换。发票、脱敏、运费共有 **20 次调用直接使用了多余的 /observations/ 路径**。共 24 次错误反馈包含“没有找到唯一 JSON 记录”，其中有程序没输出记录、去掉外壳和旧错误叠加等不同原因，不能与 20 次直接相加。', '',
'**建议**：让运行时提供一种明确的记录与字段绑定方式。常见顶层观测值优先按已有 check_id 绑定，减少模型同时手填外壳和路径；复杂值才使用显式路径，并在工具说明中给出同一份完整的小例子。路径错误应返回可用字段、绑定基准和可修复位置。若兼容旧路径，必须在无歧义时规范化并记录，不能猜值或改 expected。', '',
'同一命令已执行成功、原始输出完整、被测文件和需求版本没有变化时，允许只纠正“读取哪个字段”的绑定后重新解析证据；不应为了 JSON 包装重放整套程序，更不能重放有副作用的命令。字段缺失、比较失败、文件版本变化仍拒绝。', '',
'**验收**：合法观测只需执行一次；修格式不产生重复副作用；格式错和业务失败分开反馈；错误值仍不能通过。先做小型接口回归再跑模型题，不继续叠加大段提示词。', '',
'### P1：让无进展检测识别“来回犯错”，保留暂停但减少被迫续跑', '',
'三个暂停的位置：', '',
'| 题目 | 阶段 | 原因 | 该阶段 tokens | 后续 |', '|---|---|---|---:|---|',
'| 发票 | turn7 | 耗尽 32 步 | 350,594 | turn8 继续后成功收尾 |',
'| 脱敏 | turn7 | verification_stalled | 211,429 | turn8 继续后成功收尾 |',
'| 运费 | turn8 | 耗尽 32 步 | 259,843 | 最后仍暂停 |', '',
'v5 的三次同错保护确实在脱敏题触发，避免继续消耗到上限；但发票、运费仍在不同报错之间切换。当前错误签名包含完整错误集合与整个工作区的 epoch，错误文本变化或修改验证文件都可能重置计数，不能稳定识别“业务没有推进，只是在改验收包装”。', '',
'**建议**：按需求项、被测文件内容版本和错误类别追踪进展；包装错误来回切换也纳入有限恢复预算。把验收器修正与业务文件推进分开；先尝试无副作用的证据重新绑定，仍不能恢复再保存明确检查点。不能删除暂停保护、调高 32 步或把未完成改成 success。', '',
'### P1：保留缓存改动，先减少无效请求，再优化跨轮重建', '',
'配置迁移同阶段 23 对相邻请求中，20 对完整保留此前消息前缀；它未压缩，前缀诊断只有 1 次 history 改写。整批输入缓存率达到 57.61%，未缓存输入下降。这支持保留稳定前缀方向，但多项修改同时发生、每版只跑一次，不能把全部降幅归因于某一行代码。', '',
'返工是更直接的新增成本：发票主请求 27→62、运费 40→65、原子预留 16→32。原子预留第二阶段没有验收错误反馈，却用 15 次请求花了 254,602 tokens；需要继续分析已经完成的测试与历史为何仍被重复携带，不能把它全部归为协议错误。', '',
'**建议**：先消除协议循环，再固定并发做单项对比。按阶段记录稳定前缀长度、实际未缓存输入、工具历史大小、重建原因和验收之后的请求成本；用户提出新验收时做需求覆盖差异检查，文件未变且原证据覆盖的部分直接复用。保留新要求、撤回和文件变更导致证据失效的逻辑。', '',
'压缩次数 51→38，但压缩模型 tokens 71,018→77,921，没有因为次数减少就自然省钱。这部分仅占本轮总 tokens 约 3.5%，不应先大改召回算法来代替解决主请求返工。', '',
'### P1：补真实故障覆盖，不能让自测完成替代外部验收', '',
'原子预留外部故障探针仍发现：第一个文件替换已经发生后抛错，库存变为 9，但预留记录还是 0 条。v2、v5 均有同一个失败证据。生成实现把第一次库存写入放在后续回滚 try 之外，因此该边界异常没有被整体恢复覆盖。', '',
'**建议**：为跨文件操作提供通用故障场景与可重放的状态不变量检查，覆盖每个提交边界前后、响应丢失和相同请求重试。自写 observations/expected 是自测；即使程序登记了通过，也不能证明没有部分提交。不要把普通布尔 PASS 当作事务恢复证据，更不要给本题写固定答案。', '',
'**额外发现的判定器疑点**：原子题 validation 还报告“应抛 ValueError 却没有”，对应直接调用 reserve(..., [])。题面明确要求“CLI 请求必须非空”，但没有同样明确规定函数本身拒绝空批次；生成的 CLI 已拒绝空列表。这个新增失败项应标为契约待复核，不宜直接宣传为能力退化。原始自动分数保留；独立的部分提交失败仍成立，所以整题结果依然未通过，4/6 的汇总不受这项复核影响。', '',
'### P2：核验交付字段与来源，不先断言召回丢失', '',
'依赖锁的目标、包版本、依赖顺序、hash 都正确，但 rejected_proposals 应为 ["V1", "core=2.6.0"]，实际第一项写成了版本表达式。v2 也在同一字段失败。', '',
'v5 最后一次请求的摘要和注入记忆仍包含“Atlas 提案 V1：拟升级 core=2.5.0，并要求 crypto>=1.9.0”。因此这次不能归因于 V1 没有被召回或被压缩删除。', '',
'**建议**：为需要结构化交付的任务保留实体编号、状态、来源与变更关系；最终逐字段核对用户要求，尤其“有 ID 用 ID”这种选择规则。只验证 JSON 可解析和包版本正确不足以完成该题。不要为此直接更换 BM25/BGE/RRF，也不要硬编码 V1。', '',
'## 耗时、评分与解释边界', '',
f"- 整批墙钟：v2 {a['batch_elapsed_seconds']/60:.2f} 分钟，v5 {b['batch_elapsed_seconds']/60:.2f} 分钟。v2 两路、v5 六路，且网络情况不同，不能把这个缩短宣传为单任务算法提速。",
f"- 各题时长相加：{a['active_wall_seconds']:.1f}→{b['active_wall_seconds']:.1f} 秒（{pct(b['active_wall_seconds'], a['active_wall_seconds'])}）；模型请求时长相加：{a['model_seconds_sum']:.1f}→{b['model_seconds_sum']:.1f} 秒（{pct(b['model_seconds_sum'], a['model_seconds_sum'])}）。这些不是扣尽网络等待后的可控实验结果。",
'- 未缓存输入减少不等于账单同比减少：输出更多，而输入、缓存输入、输出的费率不同；这里比较服务回传用量，不凭总 tokens 或命中率推算费用。',
'- 题面、fixture、oracle 脚本、token/工具预算和 32 步上限与冻结基线一致；主代码中完成监督和协议实现本来就是本次比较对象，不能把它的成功状态当成独立正确率。',
'- 均为已曝光回归，一次采样结果；不冒充未见测试集成绩，不覆盖任何原始分数。当前原子 validation 疑点在独立复核标签记录。', '',
'## 可追溯记录', '',
'- [v5 原始报告](../../../.aster/evals/efficiency-revision-v5/run/report.json)',
'- [v2 原始报告](../../../.aster/evals/efficiency-revision-v2/run/report.json)',
'- [完整对比数据](../../../.aster/evals/efficiency-revision-v5/final-comparison.json)',
'- [逐阶段、字段路径和最终上下文证据](../../../.aster/evals/efficiency-revision-v5/cause-audit.json)',
'- [独立复核标签](../../../.aster/evals/efficiency-revision-v5/review-labels.json)',
'- [冻结清单](../../../.aster/evals/efficiency-revision-v5/freeze.json)', '',
'复现分析：scripts/report_efficiency_v5_final.py → scripts/audit_efficiency_v5_final.py → scripts/write_efficiency_v5_report.py；仅读取完成报告和精确路径的主 Trace，不重复跑模型，不把备份 Trace 再累计。']
DOC.write_text('\n'.join(lines)+'\n', encoding='utf-8')
review = {'raw_report_unchanged': True, 'outcome_pass_after_excluding_disputed_subcheck': '4/6',
          'labels': [{'case': 'boundary_v1_atomic_batch_reservation', 'check': 'validation',
                      'label': 'contract_ambiguity_needs_oracle_review',
                      'reason': 'Oracle requires reserve(..., []) to fail; explicit nonempty rule names CLI. CLI already rejects empty lines.',
                      'independent_failure_retained': 'atomic_recovery: partial commit at boundary 1/True: (9, 0)'}]}
(OUT / 'review-labels.json').write_text(json.dumps(review, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(DOC)
