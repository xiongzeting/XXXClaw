"""Run Eval3's original 20 cases with two parallel Luna lanes."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SUITE = ROOT / "evals" / "eval3-hardened-r1" / "suite.json"
EXCLUDED = {"large_tool_archive_contract_r1", "large_tool_archive_shipping_r1"}
JOBS = max(1, int(os.environ.get("MINICLAW_EVAL_JOBS", "2")))


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / ".aster" / "evals" / f"eval3-hardened-r1-luna20-{JOBS}lane-{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    os.environ["MINICLAW_EVAL_DEFER_JUDGE"] = "true"
    from MiniClaw.evaluation.models import load_eval_suite
    from MiniClaw.evaluation.runner import run_eval_suite
    from MiniClaw.llm.config import load_llm_settings
    from MiniClaw.llm.env_file import merged_environment, read_env_file

    env = merged_environment(read_env_file(ROOT / ".env") if (ROOT / ".env").is_file() else {})
    env["MINICLAW_EVAL_DEFER_JUDGE"] = "true"
    settings = load_llm_settings(provider="primary", model_id="gpt-5.6-luna", environment=env)
    suite = load_eval_suite(SUITE)
    selected = [case.id for case in suite.cases if case.id not in EXCLUDED]
    write(out / "run-config.json", {
        "suite": str(SUITE), "model": settings.model_id, "provider": settings.provider,
        "jobs": JOBS, "repeat": 1, "selected_cases": selected, "excluded_cases": sorted(EXCLUDED),
        "started_at": datetime.now(timezone.utc).isoformat(),
    })
    write(out / "run-status.json", {"state": "running", "cases": len(selected), "jobs": JOBS})
    print(json.dumps({"state": "running", "output": str(out), "cases": len(selected), "jobs": JOBS}, ensure_ascii=False), flush=True)
    try:
        report = await run_eval_suite(
            suite, output_directory=out / "run", environment=env,
            selected_cases=set(selected), provider="primary", model_id="gpt-5.6-luna",
            jobs=JOBS, repeat=1,
        )
        write(out / "report.json", report)
        write(out / "run-status.json", {"state": "completed_pending_judge", "cases": len(selected), "jobs": JOBS})
    except BaseException as exc:
        write(out / "run-status.json", {"state": "interrupted", "error_type": type(exc).__name__, "error": str(exc)})
        raise


if __name__ == "__main__":
    asyncio.run(main())
