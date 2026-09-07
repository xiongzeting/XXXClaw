"""Report actual executed split coverage, infrastructure failures, and exposure."""
import json,hashlib
from collections import Counter,defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];E=ROOT/'evals';RUNS=ROOT/'.aster/evals/three-split-v2'
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def collect(split):
    root=RUNS/split
    if not (root/'report.json').exists():return None
    report=read(root/'report.json')
    extra=RUNS/'retained-replacement'/'report.json'
    excluded=report.get('excluded_invalid_ids',[])
    if split=='retained' and extra.exists():
        excluded=[c['id'] for c in report['cases'] if c['id']=='dialogue_recall_retracted_contact']
        report['cases']=[c for c in report['cases'] if c['id'] not in excluded]+read(extra)['cases']
    revised={}
    if (root/'reviewed-results.json').exists():
        revised={r['id']:r for r in read(root/'reviewed-results.json')['results']}
    rows=[]
    for c in report['cases']:
        attempts=[]
        for i,a in enumerate(c['attempts']):
            checks=revised.get(c['id'],{}).get('attempts',[None]*len(c['attempts']))[i]
            checks=checks['checks'] if checks else a['checks']
            dims={d:all(x['passed'] for x in checks if x.get('required',True) and x['dimension']==d)
                  for d in ['outcome','process','efficiency','safety','reliability']}
            infra=bool(a.get('error') or a['metrics'].get('model_errors',0) or any(v.get('errors') for v in a['phases'].values()))
            reasons=[{'dimension':x['dimension'],'type':x['type'],'detail':x['detail']} for x in checks if x.get('required',True) and not x['passed']]
            attempts.append({'passed':not a.get('error') and all(dims.values()),'dimensions':dims,
                'infrastructure_or_terminal_model_error':infra,'reasons':reasons,
                'tokens':a['metrics'].get('total_tokens',0),'tool_calls':a['metrics'].get('tool_calls',0),
                'model_errors':a['metrics'].get('model_errors',0),'compactions':a['metrics'].get('compactions',0)})
        rows.append({'id':c['id'],'category':c['category'],'raw_passed':c['passed'],
                     'passed':all(a['passed'] for a in attempts),'attempts':attempts})
    return {'cases':len(rows),'passed':sum(r['passed'] for r in rows),'attempts':sum(len(r['attempts']) for r in rows),
            'infrastructure_affected_cases':sum(any(a['infrastructure_or_terminal_model_error'] for a in r['attempts']) for r in rows),
            'excluded_invalid_cases':excluded,'summary':report['summary'],'results':rows}

def main():
    frozen=read(E/'three-split-freeze-v2.json')
    for item in frozen['splits'].values():
        assert hashlib.sha256((E/item['suite']).read_bytes()).hexdigest()==item['sha256']
    outputs={k:collect(k) for k in frozen['splits']}
    assert all(outputs.values()),'Some full runs not finished'
    write(RUNS/'coverage-results.json',outputs)
    labels={'compression':'压缩记忆','recall':'记忆召回','tools':'工具调用','safety':'安全执行','completion':'总体完成'}
    table=[];dimension=[];failures=[]
    release=read(E/'reviewed-split-release-manifest.json')
    invalid_table='\n'.join(f'| `{cid}` | {reason} |' for cid,reason in release['excluded_invalid_cases'].items())
    for split,r in outputs.items():
        for cat,label in labels.items():
            cases=[c for c in r['results'] if c['category']==cat]
            table.append(f'| {split} | {label} | {len(cases)} | {sum(c["passed"] for c in cases)} |')
        for d in ['outcome','process','efficiency','safety','reliability']:
            count=sum(all(a['dimensions'][d] for a in c['attempts']) for c in r['results'])
            dimension.append(f'| {split} | {d} | {count}/{r["cases"]} |')
        for c in r['results']:
            if not c['passed']:
                reasons=sorted({a['dimension'] for t in c['attempts'] for a in t['reasons']})
                infra=any(a['infrastructure_or_terminal_model_error'] for a in c['attempts'])
                failures.append(f'| {split} | `{c["id"]}` | {", ".join(reasons)} | {"有接口/运行错误，勿直接归因能力" if infra else "需人工审查具体行为"} |')
    score_table='\n'.join(f'| {k} | {v["cases"]} | {v["passed"]} | {v["cases"]-v["passed"]} |' for k,v in outputs.items())
    total_cases=sum(v['cases'] for v in outputs.values())
    total_passed=sum(v['passed'] for v in outputs.values())
    verification=read(RUNS/'release-verification.json') if (RUNS/'release-verification.json').exists() else None
    source_note='源码版本一致性尚未核验。'
    if verification:
        if verification['source_drift']:
            names='、'.join('`'+x['path'].replace('\\','/')+'`' for x in verification['source_drift'])
            source_note=f'结束校验发现 {len(verification["source_drift"])} 个业务文件与早先 capability-campaign-manifest.json 基线不一致：{names}。其文件修改时间落在本轮执行窗口；没有逐个 worker 的启动源码快照，无法证实全部尝试来自同一版本，也无法仅凭哈希确认修改来源。因此上述成绩仅是这些已记录尝试的汇总，**不能作为同一固定代码版本的基准成绩，不能据此计算修复提升**。未覆盖或回滚这些改动。差异与结束时哈希保存于 release-verification.json、source-postrun-hashes.json。下次比较模型或配置时，先固定独立源码快照并记录逐次运行的版本。'
        else:
            source_note='结束时已记录业务源码哈希与早先基线一致。'
    text='''# MiniClaw 三分层与五维评测报告

本轮将已曝光数据留在开发集，独立编写保留集（validation）和测试集。新题在模型执行前冻结输入、fixture、oracle 与预算。模型为 gpt-5.6-luna，多路并行；这批是合成本地评测，不是公开真实仓库 Benchmark。

'''+f'''全维通过 **{total_passed}/{total_cases}**，按案例统计，重复确认不增加独立案例数。

| 分层 | 有效案例 | 全维通过 | 未通过 |
|---|---:|---:|---:|
{score_table}

{source_note}

'''+'''## 实际运行覆盖

以下为全量执行并补跑后的完整覆盖结果，失败保留。保留集和测试集初始共享 6 路时出现本机资源压力、Docker 检查失败和模型整理超时；中止后保留已完成结果，未完成项及失败项在共享 2 路下重新运行。表格采用最终完整尝试，**不是首轮一次通过率**；首次结果、恢复计划和逐例来源在 resumption-audit.json 中，不能用补跑抹掉初始可用性失败。覆盖数不等于成功数，也不证明统计意义上的充分。

| 分层 | 能力 | 已执行案例 | 全维通过 |
|---|---|---:|---:|
'''+ '\n'.join(table)+'''

## 五个维度分别的通过情况

outcome=结果，process=过程，efficiency=效率预算，safety=副作用约束，reliability=严格的运行健康检查。后者要求各回合成功运行、最终响应非空、模型调用没有报错（含摘要/记忆整理等辅助请求）。辅助调用报错后恢复、最终交付正确的案例仍会在这一维失败，不等价于任务完全不可用，也不等价于人机交互可用性研究。

| 分层 | 维度 | 通过/执行 |
|---|---|---:|
'''+ '\n'.join(dimension)+'''

效率使用运行前预设的 token、工具调用等上界；不是 min>=0 的必过占位符，不据测试结果调宽阈值。通过预算仅表示没有越界，不能声称效率提升。长任务与短任务使用不同上界，具体见各 case。所有维度都是必要检查，不能用好看的平均分抵消安全或结果失败。

## 未通过项

| 分层 | Case | 未通过维度 | 归因状态 |
|---|---|---|---|
'''+ ('\n'.join(failures) or '| — | 无 | — | — |')+'''

`.aster/evals/three-split-v2/<split>/report.json` 汇总开发集原始运行或保留/测试集恢复后的完整覆盖，后两者含 case_result_origins。原始首次尝试在对应 cases 目录，恢复和补充尝试在 recovery-2/cases、supplement/cases 目录。开发集如有 oracle 纠错，另存 reviewed-results.json；不覆盖原始结果。开发集本轮原始成绩为 41/46，审校为 42/46：压缩多约束题接受 UTF-8 与 utf-8 的大小写等价，其余字段仍精确验收。测试集失败一经查看即已曝光，后续用它调试后的成绩属于回归成绩，不能继续声称未见测试。

## 失败入库与修改优先级

5 条行为失败已进入 `evals/split-failures-v2.json`，2 条运行健康失败进入 `evals/split-availability-diagnostics-v2.json`。每例保留对话、fixture、验收与来源，证据位于 `evals/evidence/three-split-v2/`。以下是修改指导，本轮没有实施业务修复。

| 优先级 | 观察到的问题 | 修改方向与验收要求 |
|---|---|---|
| P0 | 审批拒绝或超时场景发生短暂修改后恢复 | 审批结果必须在写入前生效；拒绝与超时都不允许任何写入副作用。验收检查成功写工具事件和全过程，不能只比最终文件。 |
| P1 | 明确要求应用内审批的任务没有触发审批并完成工作 | 梳理需要审批时的状态流转，让用户能通过真正的审批入口继续；验收要求审批请求、决定及最终产物，文字询问本身不算任务完成。 |
| P1 | 嵌套规则和权限撤销对话未遵守 JSON 输出契约 | 在完成验收中检查用户指定格式，格式错误时继续修正；权限撤销题在发现与两次确认中共 3/3 未满足格式。此项是输出契约问题，不应称为安全绕过。 |
| P2 | 两条压缩题辅助模型调用报错后恢复并正确交付 | 分别记录主任务结果与辅助请求健康；补充超时、限流、重试耗尽和降级路径的诊断。保留严格健康检查，不将偶发接口故障直接认定为确定性 Agent 缺陷。 |

失败套件默认每例重复 3 次，便于修复后观察稳定性；这个配置不表示本轮每个案例都已经执行 3 次。原始尝试次数以 evidence 和 report 为准。被提升的保留集或测试集失败用于后续开发回归时，不能再计作新的独立测试样本。

## 分层、冻结与污染控制

- 开发集：`evals/measured-development-v2.json`，来自已执行过的 46 个能力案例，可持续修改和吸收失败。
- 保留集：`evals/retained-release-v2.json`，25 个有效 validation 案例，可用于选配置；选过配置以后不算未见测试。3 条歧义原题另造明确契约的新题替换，不修改原冻结套件。
- 测试集：`evals/test-release-v2.json`，25 条有效题。原 fresh-test-v1 的 25 题在首轮前冻结；其中 6 条因类型或路径约定不明确而排除，另补 6 条各自首次运行前冻结的替换题。这是经过题目质量审查的修订版，不能宣称整套是未经审查、完全未见的测试集。
- 冻结清单：`evals/three-split-freeze-v2.json`，包含 suite 与 fixtures 的 SHA-256、每例必要维度矩阵。
- 最初的 development-v1 / retained-v1 / test-v1 随机拆旧题方案已标记无效；旧题重命名无法创建干净测试集。原始运行证据保留，不能引用其分层覆盖或必过指标。

新集共生成 59 条场景（原始 50 条＋另造 9 条替换题），最终保留 50 条有效题。加开发集共 96 条有效案例。每个新集五项能力各 5 个场景，开发集各至少 8 个。相同 prompt 和跨新集 family 标签检查只能降低显式泄漏；召回、字段更新和压缩题仍存在共享结构，不代表族级完全隔离，也不能声称完全独立。对简历项目，这是阶段性覆盖基线；严谨的泛化结论还需要更多独立仓库、公开固定任务与置信区间。

## 排除的 9 条原题

没有将这些结果计成 Agent 缺陷，也没有把原报告改成通过。旧报告和新题映射见 reviewed-split-release-manifest.json；初始 suite、精确替换题的冻结哈希分别保留。

| 原 Case | 排除依据 |
|---|---|
'''+invalid_table+'''

```powershell
# 完整集各自复跑；输出使用新目录。模型在运行器中固定为 gpt-5.6-luna。
# 以下顺序执行，共享资源不要同时启动三组各 2 路。
$env:MINICLAW_LLM_MAX_RETRIES='4'
$env:MINICLAW_LLM_RETRY_BASE_SECONDS='3'
$env:MINICLAW_LLM_RETRY_MAX_SECONDS='30'
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
python scripts/run_dialogue_adapter_eval.py evals/measured-development-v2.json --env-file .env --jobs 2 --repeat 1 --out .aster/evals/next-development
python scripts/run_dialogue_adapter_eval.py evals/retained-release-v2.json --env-file .env --jobs 2 --repeat 1 --out .aster/evals/next-retained
python scripts/run_dialogue_adapter_eval.py evals/test-release-v2.json --env-file .env --jobs 2 --repeat 1 --out .aster/evals/next-test
```

本机本轮改为共享 2 路后继续执行，同时限制本地数值库线程数，保留真实模型调用。接口出现 429 时退避，不把限流当成模型能力下降。所有生成代码的隐藏验收在隔离 Docker 内运行。本轮 Eval 工作仅编写案例、运行与报告脚本，没有实施上述业务修复；工作区另有源码基线差异，见开头的版本一致性限制。
'''
    (E/'三分层五维Eval报告.md').write_text(text,encoding='utf-8')
    print({k:{'cases':v['cases'],'passed':v['passed'],'infra':v['infrastructure_affected_cases']} for k,v in outputs.items()})

if __name__=='__main__':main()
