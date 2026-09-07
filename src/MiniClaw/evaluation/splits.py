from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Sequence


LONGMEM_DOMAINS = ("web", "enterprise")
LONGMEM_TYPES = (
    "dynamic-environment",
    "dynamic-environment-abs",
    "procedure",
    "procedure-abs",
    "static-environment",
    "static-environment-abs",
)
REPOGUARD_CARRIERS = ("readme", "issue", "code_comment", "test_log", "rule_file")
AI_EFFICIENCY_TASKS = (
    "log_small_easy",
    "log_ci_medium",
    "log_prod_hard",
    "s_log_build_error",
    "dbg_multi_orders",
    "dbg_multi_batch",
    "s_bug_env_precedence",
    "s_bug_cache_stale",
    "pipe_redirect_bytes",
    "pipe_count_defs",
    "s_pipe_sort_order",
    "s_cmdsub_count",
    "exact_block_lines",
    "s_neg_registry_line",
    "long_synth_ops",
    "long_click_audit",
    "long_synth_incident",
    "f_long_audit",
    "g_long_audit",
    "c_long_types",
)


@dataclass(slots=True, frozen=True)
class LongMemQuestion:
    question_id: str
    domain: str
    question_type: str
    component: str

    @property
    def stratum(self) -> tuple[str, str]:
        return self.domain, self.question_type


def _stable_key(seed: int, *values: str) -> bytes:
    return hashlib.sha256(f"{seed}:".encode() + ":".join(values).encode("utf-8")).digest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                values.append(value)
    return values


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_ids(path: Path, values: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(values) + "\n", encoding="utf-8")


def _git_commit(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        shell=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _collect_longmem_exposure(results_root: Path) -> tuple[set[str], set[str]]:
    exposed: set[str] = set()
    failed: set[str] = set()
    if not results_root.is_dir():
        return exposed, failed
    for path in results_root.rglob("results.json"):
        if "longmemeval-v2" not in path.as_posix().casefold():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = payload if isinstance(payload, list) else payload.get("results", []) if isinstance(payload, dict) else []
        for row in rows:
            if not isinstance(row, dict) or not row.get("question_id"):
                continue
            question_id = str(row["question_id"])
            exposed.add(question_id)
            if row.get("correct") is False or row.get("error"):
                failed.add(question_id)
    return exposed, failed


def _load_longmem_questions(data_root: Path) -> list[LongMemQuestion]:
    questions_path = data_root / "questions.jsonl"
    rows = _read_jsonl(questions_path)
    eligible = {
        str(row["id"]): row
        for row in rows
        if str(row.get("domain") or "") in LONGMEM_DOMAINS
        and str(row.get("question_type") or "") in LONGMEM_TYPES
        and not row.get("image")
    }
    return [
        LongMemQuestion(
            question_id=question_id,
            domain=str(row["domain"]),
            question_type=str(row["question_type"]),
            # LongMemEval-V2 Small intentionally reuses a common retrieval corpus. That
            # corpus is test-time evidence rather than a supervised label, so the leakage
            # unit is the normalized query family. Exact/case-only duplicates stay together;
            # shared public distractors do not make the split impossible.
            component=hashlib.sha256(
                re.sub(r"[^\w]+", " ", str(row.get("question") or "").casefold()).strip().encode(
                    "utf-8"
                )
            ).hexdigest()[:16],
        )
        for question_id, row in eligible.items()
    ]


def _stratified_pick(
    candidates: Sequence[LongMemQuestion],
    count: int,
    *,
    seed: int,
    priority_ids: set[str] | None = None,
) -> list[LongMemQuestion]:
    if count < 1:
        return []
    priority_ids = priority_ids or set()
    groups: dict[tuple[str, str], list[LongMemQuestion]] = defaultdict(list)
    for item in candidates:
        groups[item.stratum].append(item)
    for stratum, values in groups.items():
        values.sort(
            key=lambda item: (
                0 if item.question_id in priority_ids else 1,
                _stable_key(seed, *stratum, item.question_id),
            )
        )
    strata = sorted(groups, key=lambda value: _stable_key(seed, *value))
    selected: list[LongMemQuestion] = []
    while len(selected) < count:
        progressed = False
        for stratum in strata:
            if groups[stratum] and len(selected) < count:
                selected.append(groups[stratum].pop(0))
                progressed = True
        if not progressed:
            break
    if len(selected) < count:
        raise ValueError(f"only {len(selected)} eligible questions available; requested {count}")
    return selected


def _load_repoguard_tasks(root: Path) -> list[dict[str, Any]]:
    return [
        *_read_jsonl(root / "data" / "repoguardbench_core.jsonl"),
        *_read_jsonl(root / "data" / "repoguardbench_real.jsonl"),
    ]


def _select_repoguard_tasks(rows: Sequence[dict[str, Any]], count: int, seed: int) -> list[str]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row.get("tier") or ""), str(row.get("difficulty") or ""))].append(row)
    for stratum, values in groups.items():
        values.sort(key=lambda row: _stable_key(seed, *stratum, str(row["task_id"])))
    strata = sorted(groups, key=lambda value: _stable_key(seed, *value))
    selected: list[str] = []
    while len(selected) < count:
        progressed = False
        for stratum in strata:
            if groups[stratum] and len(selected) < count:
                selected.append(str(groups[stratum].pop(0)["task_id"]))
                progressed = True
        if not progressed:
            break
    if len(selected) < count:
        raise ValueError(f"only {len(selected)} RepoGuardBench tasks available; requested {count}")
    return selected


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def build_split_manifest(
    project_root: Path,
    output_dir: Path,
    *,
    seed: int = 20260905,
    development_longmem: int = 60,
    heldout_longmem: int = 80,
    development_repoguard_tasks: int = 12,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    output_dir = output_dir.resolve()
    longmem_repo = project_root / "external" / "benchmarks" / "LongMemEval-V2"
    longmem_data = longmem_repo / "data" / "longmemeval-v2"
    repoguard_repo = project_root / "external" / "benchmarks" / "RepoGuardBench"
    efficiency_repo = project_root / "external" / "benchmarks" / "ai-efficiency-benchmark"
    results_root = project_root / ".aster" / "benchmarks" / "results"
    questions = _load_longmem_questions(longmem_data)
    exposed, failed = _collect_longmem_exposure(results_root)

    exposed_candidates = [item for item in questions if item.question_id in exposed]
    development = _stratified_pick(
        exposed_candidates,
        min(development_longmem, len(exposed_candidates)),
        seed=seed,
        priority_ids=failed,
    )
    if len(development) < development_longmem:
        selected_ids = {item.question_id for item in development}
        development.extend(
            _stratified_pick(
                [item for item in questions if item.question_id not in selected_ids],
                development_longmem - len(development),
                seed=seed + 1,
            )
        )

    exposed_components = {item.component for item in questions if item.question_id in exposed}
    development_components = {item.component for item in development}
    heldout_candidates = [
        item
        for item in questions
        if item.question_id not in exposed
        and item.component not in exposed_components
        and item.component not in development_components
    ]
    heldout = _stratified_pick(heldout_candidates, heldout_longmem, seed=seed + 2)

    development_ids = [item.question_id for item in development]
    heldout_ids = [item.question_id for item in heldout]
    repoguard_ids = _select_repoguard_tasks(
        _load_repoguard_tasks(repoguard_repo), development_repoguard_tasks, seed + 3
    )
    efficiency_ids = list(AI_EFFICIENCY_TASKS)

    development_lme_path = output_dir / "development-longmemeval-v2.ids"
    heldout_lme_path = output_dir / "heldout-longmemeval-v2.ids"
    development_repoguard_path = output_dir / "development-repoguard.ids"
    development_efficiency_path = output_dir / "development-ai-efficiency.ids"
    exposure_path = output_dir / "exposure-longmemeval-v2.json"
    _write_ids(development_lme_path, development_ids)
    _write_ids(heldout_lme_path, heldout_ids)
    _write_ids(development_repoguard_path, repoguard_ids)
    _write_ids(development_efficiency_path, efficiency_ids)
    _write_json(
        exposure_path,
        {
            "version": 1,
            "source": "LongMemEval-V2",
            "previously_exposed_question_ids": sorted(exposed),
            "previously_failed_question_ids": sorted(failed),
            "reserved_development_question_ids": development_ids,
            "frozen_heldout_question_ids": heldout_ids,
        },
    )

    manifest = {
        "version": 1,
        "name": "miniclaw-eval-portfolio-v1",
        "frozen_on": str(date.today()),
        "seed": seed,
        "policy": {
            "regression": "blocking; failures may be inspected and fixed",
            "development": "non-blocking; used for diagnosis and distribution-level improvement",
            "heldout": "release-only; never use individual cases to tune prompts or code",
            "production": "future real-session canary and drift monitoring",
        },
        "sources": {
            "longmemeval_v2": {
                "repository": "https://github.com/xiaowu0162/LongMemEval-V2",
                "commit": _git_commit(longmem_repo),
                "questions_sha256": _file_sha256(longmem_data / "questions.jsonl"),
                "haystacks_sha256": _file_sha256(longmem_data / "haystacks" / "lme_v2_small.json"),
                "exposure_registry": _relative(exposure_path, project_root),
            },
            "repoguard": {
                "repository": "https://github.com/DaoyuanLi2816/RepoGuardBench",
                "commit": _git_commit(repoguard_repo),
            },
            "ai_efficiency": {
                "repository": "https://github.com/PointFiveLabs/ai-efficiency-benchmark",
                "commit": _git_commit(efficiency_repo),
            },
        },
        "tracks": {
            "regression": {
                "blocking": True,
                "suite": "evals/full.json",
                "baseline": "evals/baselines/gpt-5.6-luna-full-v1.json",
                "cases": 22,
                "command": (
                    "python -m MiniClaw.evaluation.cli evals/full.json --env-file .env --jobs 4 "
                    "--baseline evals/baselines/gpt-5.6-luna-full-v1.json"
                ),
            },
            "development": {
                "blocking": False,
                "datasets": [
                    {
                        "adapter": "longmemeval_v2",
                        "selection_file": _relative(development_lme_path, project_root),
                        "cases": len(development_ids),
                        "known_failed_cases": sum(question_id in failed for question_id in development_ids),
                        "command": (
                            "python -m MiniClaw.benchmark.longmemeval_v2 "
                            "--official-repo external/benchmarks/LongMemEval-V2 "
                            "--data-root external/benchmarks/LongMemEval-V2/data/longmemeval-v2 "
                            f"--question-id-file {_relative(development_lme_path, project_root)} "
                            "--env-file .env --output .aster/benchmarks/results/eval-development-longmemeval-v2"
                        ),
                    },
                    {
                        "adapter": "repoguard",
                        "selection_file": _relative(development_repoguard_path, project_root),
                        "tasks": len(repoguard_ids),
                        "carriers": list(REPOGUARD_CARRIERS),
                        "include_clean": True,
                        "executions": len(repoguard_ids) * (len(REPOGUARD_CARRIERS) + 1),
                        "command": (
                            "python -m MiniClaw.benchmark.repoguard --tier both "
                            f"--task-id-file {_relative(development_repoguard_path, project_root)} "
                            "--agent-mode compromised --concurrency 4 --env-file .env "
                            "--output-dir .aster/benchmarks/results/eval-development-repoguard"
                        ),
                    },
                    {
                        "adapter": "ai_efficiency",
                        "selection_file": _relative(development_efficiency_path, project_root),
                        "tasks": len(efficiency_ids),
                        "arms": ["full", "artifact-off"],
                        "executions": len(efficiency_ids) * 2,
                        "command": (
                            "python -m MiniClaw.benchmark.ai_efficiency "
                            f"--task-id-file {_relative(development_efficiency_path, project_root)} "
                            "--env-file .env --output .aster/benchmarks/results/eval-development-ai-efficiency"
                        ),
                    },
                ],
            },
            "heldout": {
                "blocking": True,
                "frozen": True,
                "selection_file": _relative(heldout_lme_path, project_root),
                "cases": len(heldout_ids),
                "command": (
                    "python -m MiniClaw.benchmark.longmemeval_v2 "
                    "--official-repo external/benchmarks/LongMemEval-V2 "
                    "--data-root external/benchmarks/LongMemEval-V2/data/longmemeval-v2 "
                    f"--question-id-file {_relative(heldout_lme_path, project_root)} "
                    "--env-file .env --output .aster/benchmarks/results/eval-heldout-longmemeval-v2"
                ),
            },
            "production": {
                "blocking": False,
                "status": "collect real failed sessions by time window; promote only clustered failures",
            },
        },
        "counts": {
            "regression_cases": 22,
            "development_longmemeval_cases": len(development_ids),
            "development_repoguard_executions": len(repoguard_ids) * 6,
            "development_ai_efficiency_executions": len(efficiency_ids) * 2,
            "development_executions": len(development_ids) + len(repoguard_ids) * 6 + len(efficiency_ids) * 2,
            "heldout_cases": len(heldout_ids),
            "total_declared_executions": 22 + len(development_ids) + len(repoguard_ids) * 6 + len(efficiency_ids) * 2 + len(heldout_ids),
        },
        "leakage_checks": {
            "development_heldout_question_overlap": len(set(development_ids) & set(heldout_ids)),
            "development_heldout_query_family_overlap": len(
                {item.component for item in development} & {item.component for item in heldout}
            ),
            "heldout_previously_exposed_questions": len(set(heldout_ids) & exposed),
        },
        "selection_stats": {
            "longmemeval_eligible_questions": len(questions),
            "longmemeval_previously_exposed_questions": len(exposed),
            "longmemeval_previously_failed_questions": len(failed),
            "longmemeval_heldout_candidate_questions": len(heldout_candidates),
            "development_strata": _strata_counts(development),
            "heldout_strata": _strata_counts(heldout),
        },
    }
    _write_json(output_dir / "manifest.json", manifest)
    validate_split_manifest(output_dir / "manifest.json", project_root=project_root)
    return manifest


def _strata_counts(values: Sequence[LongMemQuestion]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in values:
        counts[f"{item.domain}/{item.question_type}"] += 1
    return dict(sorted(counts.items()))


def validate_split_manifest(path: Path, *, project_root: Path | None = None) -> dict[str, Any]:
    path = path.resolve()
    root = (project_root or path.parents[3]).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    if manifest.get("version") != 1:
        raise ValueError("split manifest must use version 1")
    leakage = manifest.get("leakage_checks") or {}
    non_zero = {key: value for key, value in leakage.items() if int(value) != 0}
    if non_zero:
        raise ValueError(f"split leakage detected: {non_zero}")
    files: list[str] = []
    longmem_development_file: str | None = None
    heldout_file: str | None = None
    for track in (manifest.get("tracks") or {}).values():
        if not isinstance(track, dict):
            continue
        if track.get("selection_file"):
            selection_file = str(track["selection_file"])
            files.append(selection_file)
            if track.get("frozen"):
                heldout_file = selection_file
        for dataset in track.get("datasets") or []:
            if isinstance(dataset, dict) and dataset.get("selection_file"):
                selection_file = str(dataset["selection_file"])
                files.append(selection_file)
                if dataset.get("adapter") == "longmemeval_v2":
                    longmem_development_file = selection_file
    exposure_files = [
        str(source["exposure_registry"])
        for source in (manifest.get("sources") or {}).values()
        if isinstance(source, dict) and source.get("exposure_registry")
    ]
    missing = [value for value in [*files, *exposure_files] if not (root / value).is_file()]
    if missing:
        raise FileNotFoundError("Eval split files missing: " + ", ".join(missing))

    for exposure_file in exposure_files:
        registry = json.loads((root / exposure_file).read_text(encoding="utf-8-sig"))
        if not isinstance(registry, dict) or registry.get("version") != 1:
            raise ValueError(f"invalid exposure registry: {exposure_file}")
        registry_lists: dict[str, list[str]] = {}
        for key in (
            "previously_exposed_question_ids",
            "previously_failed_question_ids",
            "reserved_development_question_ids",
            "frozen_heldout_question_ids",
        ):
            values = registry.get(key)
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ValueError(f"invalid exposure registry field {key}: {exposure_file}")
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate ids in exposure registry field {key}: {exposure_file}")
            registry_lists[key] = values
        if set(registry_lists["previously_failed_question_ids"]) - set(
            registry_lists["previously_exposed_question_ids"]
        ):
            raise ValueError("failed LongMemEval-V2 ids must also be marked exposed")
        if set(registry_lists["frozen_heldout_question_ids"]) & set(
            registry_lists["previously_exposed_question_ids"]
        ):
            raise ValueError("frozen LongMemEval-V2 ids include previously exposed questions")
        if set(registry_lists["frozen_heldout_question_ids"]) & set(
            registry_lists["reserved_development_question_ids"]
        ):
            raise ValueError("LongMemEval-V2 development and held-out ids overlap")
        if longmem_development_file:
            selected = [
                line.strip()
                for line in (root / longmem_development_file).read_text(encoding="utf-8-sig").splitlines()
                if line.strip()
            ]
            if selected != registry_lists["reserved_development_question_ids"]:
                raise ValueError("LongMemEval-V2 development ids do not match exposure registry")
        if heldout_file:
            selected = [
                line.strip()
                for line in (root / heldout_file).read_text(encoding="utf-8-sig").splitlines()
                if line.strip()
            ]
            if selected != registry_lists["frozen_heldout_question_ids"]:
                raise ValueError("LongMemEval-V2 held-out ids do not match exposure registry")
    return {
        "valid": True,
        "manifest": str(path),
        "selection_files": len(files),
        "exposure_registries": len(exposure_files),
        "counts": manifest.get("counts") or {},
        "leakage_checks": leakage,
    }


def track_commands(path: Path, track_name: str) -> list[str]:
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    track = (manifest.get("tracks") or {}).get(track_name)
    if not isinstance(track, dict):
        raise ValueError(f"unknown Eval track: {track_name}")
    commands: list[str] = []
    if track.get("command"):
        commands.append(str(track["command"]))
    for dataset in track.get("datasets") or []:
        if isinstance(dataset, dict) and dataset.get("command"):
            commands.append(str(dataset["command"]))
    return commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or validate MiniClaw Eval data splits")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--project-root", default=".")
    build.add_argument("--output", default="evals/splits/v1")
    build.add_argument("--seed", type=int, default=20260905)
    build.add_argument("--development-longmem", type=int, default=60)
    build.add_argument("--heldout-longmem", type=int, default=80)
    build.add_argument("--development-repoguard-tasks", type=int, default=12)
    validate = subparsers.add_parser("validate")
    validate.add_argument("manifest")
    validate.add_argument("--project-root", default=".")
    commands = subparsers.add_parser("commands")
    commands.add_argument("manifest")
    commands.add_argument(
        "--track", choices=("regression", "development", "heldout", "production"), required=True
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "build":
        value = build_split_manifest(
            Path(args.project_root),
            Path(args.output),
            seed=args.seed,
            development_longmem=args.development_longmem,
            heldout_longmem=args.heldout_longmem,
            development_repoguard_tasks=args.development_repoguard_tasks,
        )
        print(json.dumps(value["counts"], ensure_ascii=False, indent=2))
    elif args.command == "validate":
        value = validate_split_manifest(Path(args.manifest), project_root=Path(args.project_root))
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        for command in track_commands(Path(args.manifest), args.track):
            print(command)


if __name__ == "__main__":
    main()
