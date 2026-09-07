# Eval 入口与数据分层

<!-- current-eval-partition:start -->
## 当前两轮题集口径（2026-09-06）

第一轮：开发集 **15**、测试集 **15**。第二轮：开发集 **8**、测试集 **7**，另有 **5** 道旧题回归。原保留题按能力分组、稳定 ID 排序后交替分配，不按成绩挑题。两轮均已曝光；测试集用于内部验证，不冒充未见成绩。

**当前不创建对比保留集。** 等用户明确要求 MiniClaw / Codex 最终对比时，再生成新的任务族。

可执行划分以 `evals/current-round-partitions.json`、`round1-*-current.json`、`round2-*-current.json` 及 active portfolio v5/v6 为准。下一轮使用 `evals/next-quality-limits-v1.json`，缓存命中率（%）与费用估算（USD）作为效率观察项进入逐题报告，**不设硬门槛，不计入五维分数**。费用按缓存输入、未缓存输入、输出各自的配置费率估算，不是服务商账单；缺失数据标为未采集。

历史冻结 snapshot、原始结果和 ZIP 保留原分层与原分数。报告正文涉及旧分层的执行记录属于历史口径，不是当前题集配置。当前分类只重组题目，未重跑模型或改变单题成绩。

归档中的资料清单和核对记录记录的是归档时的哈希；本次更新的可读说明与报告不再对应原哈希。原始清单不覆盖，冻结 JSON、ZIP 及原始评分仍按原清单追溯。
<!-- current-eval-partition:end -->

## 第一轮可视化展示

[打开第一轮展示页](../frontend/eval-round1/dashboard.html)，可离线使用。展示题集构成、五维结果、成本、逐题对话与检查依据，并在尾部单列压缩/召回和搜索优化。

按用户最新口径：路径误报纠正后，6 道网络波动的可用性直接按通过计，分母不变。页面综合 **15/30**、可用性 **27/30**；原始记录保留。工具失败次数、耗时本轮仅观察；[下一轮初始门槛](quality-limits-v1说明.md)已独立配置，尚未执行，不改变当前冻结批次。

## 当前入口：网络恢复口径 v5

使用 `active-eval-portfolio-v5.json` 或 `main-challenge-v5.json`，当前开发、测试入口对应 `round1-*-current.json`；旧保留入口已停用。沿用 v4 的题目、输入、结果和安全检查，仅更新网络恢复与运行健康口径；尚未运行 v5 真实模型题集。

- VPN 切换造成的短暂断网只恢复中断的模型请求，不自动重跑整个案例或题集。传输层有限重试耗尽后，Eval 最多追加一次请求恢复，沿用已成功工具结果和剩余步数。
- `model_errors` 保留原始错误总数；`network_model_errors` 与 `non_network_model_errors` 分开记录。健康检查使用后者和 `unrecovered_runs`，并保留成功回合、最终结果与安全等原有约束。
- `network_recovered_requests` 表示一次模型请求内经历网络失败后恢复；`network_recovered_runs` 表示有明确来源关联的运行恢复。`undelivered_runs` 保留原始未交付运行次数，`unrecovered_runs` 只扣除已明确恢复的旧网络失败。交付指标表示运行成功返回，业务是否正确仍由 outcome 检查决定。
- 统计仍包含累计 tokens、时延和重试开销；中断尝试未返回的服务端计费用量无法精确估计。原始 Trace、v4 套件及历史评分不覆盖，不把恢复成绩宣称为首轮无故障成绩。

本轮仅做三项修改的简单离线回归（49 项通过），没有模型复测。生成入口：`scripts/build_network_recovery_v5.py`。

## 上一版入口：路径检查修订版 v4

使用 `active-eval-portfolio-v4.json` 或 `main-challenge-v4.json`；三个分层对应 `*-challenge-v4.json`。新增 `tool_write_paths` 检查统一映射相对路径、Docker `/workspace` 路径和宿主机绝对路径，生成包含原始路径、执行路径、宿主路径和工作区相对路径的审计记录。

旧首轮 **7/30** 原样保留；仅纠正路径检查后为 **11/30**，这是评分纠错，不是模型能力提升。原始分数、复核标签、修订分数分别保存在 `evidence/path-oracle-v2.1/regrade.json` 的 `original_score`、`review_labels`、`revised_score`。4 条纯 oracle 误报不进入修订失败集；其余 19 条见 `hard-observed-failures-v2.json`。Windows + Docker 验证证据在 `.aster/evals/path-oracle-v2/docker-validation.json`。

v3 及其冻结输入、原始报告、旧复核标签均作为历史保留。以下为旧版记录；新运行请使用上面的 v4 入口。该路径检查审计成功 edit/write 的目标，不等同于任意 bash 副作用监控或任务阶段授权。

当前挑战题与旧基础题分开管理。主集包含开发、保留、测试三个分层，每层 10 条，五项能力各 2 条，共 30 条、计划 161 个对话回合。

**首轮已完成。原始全维 7/30，结果维度 29/30。安全题共用的 Windows/Docker 路径检查有缺陷，6 条受影响，其中 4 条只有此误报，不能计为 Agent 安全失败。冻结题目保留历史；复跑前应修订并自检这项 oracle，不能直接沿用有缺陷的安全检查。** 详情及修改优先级见 [高难度Eval首轮报告.md](高难度Eval首轮报告.md)；主入口 `active-eval-portfolio-v3.json`、失败导出及证据均附复核标签。

| 用途 | 文件 |
|---|---|
| 主集全部案例 | `main-challenge-v3.json`（整批审查完成后生成入口） |
| 开发集 | `development-challenge-v3.json` |
| 保留集 / validation | `retained-challenge-v3.json` |
| 测试集 | `test-challenge-v3.json` |
| 基础机制 smoke，31 条 | `smoke-basic-v3.json` |
| 简单题迁移清单 | `difficulty-migration-v3.json` |
| 历史行为失败回归 | `split-failures-v2.json` |
| 历史可用性诊断 | `split-availability-diagnostics-v2.json` |

三个 `*-challenge-v3.json` 在执行前分好层，成功和失败均保留。整批结束后按原分层导出 `hard-failures-<split>-v1.json`（该层有失败时生成）。失败子集适合定位和回归，但不能代替完整分层的通过率。测试失败一旦用于开发，就已经曝光，不能继续作为未见测试成绩。

旧 `measured-development-v2.json`、`retained-release-v2.json`、`test-release-v2.json` 共 96 条仍保存为历史基线，不进入当前挑战题的主集统计。只有迁移清单中的 31 条被明确归为 smoke；其余旧题不因未进入新主集就被认定为无价值。已知安全失败仍保留回归用途。

## 挑战题的验收

- 6 条跨会话记忆题：6–8 轮事实、更新、撤销、实体与时间作用域；末轮实际召回记忆后交付。
- 6 条压缩题：8 轮需求与大段干扰交错，至少两次压缩及历史归档后交付可执行模块与文件式 CLI。
- 12 条工具和交付题：缓存、JSON Pointer、时间关联、事件幂等、分账、迁移、任务调度等明确行为；函数和 CLI 都有独立验收。
- 6 条安全题：跨文件账本、依赖门槛、权限窗口、事故证据、库存和退款，包含不可信输入与晚到用户更新。

五维均有必要检查：结果、过程、效率预算、安全副作用和严格运行健康。预算通过不代表效率提升；发生模型调用错误后交付正确，也可能不通过严格健康维度。

18 条编程题的函数及 CLI 验收已在模型执行前用参考实现于 Docker 自检。参考实现不在模型工作区。其他输入满足各自声明的类型；异常验收针对对话中明确列出的错误条件。

## 固定版本和复跑

首轮执行程序为 `scripts/run_hard_campaign_v1.py`，源码、题目及 fixture 冻结在 `.aster/evals/hard-campaign-v1/snapshot/`。实际运行进程从该源码快照加载。冻结清单为 `hard-campaign-freeze-v1.json`；在模型执行前修正过一次负输入字段完整性，草稿清单另存，没有根据模型结果改题。

如需对当前业务源码复跑某一分层，先修订上述安全 oracle 并另存新版本，再参考以下命令替换套件路径；必须指定新输出目录。此命令评估当前源码，不能冒称复现了首轮快照版本。

```powershell
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
python scripts/run_dialogue_adapter_eval.py evals/development-challenge-v3.json --env-file .env --jobs 2 --repeat 1 --out .aster/evals/hard-development-next
```

不要同时启动三组各两路。当前机器剩余内存有限，本轮共享两路运行，以避免之前六路时的资源压力。

`scripts/build_hard_verified_v1.py` 是审查后生成器；已有 fixture 时会拒绝覆盖。旧 `build_hard_memory_v1.py`、`build_hard_delivery_v1.py`、`build_hard_safety_v1.py` 是已停用草稿。未运行的无效 fixture 草稿移入 `.aster/evals/hard-authoring/rejected-fixtures/`，不参与评测。
