"""Prefix stability must preserve revocation, tool pairs and durable recovery."""
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock

from MiniClaw.agent.context import ContextJournal, decode_update, is_context_update
from MiniClaw.agent.loop import AgentLoop
from MiniClaw.coding_agent.tools.executor import ToolExecutor
from MiniClaw.coding_agent.tools.factory import create_coding_tools
from MiniClaw.llm.types import AssistantReply, ChatMessage, ModelEvent, ModelProfile, ToolInvocation
from tests.test_agent_loop import ScriptedModelClient
from tests.test_context_efficiency import pair
from tests.test_memory_cost import make_context
from tests import test_task_recovery as helpers


class JournalTests(unittest.TestCase):
    def test_deltas_reorder_update_remove_and_rebase_are_explicit(self):
        journal=ContextJournal(max_updates=3)
        history=[ChatMessage(role='user',content='V1 withdrawn. Only deliver V2.')]
        desired={'task.criteria':[{'id':'b','version':2}], 'memory.A':{'content':'old'}, 'memory.B':{'content':'keep'}}
        history+=journal.update(history,desired)
        first=json.dumps([asdict(m) for m in history])
        self.assertEqual(journal.update(history,dict(reversed(list(desired.items())))),[])
        desired['memory.A']={'content':'new','content_version':'v2'}
        history+=journal.update(history,desired)
        delta=decode_update(history[-1])
        self.assertEqual(delta['set'],{'memory.A':desired['memory.A']})
        self.assertEqual(json.dumps([asdict(m) for m in history[:-1]]),first)
        del desired['memory.B']
        history+=journal.update(history,desired)
        self.assertEqual(decode_update(history[-1])['remove'],['memory.B'])
        desired['task.ready']=False
        history+=journal.update(history,desired)
        self.assertTrue(decode_update(history[-1])['reset'])
        projected=journal.project(history)
        self.assertEqual(projected[0].content,'V1 withdrawn. Only deliver V2.')
        self.assertEqual(sum(is_context_update(m) for m in projected),1)
        self.assertEqual(decode_update(projected[-1])['set'],desired)
        self.assertNotIn('memory.B',decode_update(projected[-1])['set'])
        self.assertEqual(len(history),5)  # Full audit not deleted by projection.

    def test_restart_and_compaction_reconstruct_latest_desired_state(self):
        with tempfile.TemporaryDirectory() as d:
            context=make_context(Path(d));journal=ContextJournal()
            messages=[ChatMessage(role='user',content='real user instruction')]
            first={'task.version':1,'memory.old':'old'}
            messages+=journal.update(messages,first)
            current={'task.version':2,'task.pending':['reverify'],'memory.new':'new'}
            messages+=journal.update(messages,current)
            for message in messages:context.append_message(message)
            restored=make_context(Path(d)).load()
            self.assertEqual(ContextJournal().update(restored,current),[])
            # If compaction retained only a delta, missing baseline fields are
            # reconstructed from authoritative stores, not guessed from prose.
            compacted=[ChatMessage(role='assistant',content='old summary: use version 1'),restored[-1]]
            current['task.criteria']=['must use version 2']
            compacted+=journal.update(compacted,current)
            self.assertEqual(decode_update(compacted[-1])['set']['task.criteria'],['must use version 2'])
            self.assertNotIn('memory.old',decode_update(compacted[-1])['set'])

    def test_no_update_inside_an_unfinished_tool_batch(self):
        journal=ContextJournal()
        history=[ChatMessage(role='assistant',tool_calls=[ToolInvocation('a','read',{}),ToolInvocation('b','read',{})]),
                 ChatMessage(role='tool',tool_call_id='a',content='one')]
        self.assertEqual(journal.update(history,{'task.ready':False}),[])
        history.append(ChatMessage(role='tool',tool_call_id='b',content='two'))
        self.assertEqual(len(journal.update(history,{'task.ready':False})),1)

    def test_generated_context_does_not_destroy_fresh_read_or_become_new_user_request(self):
        with tempfile.TemporaryDirectory() as d:
            context=make_context(Path(d));content='full fresh source\n'*1500
            messages=[ChatMessage(role='user',content='Keep exact middle source'),*pair('read',content)]
            messages+=ContextJournal().update(messages,{'task.pending':['read middle']})
            view=context.transform_request_context(messages)
            self.assertEqual(view[-2].content,content)
            self.assertEqual(context._latest_user_request(view),'Keep exact middle source')


class RequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_tool_batch_then_error_state_appends_after_stable_prefix(self):
        with tempfile.TemporaryDirectory() as d:
            state={'task.version':1,'memory.A':{'content':'reference'}}
            class Client(ScriptedModelClient):
                async def stream(self, request):
                    if len(self.requests)==0:
                        state['task.verification_error']='new failed check'
                    async for event in super().stream(request):yield event
            model=Client([AssistantReply(tool_calls=[ToolInvocation('a','write',{'path':'a','content':'A'}),
                                                    ToolInvocation('b','write',{'path':'b','content':'B'})]),AssistantReply(content='done')])
            executor=ToolExecutor()
            for tool in create_coding_tools(d):executor.register(tool)
            loop=AgentLoop(model,ModelProfile('fake'),executor,system_prompt='fixed',context_updates_provider=lambda:dict(state))
            events=[e async for e in loop.run('write both files')]
            first,second=model.requests
            self.assertEqual(second.messages[:len(first.messages)],first.messages)
            self.assertEqual([m.role for m in second.messages[-4:]],['assistant','tool','tool','assistant'])
            self.assertEqual(decode_update(second.messages[-1])['set'],{'task.verification_error':'new failed check'})
            self.assertEqual(second.metadata['request_prefix']['first_changed_component'],'append_only')
            self.assertGreater(second.metadata['request_prefix']['stable_prefix_serialized_bytes'],0)
            self.assertEqual(sum(is_context_update(e.message) for e in events if e.type=='message_added'),2)

    async def test_coding_memory_rank_changes_do_not_rewrite_or_reemit_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            assistant=helpers.RecoveryTests().assistant(ScriptedModelClient([]),d)
            rows=[{'source':'archive','record_id':key,'content':text,'fused_score':score,
                   'metadata':{'session_id':'other-task','source_path':'archive.jsonl','score':score}}
                  for key,text,score in [('A','version V1',1),('B','related details',2)]]
            assistant.memory._last_rendered_trace=rows
            first=assistant._provide_context_updates()
            messages=ContextJournal().update([],first)
            assistant.memory._last_rendered_trace=list(reversed(rows))
            rows[0]['metadata']['score']=99
            self.assertEqual(ContextJournal().update(messages,assistant._provide_context_updates()),[])
            rows[0]['content']='V1 withdrawn; use V2'
            update=ContextJournal().update(messages,assistant._provide_context_updates())
            self.assertEqual(len(decode_update(update[0])['set']),1)
            self.assertIn('V1 withdrawn; use V2',update[0].content)
            messages+=update
            assistant.memory._last_rendered_trace=[rows[1]]
            withdrawn=ContextJournal().update(messages,assistant._provide_context_updates())
            self.assertEqual(len(decode_update(withdrawn[0])['remove']),1)

    async def test_internal_updates_persist_without_becoming_chat_or_relearned_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            assistant=helpers.RecoveryTests().assistant(ScriptedModelClient([AssistantReply(content='done')]),d)
            assistant.memory.semantic.remember('project','journal test database is SQLite')
            events=[e async for e in assistant.run('journal test database')]
            self.assertFalse(any(e.type=='message_added' and is_context_update(e.message) for e in events))
            self.assertTrue(any(is_context_update(m) for m in assistant.memory.working.load()))
            delta=assistant.memory._consolidation_delta(assistant.loop.messages,0)
            self.assertFalse(any(is_context_update(m) for m in delta))
            journal_messages=[m for m in assistant.loop.messages if is_context_update(m)]
            self.assertEqual(assistant.memory.archive.add_messages('test','trace.jsonl',journal_messages),0)

    async def test_compaction_interval_defers_small_churn_but_bypasses_at_safety_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            context=make_context(Path(d))
            context._messages_after_compaction=0
            context._compact=AsyncMock(return_value=None)
            messages=[ChatMessage(role='user',content='latest'),ChatMessage(role='assistant',content='answer')]
            await context.maybe_compact(messages,4000)
            context._compact.assert_not_awaited()
            self.assertEqual(context.last_compaction_decision['reason'],'minimum_compaction_interval')
            await context.maybe_compact(messages,context.profile.context_window)
            context._compact.assert_awaited_once()

    async def test_verified_file_change_reopens_runtime_and_appends_new_pending_state(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'x').write_text('old')
            assistant=helpers.RecoveryTests().assistant(ScriptedModelClient([]),d)
            p=assistant.task_progress
            p.checkpoint('task',[],'deliver',[{'criterion_id':'c','description':'check x','version':1}])
            p.state['verified']={'c':{'version':1,'artifacts':p._fingerprints(['x'])}}
            assistant._task_updated_this_run=True
            journal=ContextJournal();history=journal.update([],assistant._provide_context_updates())
            self.assertEqual(assistant._request_tools(),[])
            (root/'x').write_text('changed')
            self.assertTrue(assistant._request_tools())
            update=journal.update(history,assistant._provide_context_updates())
            self.assertEqual(decode_update(update[0])['set']['task.delivery_state'],'work_remaining')
            self.assertEqual(decode_update(update[0])['set']['task.pending'],['check x'])
            self.assertIsNotNone(p.final_blocker())
