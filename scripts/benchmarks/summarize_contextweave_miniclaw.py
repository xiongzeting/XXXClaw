from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BENCH_ROOT = ROOT / "external" / "benchmarks" / "ContextWeave-main"
DEFAULT_SELECTION = BENCH_ROOT / "generated" / "miniclaw-coding-memory-6.json"
DEFAULT_RESULTS = ROOT / ".aster" / "benchmarks" / "results" / "contextweave"
SUBTASK_RE = re.compile(r"subtask_\d+")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def seconds_between(start: str, finish: str) -> float | None:
    if not start or not finish:
        return None
    return (datetime.fromisoformat(finish) - datetime.fromisoformat(start)).total_seconds()


def relevant_subtasks(person: str, subtask_id: str) -> set[str]:
    payload = read_json(BENCH_ROOT / "data" / "metrics" / "relevance" / f"{person}.json", {})
    for row in payload.get("subtasks", []):
        if row.get("subtask_id") == subtask_id:
            return {
                str(item.get("subtask_id"))
                for item in row.get("relevant_previous_subtasks", [])
                if item.get("subtask_id")
            }
    return set()


def provenance_ids(diagnostics: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for item in [*(diagnostics.get("items") or []), *(diagnostics.get("procedural_items") or [])]:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        values = [item.get("record_id"), metadata.get("source_path"), metadata.get("subject")]
        for value in values:
            found.update(SUBTASK_RE.findall(str(value or "")))
    return found


def summarize_run(
    case: dict[str, Any],
    arm: str,
    *,
    run_root: Path,
    launcher_rows: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    person = str(case["person"])
    subtask_id = str(case["subtask_id"])
    subtask_dir = run_root / person / arm / subtask_id
    trace = read_trace(subtask_dir / "miniclaw_trace.jsonl")
    diagnostics = read_json(subtask_dir / "miniclaw_memory_diagnostics.json", {})
    run_meta = read_json(subtask_dir / "run_meta.json", {})
    requests = [row.get("data") or {} for row in trace if row.get("type") == "model.request"]
    tools = [row.get("data") or {} for row in trace if row.get("type") == "tool.call"]
    event_counts = Counter(str(row.get("type")) for row in trace)
    usages = [row.get("usage") or {} for row in requests]
    source_counts = Counter(str(item.get("source")) for item in diagnostics.get("items", []))
    source_counts["procedural"] += len(diagnostics.get("procedural_items") or [])
    known_provenance = provenance_ids(diagnostics)
    gold = relevant_subtasks(person, subtask_id)
    target_index = int(run_meta.get("target_index") or 0)
    if arm == "concat":
        known_provenance = {f"subtask_{index:04d}" for index in range(1, target_index)}
    gold_hits = gold & known_provenance
    provenance_recall = len(gold_hits) / len(gold) if gold else None
    provenance_precision = len(gold_hits) / len(known_provenance) if known_provenance else None

    official_result = read_json(
        run_root
        / "_official_workspace_metrics"
        / person
        / arm
        / "workspace"
        / subtask_id
        / "output"
        / "result.json",
        {},
    )
    launcher = launcher_rows.get((person, subtask_id, arm), {})
    agent_result = run_meta.get("agent_result") if isinstance(run_meta.get("agent_result"), dict) else {}
    commit_record = run_meta.get("commit_record") if isinstance(run_meta.get("commit_record"), dict) else {}
    return {
        "person": person,
        "subtask_id": subtask_id,
        "arm": arm,
        "history_tokens": int(case.get("history_tokens") or 0),
        "current_task_tokens": int(case.get("current_tokens") or 0),
        "memory_context_tokens": int(diagnostics.get("context_tokens") or 0),
        "workspace_score": official_result.get("score"),
        "workspace_pass_60": isinstance(official_result.get("score"), (int, float)) and official_result["score"] >= 60,
        "workspace_reference_80": isinstance(official_result.get("score"), (int, float)) and official_result["score"] >= 80,
        "model_requests": len(requests),
        "input_tokens": sum(int(usage.get("input_tokens") or 0) for usage in usages),
        "cached_tokens": sum(int(usage.get("cached_tokens") or 0) for usage in usages),
        "uncached_input_tokens": sum(
            max(0, int(usage.get("input_tokens") or 0) - int(usage.get("cached_tokens") or 0))
            for usage in usages
        ),
        "output_tokens": sum(int(usage.get("output_tokens") or 0) for usage in usages),
        "cost_usd": round(sum(float(usage.get("cost_usd") or 0.0) for usage in usages), 8),
        "peak_request_input_tokens": max((int(usage.get("input_tokens") or 0) for usage in usages), default=0),
        "mean_ttft_ms": round(mean(
            float(row.get("time_to_first_token_ms") or 0.0)
            for row in requests
            if row.get("time_to_first_token_ms") is not None
        ), 2) if requests else None,
        "model_retries": sum(int(row.get("retries") or 0) for row in requests),
        "tool_calls": len(tools),
        "tool_failures": sum(1 for row in tools if row.get("status") != "success"),
        "tool_names": dict(sorted(Counter(str(row.get("tool_name")) for row in tools).items())),
        "approval_requests": event_counts["approval.requested"],
        "compaction_started": event_counts["compaction.started"],
        "compaction_completed": event_counts["compaction.completed"],
        "compaction_aborted": event_counts["compaction.aborted"],
        "source_counts": dict(sorted(source_counts.items())),
        "gold_relevant_sessions": sorted(gold),
        "retrieved_session_provenance": sorted(known_provenance),
        "provenance_recall": round(provenance_recall, 4) if provenance_recall is not None else None,
        "provenance_precision": round(provenance_precision, 4) if provenance_precision is not None else None,
        "agent_seconds": seconds_between(
            str(agent_result.get("started_at") or ""), str(agent_result.get("finished_at") or "")
        ),
        "commit_seconds": seconds_between(
            str(commit_record.get("started_at") or ""), str(commit_record.get("finished_at") or "")
        ),
        "end_to_end_seconds": seconds_between(
            str(launcher.get("started_at") or ""), str(launcher.get("finished_at") or "")
        ),
        "agent_status": agent_result.get("status"),
    }


def aggregate(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    selected = [row for row in rows if row["arm"] == arm]
    scores = [float(row["workspace_score"]) for row in selected if isinstance(row.get("workspace_score"), (int, float))]
    total_input = sum(row["input_tokens"] for row in selected)
    return {
        "count": len(selected),
        "graded_count": len(scores),
        "workspace_score_mean": round(mean(scores), 2) if scores else None,
        "workspace_pass_rate_60": round(sum(row["workspace_pass_60"] for row in selected) / len(scores), 4) if scores else None,
        "workspace_reference_rate_80": round(sum(row["workspace_reference_80"] for row in selected) / len(scores), 4) if scores else None,
        "memory_context_tokens_mean": round(mean(row["memory_context_tokens"] for row in selected), 2),
        "input_tokens_total": total_input,
        "cached_tokens_total": sum(row["cached_tokens"] for row in selected),
        "uncached_input_tokens_total": sum(row["uncached_input_tokens"] for row in selected),
        "output_tokens_total": sum(row["output_tokens"] for row in selected),
        "cost_usd_total": round(sum(row["cost_usd"] for row in selected), 8),
        "model_requests_total": sum(row["model_requests"] for row in selected),
        "tool_calls_total": sum(row["tool_calls"] for row in selected),
        "agent_seconds_total": round(sum(row["agent_seconds"] or 0.0 for row in selected), 2),
        "end_to_end_seconds_total": round(sum(row["end_to_end_seconds"] or 0.0 for row in selected), 2),
        "provenance_recall_mean": round(mean(
            row["provenance_recall"] for row in selected if row["provenance_recall"] is not None
        ), 4),
        "provenance_precision_mean": round(mean(
            row["provenance_precision"] for row in selected if row["provenance_precision"] is not None
        ), 4),
    }


def percentage_reduction(smaller: float, larger: float) -> float | None:
    if not larger:
        return None
    return round((1.0 - smaller / larger) * 100.0, 2)


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    retrieval = report["aggregate"]["retrieval"]
    concat = report["aggregate"]["concat"]
    comparison = report["comparison"]
    lines = [
        "# MiniClaw ContextWeave Coding Memory Benchmark",
        "",
        "## 结论",
        "",
        f"- 检索组官方工作区平均分：{retrieval['workspace_score_mean']}；全量拼接组：{concat['workspace_score_mean']}。",
        f"- 检索组累计输入 Token 比全量拼接减少 {comparison['input_token_reduction_percent']}%。",
        f"- 检索组记忆注入量比全量拼接减少 {comparison['memory_context_reduction_percent']}%。",
        f"- 检索组成本比全量拼接减少 {comparison['cost_reduction_percent']}%。",
        "- 60 分表示满足最低任务要求，80 分表示达到官方参考实现水平。",
        "",
        "## 分组汇总",
        "",
        "| 指标 | 四层记忆检索 | 全量拼接 |",
        "|---|---:|---:|",
        f"| 官方平均工作区分 | {retrieval['workspace_score_mean']} | {concat['workspace_score_mean']} |",
        f"| >=60 成功率 | {retrieval['workspace_pass_rate_60']:.1%} | {concat['workspace_pass_rate_60']:.1%} |" if retrieval['workspace_pass_rate_60'] is not None and concat['workspace_pass_rate_60'] is not None else "| >=60 成功率 | 待评分 | 待评分 |",
        f"| >=80 参考级比例 | {retrieval['workspace_reference_rate_80']:.1%} | {concat['workspace_reference_rate_80']:.1%} |" if retrieval['workspace_reference_rate_80'] is not None and concat['workspace_reference_rate_80'] is not None else "| >=80 参考级比例 | 待评分 | 待评分 |",
        f"| 平均注入记忆 Token | {retrieval['memory_context_tokens_mean']:.0f} | {concat['memory_context_tokens_mean']:.0f} |",
        f"| 累计输入 Token | {retrieval['input_tokens_total']:,} | {concat['input_tokens_total']:,} |",
        f"| 累计输出 Token | {retrieval['output_tokens_total']:,} | {concat['output_tokens_total']:,} |",
        f"| 估算成本 USD | {retrieval['cost_usd_total']:.6f} | {concat['cost_usd_total']:.6f} |",
        f"| 模型请求数 | {retrieval['model_requests_total']} | {concat['model_requests_total']} |",
        f"| Tool Call 数 | {retrieval['tool_calls_total']} | {concat['tool_calls_total']} |",
        f"| 严格会话来源 Recall | {retrieval['provenance_recall_mean']:.1%} | {concat['provenance_recall_mean']:.1%} |",
        f"| 严格会话来源 Precision | {retrieval['provenance_precision_mean']:.1%} | {concat['provenance_precision_mean']:.1%} |",
        "",
        "## 单题结果",
        "",
        "| 任务 | 组别 | 工作区分 | 注入Token | 累计输入Token | 成本USD | Agent秒 | Tool Calls |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["runs"]:
        lines.append(
            f"| {row['person']}/{row['subtask_id']} | {row['arm']} | {row['workspace_score']} | "
            f"{row['memory_context_tokens']:,} | {row['input_tokens']:,} | {row['cost_usd']:.6f} | "
            f"{(row['agent_seconds'] or 0):.1f} | {row['tool_calls']} |"
        )
    lines.extend(
        [
            "",
            "## 口径说明",
            "",
            "- 两组使用相同历史会话、当前任务、Docker 起始状态、模型和工具配置，只改变记忆注入方式。",
            "- `retrieval` 最多注入约 15k Token；`concat` 将同一批四层记忆材料全部放到开头。",
            "- 严格会话来源 Recall/Precision 只统计可追溯到官方 relevant previous subtask 的命中；Semantic 中没有来源 ID 的等价事实不会被算作命中，因此该指标偏保守。",
            "- 官方工作区分由独立 Judge 实际检查候选与参考 Docker 镜像得到，不以 Agent 退出码代替正确率。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize MiniClaw ContextWeave benchmark traces")
    parser.add_argument("--run-id", default="core-4-graded")
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--group", choices=("core", "overflow_stress", "all"), default="core")
    args = parser.parse_args()

    selection = read_json(args.selection, {})
    cases = list(selection.get("core") or [])
    if args.group == "overflow_stress":
        cases = list(selection.get("overflow_stress") or [])
    elif args.group == "all":
        cases.extend(selection.get("overflow_stress") or [])
    run_root = DEFAULT_RESULTS / args.run_id
    launcher_payload = read_json(run_root / "launcher_summary.json", {})
    launcher_rows = {
        (str(row.get("person")), str(row.get("subtask_id")), str(row.get("arm"))): row
        for row in launcher_payload.get("results", [])
    }
    rows = [
        summarize_run(case, arm, run_root=run_root, launcher_rows=launcher_rows)
        for case in cases
        for arm in ("retrieval", "concat")
    ]
    retrieval = aggregate(rows, "retrieval")
    concat = aggregate(rows, "concat")
    report = {
        "benchmark": "ContextWeave",
        "run_id": args.run_id,
        "model": "gpt-5.6-luna",
        "runs": rows,
        "aggregate": {"retrieval": retrieval, "concat": concat},
        "comparison": {
            "memory_context_reduction_percent": percentage_reduction(
                retrieval["memory_context_tokens_mean"], concat["memory_context_tokens_mean"]
            ),
            "input_token_reduction_percent": percentage_reduction(
                retrieval["input_tokens_total"], concat["input_tokens_total"]
            ),
            "uncached_input_token_reduction_percent": percentage_reduction(
                retrieval["uncached_input_tokens_total"], concat["uncached_input_tokens_total"]
            ),
            "cost_reduction_percent": percentage_reduction(
                retrieval["cost_usd_total"], concat["cost_usd_total"]
            ),
            "agent_time_reduction_percent": percentage_reduction(
                retrieval["agent_seconds_total"], concat["agent_seconds_total"]
            ),
        },
    }
    (run_root / "benchmark-summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_markdown(run_root / "BENCHMARK_REPORT.md", report)
    print(json.dumps(report["aggregate"], ensure_ascii=False, indent=2))
    print(json.dumps(report["comparison"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
