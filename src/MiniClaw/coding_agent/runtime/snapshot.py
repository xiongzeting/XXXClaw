from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import RuntimeSettings
from .workspace import is_protected_relative_path


SNAPSHOT_SKIP_DIRECTORIES = {
    ".venv", "venv", "node_modules", "dist", "build", "coverage", ".cache", "__pycache__",
}


@dataclass(slots=True, frozen=True)
class SnapshotManifest:
    version: int
    task_id: str
    created_at: str
    source_workspace: str
    task_workspace: str
    sandbox: str
    copied_files: int
    copied_bytes: int
    excluded_entries: int


def is_safe_snapshot_path(relative_path: str | Path) -> bool:
    path = Path(relative_path)
    parts = tuple(part.casefold() for part in path.parts if part not in {"", "."})
    if not parts:
        return True
    if any(part in SNAPSHOT_SKIP_DIRECTORIES for part in parts):
        return False
    return not is_protected_relative_path(path, "execute")


def prepare_snapshot_workspace(
    source_workspace: str | Path,
    task_workspace: str | Path,
    manifest_path: str | Path,
    task_id: str,
    settings: RuntimeSettings,
) -> SnapshotManifest:
    source = Path(source_workspace).resolve(strict=True)
    task = Path(task_workspace).resolve(strict=False)
    manifest_file = Path(manifest_path).resolve(strict=False)
    _assert_separate_workspace(source, task)
    if manifest_file.exists():
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest = SnapshotManifest(**data)
        if manifest.task_id != task_id:
            raise ValueError("[SNAPSHOT_TASK_MISMATCH] Existing snapshot belongs to another task")
        if Path(manifest.source_workspace).resolve() != source:
            raise ValueError("[SNAPSHOT_SOURCE_MISMATCH] Existing snapshot has another source workspace")
        return manifest

    usage = _scan(source, settings)
    task.mkdir(parents=True, exist_ok=True)
    _copy_safe_tree(source, task, source)
    manifest = SnapshotManifest(
        version=1,
        task_id=task_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        source_workspace=str(source),
        task_workspace=str(task),
        sandbox=settings.sandbox,
        copied_files=usage[0],
        copied_bytes=usage[1],
        excluded_entries=usage[2],
    )
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest = manifest_file.with_suffix(manifest_file.suffix + ".tmp")
    temporary_manifest.write_text(
        json.dumps(asdict(manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_manifest.replace(manifest_file)
    return manifest


def _scan(root: Path, settings: RuntimeSettings) -> tuple[int, int, int]:
    files = 0
    size = 0
    excluded = 0
    for directory, names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        kept_names: list[str] = []
        for name in names:
            path = directory_path / name
            if path.is_symlink() or not is_safe_snapshot_path(path.relative_to(root)):
                excluded += 1
            else:
                kept_names.append(name)
        names[:] = kept_names
        for name in file_names:
            path = directory_path / name
            if path.is_symlink() or not is_safe_snapshot_path(path.relative_to(root)):
                excluded += 1
                continue
            files += 1
            size += path.stat().st_size
            if files > settings.snapshot_max_files:
                raise ValueError(f"[SNAPSHOT_FILE_LIMIT] Workspace exceeds {settings.snapshot_max_files} files")
            if size > settings.snapshot_max_bytes:
                raise ValueError(f"[SNAPSHOT_SIZE_LIMIT] Workspace exceeds {settings.snapshot_max_bytes} bytes")
    return files, size, excluded


def _copy_safe_tree(source: Path, destination: Path, root: Path) -> None:
    for child in source.iterdir():
        relative = child.relative_to(root)
        if not is_safe_snapshot_path(relative) or child.is_symlink():
            continue
        target = destination / child.name
        if child.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _copy_safe_tree(child, target, root)
        elif child.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)


def _assert_separate_workspace(source: Path, task: Path) -> None:
    if source == task:
        raise ValueError("[INVALID_SNAPSHOT_WORKSPACE] Snapshot must differ from source workspace")
    try:
        source.relative_to(task)
    except ValueError:
        return
    raise ValueError("[INVALID_SNAPSHOT_WORKSPACE] Snapshot cannot contain the source workspace")
