"""Retire exact fact duplicates created by the old explicit-directive ingestor.

Only touches facts whose audit ledger identifies them as automatic directives
and whose complete payload already exists as a preference. No fuzzy matching.
"""
import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from MiniClaw.coding_agent.memory.semantic import SemanticMemoryStore
from MiniClaw.coding_agent.memory.evidence import MemoryEvidenceStore
from MiniClaw.coding_agent.memory.locking import MemoryFileLock


def reconcile(workspace, apply=False):
    root = Path(workspace).resolve()
    store = SemanticMemoryStore(root / '.aster/memory/MEMORY.md')
    ledger = MemoryEvidenceStore(root / '.aster/memory/evidence.jsonl')
    with MemoryFileLock(store.path):
        entries = store.entries()
        payloads = {store.equivalence_key(re.sub(r'^(?:记住|remember)\s*(?:\[preference\])?\s*[:：]\s*', '', text, flags=re.I))
                    for text in entries['preference']}
        automatic = {record.content for record in ledger.records(kinds={'semantic'})
                     if record.metadata.get('explicit_user_directive') and record.metadata.get('category') == 'fact'}
        retired = [text for text in entries['fact'] if text in automatic and store.equivalence_key(text) in payloads]
        result = {'retired': retired, 'applied': False}
        if apply and retired:
            backup = root / '.aster/memory-reviews' / ('directives-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
            backup.mkdir(parents=True)
            for source in (store.path, root / '.aster/memory/evidence.jsonl'):
                if source.exists():
                    shutil.copyfile(source, backup / source.name)
                    assert source.read_bytes() == (backup / source.name).read_bytes()
            entries['fact'] = [text for text in entries['fact'] if text not in retired]
            store._write(entries)
            for text in retired:
                ledger.append(kind='semantic_review', content=text, session_id='directive-reconciliation',
                              source_path=str(backup / 'MEMORY.md'),status='inactive',
                              metadata={'reason':'Exact payload already retained as preference; legacy ingestor assigned fact'})
            result.update(applied=True, backup=str(backup))
            (backup / 'review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace')
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    print(json.dumps(reconcile(args.workspace,args.apply),ensure_ascii=False,indent=2))
