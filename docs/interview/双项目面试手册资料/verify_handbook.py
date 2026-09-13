from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import math
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCUMENT = ROOT.parent / '双项目与完整简历_面试精讲与口语回答大全.md'
text = DOCUMENT.read_text(encoding='utf-8')

# Add explicit, stable anchors and a navigable chapter table of contents.
text = text.replace('\\[\n', '$$\n').replace('\\]\n', '$$\n')
chapters = re.findall(r'^## (\d{2})\. (.+)$', text, flags=re.M)
assert len(chapters) >= 30
assert [int(number) for number, _ in chapters] == list(range(1, len(chapters) + 1))
if '<!-- TOC -->' in text:
    toc = '\n'.join(f'- [{number}. {title}](#chapter-{number})' for number, title in chapters)
    text = text.replace('<!-- TOC -->', toc)
    text = re.sub(r'^## (\d{2})\. (.+)$',
                  lambda m: f'<a id="chapter-{m[1]}"></a>\n\n{m[0]}', text, flags=re.M)
DOCUMENT.write_text(text, encoding='utf-8', newline='\n')

questions = re.findall(r'^### (\d{2}\.\d+) (.+)$', text, flags=re.M)
assert len({number for number, _ in questions}) == len(questions)
fences = re.findall(r'^```.*$', text, flags=re.M)
assert len(fences) % 2 == 0, 'unbalanced code fences'
assert len(re.findall(r'^\$\$$', text, flags=re.M)) % 2 == 0, 'unbalanced equations'
assert '\ufffd' not in text
assert '<!-- TOC -->' not in text

local_links = re.findall(r'\]\((D:/[^)]+)\)', text)
missing = [path for path in local_links if not Path(path).exists()]
assert not missing, missing
anchors = set(re.findall(r'<a id="([^"]+)"></a>', text))
assert all(anchor in anchors for anchor in re.findall(r'\]\(#([^)]+)\)', text))

terms = ['Python', 'PyTorch', 'RLHF', 'PPO', 'DPO', 'GRPO', 'RoPE', 'KV Cache',
         'Flash Attention', 'Transformer', 'Agent', 'Tool Calling', 'Docker',
         'pimono', 'pi-mono', 'BM25', 'BGE-M3', 'RRF', 'Cross-Encoder', 'veRL',
         'vLLM', 'Rubric', 'ReAct', 'RAG', 'LoRA', 'SFT', 'on-policy',
         'Assistant-only Loss', 'Checkpoint', 'Hard', 'Soft', 'Harness',
         'Action Guard', 'Observation', 'WorkspaceGuard', 'Tool Runtime',
         'Teacher', 'Benchmark', 'Trace', 'LLM-as-Judge', 'Core-180',
         'Challenge-60', 'pass@4', 'pass^4', 'Qwen3.5-2B', 'Qwen3.8-27B']
assert all(term in text for term in terms)

snippets = re.findall(r'^```python\n(.*?)^```\s*$', text, flags=re.M | re.S)
assert len(snippets) == 12, len(snippets)
for i, source in enumerate(snippets, 1):
    ast.parse(source, filename=f'handbook_snippet_{i}')

namespace = {}
for source in snippets:
    exec(compile(source, '<handbook-example>', 'exec'), namespace)
torch = namespace['torch']
torch.set_num_threads(1)

checks = []
def checked(name, result):
    assert result, name
    checks.append(name)

softmax = namespace['stable_softmax']([1000.0, 1001.0])
checked('stable_softmax_numeric', abs(sum(softmax) - 1) < 1e-12 and softmax[1] > softmax[0])

q = torch.zeros(1, 1, 3, 2)
v = torch.tensor([[[[1., 2.], [3., 4.], [5., 6.]]]])
attention = namespace['causal_attention'](q, q, v)
checked('causal_attention_visible_prefix', torch.allclose(attention, torch.tensor([[[[1., 2.], [2., 3.], [3., 4.]]]])))

x = torch.arange(24, dtype=torch.float32).reshape(1, 2, 3, 4)
rope = namespace['apply_rope'](x, torch.arange(3))
checked('rope_position_zero', torch.allclose(rope[:, :, 0], x[:, :, 0]))
checked('rope_preserves_norm', torch.allclose(rope.square().sum(-1), x.square().sum(-1), atol=1e-3))

logits = torch.zeros(1, 4, 5, requires_grad=True)
ids = torch.tensor([[0, 1, 2, 3]])
loss = namespace['assistant_only_loss'](logits, ids, torch.tensor([[False, False, True, True]]))
loss.backward()
checked('assistant_mask_loss', abs(loss.item() - math.log(5)) < 1e-6)
checked('assistant_mask_gradient', logits.grad[0, 0].abs().sum().item() == 0 and logits.grad[0, 1].abs().sum().item() > 0)

rewards = torch.tensor([[1., .8, .5, -1.], [1., 1., 1., 1.]])
adv = namespace['grouped_advantage'](rewards)
checked('grpo_group_centering', torch.allclose(adv[0], torch.tensor([.675, .475, .175, -1.325])) and torch.equal(adv[1], torch.zeros(4)))

new_logp = torch.log(torch.tensor([[1.5], [.5], [.5], [1.5]])).requires_grad_()
policy_loss = namespace['clipped_policy_loss'](new_logp, torch.zeros_like(new_logp),
        torch.tensor([1., 1., -1., -1.]), torch.ones_like(new_logp, dtype=torch.bool))
policy_loss.backward()
checked('ppo_positive_negative_clipping', abs(policy_loss.item() - .15) < 1e-6)
checked('ppo_clip_gradient_direction', new_logp.grad[0].item() == 0 and new_logp.grad[2].item() == 0 and new_logp.grad[1].item() < 0 and new_logp.grad[3].item() > 0)

zero = torch.zeros(3)
checked('dpo_equal_policy_reference', abs(namespace['dpo_loss'](zero, zero, zero, zero).item() - math.log(2)) < 1e-6)
rrf = namespace['reciprocal_rank_fusion']([['A', 'B', 'C'], ['B', 'D', 'A']])
checked('rrf_worked_example', [row[0] for row in rrf[:2]] == ['B', 'A'])
checked('wait_interval_union', namespace['interval_union_duration']([(1, 5), (3, 7), (10, 12)]) == 8)

cache = namespace['LRUCache'](2)
cache.put('a', 1)
cache.put('b', 2)
cache.get('a')
cache.put('c', 3)
checked('lru_recency_eviction', cache.get('b') is None and cache.get('a') == 1)

async def run_async_example():
    active = 0
    peak = 0
    async def worker(value):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return value * 2
    result = await namespace['bounded_map'](list(range(10)), worker, concurrency=3)
    return result == [i * 2 for i in range(10)] and peak <= 3
checked('bounded_async_order_and_limit', asyncio.run(run_async_example()))

with tempfile.TemporaryDirectory(prefix='interview-path-') as directory:
    base = Path(directory)
    checked('workspace_path_inside', namespace['checked_workspace_path'](base, 'a.txt') == base / 'a.txt')
    try:
        namespace['checked_workspace_path'](base, '../outside.txt')
    except PermissionError:
        checks.append('workspace_path_escape_rejected')
    else:
        raise AssertionError('workspace escape not rejected')

shopping = Path('D:/shopping-grpo-longhorizon-main2-reward-v4')
four_round = json.loads((shopping / '重点/4.评测阶段/四轮评测/pass-at-4-and-pass-power-4.json').read_text(encoding='utf-8'))
g = four_round['GRPO230-v3']
qwen = four_round['Qwen38-27B-v3']
checked('four_round_counts', g['pass@4_count'] == 202 and g['pass^4_count'] == 157 and sum(g['per_round_successes']) == 720)
checked('reference_stability', qwen['pass^4_count'] == 169 and sum(qwen['per_round_successes']) == 694)
checked('teacher_source_total', sum([4762, 1886, 1746, 274, 40]) == 8708)
checked('rubric_total', 1611 + 72 + 79 + 7 == 1769 and 1611 - 1555 == 56)

report = {
    'document': str(DOCUMENT),
    'chapter_count': len(chapters),
    'subsection_count': len(questions),
    'characters': len(text),
    'chinese_characters': len(re.findall(r'[\u4e00-\u9fff]', text)),
    'lines': len(text.splitlines()),
    'bytes': DOCUMENT.stat().st_size,
    'sha256': hashlib.sha256(DOCUMENT.read_bytes()).hexdigest(),
    'required_terms_covered': len(terms),
    'local_links_checked': len(local_links),
    'python_snippets_syntax_checked': len(snippets),
    'example_and_metric_checks': checks,
    'token_reduction_pct': (5090509 - 2218747) / 5090509 * 100,
    'cache_increase_pp': 56.44 - 37.43,
    'cache_relative_increase_pct': (56.44 - 37.43) / 37.43 * 100,
    'judge_mean_gap': sum([1.642, 1.629, 1.650, 1.583, 1.500]) / 5 - sum([1.667, 1.583, 1.504, 1.550, 1.587]) / 5,
    'scope': 'Document and illustrative CPU examples only; no project training or model evaluation executed.'
}
(ROOT / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=True, indent=2))
