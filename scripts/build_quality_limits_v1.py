"""Generate the next boundary suite with proposed process/efficiency limits.

The source suite is read-only input. Existing case checks are copied unchanged;
new metric checks are appended from the independent policy file.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "evals" / "boundary-campaign-v1.json"
DEFAULT_POLICY = ROOT / "evals" / "quality-limits-policy-v1.json"
DEFAULT_OUTPUT = ROOT / "evals" / "next-quality-limits-v1.json"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def validate_source(suite: dict[str, Any]) -> None:
    if not isinstance(suite.get("cases"), list) or not suite["cases"]:
        raise ValueError("source suite must contain a non-empty cases array")
    for case in suite["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("checks"), list):
            raise ValueError("each source case must contain a checks array")


def build_suite(
    source: dict[str, Any], policy: dict[str, Any], *, source_sha256: str | None = None
) -> dict[str, Any]:
    validate_source(source)
    limits = policy["limits"]
    tool_error_limit = limits["process"]["tool_errors"]["max"]
    duration = limits["efficiency"]["wall_duration_seconds"]["by_category"]
    output = copy.deepcopy(source)
    output["version"] = 1
    output["name"] = "next-quality-limits-v1"
    output["generated_from"] = {
        "suite": "evals/boundary-campaign-v1.json",
        "suite_sha256": source_sha256
        or hashlib.sha256(
            json.dumps(source, ensure_ascii=False, indent=2).encode("utf-8")
        ).hexdigest(),
        "policy": "evals/quality-limits-policy-v1.json",
    }
    for case in output["cases"]:
        for observation in policy.get('observations',[]):
            case['checks'].append({'type':'metric','dimension':'efficiency','required':False,
                                  'report_only':True,**observation})
        case["checks"].extend(
            [
                {
                    "type": "metric",
                    "name": "tool_errors",
                    "max": tool_error_limit,
                    "dimension": "process",
                },
                {
                    "type": "metric",
                    "name": "wall_duration_seconds",
                    "max": duration.get(case.get("category"), duration["default"]),
                    "dimension": "efficiency",
                },
            ]
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.resolve() in {args.source.resolve(), args.policy.resolve()}:
        raise ValueError("Output must not overwrite the source suite or policy")
    source_bytes = args.source.read_bytes()
    source = load_json(args.source)
    policy = load_json(args.policy)
    result = build_suite(source, policy, source_sha256=hashlib.sha256(source_bytes).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"generated {args.output} ({len(result['cases'])} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
