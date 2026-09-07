import importlib.util
import tempfile
import threading
import unittest
import uuid
import asyncio
import queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('local_agent_server_test', Path(__file__).resolve().parents[1] / 'frontend/local_agent_server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class SessionIsolationTests(unittest.TestCase):
    def test_internal_context_journal_is_not_shown_as_assistant_reply(self):
        import json
        from dataclasses import asdict
        from MiniClaw.agent.context import ContextJournal
        with tempfile.TemporaryDirectory() as directory:
            session=server.AgentSession.__new__(server.AgentSession)
            session.workspace=Path(directory);session.session_id='test'
            path=session.workspace/'.aster/web/test/session.jsonl';path.parent.mkdir(parents=True)
            internal=ContextJournal().update([],{'task.pending':['private runtime bookkeeping']})[0]
            path.write_text(json.dumps({'message':asdict(internal)})+'\n'+json.dumps({'message':{'role':'assistant','content':'Delivered.'}}),encoding='utf-8')
            self.assertEqual([m['text'] for m in session.history()['messages']],['Delivered.'])

    def test_stream_done_reports_real_terminal_statuses(self):
        from MiniClaw.agent.events import AgentEvent
        cases = [('stop', False, 'success'), ('budget_exhausted', False, 'paused'),
                 ('aborted', False, 'cancelled'), ('error', True, 'error')]
        for stop_reason, is_error, expected in cases:
            class FakeAssistant:
                def __init__(self):
                    self.task_progress = SimpleNamespace(
                        state={'next_action': 'inspect remaining work', 'remaining': ['criterion']},
                        pending=lambda: ['pending criterion'])
                async def run(self, prompt):
                    yield AgentEvent(type='run_finished', is_error=is_error,
                                     details={'stop_reason': stop_reason,
                                              'status': 'paused' if stop_reason == 'budget_exhausted' else None,
                                              'resumable': stop_reason == 'budget_exhausted'})
                def goal_status(self):
                    return ''
            session = server.AgentSession.__new__(server.AgentSession)
            session.assistant = FakeAssistant(); session.lock = threading.Lock(); session.lock.acquire()
            session.status = 'idle'; output = queue.Queue()
            asyncio.run(session.stream('test', output))
            rows = []
            while not output.empty(): rows.append(output.get())
            self.assertEqual(rows[-1]['event'], 'done')
            self.assertEqual(rows[-1]['data']['status'], expected)
            if expected == 'paused':
                self.assertTrue(rows[-1]['data']['resumable'])
                self.assertEqual(rows[-1]['data']['pending'], ['pending criterion'])

    def test_resume_uses_assistant_resume(self):
        from MiniClaw.agent.events import AgentEvent
        class FakeAssistant:
            def __init__(self): self.resumed = False
            async def resume(self):
                self.resumed = True
                yield AgentEvent(type='run_finished', details={'stop_reason': 'stop'})
            async def run(self, prompt): raise AssertionError('resume must not call run')
            def goal_status(self): return ''
        session = server.AgentSession.__new__(server.AgentSession)
        session.assistant = FakeAssistant(); session.lock = threading.Lock(); session.lock.acquire()
        session.status = 'idle'; output = queue.Queue()
        asyncio.run(session.stream('', output, resume=True))
        self.assertTrue(session.assistant.resumed)
        self.assertEqual(output.get()['event'], 'status')
        self.assertEqual(output.get()['data']['status'], 'success')

    def test_cancelled_tool_event_survives_http_serialization(self):
        from MiniClaw.agent.events import AgentEvent
        from MiniClaw.llm.types import ToolInvocation
        class FakeAssistant:
            async def run(self, prompt):
                yield AgentEvent(type='tool_finished', tool_call=ToolInvocation(call_id='cancel-test',name='bash',arguments={}),
                                 tool_result='cancelled',details={'cancelled':True})
            def goal_status(self):
                return ''
        session = server.AgentSession.__new__(server.AgentSession)
        session.assistant = FakeAssistant()
        session.lock = threading.Lock()
        session.lock.acquire()
        output = queue.Queue()
        asyncio.run(session.stream('test',output))
        rows = []
        while not output.empty():
            rows.append(output.get())
        tool = next(row['data'] for row in rows if row['event']=='agent')
        self.assertTrue(tool['cancelled'])
        self.assertEqual(tool['tool_call_id'],'cancel-test')
        self.assertEqual(session.status,'idle')
        self.assertFalse(session.lock.locked())

    def test_switch_restores_assistant_and_preserves_event_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            session = server.AgentSession.__new__(server.AgentSession)
            session.workspace = Path(directory)
            session.lock = threading.Lock()
            session.settings = SimpleNamespace(provider='test')
            session.runtime = None
            session.approval = SimpleNamespace(timeout_seconds=1)
            session.environment = {}
            session._assistants = {}
            session.loop = object()
            original_loop = session.loop
            session.info = lambda: {'session_id': session.session_id}
            with patch.object(server, 'CodingAssistant', side_effect=lambda **kw: SimpleNamespace(session_id=kw['session_id'])), patch.object(server, 'create_model_client'), patch.object(server, 'model_profile_from_settings'):
                first = session.reset()['session_id']
                original = session.assistant
                second = session.reset()['session_id']
                self.assertNotEqual(first, second)
                session.select(first)
                self.assertIs(session.assistant, original)
                self.assertIs(session.loop, original_loop)
                with self.assertRaises(FileNotFoundError):
                    session.select(uuid.uuid4().hex)
                self.assertFalse(session.lock.locked())
                self.assertEqual(session.session_id, first)
                session.lock.acquire()
                try:
                    with self.assertRaises(RuntimeError):
                        session.reset()
                finally:
                    session.lock.release()
                restored = uuid.uuid4().hex
                saved = session.workspace / '.aster/web' / restored / 'session.jsonl'
                saved.parent.mkdir(parents=True)
                saved.write_text('')
                session.select(restored)
                self.assertEqual(session.assistant.session_id, restored)
