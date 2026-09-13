"""Validate publication provenance, source preservation, links and offline rebuild."""
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0,str(Path(__file__).parent))
from publish import ROOT, FRONT, ARCHIVE, embed_dashboard

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
report=json.loads((ARCHIVE/'assistant-judged-report.json').read_text('utf-8'))
out=Path(report['source_run_directory'])
for rel,expected in report['source_hashes'].items():
    path=Path(__file__).with_name('assistant_judgments.json') if rel=='assistant_judgments.json' else out/rel
    assert sha(path)==expected,rel
    assert sha(ARCHIVE/rel)==expected,rel
assert json.loads((out/'report.json').read_text('utf-8'))['cases']
before=sha(FRONT/'dashboard.html');embed_dashboard();assert sha(FRONT/'dashboard.html')==before
html=(FRONT/'dashboard.html').read_text('utf-8')
assert not re.search(r'<script src=|<link rel="stylesheet"',html)
assert html.count('<section id="eval3-r1"')==1
files=[ARCHIVE/'最终评测报告.md',ARCHIVE.parent/'Eval3五维能力提升路线.md',ARCHIVE.parent/'README.md',ARCHIVE.parent/'题目设计.md']
checked=0
for p in files:
    for target in re.findall(r'\]\(([^)]+)\)',p.read_text('utf-8')):
        target=unquote(target.strip('<>')).split('#')[0]
        if target.startswith(('https://','http://')):continue
        target=re.sub(r':\d+$','',target)
        path=Path(target)
        if not path.is_absolute():path=p.parent/path
        assert path.exists(),(p,target)
        checked+=1
assert abs(sum(c['observations']['effective_seconds'] for c in report['cases'])-445.147)<0.001
assert all(abs(c['observations']['raw_wall_seconds']-c['observations']['model_wait_union_seconds']-c['observations']['effective_seconds'])<0.002 for c in report['cases'])
print(json.dumps({'source_hashes_verified':len(report['source_hashes']),'markdown_links_verified':checked,'offline_rebuild_idempotent':True,'effective_time_verified':True}))
