"""Read-only comparison of recorded requests; incomplete runs are never scored."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'.aster/evals'
SUITE=json.loads((BASE/'efficiency-revision-v4/snapshot/evals/boundary-efficiency-v4.json').read_text(encoding='utf-8'))
SELECTED=json.loads((BASE/'efficiency-revision-v4/freeze.json').read_text(encoding='utf-8'))['selected']


def events_for(version, case):
    root=BASE/f'efficiency-revision-{version}/run/cases'/case
    events={}
    for path in root.rglob('trace.jsonl'):
        for line in path.read_text(encoding='utf-8').splitlines():
            try:e=json.loads(line)
            except ValueError:continue
            events[e['event_id']]=e
    return sorted(events.values(),key=lambda e:e['timestamp'])


def metrics(events):
    result=Counter()
    first_changes=Counter()
    for event in events:
        data=event['data']
        if event['type']=='model.request':
            usage=data.get('usage',{})
            result['requests']+=1
            for key in ['input_tokens','output_tokens','cached_tokens','total_tokens']:
                result[key]+=usage.get(key,0)
            result['model_ms']+=data.get('duration_ms',0)
            if data.get('purpose')=='agent':
                result['agent_requests']+=1
                result['agent_input']+=usage.get('input_tokens',0)
                result['agent_cached']+=usage.get('cached_tokens',0)
                prefix=data.get('context',{}).get('metadata',{}).get('request_prefix',{})
                if prefix:first_changes[prefix['first_changed_component']]+=1
            if data.get('purpose')=='compaction':result['compaction_tokens']+=usage.get('total_tokens',0)
        if event['type']=='tool.call':
            result['tool_calls']+=1
            result['tool_errors']+=data.get('status')!='success'
            result['verification_errors']+=bool(data.get('details',{}).get('task_verification',{}).get('error'))
        if event['type']=='model.transport' and data.get('phase')=='retry_scheduled':result['retry_scheduled']+=1
    for key in ['input_tokens','output_tokens','cached_tokens','total_tokens','tool_calls','tool_errors','agent_requests','compaction_tokens','verification_errors']:
        result.setdefault(key,0)
    result['uncached_input']=result['input_tokens']-result['cached_tokens']
    result['cache_rate_pct']=round(100*result['cached_tokens']/result['input_tokens'],2) if result['input_tokens'] else None
    result['agent_cache_rate_pct']=round(100*result['agent_cached']/result['agent_input'],2) if result['agent_input'] else None
    return {**result,'prefix_changes':dict(first_changes)}


rows=[]
for case_id in SELECTED:
    case=next(c for c in SUITE['cases'] if c['id']==case_id)
    phase_map={p['prompt']:p['id'] for p in case['phases']}
    versions={}
    for version in ['v2','v3','v4']:
        events=events_for(version,case_id)
        starts={e['run_id']:e for e in events if e['type']=='run.started'}
        ends={e['run_id']:e for e in events if e['type']=='run.completed'}
        by_phase={}
        for run_id,start in starts.items():
            phase=phase_map.get(start['data']['request'],'unknown')
            records=[e for e in events if e.get('run_id')==run_id]
            by_phase.setdefault(phase,[]).extend(records)
        completed={phase_map.get(starts[r]['data']['request'],'unknown'):e['data'].get('status')
                   for r,e in ends.items() if r in starts}
        result_path=BASE/f'efficiency-revision-{version}/run/cases'/case_id/'result.json'
        result=json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else None
        active=[{'run_id':r,'phase':phase_map.get(e['data']['request'],'unknown'),'started_at':e['timestamp']}
                for r,e in starts.items() if r not in ends]
        versions[version]={'metrics':metrics(events),'phases':{p:metrics(es) for p,es in by_phase.items()},
            'ended_phases':completed,'required_phases':len(case['phases']),'active_without_completion':active,
            'last_event_at':events[-1]['timestamp'] if events else None,
            'final_result':({'passed':result['passed'],'dimensions':result['dimensions'],
                             'metrics':result['metrics'],'failure_reasons':result['failure_reasons']} if result else None)}
    common=[p['id'] for p in case['phases'] if all(versions[v]['ended_phases'].get(p['id'])=='success' for v in ['v2','v4'])]
    matched={}
    for version in ['v2','v4']:
        events=events_for(version,case_id)
        runs={e['run_id'] for e in events if e['type']=='run.started' and phase_map.get(e['data']['request']) in common}
        matched[version]=metrics([e for e in events if e.get('run_id') in runs])
    rows.append({'case':case_id,'versions':versions,'matched_successful_phases':common,'matched_metrics':matched})
out=BASE/'efficiency-revision-v4/partial-comparison.json'
out.write_text(json.dumps({'observed_at':datetime.now(timezone.utc).isoformat(),'status':'incomplete_process_exited_no_final_report',
    'warning':'No capability scores for unfinished cases. Usage is recorded usage only; last uncompleted requests may have unreported provider cost. Matched comparisons cover only listed successful phases, not whole-task optimization. Different concurrency and cache routing confound comparisons.',
    'cases':rows},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
for row in rows:
    print(json.dumps({'case':row['case'], 'v4':row['versions']['v4']['metrics'],
        'ended':row['versions']['v4']['ended_phases'],'required':row['versions']['v4']['required_phases'],
        'active':row['versions']['v4']['active_without_completion'],'matched':row['matched_successful_phases'],
        'matched_v2':row['matched_metrics']['v2'],'matched_v4':row['matched_metrics']['v4'],
        'v3_final':row['versions']['v3']['final_result'] is not None},ensure_ascii=False))

names={'hard_v1_tiered_invoice':'阶梯发票','hard_v1_redaction_priority':'脱敏优先级',
       'hard_v1_shipping_caps':'运费规则','boundary_v1_compression_dependency_lock_retraction':'依赖锁与撤回',
       'boundary_v1_atomic_batch_reservation':'原子批量预留','boundary_v1_config_migration_cli_contract':'配置迁移'}
lines=['# 六路 Luna v4 阶段结果与对比（未完成）', '',
    '核查时间：2026-09-06 17:06～17:10（北京时间）。这是中断现场分析，不是最终 Eval 成绩。', '',
    '## 先看运行状态', '',
    '- v4 六题均没有最终 result.json，整批没有 report.json 或 completion.json；原进程 20360 已不存在。',
    '- 六题最后记录集中在 17:04:42～17:04:48。没有正常结束记录，也没有留下解释整批退出的异常；退出原因未知，不能认定是 VPN、网络或 Agent 能力失败。',
    '- 发票、脱敏、运费已完成前 6/8 轮，实施阶段 turn7 中断；依赖锁已结束前 6/8 轮、尚无 turn7 启动记录；原子预留和配置迁移均还在 turn1，整题有 2 轮。',
    '- 此处“阶段成功”只表示对话正常结束，不代表独立业务 oracle 全部通过。尚无六题的完整正确率与预算结论。', '',
    '## 与 v2 比较相同的已完成阶段', '',
    '只比较两版都正常结束的 turn1～turn6。总 tokens 包含这些阶段已记录的主/辅助请求；未缓存输入 = input_tokens − cached_tokens。', '',
    '| 题目 | v2 总 tokens | v4 总 tokens | 变化 | v2 未缓存输入 | v4 未缓存输入 | 变化 |',
    '|---|---:|---:|---:|---:|---:|---:|']
matched_rows=[r for r in rows if r['matched_successful_phases']]
for row in matched_rows:
    a,b=row['matched_metrics']['v2'],row['matched_metrics']['v4']
    lines.append(f"| {names[row['case']]} | {a['total_tokens']:,} | {b['total_tokens']:,} | {(b['total_tokens']/a['total_tokens']-1)*100:+.1f}% | {a['uncached_input']:,} | {b['uncached_input']:,} | {(b['uncached_input']/a['uncached_input']-1)*100:+.1f}% |")
a={key:sum(r['matched_metrics']['v2'].get(key,0) for r in matched_rows) for key in ['total_tokens','uncached_input','model_ms']}
b={key:sum(r['matched_metrics']['v4'].get(key,0) for r in matched_rows) for key in a}
lines += ['',f"四题这些阶段合计：总 tokens **{a['total_tokens']:,} → {b['total_tokens']:,}（{(b['total_tokens']/a['total_tokens']-1)*100:+.1f}%）**；未缓存输入 **{a['uncached_input']:,} → {b['uncached_input']:,}（{(b['uncached_input']/a['uncached_input']-1)*100:+.1f}%）**。", '',
    f"累计模型请求耗时却从 **{a['model_ms']/1000:.1f} 秒 → {b['model_ms']/1000:.1f} 秒**。这不是剔除网络重试后的有效耗时；v4 为六路且与旧批次重叠，原 v2 为两路，路由与并发也有差异，不能归因为缓存修复导致加速或变慢。", '',
    '## 机制上的正向证据', '',
    '- v4 配置迁移已记录的 12 次主请求中，第一条之后的 11 条均被前缀诊断标记为 append_only；原子预留为首条之后 10/10。动态状态没有在这些请求中打断已发送的消息前缀。',
    '- 这两个未结束阶段的主请求输入缓存占比分别为 64.5%、69.7%。只能作为阶段观察，不能与上一版整题缓存率当作控制实验比较。',
    '- 四道可对齐题中，未缓存输入均下降，但还没有证明最后实现和验收也能以更低成本完成。', '',
    '## 仍需注意的问题', '',
    '1. **先处理执行中断。**当前不是“还在后台跑”。应先核对遗留工具和工作区状态，再从中断请求或阶段恢复；不能直接重放已成功写入、全量重跑或增加原步数预算。',
    '2. **验收返工仍存在。**v4 原子预留已有 3 次协议错误：一次找不到 task_checks，另两次缺少 cli_behavior、reserve_behavior 比较项。支持 stderr/日志只能修输出通道问题，不能补齐缺失的业务检查。',
    '3. **v3 有明确负面结果。**不含缓存修复的 v3 只有配置迁移形成完整结果：657,650 tokens、86 次工具，超过 130,000 tokens / 65 次工具预算；两轮均触及 32 步暂停，14 次验收错误。v2 同题为 300,089 tokens，因此 v3 这题反而恶化约 119.2%。',
    '4. v3 配置迁移的 oracle 报 ImportError（找不到 package.parser.parse），自动归类 oracle_invalid；还需核对是判定器假设还是被测接口破坏，不能直接宣称为纯误报或通过。该题的预算超限和暂停证据独立成立。', '',
    '## 统计限制', '',
    'v4 当前六题合计已记录 494,101 tokens，不能拿它和 v2 六题完整的 1,730,372 比降幅。最后未完成请求可能已经发生服务端成本但尚未回传 usage，因此已记录用量不是最终账单。网络重试保留原始记录，不因此判能力失败。', '',
    '本次只读取原始记录，没有补分、修改题目或恢复执行。机器对比数据：`.aster/evals/efficiency-revision-v4/partial-comparison.json`；分析脚本：`scripts/compare_efficiency_v4_partial.py`。']
report=ROOT/'docs/interview/eval no 2/六路Luna-v4阶段对比.md'
report.write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('Written:',report)
