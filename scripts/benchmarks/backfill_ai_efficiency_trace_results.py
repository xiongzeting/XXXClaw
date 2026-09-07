from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from MiniClaw.benchmark.ai_efficiency import _load_manifest
from MiniClaw.trace.store import TraceRecorder, read_trace_records


def _read_results(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def backfill(output_root: Path) -> dict[str, int]:
    manifest = _load_manifest()
    added = 0
    skipped = 0
    missing = 0
    for result in _read_results(output_root / "results.jsonl"):
        task_id = str(result["task_id"])
        arm = str(result["arm"])
        task = manifest.get(task_id)
        trace_path = output_root / "cases" / task_id / arm / "work" / ".aster" / "benchmark" / "trace.jsonl"
        if not isinstance(task, dict) or not trace_path.is_file():
            missing += 1
            continue
        records = read_trace_records(trace_path)
        run_ids = [
            str(record.get("run_id"))
            for record in records
            if record.get("type") == "run.completed" and record.get("run_id")
        ]
        if not run_ids:
            missing += 1
            continue
        run_id = run_ids[-1]
        if any(
            record.get("type") == "benchmark.case.completed" and record.get("run_id") == run_id
            for record in records
        ):
            skipped += 1
            continue
        TraceRecorder(trace_path.parent, "benchmark", f"{task_id}-{arm}").record(
            "benchmark.case.completed",
            {
                "benchmark": "PointFiveLabs/ai-efficiency-benchmark",
                "task_id": task_id,
                "family": task.get("family"),
                "difficulty": task.get("difficulty"),
                "question": task.get("prompt"),
                "output": result.get("result_text"),
                "success": result.get("success"),
                "failure_reason": result.get("failure_reason"),
                "judge_spec": task.get("judge"),
                "metrics": result.get("metrics"),
            },
            run_id=run_id,
        )
        added += 1
    return {"added": added, "skipped": skipped, "missing": missing}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill AI Efficiency judge results into unified Trace")
    parser.add_argument("output_root")
    args = parser.parse_args()
    print(json.dumps(backfill(Path(args.output_root).resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
