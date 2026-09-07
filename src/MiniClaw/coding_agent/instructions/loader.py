from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .config import InstructionConfig
from .model import InstructionResolution, InstructionSource


@dataclass(slots=True, frozen=True)
class _CachedFile:
    mtime_ns: int
    size_bytes: int
    content: str
    sha256: str


@dataclass(slots=True, frozen=True)
class _Candidate:
    path: Path
    scope: Path | None
    level: str
    depth: int
    activation_order: int
    cached: _CachedFile


class ProjectInstructionLoader:
    """Discover scoped project instructions and fit them into a deterministic budget."""

    def __init__(self, workspace_root: str | Path, config: InstructionConfig) -> None:
        self.workspace_root = Path(workspace_root).resolve(strict=True)
        self.config = config
        self._cache: dict[Path, _CachedFile] = {}
        self._active_targets: dict[Path, int] = {}
        self._activation_counter = 0
        self.begin_run()

    def begin_run(self) -> None:
        self._active_targets.clear()
        self._activation_counter = 0
        self.activate_path(self.workspace_root)

    def activate_path(self, path: str | Path) -> Path:
        target = Path(path).resolve(strict=False)
        self._assert_inside_workspace(target)
        self._activation_counter += 1
        self._active_targets[target] = self._activation_counter
        return target

    def activate_paths(self, paths: list[str | Path] | tuple[str | Path, ...]) -> None:
        for path in paths:
            self.activate_path(path)

    def resolve(self) -> InstructionResolution:
        if not self.config.enabled:
            return InstructionResolution("", (), self.config.token_budget, 0, (), _digest([]))
        candidates = self._discover_candidates()
        intro_tokens = _estimate_tokens(_PROMPT_INTRO)
        allocated = self._allocate(candidates, max(0, self.config.token_budget - intro_tokens))
        prompt = self._render(allocated)
        used_tokens = _estimate_tokens(prompt)
        target_paths = tuple(
            self._display_workspace_path(path)
            for path, _ in sorted(self._active_targets.items(), key=lambda item: item[1])
        )
        digest = _digest(
            [
                self.config.token_budget,
                *target_paths,
                *[
                    [
                        str(source.path),
                        source.sha256,
                        source.status,
                        source.injected_tokens,
                    ]
                    for source in allocated
                ],
            ]
        )
        return InstructionResolution(
            prompt=prompt,
            sources=tuple(allocated),
            budget_tokens=self.config.token_budget,
            used_tokens=used_tokens,
            target_paths=target_paths,
            digest=digest,
        )

    def _discover_candidates(self) -> list[_Candidate]:
        values: list[_Candidate] = []
        seen: set[Path] = set()
        for index, directory in enumerate(self.config.global_directories):
            found = self._find_file(directory)
            if found is None or found in seen:
                continue
            cached = self._read_cached(found)
            if cached is None:
                continue
            seen.add(found)
            values.append(_Candidate(found, None, "global", -1, index, cached))

        directory_orders: dict[Path, int] = {self.workspace_root: 0}
        for target, activation_order in self._active_targets.items():
            scope = target if target.exists() and target.is_dir() else target.parent
            self._assert_inside_workspace(scope)
            current = scope
            while True:
                directory_orders[current] = max(directory_orders.get(current, 0), activation_order)
                if current == self.workspace_root:
                    break
                current = current.parent

        directories = sorted(
            directory_orders.items(),
            key=lambda item: (len(item[0].relative_to(self.workspace_root).parts), item[1], str(item[0])),
        )
        for directory, activation_order in directories:
            found = self._find_file(directory)
            if found is None or found in seen:
                continue
            cached = self._read_cached(found)
            if cached is None:
                continue
            seen.add(found)
            depth = len(directory.relative_to(self.workspace_root).parts)
            values.append(
                _Candidate(
                    found,
                    directory,
                    "workspace" if directory == self.workspace_root else "local",
                    depth,
                    activation_order,
                    cached,
                )
            )
        return values

    def _find_file(self, directory: Path) -> Path | None:
        for filename in self.config.filenames:
            path = (directory / filename).resolve(strict=False)
            if path.is_file():
                return path
        return None

    def _read_cached(self, path: Path) -> _CachedFile | None:
        try:
            stat = path.stat()
        except OSError:
            self._cache.pop(path, None)
            return None
        cached = self._cache.get(path)
        if cached and cached.mtime_ns == stat.st_mtime_ns and cached.size_bytes == stat.st_size:
            return cached
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            self._cache.pop(path, None)
            return None
        value = _CachedFile(
            mtime_ns=stat.st_mtime_ns,
            size_bytes=stat.st_size,
            content=content,
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        self._cache[path] = value
        return value

    def _allocate(
        self,
        candidates: list[_Candidate],
        available_tokens: int,
    ) -> list[InstructionSource]:
        remaining = available_tokens
        allocated: dict[Path, tuple[str, str, int]] = {}
        priority = sorted(
            candidates,
            key=lambda item: (
                0 if item.level == "global" else 1,
                item.depth,
                item.activation_order,
            ),
            reverse=True,
        )
        for candidate in priority:
            header = self._header(candidate)
            header_tokens = _estimate_tokens(header)
            content_tokens = _estimate_tokens(candidate.cached.content)
            total = header_tokens + content_tokens
            if remaining <= header_tokens:
                allocated[candidate.path] = ("omitted", "", 0)
                continue
            if total <= remaining:
                allocated[candidate.path] = ("included", candidate.cached.content, total)
                remaining -= total
                continue
            available_content_tokens = max(1, remaining - header_tokens)
            injected = _truncate_content(candidate.cached.content, available_content_tokens * 4)
            used = min(remaining, header_tokens + _estimate_tokens(injected))
            allocated[candidate.path] = ("truncated", injected, used)
            remaining -= used

        sources: list[InstructionSource] = []
        for candidate in candidates:
            status, injected, injected_tokens = allocated[candidate.path]
            sources.append(
                InstructionSource(
                    path=candidate.path,
                    display_path=self._display_source_path(candidate),
                    scope=candidate.scope,
                    display_scope=(
                        "all workspaces"
                        if candidate.scope is None
                        else self._display_workspace_path(candidate.scope)
                    ),
                    level=candidate.level,  # type: ignore[arg-type]
                    depth=candidate.depth,
                    sha256=candidate.cached.sha256,
                    mtime_ns=candidate.cached.mtime_ns,
                    size_bytes=candidate.cached.size_bytes,
                    estimated_tokens=_estimate_tokens(candidate.cached.content),
                    injected_tokens=injected_tokens,
                    status=status,  # type: ignore[arg-type]
                    content=candidate.cached.content,
                    injected_content=injected,
                )
            )
        return sources

    def _render(self, sources: list[InstructionSource]) -> str:
        visible = [source for source in sources if source.status != "omitted"]
        if not visible:
            return ""
        lines = [_PROMPT_INTRO]
        for source in visible:
            lines.extend(
                [
                    "",
                    f"## {source.display_path}",
                    f"Level: {source.level} | Applies to: {source.display_scope} | Status: {source.status}",
                    "",
                    source.injected_content.rstrip(),
                ]
            )
        return "\n".join(lines).rstrip()

    def _header(self, candidate: _Candidate) -> str:
        return (
            f"## {self._display_source_path(candidate)}\n"
            f"Level: {candidate.level} | Applies to: "
            f"{'all workspaces' if candidate.scope is None else self._display_workspace_path(candidate.scope)} "
            "| Status: truncated\n\n"
        )

    def _display_source_path(self, candidate: _Candidate) -> str:
        if candidate.scope is None:
            return str(candidate.path)
        return self._display_workspace_path(candidate.path)

    def _display_workspace_path(self, path: Path) -> str:
        relative = path.resolve(strict=False).relative_to(self.workspace_root)
        return relative.as_posix() or "."

    def _assert_inside_workspace(self, path: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError(f"instruction target is outside workspace: {path}") from exc


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def _truncate_content(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    marker = "\n\n[... instruction content truncated by token budget ...]\n\n"
    available = max(0, max_chars - len(marker))
    head = available * 2 // 3
    tail = available - head
    tail_text = content[-tail:].lstrip() if tail else ""
    return content[:head].rstrip() + marker + tail_text


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_PROMPT_INTRO = (
    "# Project Instructions\n\n"
    "Apply instructions only inside their stated scope. Within user/project guidance, priority is: "
    "user-global < workspace root < deeper local scope < current user request. When rules conflict, "
    "the more local higher-priority source wins for files in its scope."
)
