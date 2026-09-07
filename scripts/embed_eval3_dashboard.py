import re
from pathlib import Path
p=Path('frontend/eval-round1/dashboard.html'); s=p.read_text(encoding='utf-8'); d=Path('frontend/eval-round1/round2-data.js').read_text(encoding='utf-8').strip(); s=re.sub(r'<script>window\.EVAL_ROUND2 = .*?;</script>', '<script>'+d+'</script>', s, count=1, flags=re.S); p.write_text(s,encoding='utf-8')
