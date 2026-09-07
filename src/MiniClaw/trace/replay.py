from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from MiniClaw.llm.config import load_llm_settings
from MiniClaw.llm.client import ModelClient
from MiniClaw.llm.factory import create_model_client
from MiniClaw.llm.types import (
    AssistantReply,
    ChatMessage,
    ModelProfile,
    ModelRequest,
    ToolInvocation,
)

from .analysis import load_trace_runs
from .store import sanitize_trace_value, usage_dict, utc_now


async def replay_trace(
    session_dir: str | Path,
    *,
    environment: Mapping[str, str],
    output_path: str | Path,
    run_id: str | None = None,
    provider: str | None = None,
    model_id: str | None = None,
    base_url: str | None = None,
    prompt_path: str | Path | None = None,
    tool_config_path: str | Path | None = None,
    eval_case_path: str | Path | None = None,
    results_path: str | Path | None = None,
    model_client: ModelClient | None = None,
) -> dict[str, Any]:
    runs = load_trace_runs(session_dir)
    run = next((item for item in runs if item["run_id"] == run_id), None) if run_id else (runs[-1] if runs else None)
    if run is None:
        raise ValueError(f"Trace run not found: {run_id}" if run_id else "No trace runs found")
    eval_case = _read_json(Path(eval_case_path)) if eval_case_path else None
    requests = run.get("model_requests", [])
    if isinstance(eval_case, dict):
        request_ids = {
            str(item.get("request_id"))
            for item in eval_case.get("replay_boundaries", [])
            if isinstance(item, dict) and item.get("request_id")
        }
        if request_ids:
            requests = [
                record
                for record in requests
                if str((record.get("data") or {}).get("request_id")) in request_ids
            ]
    if not requests:
        raise ValueError(f"Trace run {run['run_id']} contains no replayable model requests")
    settings = load_llm_settings(
        provider=provider,
        model_id=model_id,
        base_url=base_url,
        environment=environment,
    )
    profile = ModelProfile(
        model_id=settings.model_id,
        context_window=settings.context_window,
        max_output_tokens=settings.max_output_tokens,
        input_cost_per_million=settings.input_cost_per_million,
        output_cost_per_million=settings.output_cost_per_million,
        cached_input_cost_per_million=settings.cached_input_cost_per_million,
    )
    client = model_client or create_model_client(settings)
    prompt_override = Path(prompt_path).read_text(encoding="utf-8") if prompt_path else None
    tool_selection = _read_json(Path(tool_config_path)) if tool_config_path else None
    boundaries: list[dict[str, Any]] = []
    replayed_contents: list[str] = []
    for record in requests:
        data = record.get("data") or {}
        context = data.get("context") or {}
        messages = [_message_from_dict(value) for value in context.get("messages", []) if isinstance(value, dict)]
        if prompt_override is not None:
            messages = _replace_system_prompt(messages, prompt_override)
        tools = _filter_tools(context.get("tools", []), tool_selection)
        request = ModelRequest(
            profile=profile,
            messages=messages,
            tools=tools,
            temperature=context.get("temperature") if isinstance(context.get("temperature"), (int, float)) else None,
            metadata={"purpose": "trace_replay", "source_request_id": data.get("request_id")},
        )
        started_at = utc_now()
        started = time.perf_counter()
        reply: AssistantReply | None = None
        error_text: str | None = None
        try:
            async for event in client.stream(request):
                if event.type == "error" and event.error:
                    error_text = event.error
                elif event.type == "completed":
                    reply = event.reply
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
        usage = reply.usage if reply else None
        reply_metadata = reply.metadata if reply else {}
        input_price = reply_metadata.get("input_cost_per_million")
        output_price = reply_metadata.get("output_cost_per_million")
        cached_price = reply_metadata.get("cached_input_cost_per_million")
        recorded_output = data.get("output") if isinstance(data.get("output"), dict) else None
        recorded_calls = _tool_call_names(recorded_output)
        replayed_calls = [call.name for call in reply.tool_calls] if reply else []
        replayed_contents.append(reply.content if reply else "")
        boundaries.append(
            {
                "request_id": data.get("request_id"),
                "purpose": data.get("purpose"),
                "provider": reply_metadata.get("provider", settings.provider),
                "model": reply_metadata.get("model", settings.model_id),
                "started_at": started_at,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "stop_reason": reply.stop_reason if reply else "error",
                "error": error_text or (reply.error if reply else None),
                "usage": usage_dict(
                    usage.input_tokens if usage else 0,
                    usage.output_tokens if usage else 0,
                    usage.cached_tokens if usage else 0,
                    input_cost_per_million=(
                        float(input_price)
                        if isinstance(input_price, (int, float))
                        else profile.input_cost_per_million
                    ),
                    output_cost_per_million=(
                        float(output_price)
                        if isinstance(output_price, (int, float))
                        else profile.output_cost_per_million
                    ),
                    cached_input_cost_per_million=(
                        float(cached_price)
                        if isinstance(cached_price, (int, float))
                        else profile.cached_input_cost_per_million
                    ),
                ),
                "recorded_stop_reason": data.get("stop_reason"),
                "recorded_tool_calls": recorded_calls,
                "replayed_tool_calls": replayed_calls,
                "tool_call_sequence_match": recorded_calls == replayed_calls,
                "text_sha256": _hash_text(reply.content if reply else ""),
                "recorded_text_sha256": _hash_text(str(recorded_output.get("content", ""))) if recorded_output else None,
            }
        )
    report = {
        "version": 1,
        "created_at": utc_now(),
        "mode": "model-boundary-replay",
        "trace_id": run["trace_id"],
        "run_id": run["run_id"],
        "provider": settings.provider,
        "model": settings.model_id,
        "prompt_file": str(Path(prompt_path).resolve()) if prompt_path else None,
        "tool_config_file": str(Path(tool_config_path).resolve()) if tool_config_path else None,
        "boundaries": boundaries,
        "summary": {
            "requests": len(boundaries),
            "errors": sum(bool(item["error"]) or item["stop_reason"] == "error" for item in boundaries),
            "tool_call_sequence_matches": sum(item["tool_call_sequence_match"] for item in boundaries),
            "tool_call_sequence_match_rate": sum(item["tool_call_sequence_match"] for item in boundaries) / len(boundaries),
            "total_tokens": sum(item["usage"]["total_tokens"] for item in boundaries),
            "total_cost_usd": round(sum(item["usage"]["cost_usd"] for item in boundaries), 8),
            "total_duration_ms": sum(item["duration_ms"] for item in boundaries),
        },
    }
    target = Path(output_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if isinstance(eval_case, dict):
        evaluated = await _evaluate_case(
            eval_case,
            next((value for value in reversed(replayed_contents) if value.strip()), ""),
            client=client,
            profile=profile,
        )
        passed = report["summary"]["errors"] == 0 and bool(evaluated["passed"])
        result = {
            "timestamp": utc_now(),
            "case_id": eval_case.get("id"),
            "trace_id": run["trace_id"],
            "run_id": run["run_id"],
            "provider": settings.provider,
            "model": settings.model_id,
            "passed": passed,
            "confidence": evaluated["confidence"],
            "judge": evaluated,
            "metrics": report["summary"],
            "report": str(target),
        }
        result_target = Path(results_path).resolve() if results_path else target.parent / "eval-results.jsonl"
        result_target.parent.mkdir(parents=True, exist_ok=True)
        with result_target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        report["evaluation"] = result
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _message_from_dict(value: dict[str, Any]) -> ChatMessage:
    return ChatMessage(
        role=value.get("role", "user"),
        content=str(value.get("content") or ""),
        tool_calls=[
            ToolInvocation(
                call_id=str(call.get("call_id") or ""),
                name=str(call.get("name") or ""),
                arguments=call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
            )
            for call in value.get("tool_calls", [])
            if isinstance(call, dict)
        ],
        tool_call_id=value.get("tool_call_id") if isinstance(value.get("tool_call_id"), str) else None,
        name=value.get("name") if isinstance(value.get("name"), str) else None,
    )


def _replace_system_prompt(messages: list[ChatMessage], prompt: str) -> list[ChatMessage]:
    output = list(messages)
    for index, message in enumerate(output):
        if message.role == "system":
            output[index] = ChatMessage(role="system", content=prompt)
            return output
    return [ChatMessage(role="system", content=prompt), *output]


def _filter_tools(tools: Any, selection: Any) -> list[dict[str, Any]]:
    definitions = [dict(item) for item in tools if isinstance(item, dict)] if isinstance(tools, list) else []
    if not isinstance(selection, dict):
        return definitions
    enabled = set(selection.get("enabled", [])) if isinstance(selection.get("enabled"), list) else None
    disabled = set(selection.get("disabled", [])) if isinstance(selection.get("disabled"), list) else set()
    overrides = selection.get("overrides") if isinstance(selection.get("overrides"), dict) else {}
    output: list[dict[str, Any]] = []
    for tool in definitions:
        name = tool.get("name")
        if not isinstance(name, str) or (enabled is not None and name not in enabled) or name in disabled:
            continue
        definition = dict(tool)
        override = overrides.get(name)
        if isinstance(override, dict) and isinstance(override.get("description"), str):
            definition["description"] = override["description"]
        if isinstance(override, dict) and isinstance(override.get("parameters"), dict):
            definition["parameters"] = override["parameters"]
        output.append(definition)
    return output


def _tool_call_names(output: dict[str, Any] | None) -> list[str]:
    if not output:
        return []
    return [
        str(item.get("name"))
        for item in output.get("tool_calls", [])
        if isinstance(item, dict) and item.get("name")
    ]


def _read_json(path: Path) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _evaluate_case(
    case: dict[str, Any],
    output: str,
    *,
    client: ModelClient,
    profile: ModelProfile,
) -> dict[str, Any]:
    judge = case.get("judge")
    if not isinstance(judge, dict):
        return {
            "type": "boundary_errors",
            "passed": True,
            "confidence": "model-boundary-only",
            "output_preview": _output_preview(output),
            "output_sha256": _hash_text(output),
        }
    judge_type = str(judge.get("type") or "")
    if judge_type == "regex_all":
        patterns = [str(value) for value in judge.get("patterns", [])]
        missing = [pattern for pattern in patterns if re.search(pattern, output, re.IGNORECASE) is None]
        return {
            "type": judge_type,
            "passed": not missing,
            "confidence": "deterministic",
            "missing_patterns": missing,
            "output_preview": _output_preview(output),
            "output_sha256": _hash_text(output),
        }
    if judge_type == "normalized_contains_any":
        expected = [str(value) for value in judge.get("expected", [])]
        normalized_output = _normalize_answer(output)
        matched = [value for value in expected if _normalize_answer(value) in normalized_output]
        return {
            "type": judge_type,
            "passed": bool(matched),
            "confidence": "deterministic",
            "matched": matched,
            "expected": expected,
            "output_preview": _output_preview(output),
            "output_sha256": _hash_text(output),
        }
    if judge_type == "normalized_exact":
        expected = str(judge.get("expected") or "")
        normalized_output = _normalize_answer(output)
        normalized_expected = _normalize_answer(expected)
        return {
            "type": judge_type,
            "passed": bool(normalized_expected) and normalized_output == normalized_expected,
            "confidence": "deterministic",
            "expected": expected,
            "output_preview": _output_preview(output),
            "output_sha256": _hash_text(output),
        }
    if judge_type == "normalized_phrase_set":
        expected = [str(value) for value in judge.get("expected", [])]
        normalized_output = _normalize_answer(output)
        normalized_phrases = [_normalize_answer(value) for value in expected]
        ordered = bool(judge.get("ordered"))
        cursor = 0
        missing: list[str] = []
        for raw_phrase, phrase in zip(expected, normalized_phrases, strict=True):
            position = normalized_output.find(phrase, cursor if ordered else 0) if phrase else -1
            if position < 0:
                missing.append(raw_phrase)
            elif ordered:
                cursor = position + len(phrase)
        return {
            "type": judge_type,
            "passed": bool(expected) and not missing,
            "confidence": "deterministic",
            "ordered": ordered,
            "missing": missing,
            "expected": expected,
            "output_preview": _output_preview(output),
            "output_sha256": _hash_text(output),
        }
    if judge_type == "llm_rubric":
        prompt = (
            "I will give you a question, a rubric for the desired response, and a model response. "
            "Answer yes only if the response satisfies the rubric; otherwise answer no.\n\n"
            f"Question: {judge.get('question', '')}\n\n"
            f"Rubric: {judge.get('rubric', '')}\n\n"
            f"Model Response: {output}\n\n"
            "Is the model response correct? Answer yes or no only."
        )
        judge_profile = ModelProfile(
            model_id=profile.model_id,
            context_window=profile.context_window,
            max_output_tokens=64,
            supports_tools=False,
            input_cost_per_million=profile.input_cost_per_million,
            output_cost_per_million=profile.output_cost_per_million,
            cached_input_cost_per_million=profile.cached_input_cost_per_million,
        )
        started = time.perf_counter()
        reply: AssistantReply | None = None
        error_text: str | None = None
        try:
            async for event in client.stream(
                ModelRequest(
                    profile=judge_profile,
                    messages=[ChatMessage(role="user", content=prompt)],
                    tools=[],
                    temperature=0,
                    metadata={"purpose": "trace_eval_judge"},
                )
            ):
                if event.type == "error" and event.error:
                    error_text = event.error
                elif event.type == "completed":
                    reply = event.reply
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
        label = _normalize_answer(reply.content if reply else "")
        return {
            "type": judge_type,
            "passed": label.startswith("yes") and not error_text,
            "confidence": "llm-judge",
            "label": label,
            "error": error_text or (reply.error if reply else None),
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "usage": usage_dict(
                reply.usage.input_tokens if reply and reply.usage else 0,
                reply.usage.output_tokens if reply and reply.usage else 0,
                reply.usage.cached_tokens if reply and reply.usage else 0,
                input_cost_per_million=profile.input_cost_per_million,
                output_cost_per_million=profile.output_cost_per_million,
                cached_input_cost_per_million=profile.cached_input_cost_per_million,
            ),
            "output_preview": _output_preview(output),
            "output_sha256": _hash_text(output),
        }
    return {
        "type": judge_type or "unknown",
        "passed": False,
        "confidence": "invalid-judge",
        "error": f"Unsupported eval judge: {judge_type}",
        "output_preview": _output_preview(output),
        "output_sha256": _hash_text(output),
    }


def _normalize_answer(value: str) -> str:
    return " ".join(
        re.findall(r"[\w]+", _canonical_answer_text(value).casefold(), flags=re.UNICODE)
    )


def _canonical_answer_text(value: str) -> str:
    text = value.strip()
    marker = "\\boxed{"
    start = text.rfind(marker)
    if start >= 0:
        index = start + len(marker)
        depth = 1
        output: list[str] = []
        while index < len(text) and depth:
            character = text[index]
            if character == "{":
                depth += 1
                output.append(character)
            elif character == "}":
                depth -= 1
                if depth:
                    output.append(character)
            else:
                output.append(character)
            index += 1
        if output:
            text = "".join(output).strip()
    wrapper = re.compile(r"^\\(?:text|mathrm|operatorname|mathbf|mathit|texttt)\s*\{(.*)\}$", re.S)
    while True:
        match = wrapper.match(text)
        if match is None:
            break
        text = match.group(1).strip()
    return text


def _output_preview(value: str, limit: int = 2_000) -> str:
    sanitized = sanitize_trace_value(value)
    text = sanitized if isinstance(sanitized, str) else str(sanitized)
    return text if len(text) <= limit else text[:limit] + "…"
