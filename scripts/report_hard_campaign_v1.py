"""Review the completed batch once, export observed failures, activate hard suites."""
from __future__ import annotations
from collections import Counter
import copy
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'evals'
R = ROOT / '.aster/evals/hard-campaign-v1'
DIMS = ['outcome', 'process', 'efficiency', 'safety', 'reliability']
LABELS = {'recall': '记忆召回', 'compression': '压缩记忆', 'tools': '工具调用',
          'safety': '安全执行', 'completion': '总体完成'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    assert (R / 'completion.json').is_file(), 'Wait for entire campaign before reviewing results'
    raw = read(R / 'run/report.json')
    frozen = read(R / 'snapshot/evals/hard-campaign-v1.json')
    specs = {c['id']: c for c in frozen['cases']}
    assert len(raw['cases']) == len(specs) == 30
    assert {c['id'] for c in raw['cases']} == set(specs)
    rows = []
    failures = {s: [] for s in ('development', 'retained', 'test')}
    for case in raw['cases']:
        spec = specs[case['id']]
        attempts = case['attempts']
        failed_checks = [x for a in attempts for x in a['checks']
                         if x.get('required', True) and not x['passed']]
        dimensions = {d: all(x['passed'] for a in attempts for x in a['checks']
                            if x.get('required', True) and x['dimension'] == d) for d in DIMS}
        infrastructure = any(a.get('error') or a['metrics'].get('model_errors', 0)
                             or any(p.get('errors') for p in a['phases'].values()) for a in attempts)
        row = {'id': case['id'], 'split': spec['source']['split'], 'category': case['category'],
               'passed': case['passed'], 'dimensions': dimensions,
               'infrastructure_affected': infrastructure, 'failed_checks': failed_checks,
               'attempts': len(attempts), 'tokens': sum(a['metrics'].get('total_tokens', 0) for a in attempts)}
        rows.append(row)
        if case['passed']:
            continue
        exported = copy.deepcopy(spec)
        exported['source'].update(original_split=spec['source']['split'],
            track='observed-failure', exposure='Observed after frozen first run; any future tuning makes this development evidence.',
            infrastructure_affected=infrastructure,
            evidence=f'evidence/hard-campaign-v1/{case["id"]}.json')
        failures[row['split']].append(exported)
        observations = []
        for attempt in attempts:
            trace_links = {k: p['trace_path'] for k, p in attempt['phases'].items()}
            observations.append({'answers': {k: p['final_text'] for k, p in attempt['phases'].items()},
                                 'checks': attempt['checks'], 'metrics': attempt['metrics'],
                                 'error': attempt.get('error'), 'trace_paths': trace_links,
                                 'workspace_changes': attempt['workspace_changes']})
        dump(E / f'evidence/hard-campaign-v1/{case["id"]}.json',
             {'id': case['id'], 'source': exported['source'], 'phases': spec['phases'],
              'observations': observations,
              'original_result': str((R / f'run/cases/{case["id"]}/result.json').relative_to(ROOT)),
              'classification': 'Needs trace review; run-health failures alone do not establish an Agent defect.'})
    for split, cases in failures.items():
        if cases:
            dump(E / f'hard-failures-{split}-v1.json', {'version': 1,
                 'name': f'hard-failures-{split}-v1', 'cases': cases,
                 'policy': 'Original assignment retained; failure-selected subset, not unbiased split accuracy.'})
    flat = [c for group in failures.values() for c in group]
    if flat:
        dump(E / 'hard-observed-failures-v1.json', {'version': 1, 'name': 'hard-observed-failures-v1',
             'cases': flat, 'policy': 'Observed failures for development review; includes infrastructure diagnostics, not all confirmed defects.'})
    summary = {split: {'cases': sum(r['split'] == split for r in rows),
                       'passed': sum(r['split'] == split and r['passed'] for r in rows)} for split in failures}
    planned_phases=sum(len(c['phases']) for c in specs.values())
    returned_phases=sum(len(a['phases']) for c in raw['cases'] for a in c['attempts'])
    dump(R / 'review-results.json', {'splits': summary, 'results': rows,
         'policy': 'Raw first-pass outcomes; no post-result retries, relabeling, or threshold adjustment.'})
    dump(E / 'main-challenge-v3.json', {'version': 1, 'name': 'main-challenge-v3',
         'includes': [f'{s}-challenge-v3.json' for s in failures]})
    portfolio = {'version': 3, 'main': {s: f'{s}-challenge-v3.json' for s in failures},
                 'main_combined': 'main-challenge-v3.json', 'main_case_count': 30,
                 'smoke': 'smoke-basic-v3.json',
                 'legacy_baselines': ['measured-development-v2.json', 'retained-release-v2.json', 'test-release-v2.json'],
                 'previous_regressions': ['split-failures-v2.json', 'split-availability-diagnostics-v2.json'],
                 'new_failures_by_original_split': {s: f'hard-failures-{s}-v1.json' for s, c in failures.items() if c},
                 'policy': 'Simple cases removed from active main coverage, not deleted; failures remain in their frozen assigned split.'}
    dump(E / 'active-eval-portfolio-v3.json', portfolio)
    split_table = '\n'.join(f'| {s} | {v["cases"]} | {v["passed"]} | {v["cases"]-v["passed"]} |' for s, v in summary.items())
    matrix = '\n'.join(f'| {s} | {LABELS[cat]} | {sum(r["split"]==s and r["category"]==cat for r in rows)} | {sum(r["split"]==s and r["category"]==cat and r["passed"] for r in rows)} |'
                       for s in failures for cat in LABELS)
    dimension_table = '\n'.join(f'| {s} | {d} | {sum(r["split"]==s and r["dimensions"][d] for r in rows)}/10 |'
                                for s in failures for d in DIMS)
    failed_table = '\n'.join(f'| {r["split"]} | `{r["id"]}` | {", ".join(d for d in DIMS if not r["dimensions"][d])} | {"含模型/运行故障，需分离归因" if r["infrastructure_affected"] else "待对照轨迹定位"} |'
                             for r in rows if not r['passed']) or '| — | 无 | — | — |'
    desc = '\n'.join(f'| {c["source"]["split"]} | `{c["id"]}` | {LABELS[c["category"]]} | {len(c["phases"])} | {c["source"].get("family", "")} |' for c in specs.values())
    text = f'''# MiniClaw 高难度 Eval 第一轮

本轮新造 30 条合成复杂场景，预先分为开发、保留、测试各 10 条，每层五项能力各 2 条。使用 gpt-5.6-luna 共享 2 路并行，在整批结束后统一查看结果。未根据中间输出重试、改题、调宽阈值或重新分层。

## 首次完整批次结果

| 分层 | 案例 | 通过 | 未通过 |
|---|---:|---:|---:|
{split_table}

全维通过 {sum(r['passed'] for r in rows)}/30。计划 {planned_phases} 个对话回合，运行器返回 {returned_phases} 个回合记录（含可能报错的回合，不等于成功回合数）。每例只运行一次；接口内部的有限重试不等于额外独立样本。单次失败不自动视为稳定复现缺陷。

| 分层 | 能力 | 案例 | 全维通过 |
|---|---|---:|---:|
{matrix}

| 分层 | 维度 | 通过/执行 |
|---|---|---:|
{dimension_table}

效率表示预设预算是否越界，不代表效率提升；可用性采用严格运行健康标准。模型调用失败后正确交付的案例也可能不通过这一维。

## 未通过项与入库

| 原分层 | Case | 未通过维度 | 初步归因 |
|---|---|---|---|
{failed_table}

失败都已留在各自 `*-challenge-v3.json` 主集中，另按原分层导出 `hard-failures-<split>-v1.json`，用于定位和后续回归。综合导出为 `hard-observed-failures-v1.json`（有失败时生成）。仅统计失败子集不能作为三套数据的通过率。

证据在 `evals/evidence/hard-campaign-v1/`，包含对话、实际回答、检查结果与完整 Trace 路径；原始结果在 `.aster/evals/hard-campaign-v1/run/`。保留/测试失败被用于修改后属于已曝光开发证据，不能继续宣称未见测试。

## 简单题退出主评测

当前入口为 `evals/active-eval-portfolio-v3.json`；主集为 `evals/main-challenge-v3.json`。旧的 96 条仅保留为历史基线，31 条已识别的简单题另列在 `evals/smoke-basic-v3.json`。旧失败回归集继续保留，不因题目简单而丢弃已知问题。具体迁移列表见 `evals/difficulty-migration-v3.json`。

## 版本、执行与局限

代码和题目在首轮前复制到独立 snapshot，执行进程从快照加载 MiniClaw。开始与结束均验证源码和题目哈希；工作区后续编辑不影响本轮来源。冻结清单为 `evals/hard-campaign-freeze-v1.json`。Docker 镜像、并发与线程限制已记录；没有导出 .env 凭据。

18 条编程题的函数与真实 CLI 隐藏验收在执行模型前，均用可信参考实现于 Docker 中运行通过，空实现也均被函数检查拒绝；参考代码没有进入模型 fixture。负输入、中文路径、重复输出、错误时保持已有输出以及无旧输出时不创建文件已由验收脚本自检。回归式输入验证仅针对明确列出的错误条件，其他输入满足契约；不宣称穷举所有异常。

安全题正向结果按完整对象与严格类型验收；全过程审计覆盖成功 edit/write 的归一化目标路径，最终文件差异与 canary 检查补充约束。这不等于对任意 shell 程序所有瞬时副作用的完整证明。跨会话召回检查实际注入记忆，压缩题要求至少两次 compaction 与历史归档；机制没触发和最终交付失败应分开定位。

这是30条人工合成复杂任务的首轮，不是公开真实仓库成绩。各能力每层2条用于提高挑战深度，不宣称统计覆盖充分；共享机制仍可能造成语义相关。输入契约和独立验收需要结合完整结果继续审查，发现歧义应另列无效，不能改原始结果成通过。

## 案例索引

| 分层 | Case | 能力 | 对话回合 | 任务族 |
|---|---|---|---:|---|
{desc}
'''
    (E / '高难度Eval首轮报告.md').write_text(text, encoding='utf-8')
    print(json.dumps({'splits': summary, 'failure_exports': len(flat)}, ensure_ascii=False))
    # Always restore post-run oracle diagnostics when regenerating this report.
    runpy.run_path(str(ROOT / 'scripts/audit_hard_results_v1.py'), run_name='__main__')


if __name__ == '__main__':
    main()
