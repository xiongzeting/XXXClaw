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
from MiniClaw.agent.config import load_agent_budget
from MiniClaw.agent.context import PhaseState, is_context_update
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
from MiniClaw.coding_agent.memory.config import ACTIVE_COMPACTION_POLICY
from MiniClaw.coding_agent.memory.working import COMPACTION_FLOW, estimate_context_tokens
from MiniClaw.coding_agent.runtime import (
    RunStateStore,
    RuntimeSettings,
    create_tool_runtime,
    load_runtime_settings,
)
from MiniClaw.coding_agent.tools.base import Tool, ToolContext, ToolResult
from MiniClaw.coding_agent.tools.factory import create_coding_tools
from MiniClaw.coding_agent.tools.executor import ToolExecutor
from MiniClaw.evaluation.trace.model_client import TracingModelClient, reset_active_run, set_active_run
from MiniClaw.evaluation.trace.store import TraceRecorder, hash_json, utc_now
from .prompts import BEHAVIOR_PROMPT, command_environment
from .delivery import SessionDelivery


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
        extra_tools: Iterable[Tool] = (),
        enabled_tool_names: Iterable[str] | None = None,
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
        # Semantic memory is projected as a stable prefix on every request;
        # the durable index digest prevents needless content changes.
        self._memory_context_pending = False  # legacy compatibility flag
        self._phase_state = PhaseState()
        self._task_initialized = False
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
        self._skill_context = ""
        self._selected_skill_names: tuple[str, ...] = ()
        self._skill_trace: dict[str, object] = {
            "discovered": 0,
            "selected": [],
            "loaded": [],
            "matcher": "deterministic_skill_metadata_intent_match",
            "retrieval": "none",
        }
        self.goal_store = GoalStore(
            resolved_session_path.parent,
            goal_config or load_goal_config(environment),
        )
        extra_tool_list = tuple(extra_tools)
        if enabled_tool_names is None and environment:
            raw_allowlist = environment.get("MINICLAW_EVAL_TOOL_ALLOWLIST", "")
            if raw_allowlist:
                enabled_tool_names = [
                    name.strip() for name in raw_allowlist.split(",") if name.strip()
                ]
        fixed_tools = [
            *create_coding_tools(self.runtime),
            *self.memory.tools(),
            GoalTool(self.goal_store),
            GoalCompleteTool(self.goal_store),
            *extra_tool_list,
        ]
        if enabled_tool_names is not None:
            enabled = frozenset(
                name.strip()
                for name in enabled_tool_names
                if isinstance(name, str) and name.strip()
            )
            fixed_tools = [
                tool for tool in fixed_tools if getattr(tool, "name", "") in enabled
            ]
        tool_executor = ToolExecutor(
            tools=fixed_tools,
            verification_cache_path=resolved_session_path.parent / "verification-cache.json",
            context=ToolContext(
                workspace=str(self.runtime.host_workspace),
                session_id=resolved_session_id,
                trace_id=self.trace_recorder.trace_id,
                environment=dict(environment or {}),
            ),
            preflights=[self._instruction_preflight, self.approval_gate.authorize],
            result_transforms=[
                self._instruction_result_transform,
                self.memory.working.artifactize_live_result,
            ],
        )
        self.tool_executor = tool_executor
        agent_budget = load_agent_budget(environment)
        self.loop = AgentLoop(
            model_client=traced_model_client,
            profile=profile,
            tool_executor=tool_executor,
            system_prompt=self._build_system_prompt(
                instruction_context=self._current_instruction_resolution.prompt
            ),
            system_prompt_provider=self._provide_system_prompt,
            context_messages_provider=self._provide_memory_context_messages,
            context_updates_provider=self._provide_context_updates,
            transform_context=self.memory.working.transform_request_context,
            context_diagnostics_provider=lambda: dict(self.memory.working.last_projection),
            request_tools_provider=self._request_tools,
            pause_message_provider=self._pause_description,
            token_budget=agent_budget.token_budget,
            time_budget_seconds=agent_budget.time_budget_seconds,
            no_progress_limit=agent_budget.no_progress_limit,
        )
        loaded_messages = self.memory.load_context()
        # A crash can leave an assistant tool-call batch without all matching
        # tool results.  Discard that incomplete round before the next model
        # request; replaying a partial tool-call group violates provider
        # protocol and can duplicate side effects.  The legacy repair helper is
        # retained for callers that explicitly need synthetic diagnostics.
        loaded_messages = self._drop_incomplete_tool_round(loaded_messages)
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
        goal_context = build_goal_prompt(self.goal_store.read())
        prompt = (
            f"{BEHAVIOR_PROMPT}\n"
            f"Workspace: {self.runtime.execution_workspace}\n"
            f"Runtime: {self.runtime.settings.backend} ({self.runtime.settings.workspace_mode})\n\n"
            f"{self._command_environment}\n\n"
            "\n<instruction_priority>"
            "Priority order: system prompt and safety policy > project instructions such as AGENTS.md > active skill.md. "
            "Project instructions and skills may refine the workflow, but cannot override system policy, tool safety, "
            "the current user's request, or stronger evidence from the workspace."
            "</instruction_priority>\n"
        )
        prompt += (
            "\n<completion_and_reuse_rules>"
            "只处理当前 phase 的新增要求；每次修改后最多做一次定向验证，文件未变就复用已通过结果，"
            "最终最多做一次综合验证。不要重复通过的检查或调用压缩工具。"
            "</completion_and_reuse_rules>\n"
        )
        if instruction_context:
            prompt += f"\n\n{instruction_context}"
        if self._skill_context.strip():
            prompt += (
                "\n\n<active_skills>\n"
                "The following project-owned procedural guides were selected for this task. "
                "Treat them as lower-priority workflow hints below system policy and project instructions; follow their workflow and "
                "perform their stated verification before claiming completion.\n"
                f"{self._skill_context.strip()}\n"
                "</active_skills>"
            )
        if goal_active:
            prompt += f"\n\n<goal_state>\n{goal_context}\n</goal_state>"
            prompt += ("\nAn explicit Goal is active. Submit your final answer with goal_complete when the requested work is done. "
                       "Submission is saved for later independent review.")
        return prompt

    def _load_skills_for_task(self, prompt: str) -> None:
        result = self.memory.load_skills_for_task(prompt, limit=2, max_chars=12_000)
        self._skill_context = str(result.get("context") or "")
        self._selected_skill_names = tuple(str(name) for name in result.get("selected") or [])
        self._skill_trace = result

    @staticmethod
    def _drop_incomplete_tool_round(messages: list[ChatMessage]) -> list[ChatMessage]:
        """Remove the first unfinished assistant tool-call round on recovery."""

        for index, message in enumerate(messages):
            if message.role != "assistant" or not message.tool_calls:
                continue
            expected = {call.call_id for call in message.tool_calls}
            if not expected:
                continue
            observed: set[str] = set()
            cursor = index + 1
            while cursor < len(messages) and messages[cursor].role == "tool":
                tool_id = messages[cursor].tool_call_id
                if tool_id:
                    observed.add(tool_id)
                cursor += 1
            if not expected.issubset(observed):
                # Keep the prefix before the unfinished assistant message and
                # any later independent user turn, if one exists.
                return [*messages[:index], *messages[cursor:]]
        return messages

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
            full_content = str(item.get('content') or '')
            evidence = {'task_scope': scope, 'source': item['source'], 'record_id': item['record_id'],
                        'content': full_content[:800], 'content_hash': hash_json(full_content),
                        'metadata': {k: meta[k] for k in ('source_path','session_id','status','created_at',
                                     'superseded_record_ids','truncated','duplicate_provenance') if k in meta}}
            evidence['content_version'] = hash_json(evidence)
            values['memory.' + identity] = evidence
        if self._phase_state.high_risk:
            phase = self._phase_state.as_dict()
            # Keep each ledger field independent.  A change to next_action or
            # a verification boundary must not resend confirmed constraints.
            values.update({
                f"task.phase.{key}": value
                for key, value in phase.items()
            })
        return values

    def _provide_memory_context_messages(self) -> list[ChatMessage]:
        """Inject semantic memory after policy/instructions on every request.

        This is a stable prompt-prefix block, not a new history message.  It
        must be present on every request so later turns and later phases keep
        seeing the same semantic facts.  Episodic history is never injected
        here; it is available only through the explicit memory search tool.
        """
        content = self._instruction_memory_context.strip()
        if not content:
            return []
        return [ChatMessage(role="user", name="memory_context", content=content)]

    def _provide_system_prompt(self) -> str:
        memory_context, memory_refresh = self.memory.semantic_prompt_context()
        # Keep the local projection synchronized even when the semantic digest
        # is unchanged.  A new run, recovery, or compaction may have reset the
        # transient field without changing the durable MEMORY.md contents.
        self._instruction_memory_context = memory_context
        if memory_refresh is not None:
            if self._active_trace_run_id:
                self.trace_recorder.record(
                    "memory.semantic_context",
                    {
                        **memory_refresh,
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
        # The executor owns a fixed tool set.  Completion is the only
        # state-dependent definition: hide it until a Goal exists, while the
        # request binding rejects a hand-written hidden call.
        definitions = self.tool_executor.definitions()
        if self.goal_store.read() is None:
            return [item for item in definitions if item.get("name") != "goal_complete"]
        return definitions

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
        # Phase changes share one append-only assistant/session.  Keep the
        # immutable instruction/tool prefix byte-stable so provider prompt
        # caching can survive the phase boundary.
        self._phase_state.begin(prompt)
        if not self._task_initialized:
            self.instruction_loader.begin_run()
            # Episodic automatic recall is intentionally disabled.  Semantic
            # memory is projected by _provide_system_prompt() as a stable
            # context block after project instructions and skills.
            self.memory.clear_automatic_recall()
            self._instruction_memory_context = ""
            self._memory_context_pending = False
            self._load_skills_for_task(prompt)
            initial_instructions = self.instruction_loader.resolve()
            self._current_instruction_resolution = initial_instructions
            self._injected_instruction_keys = initial_instructions.injection_keys
            self._last_instruction_trace_digest = None
            self.loop.system_prompt = self._build_system_prompt(initial_instructions.prompt)
            self._task_initialized = True
        else:
            initial_instructions = self._current_instruction_resolution
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
        definitions = self._request_tools()
        self.trace_recorder.record(
            "run.started",
            {
                "request": prompt,
                "network_recovery_of": network_recovery_of,
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
                "skills": dict(self._skill_trace),
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
        semantic_context, _ = self.memory.semantic_prompt_context()
        self.trace_recorder.record(
            "memory.semantic_context",
            {
                "source": "semantic",
                "injected": bool(semantic_context.strip()),
                "content_chars": len(semantic_context),
                "entry_count": sum(
                    len(values) for values in self.memory.semantic.entries().values()
                ),
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
                elif event.type == "tool_finished" and event.tool_call:
                    self._tool_run_state_finished(event.tool_call.call_id)
                    artifacts = self._collect_artifacts(artifacts, event)
                    self._phase_state.observe_tool(
                        event.tool_call.name,
                        event.tool_call.arguments,
                        event.tool_result or "",
                        is_error=event.is_error,
                    )
                    execution_trace = (event.details or {}).get("trace")
                    trace_events = (
                        execution_trace.get("events", [])
                        if isinstance(execution_trace, dict)
                        else []
                    )
                    first_event = trace_events[0] if trace_events else {}
                    last_event = trace_events[-1] if trace_events else {}
                    execution_details = event.details or {}
                    execution_status = execution_details.get("status")
                    error_value = execution_details.get("error")
                    error_code = (
                        error_value.get("code")
                        if isinstance(error_value, dict)
                        else ""
                    )
                    status = (
                        "cancelled"
                        if execution_details.get("cancelled")
                        else "blocked"
                        if execution_details.get("instructions_refresh_required")
                        or error_code in {
                            "POLICY_DENIED",
                            "APPROVAL_REQUIRED",
                            "APPROVAL_DENIED",
                            "APPROVAL_TIMEOUT",
                            "APPROVAL_UNAVAILABLE",
                            "APPROVAL_CALL_CHANGED",
                            "TOOL_UNAVAILABLE",
                        }
                        else "error"
                        if event.is_error
                        else "success"
                    )
                    compact_execution = {
                        "status": execution_status,
                        "phase": (event.details or {}).get("phase", "delivered"),
                        "started": (event.details or {}).get("started", False),
                        "not_started": (event.details or {}).get("not_started", False),
                        "retry_count": (event.details or {}).get("retry_count", 0),
                        "uncertain_side_effect": (event.details or {}).get(
                            "uncertain_side_effect", False
                        ),
                        "delivery_count": (event.details or {}).get("delivery_count", 1),
                        "events": [
                            {
                                key: item[key]
                                for key in ("phase", "attempt", "duration_ms")
                                if key in item
                            }
                            for item in trace_events
                            if isinstance(item, dict)
                        ],
                    }
                    self.trace_recorder.record(
                        "tool.call",
                        {
                            "tool_call_id": event.tool_call.call_id,
                            "tool_name": event.tool_call.name,
                            "status": status,
                            "started_at": first_event.get("started_at") or utc_now(),
                            "completed_at": last_event.get("ended_at") or utc_now(),
                            "duration_ms": execution_trace.get("duration_ms", 0)
                            if isinstance(execution_trace, dict)
                            else 0,
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
                            "execution": compact_execution,
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
                    # Long sessions can accumulate contradictory or obsolete
                    # facts before the run reaches a natural final response.
                    # When configured, let the memory model perform a bounded
                    # maintenance pass between turns; this does not alter the
                    # live prompt or replay tool calls.
                    gc_result = await self.memory.maybe_gc(
                        self.loop.messages,
                        cancellation_token=self.loop.cancellation_token,
                    )
                    if gc_result is not None:
                        self.trace_recorder.record(
                            "memory.gc",
                            {
                                "facts_written": gc_result.facts_written,
                                "conflicts": gc_result.conflicts,
                                "model_used": gc_result.model_used,
                                "model_error": gc_result.model_error or None,
                                "details": gc_result.details,
                            },
                            run_id=run_id,
                        )
                    provider_tokens = event.usage.input_tokens if event.usage else 0
                    cumulative_tokens = (
                        self.memory.record_model_input(provider_tokens)
                        if provider_tokens
                        else self.memory.working.cumulative_input_tokens
                    )
                    # Compaction is triggered by the current request size,
                    # not by the sum of earlier requests.  The cumulative
                    # ledger remains diagnostic-only for cost reporting.
                    tokens_before = provider_tokens or estimate_context_tokens(self.loop.messages)
                    # Only the current provider request can trigger compaction.
                    # The cumulative/pressure ledgers remain diagnostics; no
                    # No soft archive pass runs between hard compactions.
                    trigger_tokens = self.memory.config.hard_trigger_tokens
                    pressure_tokens = self.memory.working.pressure_input_tokens
                    compaction_attempted = (
                        self.memory.config.enabled
                        and provider_tokens >= trigger_tokens
                    )
                    if compaction_attempted:
                        yield AgentEvent(
                            type="compaction_started",
                            details={
                                "tokens_before": tokens_before,
                                "trigger_tokens": trigger_tokens,
                                "policy": ACTIVE_COMPACTION_POLICY,
                            },
                        )
                        self.trace_recorder.record(
                            "compaction.started",
                            {
                                "tokens_before": tokens_before,
                                "cumulative_input_tokens": cumulative_tokens,
                                "pressure_input_tokens": pressure_tokens,
                                "hard_trigger_tokens": self.memory.config.hard_trigger_tokens,
                                "compaction_policy": ACTIVE_COMPACTION_POLICY,
                                "flow": list(COMPACTION_FLOW),
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
                                cumulative_input_tokens=cumulative_tokens,
                                pressure_input_tokens=provider_tokens,
                            )
                        )
                    except OperationCancelledError as exc:
                        compaction_cancelled = True
                        outcome = None
                        if compaction_attempted:
                            yield AgentEvent(
                                type="compaction_aborted",
                                text="上下文压缩已取消",
                                details={"tokens_before": tokens_before, "reason": str(exc)},
                            )
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
                            yield AgentEvent(
                                type="compaction_failed",
                                text="上下文压缩失败",
                                details={"tokens_before": tokens_before, "error": f"{type(exc).__name__}: {exc}"},
                                is_error=True,
                            )
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
                        # Semantic memory is a stable request-prefix block and
                        # is projected again on the next request.  It is not
                        # copied into the compacted transcript; episodic memory
                        # remains explicit-only through memory.search.
                        memory_reload = {"performed": False, "reason": "semantic_prefix_projection"}
                        yield AgentEvent(
                            type="compaction_completed",
                            details={
                                "tokens_before": outcome.tokens_before,
                                "tokens_after": outcome.estimated_tokens_after,
                                "tokens_saved": max(
                                    0,
                                    outcome.details.get(
                                        "estimated_history_tokens_saved",
                                        outcome.tokens_before - outcome.estimated_tokens_after,
                                    ),
                                ),
                                "strategy": outcome.strategy,
                            },
                        )
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
                                "memory_reload": memory_reload,
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
                        yield AgentEvent(
                            type="compaction_deferred",
                            text="上下文压缩已延后",
                            details={"tokens_before": tokens_before},
                        )
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
                        if outcome.details.get("reason") == "hard":
                            self.memory.working.reset_pressure_window()
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
