"""Import completed Eval session/trace records into one local web session.

The source Eval files are read-only.  A new .aster/web session is created so
the 8767 UI can inspect the five runs as one chronological conversation.
"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from pathlib import Path


CASES = [
    "version_resolution_r3",
    "audit_bundle_integration_r3",
    "compression_transaction_rollback_r3",
    "compression_rule_priority_exceptions_r3",
    "diagnose_and_patch_release_r3",
]

# Keep accidental credentials out of the browser transcript while preserving
# ordinary Eval content.  The checked-in runs currently contain no matches.
SECRET_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")


def redact(text: str) -> str:
    return SECRET_RE.sub("[REDACTED_API_KEY]", text)


def separator(case_id: str, source: Path) -> str:
    return (
        "【Eval 导入】\n"
        f"题目：{case_id}\n"
        "模型：gpt-5.6-luna\n"
        "来源：eval3-luna-5-current / attempt-001\n"
        f"原始会话：{source}\n"
        "以下为该题完整原始运行轨迹（请求、回复、工具调用、工具结果和错误）。"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--eval-root", type=Path)
    parser.add_argument("--session-id")
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    eval_root = (args.eval_root or workspace / ".aster" / "evals" / "eval3-luna-5-current").resolve()
    session_id = uuid.UUID(args.session_id).hex if args.session_id else uuid.uuid4().hex
    target = workspace / ".aster" / "web" / session_id
    target.mkdir(parents=True, exist_ok=False)
    out_session = target / "session.jsonl"
    out_trace = target / "trace.jsonl"

    session_count = trace_count = 0
    with out_session.open("w", encoding="utf-8", newline="\n") as session_out, out_trace.open(
        "w", encoding="utf-8", newline="\n"
    ) as trace_out:
        for case_id in CASES:
            source_dir = eval_root / "cases" / case_id / "attempt-001" / "workspace" / ".aster" / "eval-sessions" / "shared"
            source_session = source_dir / "session.jsonl"
            source_trace = source_dir / "trace.jsonl"
            if not source_session.is_file() or not source_trace.is_file():
                raise FileNotFoundError(f"缺少 {case_id} 的 session.jsonl 或 trace.jsonl")

            marker = {
                "type": "message",
                "id": uuid.uuid4().hex,
                "message": {"role": "assistant", "content": separator(case_id, source_session), "tool_calls": []},
                "timestamp": "2026-09-11T00:00:00+00:00",
            }
            session_out.write(json.dumps(marker, ensure_ascii=False) + "\n")
            session_count += 1

            for raw in source_session.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                session_out.write(redact(raw) + "\n")
                session_count += 1
            for raw in source_trace.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                trace_out.write(redact(raw) + "\n")
                trace_count += 1

    last_id = workspace / ".aster" / "web" / "last-session-id"
    last_id.write_text(session_id + "\n", encoding="utf-8")
    print(json.dumps({"session_id": session_id, "cases": CASES, "session_records": session_count, "trace_records": trace_count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
