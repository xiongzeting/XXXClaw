# 六路 Luna v4 完整复测与前版对比

最新请求前缀修复版，6 道已曝光原题。中断后从原工作区续跑，已完成整批评分。未改题、判定器、token/工具预算或 32 步上限。

## 结论与下一步

**六题均已结束评分，不等于六题均完成任务。缓存占比提高，但整批降成本目标没有达成。** 依赖锁题是本轮唯一五维全通过的题；其余五题各有两个阶段耗尽 32 步。配置迁移、脱敏、运费的最终产物通过外部结果检查，但 Agent 自己仍未完成验收收尾。

1. **优先解决验收返工。** 验收报错从 8 次增至 103 次，其中 76 次是缺少 comparison ID（要求逐项核对的检查编号），另有 16 次验证命令执行失败。原子预留重复出现同一缺失编号错误 14 次。应由运行时提供稳定的检查清单与结构化输出接口，明确区分协议不完整和业务断言失败；文件未变且已取得有效证据时复用验收结果，变更或撤回后再失效。不能把未检查项自动判通过，也不能靠增加提示词或步数上限消化循环。
2. **修实际业务边界。** 发票题折扣输入应得 54 分，实际返回 0；原子预留仍接受非法输入，故障注入仍发现部分提交。优先补可重放的计算边界、输入验证和事务恢复能力，不能把这些归为纯验收格式问题。
3. **再做缓存机制的单项对比。** 本轮缓存占比从 31.25% 升至 49.60%，但模型请求从 160 次增至 358 次，重试验收不断增加新的请求，未缓存输入仍上涨。先消除上述循环，再固定并发、输入和验收机制验证请求前缀的净收益。本轮不能把全部回退归因于缓存改动，也不能用命中率上升宣称整体优化成功。

上述优先级是后续修改建议，本次仅续跑与分析，未继续修改 Agent 实现。

## 核心对比

| 指标 | v2（上一完整六题） | v4（最新修复） | 变化 |
|---|---:|---:|---:|
| 总 tokens | 1,730,372 | 3,510,478 | +102.9% |
| 未缓存输入 | 1,130,934 | 1,670,564 | +47.7% |
| 缓存输入 | 514,048 | 1,644,032 | +219.8% |
| 输出 tokens | 85,390 | 195,882 | +129.4% |
| 工具调用 | 166 | 377 | +127.1% |
| 验收报错（含协议/命令失败） | 8 | 103 | +1187.5% |
| 输入缓存占比 | 31.25% | 49.6% | 比例仅作辅助 |
| 五维全通过 | 0/6 | 1/6 | 严格逐题 |

- 结果全项通过：**2/6 → 4/6**。
- 过程全项通过：**6/6 → 6/6**。
- 效率全项通过：**1/6 → 1/6**。
- 安全全项通过：**6/6 → 6/6**。
- 可用性全项通过：**6/6 → 1/6**。

不是 summary.score 的子检查平均分；上表按各题该维度必需检查是否全部通过统计。

可用性下降来自 10 次步数耗尽后的未恢复阶段，未把已恢复网络波动判为能力失败。过程 6/6 只表示通过本轮冻结的检查；这版没有新增工具错误上限或耗时上限，不能理解为过程无错或时间达标。工具错误实际为 13 → 48 次，额外质量门槛不能追溯改写本轮评分。

## 逐题

| 题目 | v2 tokens | v4 tokens | 变化 | 未缓存输入变化 | v4 结果 | v4 效率 |
|---|---:|---:|---:|---:|---|---|
| 配置迁移 | 300,089 | 731,812 | +143.9% | +59.4% | 通过 | 未通过 |
| 阶梯发票 | 323,281 | 592,605 | +83.3% | +31.8% | 未通过 | 未通过 |
| 脱敏优先级 | 374,092 | 633,796 | +69.4% | +16.6% | 通过 | 未通过 |
| 运费规则 | 445,486 | 633,560 | +42.2% | +26.0% | 通过 | 未通过 |
| 原子批量预留 | 169,253 | 833,497 | +392.5% | +302.5% | 未通过 | 未通过 |
| 依赖锁与撤回 | 118,171 | 85,208 | -27.9% | -44.2% | 通过 | 通过 |

## 失败证据

### 配置迁移

- efficiency / metric：total_tokens=731812; constraints={'name': 'total_tokens', 'max': 130000}
- efficiency / metric：tool_calls=83; constraints={'name': 'tool_calls', 'max': 65}
- reliability / metric：successful_runs=0; constraints={'name': 'successful_runs', 'min': 2}
- reliability / metric：unrecovered_runs=2; constraints={'name': 'unrecovered_runs', 'equals': 0}
- 运行中的验收错误：{"Invalid verification: expected exactly one task_checks JSON record, found 0": 1, "Invalid verification: missing comparison IDs: entrypoint, schema2_conflict, schema2_recursive, schema2_unknown": 5, "Invalid verification: evidence must be a nonempty string or JSON object": 1, "Invalid verification: missing comparison IDs: schema2_recursive": 2, "Verification command did not exit successfully": 1, "Invalid verification: missing comparison IDs: migration_behavior": 1}

### 阶梯发票

- outcome / command：exit=1, expected=0; output=Traceback (most recent call last):
  File "<string>", line 12, in <module>
    assert same(actual,expected), (original,actual,expected)
           ~~~~^^^^^^^^^^^^^^^^^
AssertionError: ({'lines': [{'id': 'a', 'units': 1, 'exempt': False}], 'discount_cents': 50}, {'lines': [{'id': 'a', 'base_cents': 0, 'tax_cents': 0, 'total_cents': 0}], 'grand_total_cents': 0}, {'lines': [{'id': 'a', 'base_cents': 50, 'tax_cents': 4, 'total_cents': 54}], 'grand_total_cents': 54})

- efficiency / metric：total_tokens=592605; constraints={'name': 'total_tokens', 'max': 240000}
- efficiency / metric：tool_calls=72; constraints={'name': 'tool_calls', 'max': 65}
- reliability / metric：successful_runs=6; constraints={'name': 'successful_runs', 'min': 8}
- reliability / metric：unrecovered_runs=2; constraints={'name': 'unrecovered_runs', 'equals': 0}
- 运行中的验收错误：{"Invalid verification: expected exactly one task_checks JSON record, found 0": 1, "Invalid verification: missing comparison IDs: solve-calculation": 5, "Invalid verification: evidence must be a nonempty string or JSON object": 1, "Verification command did not exit successfully": 3, "Invalid verification: missing comparison IDs: solve-calculation, solve-discount-validation, solve-input-unchanged": 5}

### 脱敏优先级

- efficiency / metric：total_tokens=633796; constraints={'name': 'total_tokens', 'max': 240000}
- efficiency / metric：tool_calls=75; constraints={'name': 'tool_calls', 'max': 65}
- reliability / metric：successful_runs=6; constraints={'name': 'successful_runs', 'min': 8}
- reliability / metric：unrecovered_runs=2; constraints={'name': 'unrecovered_runs', 'equals': 0}
- 运行中的验收错误：{"Verification command did not exit successfully": 7, "Invalid verification: missing comparison IDs: public_verify": 11, "Invalid verification: expected exactly one task_checks JSON record, found 0": 1, "Invalid verification: missing comparison IDs: core-solve": 6, "Invalid verification: missing comparison IDs: additional-cases, core-output": 3, "Invalid verification: missing comparison IDs: additional-cases, core-output, protected-overlap": 1}

### 运费规则

- efficiency / metric：total_tokens=633560; constraints={'name': 'total_tokens', 'max': 240000}
- efficiency / metric：tool_calls=75; constraints={'name': 'tool_calls', 'max': 65}
- reliability / metric：successful_runs=6; constraints={'name': 'successful_runs', 'min': 8}
- reliability / metric：unrecovered_runs=2; constraints={'name': 'unrecovered_runs', 'equals': 0}
- 运行中的验收错误：{"Verification command did not exit successfully": 2, "Invalid verification: missing comparison IDs: behavior_cap, behavior_core": 10, "Invalid verification: missing comparison IDs: shipping.solve": 1, "Invalid verification: missing comparison IDs: solve_new_input": 4, "Invalid verification: unknown criterion_id": 4, "Invalid verification: missing comparison IDs: behavior_core": 1}

### 原子批量预留

- outcome / oracle：{"passed": false, "classification": "capability_failure", "checks": [{"requirement_id": "merge_replay", "passed": true, "evidence": "turn1: merge same SKU; same ID plus normalized payload replays without second deduction"}, {"requirement_id": "validation", "passed": false, "evidence": "expected ValueError, no exception"}, {"requirement_id": "cli", "passed": false, "evidence": "invalid CLI accepted"}, {"requirement_id": "atomic_recovery", "passed": false, "evidence": "partial commit at boundary 1/True: (9, 0)"}], "exit_code": 10, "stderr": "", "sha256": "ecd42b3ac733a9ad0f9f17c7fba846e89e411eaf920aeb2f2fca70488fed293e"}
- efficiency / metric：total_tokens=833497; constraints={'name': 'total_tokens', 'max': 130000}
- efficiency / metric：tool_calls=66; constraints={'name': 'tool_calls', 'max': 65}
- reliability / metric：successful_runs=0; constraints={'name': 'successful_runs', 'min': 2}
- reliability / metric：unrecovered_runs=2; constraints={'name': 'unrecovered_runs', 'equals': 0}
- 运行中的验收错误：{"Invalid verification: expected exactly one task_checks JSON record, found 0": 1, "Invalid verification: missing comparison IDs: cli_behavior, reserve_behavior": 2, "Invalid verification: missing comparison IDs: reserve_behavior": 1, "Invalid verification: missing comparison IDs: reserve-implementation": 14, "Invalid verification: missing comparison IDs: reserve-implementation, reserve_behavior": 4, "Invalid verification: expected exactly one task_checks JSON record, found 2": 1, "Verification command did not exit successfully": 3}

## 耗时与中断

各题活动时间相加：2665.8 → 5731.0 秒；所有模型请求耗时相加：2474.0 → 5447.4 秒。这些不是六路批次的墙钟，也没有完全扣除网络重试等待。

v4 原进程在约 17:04 退出，原因未知；17:16 使用独立后台进程恢复。已保存原会话副本，原 Trace 继续追加；五题从未完成请求的下一次可执行位置恢复，一题从尚未启动的下一阶段继续，没有重做前六轮。中断可能使服务端缓存过期，尚未回传 usage 的请求也可能产生未计入的费用。

并发从两路变六路，最初还与 v3 重叠，不能把耗时变化全归因于缓存机制。总 tokens、未缓存输入均为已有服务回传用量。只有业务正确性保持的题，才能同时作为降成本与正确交付的证据。

## v3 的位置

v3 不含本次请求前缀修复，且不是完整六题报告。只列已形成完整 result.json 的题，不把它的半程总量和整批相除：

- 配置迁移：657,650 tokens，86 次工具，2 次暂停。

完整评分：`.aster/evals/efficiency-revision-v4/run/report.json`；对比数据：同目录上一级 `final-comparison.json`；恢复记录与原会话副本：`recovery/`。
