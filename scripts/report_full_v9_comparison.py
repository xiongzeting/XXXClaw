"""Compare frozen twenty-case runs with independent review overlays."""
import collections
from datetime import datetime
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REVIEW=ROOT/'.aster/evals/boundary-full-v9-review'
DOC=ROOT/'docs/interview/eval no 2/v9全量20题与v1复核对比.md'
DIMS=['outcome','process','efficiency','safety','reliability']
NAMES=['结果','过程','效率','安全','可用性']

def stamp(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def union(intervals):
    total=0; end=None
    for a,b in sorted(intervals):
        if end is None or a>end:total+=b-a;end=b
        elif b>end:total+=b-end;end=b
    return total

def main():
    review=json.loads((REVIEW/'regraded.json').read_text(encoding='utf-8'))
    calibration=json.loads((REVIEW/'calibration.json').read_text(encoding='utf-8'))
    assert all(any(x['variant']=='reference' and x['passed'] for x in r['runs']) and
               all(x['classification']=='capability_failure' for x in r['runs'] if x['variant'].startswith('mutant')) for r in calibration)
    overlays={(r['version'],r['id']):r['review'] for r in review['rows']}
    summary={}; rows=[]
    for version,folder in [('v1','boundary-campaign-v1'),('v9','boundary-full-v9-jobs10')]:
        path=ROOT/'.aster/evals'/folder/'run/report.json'; report=json.loads(path.read_text(encoding='utf-8'))
        stats={'metrics':report['summary']['metrics'],'raw':collections.Counter(),'reviewed':collections.Counter(),
               'report_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'reviewed_all':0,
               'error_examples':{},'verification_unresolved_feedback':0,'verification_current_feedback':0,
               'request_errors':0,'timing':{'case_total_seconds':0,'model_wait_seconds':0,'non_model_seconds':0,'missing_model_intervals':0}}
        for c in report['cases']:
            raw={d:c['dimensions'][d]['score']==1 for d in DIMS}; fixed=dict(raw)
            overlay=overlays.get((version,c['id']))
            if overlay:
                assert overlay['classification'] in ['passed','capability_failure'], overlay
                fixed['outcome']=overlay['passed']
            for d in DIMS:stats['raw'][d]+=raw[d];stats['reviewed'][d]+=fixed[d]
            stats['reviewed_all']+=all(fixed.values())
            events={}
            for t in {p['trace_path'] for p in c['phases'].values() if p.get('trace_path')}:
                for line in Path(t).read_text(encoding='utf-8').splitlines():
                    try:x=json.loads(line)
                    except json.JSONDecodeError:continue
                    events[x['event_id']]=x
            intervals=[]; missing=0; current=unresolved=0
            for x in events.values():
                data=x.get('data',{})
                if x['type']=='model.request':
                    if data.get('started_at') and data.get('completed_at'):
                        intervals.append((stamp(data['started_at']),stamp(data['completed_at'])))
                    else:missing+=1
                if x['type']=='tool.call':
                    feedback=(data.get('details') or {}).get('task_verification') or {}
                    current+=bool(feedback.get('error'))
                    unresolved+=bool(feedback.get('unresolved_error',feedback.get('error')))
                    if data.get('status')=='error':
                        key=data.get('tool_name','unknown')
                        examples=stats['error_examples'].setdefault(key,[])
                        if len(examples)<3:examples.append(str(data.get('output',data.get('error',data.get('details'))))[:1000])
            wait=union(intervals); wall=c['duration_seconds']
            stats['verification_current_feedback']+=current
            stats['verification_unresolved_feedback']+=unresolved
            stats['timing']['case_total_seconds']+=wall;stats['timing']['model_wait_seconds']+=wait
            stats['timing']['non_model_seconds']+=wall-wait;stats['timing']['missing_model_intervals']+=missing
            rows.append({'version':version,'id':c['id'],'category':c['category'],'raw':raw,'reviewed':fixed,
                'original_failure_reasons':c.get('failure_reasons'), 'review':overlay,
                'metrics':c['metrics'],'phase_count':len(c['phases']),
                'non_model_case_seconds':wall-wait,'verification_current_feedback':current})
        m=stats['metrics'];stats['same_rate_usd']=((m['input_tokens']-m['cached_tokens'])*.14+m['cached_tokens']*.0028+m['output_tokens']*.28)/1e6
        stats['jobs']=json.loads((path.parents[1]/'execution.json').read_text())['jobs']
        stats['batch_seconds']=report['summary']['elapsed_seconds'];summary[version]=stats
    supplement=json.loads((REVIEW/'queue-supplementary.json').read_text())
    for version in ['v1','v9']:
        summary[version]['expanded_reviewed']=dict(summary[version]['reviewed'])
        probe=next(r for r in supplement if r['version']==version)
        if not probe['passed']:summary[version]['expanded_reviewed']['outcome']-=1
    output={'supplementary_queue_probe':supplement,'summary':summary,'rows':rows,'oracle_sha256':review['oracle_sha256']}
    (REVIEW/'comparison.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    a,b=summary['v1'],summary['v9']; ma,mb=a['metrics'],b['metrics']
    lines=['# v9 全量 20 题与 v1：纠正误报后的对比','',
      '两轮均为同一组 20 题。v1 为 2 路、v9 为 10 路；v9 使用冻结代码，未复用之前六题。全部已完成评测，但“评测结束”不等于每题都成功交付。原报告、原始分数不改，复核标签和修订分数单独保存。','',
      '## 五维结果','', '| 维度 | v1 原始 | v1 复核 | v9 原始 | v9 复核 |','|---|---:|---:|---:|---:|']
    for d,n in zip(DIMS,NAMES):lines.append(f"| {n} | {a['raw'][d]}/20 | {a['reviewed'][d]}/20 | {b['raw'][d]}/20 | {b['reviewed'][d]}/20 |")
    lines+=['',f"五维全部通过：{a['reviewed_all']}/20 → {b['reviewed_all']}/20。按每维全部必要检查通过计数，不把检查项平均分当通过题数。",'',
      '补充边界复核：队列 add A → add B → remove A → add A。v1 正确输出 [A,B]，v9 错误输出 [A,B,A]。该错误由新增检查发现，单独保留；若将其纳入完整复核，结果通过为 **v1 17/20、v9 17/20**，而不是把原检查修正后的 18/20 当作已经确认全部正确。其他四维不变。','', '## 成本与步骤','', '| 指标 | v1 | v9 | 变化 |','|---|---:|---:|---:|']
    items=[('总 tokens','total_tokens'),('输入 tokens','input_tokens'),('输出 tokens','output_tokens'),('缓存输入 tokens','cached_tokens'),('主模型请求／决策步','agent_model_requests'),('全部模型请求（含摘要等）','model_requests'),('工具调用','tool_calls'),('工具错误','tool_errors'),('阶段暂停','paused_runs'),('成功压缩','compactions'),('摘要 tokens','compaction_tokens')]
    for label,key in items:
        x,y=ma[key],mb[key];lines.append(f'| {label} | {x:,} | {y:,} | {(y/x-1)*100:+.2f}% |' if x else f'| {label} | {x} | {y} | — |')
    ua=ma['input_tokens']-ma['cached_tokens'];ub=mb['input_tokens']-mb['cached_tokens']
    lines +=[f'| 未缓存输入 | {ua:,} | {ub:,} | {(ub/ua-1)*100:+.2f}% |',
      f"| 缓存占输入比例 | {100*ma['cached_tokens']/ma['input_tokens']:.2f}% | {100*mb['cached_tokens']/mb['input_tokens']:.2f}% | — |",
      f"| 同费率估算费用 USD | {a['same_rate_usd']:.6f} | {b['same_rate_usd']:.6f} | {(b['same_rate_usd']/a['same_rate_usd']-1)*100:+.2f}% |",'',
      '费用按两轮日志中的同一费率计算：每百万未缓存输入 $0.14、缓存输入 $0.0028、输出 $0.28。不是网站账单或实际剩余额度；重试未上报用量无法补齐。总 tokens 包含缓存输入。', '',
      '## 工具过程','', '| 工具 | v1 调用/错误 | v9 调用/错误 |','|---|---:|---:|']
    for tool in sorted(set(ma['tool_breakdown'])|set(mb['tool_breakdown'])):
        lines.append(f"| {tool} | {ma['tool_breakdown'].get(tool,0)}/{ma['tool_failure_breakdown'].get(tool,0)} | {mb['tool_breakdown'].get(tool,0)}/{mb['tool_failure_breakdown'].get(tool,0)} |")
    lines+=['',f"内部验收 error 字段反馈：v1 未提供此字段（不能记为零错误），v9 {b['verification_current_feedback']} 次。v1 缺少同口径字段，不能与 v9 直接计算增减；其他旧版 error 也可能携带历史未解决问题；v9 按旧 unresolved 口径为 {b['verification_unresolved_feedback']} 次。",'',
      'v9 新口径：参数/计划错误 39，验证命令失败 19，输出/绑定协议错误 33，共 91 次；另有实际比较失败反馈 9 次、历史待解决反馈 14 次。后两类可与同次反馈重叠，不简单累加。外部判定器误报更正不会抹除运行时已经发生的内部验收返工。','',
      '## 耗时','',
      '按每题模型请求的 started_at/completed_at 区间取并集，再从该题总执行时长扣除，最后累加 20 题。这里包含环境准备、工具、本地检索和外部评分，不是纯 Agent CPU 时间；中断请求如果缺完整区间，不能准确扣除。','',
      '| 累计时间（秒） | v1 | v9 |','|---|---:|---:|']
    for key,label in [('case_total_seconds','20 题执行时长之和'),('model_wait_seconds','已记录模型等待'),('non_model_seconds','扣除已记录模型等待的剩余时间'),('missing_model_intervals','缺完整区间的模型请求数')]:
        lines.append(f"| {label} | {a['timing'][key]:.3f} | {b['timing'][key]:.3f} |")
    lines+=['',f"批次墙钟：{a['batch_seconds']:.3f} 秒 → {b['batch_seconds']:.3f} 秒；并发 {a['jobs']} → {b['jobs']}，不把这一差值当作架构提速。",'',
      '## 逐题复核','', '| 题目 | v1 结果 原始→复核 | v9 结果 原始→复核 | v1 tokens | v9 tokens |','|---|---|---|---:|---:|']
    ra={r['id']:r for r in rows if r['version']=='v1'};rb={r['id']:r for r in rows if r['version']=='v9'}
    for key,x in ra.items():
        y=rb[key];mark=lambda v:'通过' if v else '未通过'
        lines.append(f"| {key} | {mark(x['raw']['outcome'])}→{mark(x['reviewed']['outcome'])} | {mark(y['raw']['outcome'])}→{mark(y['reviewed']['outcome'])} | {x['metrics']['total_tokens']:,} | {y['metrics']['total_tokens']:,} |")
    lines+=['','## 复核边界与证据','',
      '- v1 的配置迁移、扣款、队列、审计、发布共五题：旧检查绑定了未明示的参数顺序、输出形状或 CLI 用法，修订检查通过。库存原始通过但修订检查失败，保留漏检更正。',
      '- v9 扣款：允许不改变订单/金额/次数的状态完成更新，不再要求账本所有字节不变；冲突/非法请求仍必须保持文件。队列接受 tasks 外层；审计支持固定 bundle.json 输出和实际提供的参数形式。三题原有业务检查复核通过。',
      '- v9 发布题只有 .pytest_cache 新增导致安全失败。用户明确限定交付路径且临时文件放系统目录，因此保留范围不合规；它是测试缓存残留，不宣传成提示注入或凭据泄露。',
      '- v9 库存仍有非法请求接受和部分提交；订单回滚题到第 4 阶段后整题超时，最终任务未交付，JSON 解析错误不能描述为最终算法答错。不得因没有最终答案改判通过。',
      '- 两轮中已恢复的网络波动不记能力失败；保留未恢复/暂停的实际交付状态。此次是已曝光回归，不能作为未见测试成绩。',
      '- 额外发现 v9 队列删除后重新添加产生重复任务；v1 同输入正确。证据：queue-supplementary.json。修订原有检查与新增检查的评分分开。',
      '- 所有通过均限于现有检查，不代表穷尽正确。新增边界发现的问题应单列，不能混入旧分数后不说明。',
      '', '复核机器记录：`.aster/evals/boundary-full-v9-review/comparison.json`；逐条重评分：`regraded.json`；判定器正反校准：`calibration.json`；修订判定器：`review_oracle.py`。以上均在独立复核目录，未改冻结代码、输入或模型交付。']
    DOC.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({v:{k:s[k] for k in ['raw','reviewed','reviewed_all','same_rate_usd','timing','verification_current_feedback','verification_unresolved_feedback']} for v,s in summary.items()},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
