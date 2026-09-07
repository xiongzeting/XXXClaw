"""Summarize a finished run without model calls; estimates are not billing usage."""
import argparse
from collections import Counter, defaultdict
from dataclasses import fields
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from MiniClaw.llm.types import ChatMessage, ModelProfile, ModelRequest, ToolInvocation
from MiniClaw.trace.model_client import _input_token_breakdown


def summarize(report_path):
    report = json.loads(report_path.read_text(encoding='utf-8'))
    rows = []
    for case in report['cases']:
        if case['category'] not in {'compression', 'recall'}:
            continue
        attempt = case['attempts'][0]
        by_purpose = defaultdict(Counter)
        sources = Counter()
        for trace in {p['trace_path'] for p in attempt['phases'].values()}:
            path = Path(trace)
            if not path.is_absolute():
                path = ROOT / path
            for line in path.read_text(encoding='utf-8').splitlines():
                event = json.loads(line)
                if event.get('type') != 'model.request':
                    continue
                d = event['data']
                purpose = d.get('purpose', 'agent')
                by_purpose[purpose].update({k: d.get('usage', {}).get(k, 0) for k in ('input_tokens', 'output_tokens', 'cached_tokens')})
                by_purpose[purpose]['calls'] += 1
                by_purpose[purpose]['duration_ms'] += d.get('duration_ms') or 0
                if purpose != 'agent':
                    continue
                messages = []
                for m in d.get('context', {}).get('messages', []):
                    values = {f.name: m[f.name] for f in fields(ChatMessage) if f.name in m}
                    values['tool_calls'] = [ToolInvocation(**t) for t in m.get('tool_calls', [])]
                    messages.append(ChatMessage(**values))
                request = ModelRequest(profile=ModelProfile('accounting-only'), messages=messages,
                                       tools=d.get('context', {}).get('tools', []))
                sources.update(_input_token_breakdown(request)['by_source'])
        rows.append({'id': case['id'], 'passed': case['passed'], 'metrics': attempt['metrics'],
                     'failed_checks': [{'dimension': c['dimension'], 'detail': c['detail']} for c in attempt['checks'] if c.get('required', True) and not c['passed']],
                     'provider_by_purpose': dict(by_purpose),
                     'estimated_agent_input_sources': dict(sources)})
    return {'report': str(report_path), 'estimate_method': 'utf8_bytes_div_4_ceil; attribution only, not provider tokens', 'cases': rows}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('report', type=Path)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    result = summarize(args.report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for c in result['cases']:
        print(c['id'], c['metrics']['total_tokens'], c['estimated_agent_input_sources'])
