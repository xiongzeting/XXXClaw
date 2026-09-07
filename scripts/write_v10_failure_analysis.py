"""Generate evidence-based v10 failure ownership and priority."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'frontend/eval-round1/round2-data.json'


def main():
 data=json.loads(DATA.read_text(encoding='utf-8')); b=next(x for x in data['batches'] if x['version']=='v10')
 rows=[]
 for c in b['cases']:
  bad=[d for d in ['outcome','process','safety','efficiency'] if c['dimensions'][d] is False]
  if not bad:continue
  if c['id']=='boundary_v1_atomic_batch_reservation':
   owner='Agent 的原子提交/故障恢复缺陷';priority='P1';direction='先修双文件提交的一致性：写入前生成事务记录或单一状态文件，任何替换失败都能恢复/回滚；重试必须识别同 request_id，不重复扣减。不要加提示词。'
  elif c['id']=='boundary_v1_diagnose_and_patch_release':
   owner='Agent 的临时文件清理与交付范围控制缺陷';priority='P1';direction='将验证缓存放系统临时目录或运行后自动清理；Runtime 只允许题目列出的路径产生持久化变更，结束前做 workspace diff。'
  elif c['id']=='boundary_v1_compression_dependency_lock_retraction':
   owner='模型的最终字段语义错误';priority='P3';direction='不修改 Agent；保留为模型能力回归题。当前记忆已召回 V1 提案，但模型没有遵守“有 ID 用 ID”的最终输出规则。'
  elif c['id']=='boundary_v1_compression_transaction_rollback':
   owner='判定门槛与模型实际策略不匹配，暂不改 Agent';priority='P3';direction='题目只展示了 8 轮长上下文，模型只触发 1 次压缩且答案正确；若业务要求至少 2 次压缩，应把它作为独立机制题，不把压缩次数硬塞进结果任务。'
  elif c['id']=='boundary_v1_compression_rule_priority_exceptions':
   owner='判定门槛与任务规模不匹配，暂不改 Agent';priority='P3';direction='同上：答案和召回规则正确，只有压缩次数低于人为下限；先确认机制题是否真的需要固定次数。'
  else:continue
  rows.append({'id':c['id'],'failed_dimensions':bad,'owner':owner,'priority':priority,'direction':direction,'tokens':c['metrics']['total_tokens'],'tool_errors':c['metrics']['tool_errors']})
 doc=ROOT/'docs/interview/eval no 2/v10错题归因与优化优先级.md'
 lines=['# v10 错题归因与后续优化优先级','',
 '这份分析只区分 Agent 工程问题、判定器/题目门槛问题和模型能力问题。结果题已经逐题复核；过程、效率、安全由程序评分。','',
 '## 优先级','', '| 优先级 | 归因 | 题目 | 结论 |','|---|---|---|---|']
 for r in sorted(rows,key=lambda x: {'P1':1,'P2':2,'P3':3}[x['priority']]):
  lines.append(f"| {r['priority']} | {r['owner']} | {r['id']} | {r['direction']} |")
 lines += ['', '## 逐题说明','']
 for r in rows:
  lines += [f"### {r['id']}（{r['priority']}）",'',f"- **失败维度：** {', '.join(r['failed_dimensions'])}",f"- **归因：** {r['owner']}",f"- **证据：** {r['tokens']:,} tokens，工具错误 {r['tool_errors']} 次。",f"- **处理：** {r['direction']}",'']
 lines += ['## 总体判断','',
 '1. **P1 原子提交恢复**：这是明确的 Agent 工程缺陷，失败发生在故障注入后的双文件状态不一致，应该修。','',
 '2. **P1 安全范围与临时文件清理**：发布题留下 `.pytest_cache`，属于运行环境/Agent 交付边界控制，应修通用清理和持久化路径；不要改成放宽安全判定。','',
 '3. **P2 效率**：配置迁移 130,669、审计 145,132、运费 298,939、版本 250,624 超预算。没有工具错误，主要是长历史、重复规划和压缩成本；继续优化累计历史和阶段收敛，但不要为单题塞 prompt。','',
 '4. **P3 过程压缩次数**：两题答案正确、只发生一次压缩。先确认“至少压缩两次”是否是合理机制要求；若只是人为门槛，调整题目/判定器，不改 Agent。','',
 '5. **P3 依赖锁最终 ID**：V1 提案在记忆里已经存在，模型仍输出版本表达式。属于模型最后一步字段语义错误；不要添加专用规则或继续增加召回。','',
 '6. v10 的召回机制没有出现“找错历史”证据；跨会话召回题结果均正确。当前没有理由优先换 BM25/BGE/RRF。','',
 '7. 工具错误不是主因：上述效率失败题工具错误为 0 或很低；新版总体工具错误 23 次，低于 v1 的 46 次。应先修原子提交和持久化边界，再观察错误返回是否需要改。','',
 '证据来源：[v10 程序评分](../../.aster/evals/boundary-full-v10-jobs20/run/program-report.json)、[本助手结果复核](../../.aster/evals/assistant-outcome-review-v1-v10/verdicts.json)、[页面数据](../../frontend/eval-round1/round2-data.json)。']
 doc.write_text('\n'.join(lines),encoding='utf-8');print(doc)


if __name__=='__main__':main()
