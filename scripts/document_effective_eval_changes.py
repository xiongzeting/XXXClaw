"""Write evidence-qualified conclusions, without changing implementation or Eval grades."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.aster/evals/effective-changes-review'


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def main():
    data=read(ROOT/'frontend/eval-round1/round2-data.json')
    a,b=data['batches']
    assert a['version']=='v1' and b['version']=='v10'
    metrics=[]
    for key,label in [('total_tokens','总 tokens'),('uncached_input','未缓存输入'),('cost_usd','估算费用 USD'),
                      ('tool_calls','工具调用'),('tool_errors','工具错误'),('agent_model_requests','主模型步骤'),
                      ('compactions','成功压缩')]:
        old,new=a['totals'][key],b['totals'][key]
        metrics.append({'label':label,'before':old,'after':new,'reduction_percent':round(100*(1-new/old),2)})
    retained=[]
    v9=ROOT/'.aster/evals/efficiency-revision-v9/snapshot/src/MiniClaw'
    v10=ROOT/'.aster/evals/boundary-full-v10-jobs20/snapshot/src/MiniClaw'
    for relative in ['coding_agent/memory/read_cache.py','coding_agent/memory/history_budget.py',
                     'coding_agent/memory/working.py','coding_agent/memory/manager.py','agent/context.py']:
        digest=hashlib.sha256((v9/relative).read_bytes()).hexdigest()
        assert digest==hashlib.sha256((v10/relative).read_bytes()).hexdigest()
        retained.append({'file':relative,'v9_v10_sha256':digest})
    items=[
      {'rank':1,'version':'v2','title':'保住已读文件的有效信息，减少压缩后重读',
       'before':'压缩后只剩“读过某文件”的摘要，模型为继续工作又把同一文件读一遍。',
       'change':'缓存有大小上限的 read 结果，带文件哈希；内容未变才复用，变更就失效。相同读结果去重，但用户明确发起的 read 仍实际执行。',
       'evidence':'旧 v1 → v2 的发票、脱敏、运费 read 次数分别 58→5、43→7、31→5。六题总 tokens 352.94 万→173.04 万，减少 51.0%；这是整组改动的实测，不能全部归到读缓存。',
       'limit':'当前 20 题 read 总数 233→141，与少重读方向一致；尚无只关闭读缓存的独立对照。',
       'status':'保留 · 有任务实测支持','code':'coding_agent/memory/read_cache.py'},
      {'rank':2,'version':'v3 → v5','title':'控制旧工具记录的总大小，并保留可回读原文',
       'before':'只裁剪单条大输出；大量几 KB 的代码、编辑参数和验证命令全部留下，每次请求继续重发。',
       'change':'给已完成的工具调用及结果设置累计预算，超限时替换为原文引用；保留新结果与最近工具对。v5 保持已发送视图稳定，到边界才集中裁剪。',
       'evidence':'将 v2 的固定请求交给 v3 投影器重读，库存与配置迁移的历史估算 tokens 分别减少 28.38%、27.60%；其他四题为 0%～4.92%。这是离线重放，未调用模型。',
       'limit':'局部输入更短已核验；回放读快照还可能加回内容，应看最终净输入。不能把历史估算降幅当服务商实测账单或正确率提升。',
       'status':'保留 · 有同输入离线证据','code':'coding_agent/memory/history_budget.py'},
      {'rank':3,'version':'v3','title':'同一道题使用同一会话身份，避免把自己当外部记忆',
       'before':'共用 session.jsonl，却每一轮生成新 session_id；检索只排除本轮 ID，之前轮次摘要又被当成其他会话召回。',
       'change':'共享会话固定 case.id + session_key；自动召回排除当前会话的 episode，过滤当前对话已包含的原文片段。真正隔离的新会话仍可召回。',
       'evidence':'历史三道压缩题的记忆注入曾占主请求输入估算的 34%～37%；快照确认修复从 v3 起生效，当前仍保留。当前三道跨会话召回题两版结果都为 3/3。',
       'limit':'修掉重复召回来源有明确代码证据；3/3→3/3 只能说明本组未观察到结果退步，不能说召回能力提升或单项省了多少。',
       'status':'保留 · 来源问题明确，收益未拆分','code':'coding_agent/memory/manager.py'},
      {'rank':4,'version':'v4 → v5','title':'动态记忆追加到尾部，旧历史尽量不反复改写',
       'before':'最新记忆、检查点放在历史前面，一点状态变化就打断长前缀；逐条裁剪又不断改旧消息。',
       'change':'v4 把变化写成带版本的尾部增量；v5 保存已发送历史视图，在阶段或大小边界再重建。新版保留记忆增量；旧的验收检查点注入已在 v10 删除。',
       'evidence':'前缀稳定与撤回逻辑有离线验证。历史 v2→v5 缓存占比 31.25%→57.61%、未缓存输入约降 22.2%，但总 tokens 增 27.3%，效率仍 1/6。',
       'limit':'保留减少无谓前缀变化的机制，不能宣传缓存率提升等于整体提速。当前 37.43%→56.44% 同样是多项改动与供应商缓存共同作用。',
       'status':'保留 · 缓存机制有效，整体收益有条件','code':'agent/context.py'},
      {'rank':5,'version':'v2 / v7 / v9','title':'按需开放工具，错误正常反馈，成本记录看净值',
       'before':'普通任务也看到 goal_complete，容易无效调用；三次中间验收错误就暂停；只记裁剪量，看不到读快照加回的成本。',
       'change':'只有活跃 Goal 才开放完成工具；v7 撤销三次中间报错暂停；v9 记录裁剪前、回放前、最终历史 tokens。',
       'evidence':'当前 v1 重跑有 2 次无效 goal_complete 调用，新版为 0；v7 有五次报错后仍能修复的本地回归；v9 的净成本字段帮助识别“裁剪了又补回来”。',
       'limit':'这是小范围减负、行为修正和诊断能力。两批 20 题均为 0 次暂停、可用性均 20/20，不认领本轮可用性提升。新增统计本身不节省 tokens。',
       'status':'保留 · 辅助改动，不作主要收益来源','code':'coding_agent/assistant/coding.py'},
    ]
    whys=[
        '让仍然有效的文件内容跨压缩保留下来，模型不用为了恢复工作状态反复读盘；哈希防止把旧内容当新内容。',
        '上下文会被许多小记录一起撑大，因此必须控制累计体积；保留回读入口，避免省掉信息后无法查证。',
        '排除当前对话已有的信息，减少自我重复与旧结论干扰，不把真正的跨会话记忆一并关闭。',
        '缓存需要连续前缀匹配；将小变化放在尾部、减少旧历史改写，才有机会复用已有输入。',
        '减少无关工具选择，给模型正常修错机会；通过净成本记录识别优化是否只是把开销搬到了另一个位置。']
    for item,why in zip(items,whys):item['why']=why
    conclusions={'title':'哪些修改真正有用：v1～v9 的保留项与 v10 的关键减法',
      'lead':'最明显的提升是少消耗、少返工，而不是模型突然更会推理。v1～v9 留下了上下文减负的基础；本次跨越还包含 v10 撤掉复杂验收接口，不能把全部收益记到 v9。',
      'metrics':metrics,'items':items,'retained_source_hashes':retained,
      'result_explanation':'本次结果 16/20→18/20，多通过的是审计 CLI 根入口和目录扫描清单两题。新版生成了更符合要求的代码；目前没有消融证明是某个记忆改动使它们通过。库存原子性与提案 ID 两题仍失败。',
      'v10_explanation':'v9 与 v10 的 read_cache、history_budget、working、manager、ContextJournal 五个实现文件哈希完全一致。v10 新做的是撤掉 task_checkpoint、verification_rebind 和模型侧验收合同，改为正常回答后独立评分。历史 v9 有 100 次 task_checkpoint、5 次 rebind；本次 v1 有 73 次 task_checkpoint，新版均为 0。撤掉这些工具直接消除了相应调用和协议返工入口，是本次最有解释力的新增机制；全体 token 降幅仍不能归因于单项。',
      'negative_evidence':'v1～v9 不是越改越好：历史全量 v1→v9 总 tokens 708.10 万→591.34 万，但工具错误 53→128，五维全通过仍 5/20→5/20；加上已发现的队列重复项，结果复核为 17/20→17/20。v8→v9 六题总 tokens 165.59 万→192.81 万、工具错误 30→42。不能把复杂验收协议、字段登记、重绑定、自动附加故障场景或三次报错暂停列为应恢复的成功方案。',
      'boundaries':'本次两批均 20 路、同一组已曝光题、每题一次；没有逐项开关的消融实验，旧六题也存在并发、运行与评分版本差异。历史 .env 未归档。过程 20/20→18/20、安全 20/20→19/20，并非五维全面提升；两道过程失败是压缩次数不足，不能直接说记忆丢失。安全失败仍保留。',
      'not_credited':'BM25/BGE/RRF/重排、grep/search 提速、网络恢复与原文落盘等早期建设，不因名称高级就算成本轮增量；本次没有换检索算法的消融证据。路径和 oracle 纠错提升成绩可信度，不能说它让 Agent 答对更多题。',
      'next_step':'如果需要给单项优化填写独立节省比例，应在同一代码基线上逐个关闭读缓存、召回去重、历史预算或尾部增量，并重复采样；本次只做证据复核，不新增跑题。',
      'sources':['.aster/evals/effective-changes-review/tool-evidence.json',
                 '.aster/evals/efficiency-revision-v3/projection-replay.json',
                 'docs/interview/eval no 2/v9全量20题与v1复核对比.md',
                 'docs/interview/eval no 2/v1重跑与新版20题助手判定.md']}
    OUT.mkdir(exist_ok=True);(OUT/'conclusions.json').write_text(json.dumps(conclusions,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['## 15. 真正有效的改动：保留上下文减负，撤掉验收负担（v1～v9 复盘，含 v10 边界）','',
           '**先说结论：**'+conclusions['lead'],'',
           '**本次实际变化：**结果 **16/20→18/20**，效率 **6/20→16/20**，五维全通过 **5/20→11/20**。'+conclusions['result_explanation'],'',
           '| 指标 | v1 原代码 20 路重跑 | 新版 v10 20 路 | 变化 |','|---|---:|---:|---:|']
    for m in metrics:
        fmt=lambda n:f'{n:,.6f}' if m['label']=='估算费用 USD' else f'{n:,}'
        lines.append(f"| {m['label']} | {fmt(m['before'])} | {fmt(m['after'])} | -{m['reduction_percent']:.2f}% |")
    lines+=['','### 按证据与保留价值排序','']
    for x in items:
        lines += [f"**{x['rank']}. {x['title']}（{x['version']}）**",'',
                  f"- **原来：**{x['before']}",f"- **修改：**{x['change']}",f"- **为什么：**{x['why']}",f"- **效果：**{x['evidence']}",f"- **限制：**{x['limit']}",'']
    lines += ['### 本次最重要的新变化其实在 v10','',conclusions['v10_explanation'],'',
              '### 不再当作成功经验的改动','',conclusions['negative_evidence'],'',conclusions['not_credited'],'',
              '**结论边界：**'+conclusions['boundaries'],'','**下一步：**'+conclusions['next_step'],'',
              '**面试可以这样说：**“我通过 Eval 定位了重复读文件、同会话重复召回和工具历史累计重放，保留文件哈希校验、召回去重、累计历史预算及稳定请求前缀；复测又发现内部验收协议本身造成大量返工，于是改成提交答案后独立评分。同组 20 题、同为 20 路的单次回归中，总 tokens 降低 56.4%，效率通过从 6/20 到 16/20，结果从 16/20 到 18/20。这是组合改动效果，单项因果尚未拆开验证。”','',
              '证据：[调用统计](../../.aster/evals/effective-changes-review/tool-evidence.json)、[固定请求投影重放](../../.aster/evals/efficiency-revision-v3/projection-replay.json)、[历史 v9 全量对比](eval%20no%202/v9全量20题与v1复核对比.md)、[本次逐题判断](eval%20no%202/v1重跑与新版20题助手判定.md)。','']
    doc=ROOT/'docs/interview/问题.md';s=doc.read_text(encoding='utf-8')
    marker='## 15. 真正有效的改动：'
    if marker in s:s=s[:s.index(marker)].rstrip()+'\n\n'
    else:s=s.rstrip()+'\n\n'
    doc.write_text(s+'\n'.join(lines),encoding='utf-8')
    print('Evidence-qualified conclusions and section 15 saved.')


if __name__=='__main__':main()
