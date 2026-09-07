"""Compare complete six-case results; retain interruptions and partial v3 separately."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'.aster/evals'
OUT=BASE/'efficiency-revision-v4'
NAMES={'hard_v1_tiered_invoice':'阶梯发票','hard_v1_redaction_priority':'脱敏优先级',
       'hard_v1_shipping_caps':'运费规则','boundary_v1_compression_dependency_lock_retraction':'依赖锁与撤回',
       'boundary_v1_atomic_batch_reservation':'原子批量预留','boundary_v1_config_migration_cli_contract':'配置迁移'}


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def trace(case,version):
    path=BASE/f'efficiency-revision-{version}'/'run/cases'/case/'attempt-001/workspace/.aster/eval-sessions/shared/trace.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def extra(events):
    changes=Counter();formats=Counter();seconds=0;retries=0;prefix=[];recovery=[];phase_tokens=Counter()
    starts={e['run_id']:e['data'] for e in events if e['type']=='run.started'}
    for e in events:
        d=e['data']
        if e['type']=='model.request':
            seconds+=d.get('duration_ms',0)/1000;retries+=d.get('retries',0)
            if d.get('purpose')=='agent':
                p=d.get('context',{}).get('metadata',{}).get('request_prefix',{})
                if p:changes[p['first_changed_component']]+=1
            phase_tokens[e['run_id']]+=d.get('usage',{}).get('total_tokens',0)
        if e['type']=='tool.call':
            error=d.get('details',{}).get('task_verification',{}).get('error')
            if error:formats[error]+=1
        if e['type']=='eval.process_recovery':recovery.append(d)
    return {'model_seconds_sum':round(seconds,3),'prefix_changes':dict(changes),
            'verification_errors':dict(formats),'verification_error_count':sum(formats.values()),
            'model_retries':retries,'process_recovery':recovery,'run_tokens':dict(phase_tokens)}


def status(case,dimension):
    checks=[c for c in case['checks'] if c['dimension']==dimension and c.get('required',True)]
    return bool(checks) and all(c['passed'] for c in checks)


def metrics(case):
    m=case['metrics']
    return {**{k:m.get(k,0) for k in ['total_tokens','input_tokens','output_tokens','cached_tokens',
        'agent_model_requests','tool_calls','tool_errors','paused_runs','model_errors','compaction_tokens','compactions']},
        'uncached_input':m['input_tokens']-m['cached_tokens'],
        'cache_rate_pct':round(100*m['cached_tokens']/m['input_tokens'],2) if m['input_tokens'] else 0,
        'active_wall_seconds':case['duration_seconds'],
        'all_dimensions':case['passed'],
        'dimension_pass':{d:status(case,d) for d in ['outcome','process','efficiency','safety','reliability']}}


def pct(new,old):return f'{(new/old-1)*100:+.1f}%' if old else '—'


def main():
    assert (OUT/'completion.json').exists(),'Batch is not complete; do not turn partial usage into a result'
    old=read(BASE/'efficiency-revision-v2/run/report.json');new=read(OUT/'run/report.json')
    rows=[]
    for c in new['cases']:
        before=next(o for o in old['cases'] if o['id']==c['id'])
        v3=BASE/'efficiency-revision-v3/run/cases'/c['id']/'result.json'
        rows.append({'id':c['id'],'name':NAMES[c['id']],'v2':metrics(before),'v4':metrics(c),
            'v2_trace':extra(trace(c['id'],'v2')),'v4_trace':extra(trace(c['id'],'v4')),
            'v3_complete':metrics(read(v3)) if v3.exists() else None,
            'failed_checks':[x for x in c['checks'] if x.get('required',True) and not x['passed']]})
    totals={}
    for version in ['v2','v4']:
        keys=['total_tokens','input_tokens','output_tokens','cached_tokens','uncached_input','tool_calls',
              'tool_errors','agent_model_requests','paused_runs','compaction_tokens','compactions','active_wall_seconds']
        v={k:sum(row[version][k] for row in rows) for k in keys}
        v['dimension_pass']={d:sum(row[version]['dimension_pass'][d] for row in rows) for d in rows[0][version]['dimension_pass']}
        v['all_dimensions']=sum(row[version]['all_dimensions'] for row in rows)
        v['cache_rate_pct']=round(v['cached_tokens']/v['input_tokens']*100,2)
        v['model_seconds_sum']=sum(row[version+'_trace']['model_seconds_sum'] for row in rows)
        v['verification_errors']=sum(row[version+'_trace']['verification_error_count'] for row in rows)
        totals[version]=v
    comparison={'observed_at':datetime.now(timezone.utc).isoformat(),'cases':rows,'totals':totals,
        'limitations':['Exposed regression, once per version. Six-lane v4 vs two-lane v2.',
            'External process exit interrupted v4. Resume reused workspaces, completed tools and remaining phase turns.',
            'Known usage before and after interruption is included. Unfinished provider requests may have unreported cost.',
            'Active wall excludes the offline gap and is based on observable spans before interruption; not pure network-adjusted latency.',
            'Multiple mechanisms changed; no isolated causal attribution to prefix caching.']}
    (OUT/'final-comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    a,b=totals['v2'],totals['v4']
    lines=['# 六路 Luna v4 完整复测与前版对比','',
        '最新请求前缀修复版，6 道已曝光原题。中断后从原工作区续跑，已完成整批评分。未改题、判定器、token/工具预算或 32 步上限。','',
        '## 结论与下一步','',
        '**六题均已结束评分，不等于六题均完成任务。缓存占比提高，但整批降成本目标没有达成。** 依赖锁题是本轮唯一五维全通过的题；其余五题各有两个阶段耗尽 32 步。配置迁移、脱敏、运费的最终产物通过外部结果检查，但 Agent 自己仍未完成验收收尾。', '',
        '1. **优先解决验收返工。** 验收报错从 8 次增至 103 次，其中 76 次是缺少 comparison ID（要求逐项核对的检查编号），另有 16 次验证命令执行失败。原子预留重复出现同一缺失编号错误 14 次。应由运行时提供稳定的检查清单与结构化输出接口，明确区分协议不完整和业务断言失败；文件未变且已取得有效证据时复用验收结果，变更或撤回后再失效。不能把未检查项自动判通过，也不能靠增加提示词或步数上限消化循环。',
        '2. **修实际业务边界。** 发票题折扣输入应得 54 分，实际返回 0；原子预留仍接受非法输入，故障注入仍发现部分提交。优先补可重放的计算边界、输入验证和事务恢复能力，不能把这些归为纯验收格式问题。',
        '3. **再做缓存机制的单项对比。** 本轮缓存占比从 31.25% 升至 49.60%，但模型请求从 160 次增至 358 次，重试验收不断增加新的请求，未缓存输入仍上涨。先消除上述循环，再固定并发、输入和验收机制验证请求前缀的净收益。本轮不能把全部回退归因于缓存改动，也不能用命中率上升宣称整体优化成功。', '',
        '上述优先级是后续修改建议，本次仅续跑与分析，未继续修改 Agent 实现。', '',
        '## 核心对比','', '| 指标 | v2（上一完整六题） | v4（最新修复） | 变化 |','|---|---:|---:|---:|']
    for label,key in [('总 tokens','total_tokens'),('未缓存输入','uncached_input'),('缓存输入','cached_tokens'),
                      ('输出 tokens','output_tokens'),('工具调用','tool_calls'),('验收报错（含协议/命令失败）','verification_errors')]:
        lines.append(f'| {label} | {a[key]:,} | {b[key]:,} | {pct(b[key],a[key])} |')
    lines+=[f"| 输入缓存占比 | {a['cache_rate_pct']}% | {b['cache_rate_pct']}% | 比例仅作辅助 |",
        f"| 五维全通过 | {a['all_dimensions']}/6 | {b['all_dimensions']}/6 | 严格逐题 |",'']
    for d,label in [('outcome','结果'),('process','过程'),('efficiency','效率'),('safety','安全'),('reliability','可用性')]:
        lines.append(f"- {label}全项通过：**{a['dimension_pass'][d]}/6 → {b['dimension_pass'][d]}/6**。")
    lines+=['','不是 summary.score 的子检查平均分；上表按各题该维度必需检查是否全部通过统计。', '',
        '可用性下降来自 10 次步数耗尽后的未恢复阶段，未把已恢复网络波动判为能力失败。过程 6/6 只表示通过本轮冻结的检查；这版没有新增工具错误上限或耗时上限，不能理解为过程无错或时间达标。工具错误实际为 13 → 48 次，额外质量门槛不能追溯改写本轮评分。', '',
        '## 逐题','', '| 题目 | v2 tokens | v4 tokens | 变化 | 未缓存输入变化 | v4 结果 | v4 效率 |',
        '|---|---:|---:|---:|---:|---|---|']
    for row in rows:
        p,q=row['v2'],row['v4']
        lines.append(f"| {row['name']} | {p['total_tokens']:,} | {q['total_tokens']:,} | {pct(q['total_tokens'],p['total_tokens'])} | {pct(q['uncached_input'],p['uncached_input'])} | {'通过' if q['dimension_pass']['outcome'] else '未通过'} | {'通过' if q['dimension_pass']['efficiency'] else '未通过'} |")
    lines+=['','## 失败证据','']
    for row in rows:
        if not row['failed_checks']:continue
        lines.append('### '+row['name']);lines.append('')
        for check in row['failed_checks']:
            lines.append('- '+check['dimension']+' / '+check['type']+'：'+check['detail'][:1300])
        if row['v4_trace']['verification_errors']:
            lines.append('- 运行中的验收错误：'+json.dumps(row['v4_trace']['verification_errors'],ensure_ascii=False))
        lines.append('')
    lines+=['## 耗时与中断','',
        f"各题活动时间相加：{a['active_wall_seconds']:.1f} → {b['active_wall_seconds']:.1f} 秒；所有模型请求耗时相加：{a['model_seconds_sum']:.1f} → {b['model_seconds_sum']:.1f} 秒。这些不是六路批次的墙钟，也没有完全扣除网络重试等待。",'',
        'v4 原进程在约 17:04 退出，原因未知；17:16 使用独立后台进程恢复。已保存原会话副本，原 Trace 继续追加；五题从未完成请求的下一次可执行位置恢复，一题从尚未启动的下一阶段继续，没有重做前六轮。中断可能使服务端缓存过期，尚未回传 usage 的请求也可能产生未计入的费用。', '',
        '并发从两路变六路，最初还与 v3 重叠，不能把耗时变化全归因于缓存机制。总 tokens、未缓存输入均为已有服务回传用量。只有业务正确性保持的题，才能同时作为降成本与正确交付的证据。','',
        '## v3 的位置','',
        'v3 不含本次请求前缀修复，且不是完整六题报告。只列已形成完整 result.json 的题，不把它的半程总量和整批相除：','']
    for row in rows:
        if row['v3_complete']:
            p=row['v3_complete'];lines.append(f"- {row['name']}：{p['total_tokens']:,} tokens，{p['tool_calls']} 次工具，{p['paused_runs']} 次暂停。")
    lines+=['','完整评分：`.aster/evals/efficiency-revision-v4/run/report.json`；对比数据：同目录上一级 `final-comparison.json`；恢复记录与原会话副本：`recovery/`。']
    path=ROOT/'docs/interview/eval no 2/六路Luna-v4完整复测对比.md'
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(totals,ensure_ascii=False,indent=2))
    for row in rows:print(json.dumps({'name':row['name'],'v2':row['v2'],'v4':row['v4']},ensure_ascii=False))


if __name__=='__main__':main()
