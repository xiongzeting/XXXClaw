from __future__ import annotations

import time
import json
import uuid
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Mapping

from MiniClaw.cancellation import OperationCancelledError
from MiniClaw.agent.events import AgentEvent
from MiniClaw.agent.loop import AgentLoop
from MiniClaw.agent.context import is_context_update
from MiniClaw.coding_agent.approval import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalHandler,
    ApprovalRequest,
    ApprovalSettings,
    load_approval_settings,
)
from MiniClaw.coding_agent.goal.config import (
    GoalConfig,
    GoalJudgeConfig,
    load_goal_config,
)
from MiniClaw.coding_agent.goal.prompts import build_goal_prompt, format_goal_status
from MiniClaw.coding_agent.goal.store import GoalStore
from MiniClaw.coding_agent.goal.supervisor import GoalSupervisor
from MiniClaw.coding_agent.goal.tools import GoalCompleteTool, GoalTool
from MiniClaw.coding_agent.instructions import (
    InstructionConfig,
    InstructionResolution,
    ProjectInstructionLoader,
    load_instruction_config,
)
from MiniClaw.llm.client import ModelClient
from MiniClaw.llm.types import ChatMessage, ModelProfile, ToolInvocation
from MiniClaw.coding_agent.memory.manager import MemoryManager
from MiniClaw.coding_agent.memory.working import estimate_context_tokens
from MiniClaw.coding_agent.runtime import (
    RunStateStore,
    RuntimeSettings,
    create_tool_runtime,
    load_runtime_settings,
)
from MiniClaw.coding_agent.tools.base import Tool, ToolResult
from MiniClaw.coding_agent.tools.factory import create_coding_tools
from MiniClaw.coding_agent.tools.manager import ToolManager, ToolRolePolicy
from MiniClaw.trace.model_client import TracingModelClient, reset_active_run, set_active_run
from MiniClaw.trace.store import TraceRecorder, hash_json, utc_now
from .prompts import BEHAVIOR_PROMPT, command_environment
from .delivery import SessionDelivery
from MiniClaw.coding_agent.runtime.capabilities import discover_execution_capabilities


class CodingAssistant:
    """Composition root for the coding product."""

    def __init__(
        self,
        model_client: ModelClient,
        profile: ModelProfile,
        workspace: str | Path,
        session_path: str | Path | None = None,
        session_id: str | None = None,
        goal_config: GoalConfig | None = None,
        goal_judge_config: GoalJudgeConfig | None = None,
        environment: Mapping[str, str] | None = None,
        runtime_settings: RuntimeSettings | None = None,
        approval_settings: ApprovalSettings | None = None,
        approval_handler: ApprovalHandler | None = None,
        trace_channel: str = "local",
        trace_provider: str = "openai-compatible",
        instruction_config: InstructionConfig | None = None,
        memory_user_scope: str = "",
        memory_channel_scope: str = "",
        role: str = "coding",
        extra_tools: Iterable[Tool] = (),
        role_tools: Mapping[str, Iterable[Tool]] | None = None,
        tool_role_policies: Mapping[
            str, ToolRolePolicy | Mapping[str, Iterable[str]]
        ]
        | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.profile = profile
        resolved_session_path = Path(
            session_path or self.workspace / ".aster" / "session.jsonl"
        )
        if not resolved_session_path.is_absolute():
            resolved_session_path = (self.workspace / resolved_session_path).resolve()
        resolved_session_id = session_id or resolved_session_path.stem
        self.task_progress = SessionDelivery(resolved_session_path.parent)
        self.role = role.strip() or "coding"
        self.runtime = create_tool_runtime(
            self.workspace,
            resolved_session_path.parent,
            resolved_session_id,
            runtime_settings or load_runtime_settings(environment),
        )
        self._command_environment = command_environment(self.runtime.settings.backend)
        if not (self.workspace / '.git').exists():
            self._command_environment += '\nThis workspace has no .git entry; do not assume git status/diff can inspect its changes.'
        elif self.runtime.settings.backend == 'docker':
            self._command_environment += '\nGit metadata is protected in the container; use file tools to inspect workspace changes.'
        self._execution_capabilities = discover_execution_capabilities(runtime=self.runtime.settings.backend)
        self.trace_recorder = TraceRecorder(
            resolved_session_path.parent,
            trace_channel,
            resolved_session_id,
        )
        self.run_state_store = RunStateStore(
            resolved_session_path.parent,
            resolved_session_id,
        )
        self.recovered_run_state = self.run_state_store.recover_interrupted()
        self.instruction_loader = ProjectInstructionLoader(
            self.runtime.host_workspace,
            instruction_config or load_instruction_config(environment),
        )
        self._instruction_memory_context = ""
        self._current_instruction_resolution = self.instruction_loader.resolve()
        self._injected_instruction_keys = self._current_instruction_resolution.injection_keys
        self._last_instruction_trace_digest: str | None = None
        self._active_trace_run_id: str | None = None
        self.approval_settings = approval_settings or load_approval_settings(
            self.workspace,
            environment,
        )
        self.approval_gate = ApprovalGate(
            workspace=self.runtime.workspace,
            settings=self.approval_settings,
            handler=approval_handler,
            recorder=self._record_approval_event,
        )
        traced_model_client = TracingModelClient(
            model_client,
            self.trace_recorder,
            provider=trace_provider,
        )
        self.model_client = traced_model_client
        self.memory = MemoryManager(
            workspace=self.workspace,
            session_path=resolved_session_path,
            session_id=resolved_session_id,
            model_client=traced_model_client,
            profile=profile,
            environment=environment,
            user_scope=memory_user_scope,
            channel_scope=memory_channel_scope,
        )
        self.goal_store = GoalStore(
            resolved_session_path.parent,
            goal_config or load_goal_config(environment),
        )
        tool_executor = ToolManager(
            active_role=self.role,
            allowed_names=frozenset({"write","bash","read","edit","grep","search","memory","goal","goal_complete","skill"}),
            role_policies=tool_role_policies,
            preflights=[self._instruction_preflight, self.approval_gate.authorize],
            result_transforms=[
                self._instruction_result_transform,
                self.memory.working.artifactize_live_result,
            ]
        )
        for tool in create_coding_tools(self.runtime):
            tool_executor.register(tool)
        for tool in self.memory.tools():
            tool_executor.register(tool)
        tool_executor.register(GoalTool(self.goal_store))
        tool_executor.register(GoalCompleteTool(self.goal_store))
        for tool in extra_tools:
            tool_executor.register(tool)
        for tool_role, tools in (role_tools or {}).items():
            tool_executor.inject(tool_role, tools)
        self.tool_executor = tool_executor
        self.loop = AgentLoop(
            model_client=traced_model_client,
            profile=profile,
            tool_executor=tool_executor,
            system_prompt=self._build_system_prompt(
                instruction_context=self._current_instruction_resolution.prompt
            ),
            system_prompt_provider=self._provide_system_prompt,
            context_updates_provider=self._provide_context_updates,
            transform_context=self.memory.working.transform_request_context,
            context_diagnostics_provider=lambda: dict(self.memory.working.last_projection),
            request_tools_provider=self._request_tools,
            pause_message_provider=self._pause_description,
        )
        loaded_messages = self.memory.load_context()
        repairs = self._repair_interrupted_tool_calls(loaded_messages)
        for message in repairs:
            self.memory.append(message)
        self.loop.messages = [*loaded_messages, *repairs]
        self.goal_supervisor = GoalSupervisor(
            self.goal_store,
            self._run_once,
            self._trace_goal_attempt_finished,
        )

    def _build_system_prompt(
        self,
        instruction_context: str = "",
    ) -> str:
        goal = self.goal_store.read()
        goal_active = bool(goal and goal.status in {"active", "verifying"})
        if hasattr(self, "tool_executor"):
            self.tool_executor.set_enabled("goal_complete", goal_active)
        definitions = self._request_tools() if hasattr(self, "tool_executor") else []
        tool_lines = "\n".join(self._format_tool_definition(item) for item in definitions)
        if not tool_lines:
            tool_lines = "- No tools are available for the active role."
        goal_context = build_goal_prompt(self.goal_store.read())
        prompt = (
            f"{BEHAVIOR_PROMPT}\n"
            f"Workspace: {self.runtime.execution_workspace}\n"
            f"Runtime: {self.runtime.settings.backend} ({self.runtime.settings.workspace_mode})\n\n"
            f"{self._command_environment}\n\n"
            f"Detected execution capabilities (paths verified at startup; use via available tools, "
            f"not extra registered tools; Docker host findings do not prove container availability): "
            f"{json.dumps(self._execution_capabilities, ensure_ascii=False)}\n\n"
            f"Active role: {self.role}\n\n"
            f"Available tools:\n{tool_lines}\n\n"
        )
        if any(item.get("name") == "skill" for item in definitions):
            catalog = self.memory.procedural.catalog_for_prompt()
            if catalog:
                prompt += f"\n{catalog}\n"
        if instruction_context:
            prompt += f"\n\n{instruction_context}"
        prompt += f"\n\n<goal_state>\n{goal_context}\n</goal_state>"
        if goal_active:
            prompt += ("\nAn explicit Goal is active. Submit your final answer with goal_complete when the requested work is done. "
                       "Submission is saved for later independent review.")
        else:
            prompt += "\nNo explicit Goal is active. Deliver a normal final response when done; it is saved for later review."
        return prompt

    @staticmethod
    def _format_tool_definition(definition: dict[str, object]) -> str:
        schema = definition.get("parameters")
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = set(schema.get("required", ())) if isinstance(schema, dict) else set()
        arguments = ", ".join(
            f"{name}{'' if name in required else '?'}" for name in properties
        )
        # Descriptions and parameter schemas are already sent in ModelRequest.tools.
        # The system inventory only needs the names/signatures for the active role.
        return f"- {definition.get('name')}({arguments})"

    @staticmethod
    def _repair_interrupted_tool_calls(messages: list[ChatMessage]) -> list[ChatMessage]:
        completed = {
            message.tool_call_id
            for message in messages
            if message.role == "tool" and message.tool_call_id
        }
        repairs: list[ChatMessage] = []
        seen: set[str] = set()
        for message in messages:
            if message.role != "assistant":
                continue
            for call in message.tool_calls:
                if call.call_id in completed or call.call_id in seen:
                    continue
                seen.add(call.call_id)
                repairs.append(
                    ChatMessage(
                        role="tool",
                        name=call.name,
                        tool_call_id=call.call_id,
                        content=(
                            "[INTERRUPTED] The previous process stopped before this tool call "
                            "was durably completed. Do not assume its side effects occurred; "
                            "inspect current workspace state before deciding whether to retry."
                        ),
                    )
                )
        return repairs

    def _provide_context_updates(self) -> dict:
        # Whole-source snapshots let the journal derive append-only changes and
        # explicit removals. Retrieval rank/score are not evidence versions.
        values = {}
        for item in self.memory.retrieval_trace():
            meta = item.get('metadata', {})
            scope = str(meta.get('session_id') or meta.get('source_path') or self.memory.session_id)
            identity = json.dumps([scope, item['source'], item['record_id']], ensure_ascii=False)
            evidence = {'task_scope': scope, 'source': item['source'], 'record_id': item['record_id'],
                        'content': item['content'],
                        'metadata': {k: meta[k] for k in ('source_path','session_id','status','created_at',
                                     'superseded_record_ids','truncated','duplicate_provenance') if k in meta}}
            evidence['content_version'] = hash_json(evidence)
            values['memory.' + identity] = evidence
        return values

    def _provide_system_prompt(self) -> str:
        memory_context, memory_refresh = self.memory.maybe_refresh_prompt_context(
            self.loop.messages
        )
        if memory_refresh is not None:
            self._instruction_memory_context = memory_context
            if self._active_trace_run_id:
                self.trace_recorder.record(
                    "memory.retrieval",
                    {
                        **memory_refresh,
                        "items": [
                            {
                                **item,
                                "content": str(item.get("content") or "")[:500],
                            }
                            for item in self.memory.retrieval_trace()
                        ],
                    },
                    run_id=self._active_trace_run_id,
                )
        resolution = self.instruction_loader.resolve()
        prompt = self._build_system_prompt(resolution.prompt)
        self.loop.system_prompt = prompt
        self._current_instruction_resolution = resolution
        self._injected_instruction_keys = resolution.injection_keys
        self._record_instruction_resolution(resolution, "dynamic_refresh")
        return prompt

    def _record_instruction_resolution(
        self,
        resolution: InstructionResolution,
        reason: str,
        *,
        force: bool = False,
    ) -> None:
        if not self._active_trace_run_id:
            return
        if not force and resolution.digest == self._last_instruction_trace_digest:
            return
        self.trace_recorder.record(
            "instructions.injected",
            {"reason": reason, **resolution.to_trace()},
            run_id=self._active_trace_run_id,
        )
        self._last_instruction_trace_digest = resolution.digest

    def _instruction_preflight(self, call: ToolInvocation) -> ToolResult | None:
        target = self._instruction_target(call)
        if target is None:
            return None
        self.instruction_loader.activate_path(target)
        resolution = self.instruction_loader.resolve()
        if call.name not in {"write", "edit"}:
            return None
        missing = resolution.relevant_injection_keys(target) - self._injected_instruction_keys
        if not missing:
            return None
        sources = [
            source.display_path
            for source in resolution.sources
            if source.injection_key in missing
        ]
        return ToolResult(
            content=(
                "[PROJECT_INSTRUCTIONS_REFRESH_REQUIRED] No file was modified. "
                "New or changed instructions apply to this target: "
                + ", ".join(sources)
                + ". Review the refreshed system instructions on the next turn, then retry the edit."
            ),
            is_error=True,
            details={
                "status": "instructions_refresh_required",
                "instructions_refresh_required": True,
                "target_path": self._display_instruction_target(target),
                "sources": sources,
                "resolution_digest": resolution.digest,
            },
        )

    async def _instruction_result_transform(
        self,
        call: ToolInvocation,
        result: ToolResult,
    ) -> ToolResult:
        if call.name != "grep" or result.is_error:
            return result
        paths = result.details.get("matchedPaths")
        if not isinstance(paths, list):
            return result
        activated: list[str] = []
        for raw_path in paths:
            if not isinstance(raw_path, str):
                continue
            try:
                path = self.runtime.workspace.resolve(
                    raw_path,
                    access="search",
                    must_exist=True,
                )
                self.instruction_loader.activate_path(path)
                activated.append(self._display_instruction_target(path))
            except (FileNotFoundError, PermissionError, ValueError):
                continue
        if activated:
            result.details = {
                **result.details,
                "instructionTargetsActivated": list(dict.fromkeys(activated)),
            }
        return result

    def _instruction_target(self, call: ToolInvocation) -> Path | None:
        if call.name not in {"read", "write", "edit", "grep"}:
            return None
        raw_path = call.arguments.get("path", "." if call.name == "grep" else None)
        if not isinstance(raw_path, str) or not raw_path:
            return None
        must_exist = call.name in {"read", "edit", "grep"}
        access = {
            "read": "read",
            "write": "write",
            "edit": "write",
            "grep": "search",
        }[call.name]
        return self.runtime.workspace.resolve(
            raw_path,
            access=access,  # type: ignore[arg-type]
            must_exist=must_exist,
        )

    def _display_instruction_target(self, path: Path) -> str:
        relative = path.resolve(strict=False).relative_to(self.runtime.host_workspace)
        return relative.as_posix() or "."

    def instruction_status(self) -> str:
        resolution = self.instruction_loader.resolve()
        if not resolution.sources:
            return f"Project instructions: none (budget {resolution.budget_tokens} tokens)"
        loaded = ", ".join(
            f"{source.display_path} [{source.status}]" for source in resolution.sources
        )
        return (
            f"Project instructions: {loaded} | "
            f"{resolution.used_tokens}/{resolution.budget_tokens} estimated tokens"
        )

    def _request_tools(self):
        return self.tool_executor.definitions()

    def _pause_description(self):
        return "\n执行预算已用尽，对话和工具结果已保存。任务未确认完成；恢复时先核对当前文件，继续未完成工作。"

    async def resume(self) -> AsyncIterator[AgentEvent]:
        """Resume a budget-paused session without inventing additional authorization."""
        if self.task_progress.state.get("status") != "paused":
            raise ValueError("No budget-paused task to resume")
        self.task_progress.save()
        async for event in self._dispatch_run(
            "继续上次因执行预算暂停的任务。遵守原用户授权与最新约束，从检查点的下一步恢复，"
            "复用已完成结果；文件可能已改变时先核实，不要重新执行已成功的写入。"
        ):
            yield event

    async def run(self, prompt: str) -> AsyncIterator[AgentEvent]:
        if self.loop.is_running:
            raise RuntimeError('agent is already running')
        self.task_progress.begin_user_turn(prompt)
        async for event in self._dispatch_run(prompt):
            yield event

    async def _dispatch_run(self, prompt: str) -> AsyncIterator[AgentEvent]:
        state = self.goal_store.read()
        if state and state.status in {"active", "verifying"}:
            async for event in self.goal_supervisor.run(prompt):
                yield event
            return
        async for event in self._run_once(prompt):
            yield event

    async def run_goal(self, initial_prompt: str | None = None) -> AsyncIterator[AgentEvent]:
        if self.loop.is_running:
            raise RuntimeError('agent is already running')
        if initial_prompt is not None:
            self.task_progress.begin_user_turn(initial_prompt)
        async for event in self.goal_supervisor.run(initial_prompt):
            yield event

    async def retry_network(self) -> AsyncIterator[AgentEvent]:
        """Retry only the interrupted model boundary; keep tool results and the turn budget."""
        from MiniClaw.llm.recovery import is_network_error
        previous = self.run_state_store.read()
        if (previous is None or previous.status != "failed"
                or not is_network_error({"error": previous.error})
                or self.loop.pending_request is None):
            raise ValueError("No safely retryable interrupted model request in this assistant")
        async for event in self._run_once(
            previous.prompt, network_recovery_of=previous.run_id, start_turn=max(1, previous.turn)
        ):
            yield event

    def create_goal(
        self,
        description: str,
        acceptance_criteria: list[str],
        requested_by: str | None = None,
    ):
        return self.goal_store.create(description, acceptance_criteria, requested_by)

    def goal_status(self) -> str:
        return format_goal_status(self.goal_store.read())

    def cancel(self, reason: str = "Task cancelled by the user") -> bool:
        cancelled = self.loop.cancel(reason)
        state = self.goal_store.read()
        if state and state.status in {"active", "verifying", "waiting_for_user"}:
            self.goal_store.cancel(reason)
            cancelled = True
        if cancelled and self._active_trace_run_id:
            self.trace_recorder.record(
                "run.cancelled",
                {"status": "cancelled", "reason": reason},
                run_id=self._active_trace_run_id,
            )
        return cancelled

    def record_approval(
        self,
        *,
        tool_name: str,
        risk: str,
        reason: str,
        preview: str,
        decision: str,
    ) -> None:
        if self._active_trace_run_id:
            self.trace_recorder.record_approval(
                run_id=self._active_trace_run_id,
                tool_name=tool_name,
                risk=risk,
                reason=reason,
                preview=preview,
                decision=decision,
            )

    def _record_approval_event(
        self,
        phase: str,
        request: ApprovalRequest,
        decision: ApprovalDecision | None,
    ) -> None:
        if not self._active_trace_run_id:
            return
        if phase == "requested":
            self.trace_recorder.record_approval_request(
                run_id=self._active_trace_run_id,
                approval_id=request.approval_id,
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                risk=request.risk,
                level=request.level,
                reason=request.reason,
                preview=request.preview,
                capabilities=request.capabilities,
                normalized_call_hash=request.normalized_call_hash,
            )
            return
        if decision is not None:
            self.trace_recorder.record_approval(
                run_id=self._active_trace_run_id,
                approval_id=request.approval_id,
                tool_call_id=request.tool_call_id,
                tool_name=request.tool_name,
                risk=request.risk,
                level=request.level,
                reason=request.reason,
                preview=request.preview,
                decision=decision,
                capabilities=request.capabilities,
                normalized_call_hash=request.normalized_call_hash,
            )

    def _trace_goal_attempt_finished(self, state, run_id: str | None) -> None:
        self.trace_recorder.record(
            "goal.snapshot",
            {"phase": "attempt_finished", "goal": state.to_dict()},
            run_id=run_id,
        )

    async def _run_once(self, prompt: str, *, network_recovery_of: str | None = None,
                        start_turn: int = 1) -> AsyncIterator[AgentEvent]:
        self.instruction_loader.begin_run()
        self._instruction_memory_context = self.memory.prompt_context(prompt)
        initial_instructions = self.instruction_loader.resolve()
        self._current_instruction_resolution = initial_instructions
        self._injected_instruction_keys = initial_instructions.injection_keys
        self._last_instruction_trace_digest = None
        self.loop.system_prompt = self._build_system_prompt(initial_instructions.prompt)
        run_id = self.trace_recorder.new_run_id()
        self._active_trace_run_id = run_id
        run_state_error = ""
        try:
            self.run_state_store.begin(run_id, prompt)
        except Exception as exc:
            run_state_error = f"{type(exc).__name__}: {exc}"
        started_at = utc_now()
        started = time.perf_counter()
        goal_before = self.goal_store.read()
        last_goal_hash = hash_json(goal_before.to_dict() if goal_before else None)
        definitions = self.tool_executor.definitions()
        self.trace_recorder.record(
            "run.started",
            {
                "request": prompt,
                "network_recovery_of": network_recovery_of,
                "role": self.role,
                "provider": getattr(self.model_client, "provider", "openai-compatible"),
                "model": self.profile.model_id,
                "system_prompt_sha256": hash_json(self.loop.system_prompt),
                "tool_config_sha256": hash_json(definitions),
                "tools": [definition["name"] for definition in definitions],
                "runtime": self.runtime.trace_metadata(),
                "approval": {
                    "policy": self.approval_settings.policy,
                    "timeout_seconds": self.approval_settings.timeout_seconds,
                    "allowlist_rules": len(self.approval_settings.allowlist),
                    "config_path": self.approval_settings.config_path,
                },
                "goal": goal_before.to_dict() if goal_before else None,
                "instructions": {
                    "digest": initial_instructions.digest,
                    "budget_tokens": initial_instructions.budget_tokens,
                    "used_tokens": initial_instructions.used_tokens,
                    "source_count": len(initial_instructions.sources),
                },
                "recovery": (
                    self.recovered_run_state.to_dict()
                    if self.recovered_run_state
                    and self.recovered_run_state.status == "interrupted"
                    else None
                ),
                "run_state_error": run_state_error or None,
            },
            run_id=run_id,
            timestamp=started_at,
        )
        self.trace_recorder.record(
            "memory.retrieval",
            {
                "query": prompt,
                "injected_count": self.memory.last_rendered_count,
                **self.memory.last_render_stats,
                "items": [
                    {
                        **item,
                        "content": str(item.get("content") or "")[:500],
                    }
                    for item in self.memory.retrieval_trace()
                ],
            },
            run_id=run_id,
        )
        self._record_instruction_resolution(initial_instructions, "run_started", force=True)
        self.recovered_run_state = None
        self.trace_recorder.record(
            "goal.snapshot",
            {"phase": "run_started", "goal": goal_before.to_dict() if goal_before else None},
            run_id=run_id,
        )
        token = set_active_run(run_id)
        tool_starts: dict[str, tuple[str, float]] = {}
        final_text = ""
        artifacts: list[dict[str, object]] = []
        stop_reason = "aborted"
        pause_reason = None
        run_error = False
        error_text = ""
        try:
            async for event in self.loop.run(None if network_recovery_of else prompt, start_turn=start_turn):
                if event.type == "turn_started":
                    turn = int((event.details or {}).get("turn") or 0)
                    self._transition_run_state("waiting_model", turn=turn)
                elif event.type == "tool_started" and event.tool_call:
                    self._tool_run_state_started(event.tool_call.call_id, event.tool_call.name)
                    tool_starts[event.tool_call.call_id] = (utc_now(), time.perf_counter())
                elif event.type == "tool_finished" and event.tool_call:
                    self._tool_run_state_finished(event.tool_call.call_id)
                    artifacts = self._collect_artifacts(artifacts, event)
                    tool_started_at, tool_started = tool_starts.pop(
                        event.tool_call.call_id,
                        (utc_now(), time.perf_counter()),
                    )
                    self.trace_recorder.record(
                        "tool.call",
                        {
                            "tool_call_id": event.tool_call.call_id,
                            "tool_name": event.tool_call.name,
                            "status": (
                                "cancelled"
                                if event.details and event.details.get("cancelled")
                                else "blocked"
                                if event.details
                                and event.details.get("instructions_refresh_required")
                                else "error"
                                if event.is_error
                                else "success"
                            ),
                            "started_at": tool_started_at,
                            "completed_at": utc_now(),
                            "duration_ms": round((time.perf_counter() - tool_started) * 1000),
                            "arguments": event.tool_call.arguments,
                            "result": (
                                event.tool_result
                                if not event.is_error
                                and not (event.details and event.details.get("cancelled"))
                                else None
                            ),
                            "error": event.tool_result if event.is_error else None,
                            "cancellation_reason": (
                                event.details.get("reason")
                                if event.details and event.details.get("cancelled")
                                else None
                            ),
                            "details": event.details,
                        },
                        run_id=run_id,
                    )
                    current_goal = self.goal_store.read()
                    current_goal_value = current_goal.to_dict() if current_goal else None
                    current_goal_hash = hash_json(current_goal_value)
                    if current_goal_hash != last_goal_hash:
                        self.trace_recorder.record(
                            "goal.snapshot",
                            {
                                "phase": "after_tool",
                                "tool_call_id": event.tool_call.call_id,
                                "tool_name": event.tool_call.name,
                                "goal": current_goal_value,
                            },
                            run_id=run_id,
                        )
                        last_goal_hash = current_goal_hash
                if event.type == "message_added" and event.message is not None:
                    self.memory.append(event.message)
                    if is_context_update(event.message):
                        # Retained in session and request Trace, not a chat reply.
                        continue
                elif event.type == "turn_finished":
                    provider_tokens = event.usage.input_tokens if event.usage else 0
                    tokens_before = provider_tokens or estimate_context_tokens(self.loop.messages)
                    trigger_tokens = (
                        self.memory.config.hard_trigger_tokens
                        if self.memory.config.strategy == "legacy-summary-recent"
                        else self.memory.config.soft_trigger_tokens
                    )
                    compaction_attempted = (
                        self.memory.config.enabled
                        and tokens_before > trigger_tokens
                    )
                    if compaction_attempted:
                        self.trace_recorder.record(
                            "compaction.started",
                            {
                                "tokens_before": tokens_before,
                                "soft_trigger_tokens": self.memory.config.soft_trigger_tokens,
                                "hard_trigger_tokens": self.memory.config.hard_trigger_tokens,
                            },
                            run_id=run_id,
                        )
                    active_token = self.loop.cancellation_token
                    compaction_cancelled = bool(active_token and active_token.cancelled)
                    compaction_cancellation_recorded = False
                    try:
                        outcome = (
                            None
                            if compaction_cancelled
                            else await self.memory.maybe_compact(
                                self.loop.messages,
                                provider_tokens,
                                active_token,
                            )
                        )
                    except OperationCancelledError as exc:
                        compaction_cancelled = True
                        outcome = None
                        if compaction_attempted:
                            self.trace_recorder.record(
                                "compaction.aborted",
                                {
                                    "status": "cancelled",
                                    "reason": str(exc),
                                    "tokens_before": tokens_before,
                                },
                                run_id=run_id,
                            )
                            compaction_cancellation_recorded = True
                    except Exception as exc:
                        if compaction_attempted:
                            self.trace_recorder.record(
                                "compaction.failed",
                                {
                                    "status": "failed",
                                    "tokens_before": tokens_before,
                                    "error": f"{type(exc).__name__}: {exc}",
                                },
                                run_id=run_id,
                            )
                        raise
                    if outcome:
                        self.loop.messages = outcome.messages
                        self.trace_recorder.record(
                            "compaction.completed",
                            {
                                "status": "completed",
                                "strategy": outcome.strategy,
                                "reason": outcome.details.get("reason"),
                                "tokens_before": outcome.tokens_before,
                                "tokens_after": outcome.estimated_tokens_after,
                                "tokens_saved": max(
                                    0,
                                    outcome.details.get(
                                        "estimated_history_tokens_saved",
                                        outcome.tokens_before - outcome.estimated_tokens_after,
                                    ),
                                ),
                                "details": outcome.details,
                            },
                            run_id=run_id,
                        )
                    elif (
                        compaction_attempted
                        and compaction_cancelled
                        and not compaction_cancellation_recorded
                    ):
                        self.trace_recorder.record(
                            "compaction.aborted",
                            {
                                "status": "cancelled",
                                "reason": active_token.reason if active_token else "cancelled",
                                "tokens_before": tokens_before,
                            },
                            run_id=run_id,
                        )
                    elif compaction_attempted:
                        self.trace_recorder.record(
                            "compaction.deferred",
                            {
                                "status": "deferred",
                                **self.memory.working.last_compaction_decision,
                                "tokens_before": tokens_before,
                            },
                            run_id=run_id,
                        )
                elif event.type == "run_finished":
                    self._transition_run_state("finalizing")
                    run_error = event.is_error
                    if event.message and event.message.content.strip():
                        final_text = event.message.content.strip()
                    if event.details:
                        stop_reason = str(event.details.get("stop_reason") or stop_reason)
                        error_text = str(event.details.get("error") or "")
                    self.task_progress.finish(stop_reason, self.loop.messages)
                    memory_status = self._memory_session_status(
                        stop_reason=stop_reason,
                        run_error=event.is_error,
                    )
                    try:
                        consolidation = await self.memory.finalize_session(
                            self.loop.messages,
                            status=memory_status,
                            cancellation_token=self.loop.cancellation_token,
                        )
                        self.trace_recorder.record(
                            "memory.consolidation",
                            {
                                "status": memory_status,
                                "facts_written": consolidation.facts_written,
                                "facts_rejected": consolidation.facts_rejected,
                                "conflicts": consolidation.conflicts,
                                "procedure_candidates": consolidation.procedure_candidates,
                                "model_used": consolidation.model_used,
                                "model_error": consolidation.model_error or None,
                                "details": consolidation.details,
                            },
                            run_id=run_id,
                        )
                    except Exception as exc:
                        self.trace_recorder.record(
                            "memory.consolidation",
                            {
                                "status": "failed",
                                "session_status": memory_status,
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                            run_id=run_id,
                        )
                    metrics = self.trace_recorder.run_metrics(run_id)
                    event.details = {
                        **(event.details or {}),
                        "trace_id": self.trace_recorder.trace_id,
                        "run_id": run_id,
                        "cost_usd": metrics["usage"]["cost_usd"],
                        "artifacts": artifacts,
                    }
                yield event
        except Exception as exc:
            run_error = True
            stop_reason = "error"
            error_text = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            reset_active_run(token)
            goal_after = self.goal_store.read()
            self.trace_recorder.record(
                "goal.snapshot",
                {"phase": "run_completed", "goal": goal_after.to_dict() if goal_after else None},
                run_id=run_id,
            )
            metrics = self.trace_recorder.run_metrics(run_id)
            self.trace_recorder.record(
                "run.completed",
                {
                    "status": (
                        "error"
                        if run_error
                        else "cancelled"
                        if stop_reason == "aborted"
                        else "paused"
                        if stop_reason == "budget_exhausted"
                        else "success"
                    ),
                    "stop_reason": stop_reason,
                    "pause_reason": pause_reason,
                    "error": error_text or None,
                    "final_text": final_text,
                    "artifacts": artifacts,
                    "started_at": started_at,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                    "usage": metrics["usage"],
                    "process": {
                        "model_requests": metrics["model_requests"],
                        "model_errors": metrics["model_errors"],
                        "model_retries": metrics["model_retries"],
                        "model_fallbacks": metrics["model_fallbacks"],
                        "tool_calls": metrics["tool_calls"],
                        "tool_errors": metrics["tool_errors"],
                        "tool_cancelled": metrics["tool_cancelled"],
                        "compactions": metrics["compactions"],
                        "tokens_saved_by_compaction": metrics["tokens_saved_by_compaction"],
                    },
                    "goal": goal_after.to_dict() if goal_after else None,
                },
                run_id=run_id,
            )
            self.trace_recorder.close_run(run_id)
            terminal_status = (
                "failed"
                if run_error
                else "cancelled"
                if stop_reason == "aborted"
                else "paused"
                if stop_reason == "budget_exhausted"
                else "completed"
            )
            self._transition_run_state(
                terminal_status,
                stop_reason=stop_reason,
                error=error_text or None,
                active_tool_call_id=None,
                active_tool_name=None,
            )
            self.task_progress.submit(run_id, final_text, terminal_status, stop_reason,
                                      error_text or None, self.task_progress.directory / 'trace.jsonl')
            self._active_trace_run_id = None

    def _collect_artifacts(
        self,
        current: list[dict[str, object]],
        event: AgentEvent,
    ) -> list[dict[str, object]]:
        if (
            event.tool_call is None
            or event.is_error
            or bool((event.details or {}).get("cancelled"))
            or bool((event.details or {}).get("instructions_refresh_required"))
        ):
            return current
        candidates: list[dict[str, object]] = []
        details = event.details or {}
        context_artifact = details.get("context_artifact")
        if isinstance(context_artifact, dict) and isinstance(context_artifact.get("path"), str):
            candidates.append({"kind": "tool_result", **context_artifact})
        full_output = details.get("fullOutputPath")
        if isinstance(full_output, str):
            candidates.append(
                {
                    "kind": "tool_output",
                    "path": full_output,
                    "tool_name": event.tool_call.name,
                    "tool_call_id": event.tool_call.call_id,
                }
            )
        if event.tool_call.name in {"write", "edit"}:
            path = event.tool_call.arguments.get("path")
            if isinstance(path, str):
                candidates.append(
                    {
                        "kind": "workspace_file",
                        "path": path,
                        "tool_name": event.tool_call.name,
                        "tool_call_id": event.tool_call.call_id,
                    }
                )
        known = {str(item.get("path")) for item in current}
        return [
            *current,
            *(item for item in candidates if str(item.get("path")) not in known),
        ]

    def _transition_run_state(self, status, **changes) -> None:
        try:
            self.run_state_store.transition(status, **changes)
        except Exception as exc:
            if self._active_trace_run_id:
                self.trace_recorder.record(
                    "run.state_error",
                    {
                        "status": status,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    run_id=self._active_trace_run_id,
                )

    def _tool_run_state_started(self, call_id: str, name: str) -> None:
        try:
            self.run_state_store.tool_started(call_id, name)
        except Exception as exc:
            self._transition_run_state(
                "executing_tool",
                active_tool_call_id=call_id,
                active_tool_name=name,
                error=f"run state update failed: {type(exc).__name__}: {exc}",
            )

    def _tool_run_state_finished(self, call_id: str) -> None:
        try:
            self.run_state_store.tool_finished(call_id)
        except Exception as exc:
            self._transition_run_state(
                "waiting_model",
                active_tool_call_id=None,
                active_tool_name=None,
                error=f"run state update failed: {type(exc).__name__}: {exc}",
            )

    def _memory_session_status(self, *, stop_reason: str, run_error: bool) -> str:
        if stop_reason == "budget_exhausted":
            return "active"
        if stop_reason == "aborted":
            return "cancelled"
        state = self.goal_store.read()
        if state is not None:
            if state.status == "complete":
                return "completed"
            if state.status in {"failed", "cancelled"}:
                return state.status
            return "active"
        return "failed" if run_error else "completed"
