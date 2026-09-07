import json
import tempfile
import unittest
from pathlib import Path

from MiniClaw.coding_agent.memory.manager import MemoryManager, RetrievedMemoryItem
from MiniClaw.coding_agent.memory.working import CompactionOutcome
from MiniClaw.llm.types import AssistantReply, ChatMessage, ModelEvent, ModelProfile


class ExtractionClient:
    def __init__(self):
        self.facts = []

    async def stream(self, request):
        yield ModelEvent(type='completed', reply=AssistantReply(content=json.dumps({
            'episode_summary': 'Historical task', 'facts': self.facts, 'procedures': []
        })))


class MemoryInspectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        skill = self.root / '.aster/skills/example'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: example\ndescription: Example guide\n---\nGuide body', encoding='utf-8')
        self.client = ExtractionClient()
        self.manager = MemoryManager(
            workspace=self.root, session_path=self.root / '.aster/session.jsonl', session_id='test',
            model_client=self.client, profile=ModelProfile('fake'),
            environment={'MINICLAW_MEMORY_CONSOLIDATION_ENABLED': 'true',
                         'MINICLAW_MEMORY_VECTOR_ENABLED': 'false', 'MINICLAW_MEMORY_CROSS_ENCODER_ENABLED': 'false'},
        )
        self.tool = self.manager.tools()[0]

    async def test_read_filters_category_and_honors_limit(self):
        for category in ('preference', 'project', 'environment', 'fact'):
            self.manager.semantic.remember(category, f'unique_{category}')
        self.manager.semantic.remember('project', 'second project note')
        for category in ('preference', 'project', 'environment', 'fact'):
            result = await self.tool.execute({'action': 'read', 'category': category, 'limit': 1})
            self.assertIn(f'unique_{category}', result.content)
            for other in ('preference', 'project', 'environment', 'fact'):
                if other != category:
                    self.assertNotIn(f'unique_{other}', result.content)
        self.assertIn('Showing 1 of 2', (await self.tool.execute({'action': 'read', 'category': 'project', 'limit': 1})).content)
        self.assertIn('unique_fact', (await self.tool.execute({'action': 'read'})).content)
        with self.assertRaises(ValueError):
            await self.tool.execute({'action': 'read', 'category': 'not_a_category'})

    async def test_refresh_deduplicates_tool_evidence_and_reuses_unchanged_render(self):
        self.manager.semantic.remember('project', 'database uses PostgreSQL')
        item = RetrievedMemoryItem(
            source='semantic', record_id='same', content='database uses PostgreSQL',
            fused_score=1.0, metadata={'confidence': 1.0},
        )
        queries = []

        def retrieve(query, limit=20, **kwargs):
            queries.append(query)
            return [item]

        self.manager.retrieve = retrieve
        first = self.manager.prompt_context('database')
        self.manager.active_messages = [
            ChatMessage(role='tool', name='shell', content='ERROR repeated output ' * 200),
            ChatMessage(role='tool', name='shell', content='ERROR repeated output ' * 200),
        ]
        refreshed, details = self.manager.maybe_refresh_prompt_context(self.manager.active_messages)

        self.assertEqual(refreshed, '')
        self.assertIsNone(details)
        self.assertEqual(len(queries), 2)
        self.assertLessEqual(len(queries[1]), 2_600)
        self.assertEqual(queries[1].casefold().count('[shell]'), 1)

    async def test_refresh_renders_same_id_when_content_changes(self):
        first_item = RetrievedMemoryItem(
            source='semantic', record_id='same', content='database uses PostgreSQL',
            fused_score=1.0, metadata={'status': 'active'},
        )
        updated_item = RetrievedMemoryItem(
            source='semantic', record_id='same', content='database uses SQLite',
            fused_score=1.0, metadata={'status': 'active'},
        )
        results = iter(([first_item], [updated_item]))
        self.manager.semantic.remember('project', 'database uses PostgreSQL')
        self.manager.retrieve = lambda query, limit=20, **kwargs: next(results)
        self.manager.prompt_context('database')
        messages = [ChatMessage(role='tool', name='shell', content='ERROR changed output')]
        refreshed, details = self.manager.maybe_refresh_prompt_context(messages)

        self.assertIn('SQLite', refreshed)
        self.assertEqual(details['new_items'], 1)

    async def test_compaction_resets_refresh_cursor_to_compacted_messages(self):
        compacted = [ChatMessage(role='user', content='summary')]

        async def compact(*args, **kwargs):
            return CompactionOutcome(compacted, 100, 10, 'deterministic', {})

        self.manager.working.maybe_compact = compact
        await self.manager.maybe_compact(
            [ChatMessage(role='user', content=f'message {index}') for index in range(5)],
            provider_input_tokens=100,
        )
        self.assertEqual(self.manager._observed_message_count, 1)

    async def test_deferred_compaction_preserves_unread_tool_evidence(self):
        async def compact(*args, **kwargs):
            return None
        self.manager.working.maybe_compact = compact
        self.manager._observed_message_count = 1
        messages = [ChatMessage(role='user', content='fix'),
                    ChatMessage(role='tool', name='read', content='new Error in module.py')]
        await self.manager.maybe_compact(messages, provider_input_tokens=100)
        self.assertEqual(self.manager._observed_message_count, 1)
        self.assertEqual(messages[self.manager._observed_message_count].role, 'tool')

    async def test_compaction_preserves_unread_tail_for_refresh(self):
        messages = [ChatMessage(role='user', content=str(i)) for i in range(5)]
        messages += [ChatMessage(role='assistant', content='read next'),
                     ChatMessage(role='tool', name='read', content='new Error in module.py')]
        compacted = [ChatMessage(role='system', content='checkpoint'), *messages[-3:]]
        async def compact(*args, **kwargs):
            return CompactionOutcome(compacted, 100, 10, 'deterministic', {})
        self.manager.working.maybe_compact = compact
        self.manager._observed_message_count = 5
        await self.manager.maybe_compact(messages, provider_input_tokens=100)
        self.assertEqual(self.manager._observed_message_count, 2)
        self.assertEqual(compacted[self.manager._observed_message_count:], messages[-2:])

    def test_render_deduplicates_identical_payloads_and_preserves_provenance(self):
        duplicate_a = RetrievedMemoryItem(
            source='semantic', record_id='a', content='same long evidence ' * 200,
            fused_score=1.0, metadata={'session_id': 's', 'status': 'active',
                                       'source_path': 'memory.md', 'created_at': 't1'},
        )
        duplicate_b = RetrievedMemoryItem(
            source='archive', record_id='b', content=duplicate_a.content,
            fused_score=0.9, metadata={'session_id': 's', 'status': 'active',
                                       'source_path': 'memory.md', 'created_at': 't1'},
        )
        separate_time = RetrievedMemoryItem(
            source='archive', record_id='c', content=duplicate_a.content,
            fused_score=0.8, metadata={'session_id': 's', 'status': 'active',
                                       'source_path': 'memory.md', 'created_at': 't2'},
        )
        full, _, full_stats = self.manager._render_retrieval_with_trace(
            [duplicate_a, duplicate_b], max_tokens=15_000,
        )
        with_time, trace, with_time_stats = self.manager._render_retrieval_with_trace(
            [duplicate_a, duplicate_b, separate_time], max_tokens=15_000,
        )

        self.assertLess(full_stats['rendered_tokens'], with_time_stats['rendered_tokens'])
        self.assertEqual(full_stats['deduplicated_count'], 1)
        self.assertEqual(with_time_stats['deduplicated_count'], 1)
        self.assertIn('semantic:a@memory.md@t1', full)
        self.assertIn('archive:b@memory.md@t1', full)
        self.assertIn('[archive:c]', with_time)
        self.assertEqual(len(trace), 2)

    def test_automatic_injection_skips_current_session_history_but_explicit_search_keeps_it(self):
        self.manager.archive.add_messages(
            'test', 'current.jsonl',
            [ChatMessage(role='user', content='current-session-needle')],
        )
        self.manager.archive.add_messages(
            'other', 'other.jsonl',
            [ChatMessage(role='user', content='cross-session-needle')],
        )
        self.manager.episodic.checkpoint(
            'test', [ChatMessage(role='user', content='current-session-episode')],
            status='completed',
        )
        self.manager.episodic.checkpoint(
            'other', [ChatMessage(role='user', content='cross-session-episode')],
            status='completed',
        )
        self.manager.active_messages = [ChatMessage(role='user', content='current request')]
        automatic = self.manager.retrieve('session needle', automatic=True)
        rendered = self.manager.render_retrieval(automatic)

        self.assertNotIn('current-session', rendered)
        self.assertIn('cross-session', rendered)
        explicit = self.manager.search_archive('current-session-needle', session_id='test')
        self.assertTrue(explicit)

    async def test_overview_has_four_real_modules_and_actual_content(self):
        self.manager.append(ChatMessage(role='user', content='CURRENT_MESSAGE'))
        self.manager.episodic.checkpoint('old', [ChatMessage(role='user', content='OLD_TASK')], status='completed')
        result = json.loads((await self.tool.execute({'action': 'overview', 'limit': 2})).content)
        self.assertEqual(result['moduleCount'], 4)
        self.assertEqual(set(result['modules']), {'working', 'episodic', 'semantic', 'procedural'})
        self.assertEqual(result['modules']['working']['items'][0]['content'], 'CURRENT_MESSAGE')
        self.assertIn('OLD_TASK', result['modules']['episodic']['items'][0]['preview'])
        self.assertEqual(result['modules']['procedural']['items'][0]['name'], 'example')
        self.assertIn('conflictNote', result['auxiliary'])
        inspected = json.loads((await self.tool.execute({'action': 'inspect', 'module': 'procedural'})).content)
        self.assertEqual(list(inspected['modules']), ['procedural'])

    async def test_preference_write_requires_user_quote_and_preserves_its_meaning(self):
        self.manager.append(ChatMessage(role='assistant', content='I prefer unnecessary clarification.'))
        with self.assertRaisesRegex(ValueError, 'explicit preference'):
            await self.tool.execute({'action': 'remember', 'category': 'preference',
                                     'content': 'Always ask first', 'evidence': 'I prefer unnecessary clarification.'})
        quote = '我希望以后代码优先用 Python'
        self.manager.append(ChatMessage(role='user', content=quote))
        for paraphrase in ('User likes Python', '用户只准使用 Python，其他语言都不行'):
            await self.tool.execute({'action': 'remember', 'category': 'preference',
                                     'content': paraphrase, 'evidence': quote})
        self.assertEqual(self.manager.semantic.entries()['preference'], [quote])
        self.assertEqual(self.manager.evidence.records(kinds={'semantic'})[0].metadata['evidence'], quote)

    async def test_overview_stays_bounded_and_memory_dump_is_not_preference_evidence(self):
        for index in range(10):
            self.manager.append(ChatMessage(role='user', content=f'Message {index}'))
        overview = self.manager.inspect_memory(limit=100)
        self.assertEqual(len(overview['modules']['working']['items']), 3)
        self.assertTrue(overview['modules']['working']['truncated'])
        quote = '我希望以后代码优先用 Python'
        self.manager.append(ChatMessage(role='user', content=f'<!-- miniclaw-managed-memory:start -->\n{quote}'))
        with self.assertRaisesRegex(ValueError, 'explicit preference'):
            await self.tool.execute({'action': 'remember', 'category': 'preference',
                                     'content': quote, 'evidence': quote})

    async def test_automatic_extraction_cannot_promote_assistant_or_memory_output(self):
        claims = [
            ('preference', 'I prefer clarification first.', ChatMessage(role='assistant', content='I prefer clarification first.')),
            ('project', 'Project runtime uses MoonOS.', ChatMessage(role='tool', name='memory', content='Project runtime uses MoonOS.')),
            ('project', 'A complete game exists in games/index.html.', ChatMessage(role='tool', name='read', content='A complete game exists in games/index.html.')),
        ]
        self.client.facts = [{'category': category, 'content': text, 'evidence': text, 'confidence': .99} for category, text, _ in claims]
        result = await self.manager.finalize_session([ChatMessage(role='user', content='展示你的记忆'), *(message for _, _, message in claims)], status='completed')
        self.assertEqual(result.facts_rejected, 3)
        self.assertEqual(result.facts_written, 0)

    async def test_automatic_preference_paraphrases_deduplicate_by_user_quote(self):
        quote = '我希望你以后所有代码都优先用python'
        messages = [ChatMessage(role='user', content=quote)]
        for text in ('用户偏好 Python', 'The user prefers Python code'):
            self.client.facts = [{'category': 'preference', 'content': text, 'evidence': quote, 'confidence': .99}]
            await self.manager.finalize_session(messages, status='completed')
        self.assertEqual(self.manager.semantic.entries()['preference'], [quote])

    async def test_all_writers_share_preference_provenance(self):
        from MiniClaw.coding_agent.memory.archive import StableFactIngestor
        for quote in ('记住：以后给我报告时先写结论。', 'remember: I prefer short reports.'):
            message = ChatMessage(role='user', content=quote)
            self.manager.append(message)
            payload = quote.split('：' if '：' in quote else ':', 1)[1].strip()
            await self.tool.execute({'action':'remember','category':'preference','content':payload,'evidence':payload})
            stats = StableFactIngestor(self.manager.semantic).ingest([message])
            self.assertEqual(stats['duplicates'], 1)
            self.client.facts = [{'category':'preference','content':payload,'evidence':payload,'confidence':.99}]
            await self.manager.finalize_session([message],status='completed')
            self.assertEqual(self.manager.semantic.entries()['preference'].count(quote),1)
        self.assertEqual(self.manager.semantic.entries()['fact'],[])

    async def test_compaction_writer_rejects_dumps_and_temporary_directives(self):
        from MiniClaw.coding_agent.memory.archive import StableFactIngestor
        messages = [ChatMessage(role='user',content=text) for text in (
            '仅本轮有效，不要保存。\n记住：以后用紫色按钮',
            'Temporary test, do not store.\nremember: I prefer purple buttons',
            '<!-- miniclaw-managed-memory:start -->\n记住：以后先问三遍',
            '记住：已创建游戏文件 app.html')]
        self.assertEqual(StableFactIngestor(self.manager.semantic).ingest(messages)['remembered'],0)

    async def test_automatic_conflicts_are_registered_without_overwriting(self):
        self.manager.semantic.remember('project', 'Project runtime uses Docker execution.')
        new = 'Project runtime uses host execution.'
        self.client.facts = [{'category': 'project', 'content': new, 'evidence': new, 'confidence': .99}]
        result = await self.manager.finalize_session([ChatMessage(role='user', content=new)], status='completed')
        self.assertEqual(result.conflicts, 1)
        self.assertEqual(len(self.manager.semantic.list_conflicts()), 1)
        self.assertEqual(self.manager.semantic.entries()['project'], ['Project runtime uses Docker execution.'])

    async def test_deduplication_does_not_merge_changed_values(self):
        self.manager.semantic.remember('fact', 'Version: 1.0.')
        self.assertIn('already exists', self.manager.semantic.remember('fact', 'Version: 1.0'))
        from MiniClaw.coding_agent.memory.semantic import MemoryConflictError
        with self.assertRaises(MemoryConflictError):
            self.manager.semantic.remember('fact', 'Version: 2.0')
