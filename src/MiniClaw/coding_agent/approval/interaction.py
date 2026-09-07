from __future__ import annotations

import asyncio
import re
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .models import ApprovalRequest


@dataclass(slots=True, frozen=True)
class ApprovalResponse:
    action: str
    approval_id: str


@dataclass(slots=True)
class _PendingApproval:
    session_key: str
    user_id: str
    request: ApprovalRequest
    future: asyncio.Future[bool]


class ApprovalInbox:
    def __init__(self) -> None:
        self._pending: dict[str, _PendingApproval] = {}

    async def wait(
        self,
        session_key: str,
        user_id: str,
        request: ApprovalRequest,
        notify: Callable[[str], Awaitable[None]],
    ) -> bool:
        loop = asyncio.get_running_loop()
        pending = _PendingApproval(session_key, user_id, request, loop.create_future())
        if request.approval_id in self._pending:
            raise RuntimeError(f"duplicate approval id: {request.approval_id}")
        self._pending[request.approval_id] = pending
        try:
            await notify(format_approval_request(request))
            return await pending.future
        finally:
            self._pending.pop(request.approval_id, None)

    def resolve(self, session_key: str, user_id: str, response: ApprovalResponse) -> str:
        pending = self._pending.get(response.approval_id)
        if pending is None or pending.session_key != session_key:
            return "not-found"
        if pending.user_id and pending.user_id != user_id:
            return "wrong-user"
        if pending.future.done():
            return "already-decided"
        pending.future.set_result(response.action == "approve")
        return response.action


def parse_approval_response(text: str) -> ApprovalResponse | None:
    normalized = text.strip().strip("`").strip()
    match = re.fullmatch(r"(approve|deny|批准|拒绝)\s+([a-f0-9]{6})", normalized, re.IGNORECASE)
    if not match:
        return None
    action = "approve" if match.group(1).lower() == "approve" or match.group(1) == "批准" else "deny"
    return ApprovalResponse(action, match.group(2).upper())


def format_approval_request(request: ApprovalRequest) -> str:
    capabilities = ", ".join(request.capabilities)
    return (
        f"需要审批 `{request.approval_id}`\n"
        f"工具：{request.tool_name}\n"
        f"风险：{request.level} / {request.risk}\n"
        f"能力集合：{capabilities}\n"
        f"原因：{request.reason}\n"
        f"预览：\n{request.preview}\n\n"
        f"批准请输入：批准 {request.approval_id}\n"
        f"拒绝请输入：拒绝 {request.approval_id}"
    )


async def cli_approval_handler(
    request: ApprovalRequest,
    timeout_seconds: float = 300.0,
) -> bool:
    print(f"\n{format_approval_request(request)}")
    while True:
        response = await asyncio.to_thread(
            _read_line_with_timeout,
            "approval> ",
            timeout_seconds,
        )
        if response is None:
            raise asyncio.TimeoutError
        parsed = parse_approval_response(response)
        if parsed and parsed.approval_id == request.approval_id:
            return parsed.action == "approve"
        print(f"请输入“批准 {request.approval_id}”或“拒绝 {request.approval_id}”。")


def _read_line_with_timeout(prompt: str, timeout_seconds: float) -> str | None:
    if sys.platform == "win32" and sys.stdin.isatty():
        return _read_windows_line(prompt, timeout_seconds)
    if sys.stdin.isatty():
        try:
            import select

            print(prompt, end="", flush=True)
            ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
            if not ready:
                print()
                return None
            return sys.stdin.readline().rstrip("\r\n")
        except (ImportError, OSError, ValueError):
            pass
    print(prompt, end="", flush=True)
    return sys.stdin.readline().rstrip("\r\n")


def _read_windows_line(prompt: str, timeout_seconds: float) -> str | None:
    import msvcrt

    print(prompt, end="", flush=True)
    deadline = time.monotonic() + timeout_seconds
    characters: list[str] = []
    while time.monotonic() < deadline:
        if not msvcrt.kbhit():
            time.sleep(0.05)
            continue
        character = msvcrt.getwch()
        if character in {"\r", "\n"}:
            print()
            return "".join(characters)
        if character == "\003":
            raise KeyboardInterrupt
        if character == "\b":
            if characters:
                characters.pop()
                print("\b \b", end="", flush=True)
            continue
        if character in {"\x00", "\xe0"}:
            if msvcrt.kbhit():
                msvcrt.getwch()
            continue
        characters.append(character)
        print(character, end="", flush=True)
    print()
    return None
