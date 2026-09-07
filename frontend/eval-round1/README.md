# MiniClaw Eval 展示页

直接打开 `dashboard.html`，离线使用；`index.html` 是分文件入口。

## 当前页面顺序

1. 原第一轮 30 题报告与历史优化记录。
2. v1 原代码重新执行的 20 题，20 路 Luna。
3. 新版 v10 的 20 题，20 路 Luna，接在页面最后。

两批都已完成。结果由本助手逐题阅读有效题面、实际回答和关键代码，结合只读辅助检查判定；过程、效率、安全、可用性由当前程序评分。结果判断为 v1 **16/20**、新版 **18/20**；五维全通过为 **5/20**、**11/20**。逐题理由、原始辅助检查及评分误报说明均保留。已曝光、单次运行、单一助手复核，不作为未见盲测或穷尽正确性保证。

新版报告末尾追加“有效改动复盘”，与 `问题.md` 第 15 节同步：筛选 v1～v9 中值得保留的读快照、累计历史预算、召回去重和稳定前缀，明确区分 v10 撤除复杂验收接口的新增作用。展示反例与证据边界，不将组合改动降幅归因于单个模块；五维成绩未调整。本节数据来自 `.aster/evals/effective-changes-review/conclusions.json`。

| 维度 | v1 重跑 | 新版 v10 |
|---|---:|---:|
| 结果 | 16/20 | 18/20 |
| 过程 | 20/20 | 18/20 |
| 效率 | 6/20 | 16/20 |
| 安全 | 20/20 | 19/20 |
| 可用性 | 20/20 | 20/20 |
| 总 tokens | 5,090,509 | 2,218,747 |

页面支持题集、能力和失败维度筛选，tokens 排序、逐阶段需求与回答、判定理由、程序检查、CSV 和 JSON 导出。缺少完整模型时间区间时不估算有效耗时。费用为配置费率估算，不是服务商账单。

v1 使用已核验的原代码、题目配置和 Docker 镜像；历史 `.env` 未归档，连接和其余默认值读取现有 `.env`。主项目源码没有切换或回退。前端旧文件备份在 `.aster/frontend-before-v1-v10`；冻结代码、原始 Eval 结果没有覆盖。

## 数据与构建

- 程序评分：`.aster/evals/boundary-v1-jobs20-current-judge/run/program-report.json` 与 `.aster/evals/boundary-full-v10-jobs20/run/program-report.json`。
- 本助手结果判断：`.aster/evals/assistant-outcome-review-v1-v10/verdicts.json`。
- 说明：`docs/interview/eval no 2/v1重跑与新版20题助手判定.md`。
- 页面数据：`round2-data.json`；离线加载：`round2-data.js`。
- 构建：`python scripts/build_eval_round1_dashboard.py`，已接入当前两批已完成数据，后续构建不会恢复旧第二轮分数。
- 展示检查：`node scripts/development/verify-eval-current.cjs frontend/eval-round1`，检查两个入口、30+20+20 顺序、筛选、详情、导出、移动布局和脚本错误。

本地服务器可使用 `python -m http.server 8767 --bind 127.0.0.1 --directory frontend/eval-round1`，访问 `http://127.0.0.1:8767/dashboard.html`。

- Eval3 Luna 20题报告：打开 [eval3-report.html](./eval3-report.html)。
