"""Publish recovery-aware checks without editing frozen v4 inputs or scores."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "evals"


def save(name, value):
    path = ROOT / name
    rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != rendered:
        raise ValueError(f"Refusing to overwrite different published content: {path}")
    path.write_text(rendered, encoding="utf-8")


def main():
    for split in ("development", "retained", "test"):
        value = json.loads((ROOT / f"{split}-challenge-v4.json").read_text(encoding="utf-8"))
        value["name"] = f"{split}-challenge-v5"
        for case in value["cases"]:
            case.setdefault("environment", {})["MINICLAW_EVAL_NETWORK_RECOVERY"] = "true"
            for check in case["checks"]:
                if check.get("type") == "metric" and check.get("name") == "model_errors" and check.get("dimension") == "reliability":
                    check["name"] = "non_network_model_errors"
            case["checks"].append({"type": "metric", "name": "unrecovered_runs", "equals": 0, "dimension": "reliability"})
        save(f"{split}-challenge-v5.json", value)
    save("main-challenge-v5.json", {"version": 1, "name": "main-challenge-v5", "includes": [
        f"{split}-challenge-v5.json" for split in ("development", "retained", "test")
    ]})
    portfolio = json.loads((ROOT / "active-eval-portfolio-v4.json").read_text(encoding="utf-8"))
    portfolio.update(version=5, main_combined="main-challenge-v5.json",
                     original_portfolio="active-eval-portfolio-v4.json")
    portfolio["main"] = {split: f"{split}-challenge-v5.json" for split in ("development", "retained", "test")}
    portfolio["network_policy"] = "Retry only the interrupted request once after bounded transport retries; preserve original failures and cumulative costs. Score final delivery, non-network errors and unchanged task checks."
    portfolio["validation"] = "offline regression only; no v5 model campaign has been run"
    save("active-eval-portfolio-v5.json", portfolio)
    from MiniClaw.evaluation.models import load_eval_suite
    suite = load_eval_suite(ROOT / "main-challenge-v5.json")
    assert len(suite.cases) == 30
    print("v5 schema loaded: 30 cases; no model requests or case execution")


if __name__ == "__main__":
    main()
