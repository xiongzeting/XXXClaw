from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from MiniClaw.platforms.delivery import DeliveryManager


class DeliveryManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_and_deduplicates_delivered_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = DeliveryManager(
                Path(directory) / "delivery.jsonl",
                max_attempts=3,
                retry_base_seconds=0,
            )
            calls = 0

            async def sender() -> str:
                nonlocal calls
                calls += 1
                if calls < 3:
                    raise RuntimeError("temporary failure")
                return "remote-message-id"

            first = await manager.deliver("message-1", "reply", {"text": "hello"}, sender)
            second = await manager.deliver("message-1", "reply", {"text": "hello"}, sender)

            self.assertEqual(first, "remote-message-id")
            self.assertEqual(second, "remote-message-id")
            self.assertEqual(calls, 3)
            records = [
                json.loads(line)
                for line in (Path(directory) / "delivery.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(records[-1]["status"], "delivered")
            self.assertEqual(sum(item["status"] == "retrying" for item in records), 2)

    async def test_concurrent_duplicates_share_one_send_and_release_lock_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = DeliveryManager(Path(directory) / "delivery.jsonl", retry_base_seconds=0)
            calls = 0

            async def sender() -> str:
                nonlocal calls
                calls += 1
                await asyncio.sleep(0.01)
                return "remote-message-id"

            results = await asyncio.gather(
                *(manager.deliver("same-key", "reply", {"text": "hello"}, sender) for _ in range(5))
            )

            self.assertEqual(results, ["remote-message-id"] * 5)
            self.assertEqual(calls, 1)
            self.assertEqual(manager._locks, {})


if __name__ == "__main__":
    unittest.main()
