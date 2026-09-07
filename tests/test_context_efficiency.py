import hashlib
import tempfile
import unittest
from pathlib import Path

from MiniClaw.llm.types import ChatMessage, ToolInvocation
from MiniClaw.coding_agent.tools.base import ToolResult
from tests.test_memory_cost import make_context


def pair(identifier, body, name='read', arguments=None):
    call = ToolInvocation(identifier, name, arguments or {'path': 'module.py'})
    return [ChatMessage(role='assistant', tool_calls=[call]),
            ChatMessage(role='tool', tool_call_id=identifier, name=name, content=body)]


class ContextEfficiencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_read_can_recover_the_middle_of_large_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            context = make_context(Path(d))
            code = 'start\n' + 'code\n'*2200 + 'EXACT_MIDDLE\n' + 'code\n'*2200 + 'end'
            messages = pair('r', code)
            result = await context.artifactize_live_result(messages[0].tool_calls[0], ToolResult(code))
            self.assertEqual(result.content, code)
            self.assertEqual(context.transform_request_context(messages)[1].content, code)
            old = context.transform_request_context([*messages, ChatMessage(role='assistant', content='consumed')])
            self.assertEqual(old[1].content, code)  # A normal append must not rewrite an already sent prefix.
            rebased = context.transform_request_context([*messages, ChatMessage(role='user', content='next stage')])
            self.assertLess(len(rebased[1].content), len(code))
            self.assertEqual(messages[1].content, code)

    async def test_successful_code_containing_error_is_bounded_but_real_failure_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            context = make_context(Path(d))
            content = 'if failed: raise ValueError("error")\n'*500
            messages = pair('success', content, 'bash')
            projected = context.transform_request_context(messages)
            self.assertLess(len(projected[1].content), len(content))
            failure = pair('failure', content, 'bash')
            await context.artifactize_live_result(failure[0].tool_calls[0], ToolResult(content, is_error=True))
            self.assertEqual(context.transform_request_context(failure)[1].content, content)
            pending=[]
            archived=[context._copy_message(m) for m in failure]
            context._artifactize_old_results(archived,pending)
            self.assertEqual(archived[1].content,content)
            self.assertEqual(pending,[])
            restored = make_context(Path(d))
            restored.load()
            self.assertEqual(restored.transform_request_context(failure)[1].content, content)

    def test_dedup_requires_equal_arguments_and_equal_returned_content(self):
        with tempfile.TemporaryDirectory() as d:
            context = make_context(Path(d))
            a, b = 'old '*220, 'new '*220
            messages = [*pair('r1', a), *pair('r2', a), *pair('r3', b),
                        *pair('r4', a, arguments={'path':'other.py'})]
            projected = context.transform_request_context(messages)
            self.assertIn('r2', projected[1].content)
            self.assertEqual(projected[3].content, a)
            self.assertEqual(projected[5].content, b)
            self.assertEqual(projected[7].content, a)
            self.assertEqual(messages[1].content, a)

    async def test_snapshot_survives_compaction_and_restart_but_never_replays_changed_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            code = 'print("correct current value")\n'*20
            path = root/'module.py'
            path.write_text(code, encoding='utf-8')
            context = make_context(root)
            messages = pair('r1', code)
            await context.artifactize_live_result(messages[0].tool_calls[0], ToolResult(code, details={
                'path': str(path), 'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
            tail = [ChatMessage(role='user', content='continue from compacted checkpoint')]
            projected = context.transform_request_context(tail)
            self.assertEqual([m.role for m in projected], ['assistant','tool','user'])
            self.assertEqual(projected[1].content, code)
            self.assertEqual(len(context.transform_request_context(messages)), 2)
            restored = make_context(root)
            restored.load()
            self.assertEqual(restored.transform_request_context(tail)[1].content, code)
            path.write_text('changed', encoding='utf-8')
            self.assertEqual(restored.transform_request_context(tail), tail)
            self.assertEqual(restored.last_projection['invalidated_read_paths'], ['module.py'])
            restored.read_snapshots.restore(context.read_snapshots.entries['module.py'])
            path.unlink()
            self.assertEqual(restored.transform_request_context(tail),tail)

    async def test_snapshot_budget_and_workspace_escape(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            context = make_context(root)
            for i in range(20):
                path = root/f'{i}.py'
                path.write_text('x'*1500)
                await context.artifactize_live_result(ToolInvocation(f'r{i}','read',{'path':path.name}),
                    ToolResult('x'*1500,details={'path':str(path),'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))
            view = context.transform_request_context([ChatMessage(role='user',content='continue')])
            self.assertLessEqual(context.last_projection['snapshot_bytes'],context.read_snapshots.budget_bytes)
            self.assertLessEqual(len(context.read_snapshots.entries),16)
            self.assertEqual(len(view),5)
            context.read_snapshots.restore({'path':'../escape','sha256':'fake','call':{'call_id':'evil','name':'read','arguments':{}},'content':'secret'})
            self.assertNotIn('secret',[m.content for m in context.transform_request_context([])])

    async def test_fixed_overhead_does_not_resummarize_history_already_within_target(self):
        with tempfile.TemporaryDirectory() as d:
            context = make_context(Path(d),keep_recent_tokens=20)
            messages = [ChatMessage(role='user',content='rules '*60), ChatMessage(role='assistant',content='done '*60),
                        ChatMessage(role='user',content='continue'),ChatMessage(role='assistant',content='ok')]
            for message in messages:
                context.append_message(message)
            self.assertIsNone(await context.maybe_compact(messages,6000))
            self.assertEqual(context.last_compaction_decision['reason'],'history_within_target')
            self.assertEqual(context.model_client.requests,[])

    def test_estimation_has_no_artifact_io_and_projection_never_expands_tiny_payloads(self):
        with tempfile.TemporaryDirectory() as d:
            context = make_context(Path(d),artifact_threshold_bytes=64)
            messages = pair('r','x'*100,'bash',{'command':'y'*100})
            view = context.transform_request_context(messages,persist=False,include_snapshots=False)
            self.assertFalse(context.artifacts.root.exists())
            self.assertEqual(view[1].content,messages[1].content)
            self.assertEqual(view[0].tool_calls[0].arguments,messages[0].tool_calls[0].arguments)


if __name__ == '__main__':
    unittest.main()
