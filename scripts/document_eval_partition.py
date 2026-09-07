import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
START='<!-- current-eval-partition:start -->';END='<!-- current-eval-partition:end -->'
NOTE='''## 当前两轮题集口径（2026-09-06）

第一轮：开发集 **15**、测试集 **15**。第二轮：开发集 **8**、测试集 **7**，另有 **5** 道旧题回归。原保留题按能力分组、稳定 ID 排序后交替分配，不按成绩挑题。两轮均已曝光；测试集用于内部验证，不冒充未见成绩。

**当前不创建对比保留集。** 等用户明确要求 MiniClaw / Codex 最终对比时，再生成新的任务族。

可执行划分以 `evals/current-round-partitions.json`、`round1-*-current.json`、`round2-*-current.json` 及 active portfolio v5/v6 为准。下一轮使用 `evals/next-quality-limits-v1.json`，缓存命中率（%）与费用估算（USD）作为效率观察项进入逐题报告，**不设硬门槛，不计入五维分数**。费用按缓存输入、未缓存输入、输出各自的配置费率估算，不是服务商账单；缺失数据标为未采集。

历史冻结 snapshot、原始结果和 ZIP 保留原分层与原分数。报告正文涉及旧分层的执行记录属于历史口径，不是当前题集配置。当前分类只重组题目，未重跑模型或改变单题成绩。

归档中的资料清单和核对记录记录的是归档时的哈希；本次更新的可读说明与报告不再对应原哈希。原始清单不覆盖，冻结 JSON、ZIP 及原始评分仍按原清单追溯。
'''

def update(path):
    s=path.read_text(encoding='utf-8')
    if START in s:s=s[:s.index(START)]+s[s.index(END)+len(END):].lstrip('\n')
    block=START+'\n'+NOTE+END
    if 'eval no 2' in path.parts:
        result=s.rstrip()+'\n\n'+block+'\n'
    else:
        first,rest=s.split('\n',1)
        result=first+'\n\n'+block+'\n\n'+rest.lstrip('\n')
    path.write_text(result,encoding='utf-8')

def main():
    paths=['README.md','evals/README.md','evals/boundary-v1说明.md','frontend/eval-round1/README.md',
      'docs/interview/项目总览.md','docs/interview/Eval评测体系与数据集指标全流程详解.md',
      'docs/interview/eval no 1/README.md','docs/interview/eval no 2/README.md',
      'docs/interview/eval no 1/高难度Eval首轮报告.md','docs/interview/eval no 1/01报告/高难度Eval首轮报告.md',
      'docs/interview/eval no 2/01报告/第二轮20题Eval报告.md']
    for name in paths:
        if (ROOT/name).exists():update(ROOT/name)
    p=ROOT/'frontend/eval-round1/README.md';s=p.read_text(encoding='utf-8');s=s.replace('三层可用性为 10/10、9/10、8/10。','当前两集的分母各为 15，分组结果从逐题成绩实时重算。');p.write_text(s,encoding='utf-8')
    p=ROOT/'evals/README.md';s=p.read_text(encoding='utf-8').replace('三个分层对应 `*-challenge-v5.json`','当前开发、测试入口对应 `round1-*-current.json`；旧保留入口已停用');p.write_text(s,encoding='utf-8')
    p=ROOT/'evals/quality-limits-v1说明.md';s=p.read_text(encoding='utf-8')
    if '## 缓存与费用观察' not in s:s+='''
## 缓存与费用观察（下一轮启用）

新 suite 对每题追加 `cache_hit_percent`（%）和 `estimated_cost_usd`（USD），均设置 `report_only=true`、`required=false`，无 min/max。观察成功或数据缺失都不改变五维评分及通过率。`cache_hit_percent` 按所有主模型和辅助模型请求的缓存输入 / 输入加权计算，不平均各次百分比。价格按每次实际路由配置费率计算，缓存输入不重复计入未缓存输入。

原始 cost_usd 继续作为可追溯的已记录小计；新的完整费用观察在 usage、缓存统计或费率不足时为 null，不能将缺失显示为免费。断连但未返回 usage 的服务端费用未知；价格估算不是账单。LLM 裁判费用未取得同等完整计费证明时，也标为不完整。

生成：`python scripts/build_quality_limits_v1.py`。入口已在 active-eval-portfolio-v6.json 的 next_evaluation 中指定。当前仅配置与离线验证，没有启动下一轮模型。
'''
    p.write_text(s,encoding='utf-8')
    policy=json.loads((ROOT/'evals/current-round-partitions.json').read_text(encoding='utf-8'))
    lines=['# 第一、二轮当前题集划分','','固定规则按能力分组并按 SHA-256(ID) 排序，再交替加入开发/测试。没有按成绩分配；没有生成新题。','']
    for group,value in policy['rounds'].items():
        lines += [f'## {group}', '', '| 案例 | 原分层 | 当前分层 |','|---|---|---|']
        for cid,split in value['assignments'].items():lines.append(f"| {cid} | {value['original_splits'][cid]} | {split} |")
        lines.append('')
    (ROOT/'evals/当前两轮划分说明.md').write_text('\n'.join(lines),encoding='utf-8')
    print('Updated current project/round documentation; frozen JSON and ZIP untouched.')
if __name__=='__main__':main()
