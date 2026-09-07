from __future__ import annotations

import tempfile
import unittest
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

from MiniClaw.llm.env_file import merged_environment, read_env_file
from MiniClaw.llm.types import AssistantReply, ModelEvent, ModelProfile, ModelRequest
from MiniClaw.platforms.feishu.config import load_feishu_settings
from MiniClaw.platforms.feishu.dedupe import PersistentEventDeduplicator
from MiniClaw.platforms.feishu.models import (
    FeishuInboundMessage,
    build_conversation,
    extract_message_text,
)
from MiniClaw.platforms.feishu.router import FeishuAssistantRouter
from MiniClaw.coding_agent.runtime import RuntimeSettings


class FinalModelClient:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield ModelEvent(type="completed", reply=AssistantReply(content="飞书接入成功"))


class FakeTransport:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str, bool]] = []
        self.updates: list[tuple[str, str]] = []

    async def reply(self, message_id: str, text: str, *, in_thread: bool) -> str:
        self.replies.append((message_id, text, in_thread))
        return f"reply-{len(self.replies)}"

    async def update(self, message_id: str, text: str) -> None:
        self.updates.append((message_id, text))


def inbound(**overrides: str | bool) -> FeishuInboundMessage:
    values: dict[str, str | bool] = {
        "message_id": "om-message",
        "chat_id": "oc-chat",
        "user_id": "ou-user",
        "tenant_id": "tenant",
        "chat_type": "group",
        "message_type": "text",
        "text": "修复测试",
        "root_id": "om-root",
        "parent_id": "",
        "thread_id": "",
        "mentioned": True,
    }
    values.update(overrides)
    return FeishuInboundMessage(**values)  # type: ignore[arg-type]


class FeishuConfigTests(unittest.TestCase):
    def test_router_import_does_not_eagerly_load_lark_sdk(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import MiniClaw.platforms.feishu.router; "
                "raise SystemExit(1 if 'lark_oapi' in sys.modules else 0)",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_miniclaw_feishu_variables_are_loaded(self) -> None:
        settings = load_feishu_settings(
            {
                "MINICLAW_FEISHU_APP_ID": "cli-test",
                "MINICLAW_FEISHU_APP_SECRET": "secret",
                "MINICLAW_FEISHU_SESSION_SCOPE": "thread",
                "MINICLAW_FEISHU_MAX_CONCURRENT_SESSIONS": "4",
                "MINICLAW_FEISHU_DELIVERY_MAX_ATTEMPTS": "5",
            }
        )
        self.assertEqual(settings.app_id, "cli-test")
        self.assertEqual(settings.session_scope, "thread")
        self.assertEqual(settings.max_concurrent_sessions, 4)
        self.assertEqual(settings.delivery_max_attempts, 5)

    def test_dotenv_is_read_without_mutating_or_overriding_explicit_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "MINICLAW_PROVIDER=deepseek\n"
                "MINICLAW_DEEPSEEK_API_KEY='file-key'\n"
                "MINICLAW_DEEPSEEK_MODEL=file-model\n",
                encoding="utf-8",
            )
            values = read_env_file(path)
            merged = merged_environment(
                values,
                {
                    "MINICLAW_PROVIDER": "primary",
                    "MINICLAW_PRIMARY_API_KEY": "process-key",
                    "MINICLAW_PRIMARY_MODEL": "process-model",
                    "MINICLAW_DEEPSEEK_API_KEY": "process-deepseek-key",
                },
            )
            self.assertEqual(merged["MINICLAW_PROVIDER"], "primary")
            self.assertEqual(merged["MINICLAW_PRIMARY_API_KEY"], "process-key")
            self.assertEqual(merged["MINICLAW_PRIMARY_MODEL"], "process-model")
            self.assertEqual(merged["MINICLAW_DEEPSEEK_API_KEY"], "process-deepseek-key")

    def test_conversation_scope_matches_pi_semantics(self) -> None:
        message = inbound()
        thread = build_conversation(message, "thread")
        self.assertEqual(thread.conversation_id, "feishu-oc-chat--thread--om-root")
        self.assertEqual(
            build_conversation(message, "channel").conversation_id,
            "feishu-oc-chat--channel",
        )
        self.assertIn("--user--feishu-ou-user", build_conversation(message, "user").conversation_id)
        dm = build_conversation(inbound(chat_type="p2p", root_id=""), "thread")
        self.assertEqual(dm.conversation_id, "dm--feishu-oc-chat")

    def test_text_mentions_are_removed(self) -> None:
        text = extract_message_text("text", '{"text":"@_user_1 运行测试"}', ["@_user_1"])
        self.assertEqual(text, "运行测试")

    def test_inbound_event_can_retry_after_failure_but_not_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inbox = PersistentEventDeduplicator(Path(directory) / "events.jsonl")
            self.assertTrue(inbox.claim("event-1"))
            self.assertFalse(inbox.claim("event-1"))
            inbox.fail("event-1", "temporary")
            self.assertTrue(inbox.claim("event-1"))
            inbox.complete("event-1")
            self.assertFalse(inbox.claim("event-1"))


class FeishuRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_routes_into_miniclaw_llm_and_updates_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model = FinalModelClient()
            transport = FakeTransport()
            settings = load_feishu_settings(
                {
                    "MINICLAW_FEISHU_APP_ID": "cli-test",
                    "MINICLAW_FEISHU_APP_SECRET": "secret",
                    "MINICLAW_FEISHU_SESSION_SCOPE": "thread",
                }
            )
            router = FeishuAssistantRouter(
                workspace=Path(directory),
                model_client=model,
                profile=ModelProfile("fake"),
                settings=settings,
                transport=transport,  # type: ignore[arg-type]
                runtime_settings=RuntimeSettings(backend="host"),
            )
            message = inbound()
            await router.submit(message)
            session_key = build_conversation(message, "thread").session_key
            await router._queues[session_key].queue.join()

            self.assertEqual(model.requests[-1].messages[-1].content, "修复测试")
            self.assertEqual(transport.replies[0], ("om-message", "MiniClaw 正在处理…", True))
            self.assertEqual(transport.updates[-1][1], "飞书接入成功")
            session_path = (
                Path(directory)
                / ".aster"
                / "feishu"
                / "sessions"
                / session_key
                / "context.jsonl"
            )
            self.assertTrue(session_path.exists())
            assistant = router._assistants[session_key]
            self.assertEqual(assistant.memory.user_scope, message.namespaced_user_id)
            self.assertEqual(assistant.memory.channel_scope, message.namespaced_chat_id)

            await router.submit(message)
            self.assertEqual(len(model.requests), 1)
            await router.close()
            self.assertEqual(router._queues, {})

    async def test_goal_status_is_handled_per_feishu_session_without_llm_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            model = FinalModelClient()
            transport = FakeTransport()
            settings = load_feishu_settings(
                {
                    "MINICLAW_FEISHU_APP_ID": "cli-test",
                    "MINICLAW_FEISHU_APP_SECRET": "secret",
                    "MINICLAW_FEISHU_SESSION_SCOPE": "thread",
                }
            )
            router = FeishuAssistantRouter(
                workspace=Path(directory),
                model_client=model,
                profile=ModelProfile("fake"),
                settings=settings,
                transport=transport,  # type: ignore[arg-type]
                runtime_settings=RuntimeSettings(backend="host"),
            )
            message = inbound(message_id="goal-status", text="/goal status")
            session_key = build_conversation(message, "thread").session_key
            router._assistant(session_key).create_goal("飞书目标", ["条件"])
            await router.submit(message)
            await router._queues[session_key].queue.join()

            self.assertEqual(model.requests, [])
            self.assertIn("Goal: 飞书目标", transport.updates[-1][1])


if __name__ == "__main__":
    unittest.main()
