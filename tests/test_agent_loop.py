from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from MiniClaw.llm.types import (
    AssistantReply,
    ChatMessage,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    ToolInvocation,
)
from MiniClaw.agent.loop import AgentLoop
from MiniClaw.coding_agent.tools.executor import ToolExecutor
from MiniClaw.coding_agent.tools.factory import create_coding_tools
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.runtime import RuntimeSettings
from MiniClaw.coding_agent.tools import ToolResult


class ScriptedModelClient:
    def __init__(self, replies: list[AssistantReply]) -> None:
        self.replies = replies
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if reply.content:
            yield ModelEvent(type="text_delta", text=reply.content)
        yield ModelEvent(type="completed", reply=reply)


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_executes_tool_then_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model_client = ScriptedModelClient(
                [
                    AssistantReply(
                        tool_calls=[
                            ToolInvocation(
                                call_id="call-1",
                                name="write",
                                arguments={"path": "result.txt", "content": "OK"},
                            )
                        ],
                        stop_reason="tool_calls",
                    ),
                    AssistantReply(content="Created result.txt."),
                ]
            )
            tool_executor = ToolExecutor()
            for tool in create_coding_tools(directory):
                tool_executor.register(tool)
            loop = AgentLoop(model_client, ModelProfile("fake"), tool_executor)

            events = [event async for event in loop.run("Create the result file")]

            self.assertEqual((Path(directory) / "result.txt").read_text(), "OK")
            self.assertEqual(events[-1].type, "run_finished")
            self.assertEqual(len(model_client.requests), 2)
            second_roles = [message.role for message in model_client.requests[1].messages]
            self.assertEqual(second_roles, ["user", "assistant", "tool"])

    async def test_unknown_tool_returns_error_to_model(self) -> None:
        model_client = ScriptedModelClient(
            [
                AssistantReply(
                    tool_calls=[ToolInvocation("missing-1", "not_registered", {})],
                    stop_reason="tool_calls",
                ),
                AssistantReply(content="Recovered."),
            ]
        )
        loop = AgentLoop(model_client, ModelProfile("fake"), ToolExecutor())
        events = [event async for event in loop.run("Try a tool")]
        tool_events = [event for event in events if event.type == "tool_finished"]
        self.assertEqual(len(tool_events), 1)
        self.assertTrue(tool_events[0].is_error)

    async def test_context_evidence_is_separate_from_the_system_message(self) -> None:
        model_client = ScriptedModelClient([AssistantReply(content="done")])
        loop = AgentLoop(
            model_client,
            ModelProfile("fake"),
            ToolExecutor(),
            system_prompt="trusted policy",
            context_messages_provider=lambda: [
                ChatMessage(role="user", content="untrusted historical evidence")
            ],
        )

        [event async for event in loop.run("current request")]

        request = model_client.requests[0]
        self.assertEqual(
            [(message.role, message.content) for message in request.messages],
            [
                ("system", "trusted policy"),
                ("user", "current request"),
                ("user", "untrusted historical evidence"),
            ],
        )

    async def test_cancel_propagates_to_the_active_model_request(self) -> None:
        class CancellableModelClient:
            async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
                assert request.cancellation_token is not None
                await request.cancellation_token.wait()
                yield ModelEvent(
                    type="completed",
                    reply=AssistantReply(stop_reason="aborted"),
                )

        loop = AgentLoop(CancellableModelClient(), ModelProfile("fake"), ToolExecutor())

        async def collect():
            return [event async for event in loop.run("wait")]

        task = asyncio.create_task(collect())
        await asyncio.sleep(0)
        self.assertTrue(loop.cancel("user cancelled"))
        events = await asyncio.wait_for(task, timeout=1)
        self.assertEqual(events[-1].type, "run_finished")
        self.assertEqual(events[-1].details["stop_reason"], "aborted")

    async def test_coding_assistant_injects_only_tools_available_to_active_role(self) -> None:
        class ReviewNotesTool:
            name = "review_notes"
            description = "Record review notes"
            input_schema = {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }

            async def execute(self, arguments):
                return ToolResult(content="ok")

        with tempfile.TemporaryDirectory() as directory:
            model = ScriptedModelClient([AssistantReply(content="reviewed")])
            assistant = CodingAssistant(
                model_client=model,
                profile=ModelProfile("fake"),
                workspace=directory,
                runtime_settings=RuntimeSettings(backend="host"),
                role="reviewer",
                role_tools={"reviewer": [ReviewNotesTool()]},
                tool_role_policies={
                    "reviewer": {"allow": ["read", "grep", "search", "review_notes"]}
                },
            )

            events = [event async for event in assistant.run("review")]

            self.assertEqual(events[-1].type, "run_finished")
            names = [definition["name"] for definition in model.requests[0].tools]
            self.assertEqual(names, ["read", "grep", "search", "review_notes"])
            self.assertIn("Active role: reviewer", model.requests[0].messages[0].content)


if __name__ == "__main__":
    unittest.main()
