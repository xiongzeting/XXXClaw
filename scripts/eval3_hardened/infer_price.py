"""User-supplied billing samples, separate from runtime's unconfigured prices."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
samples=[(7182,254,6656,.000544),(6385,357,5632,.000692),(9031,485,7680,.001006),(7600,957,6656,.001470),(7866,97,1536,.001414),(7903,811,0,.002554)]
rows=[]
for inp,out,cached,charged in samples:
    estimated=((inp-cached)*.2+cached*.02+out*1.2)/1_000_000
    rows.append({'input_tokens':inp,'output_tokens':out,'cached_input_tokens':cached,'displayed_usd':charged,'estimated_usd':estimated,'difference_usd':charged-estimated})
result={'source':'User supplied six provider billing rows dated 2026-09-07 22:11:52..58 Asia/Shanghai',
        'currency':'USD','input_per_million':.2,'cached_input_per_million':.02,'output_per_million':1.2,
        'confidence':'Input/output explicitly displayed; cache rate inferred, all six examples within $0.000001.',
        'basis':'billing-sample inference, not provider invoice for entire run','samples':rows}
assert all(abs(x['difference_usd'])<.000001 for x in rows)
folder=ROOT/'.aster/evals/eval3-hardened-r1/preflight';folder.mkdir(parents=True,exist_ok=True)
(folder/'inferred-prices.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n','utf-8')
print(json.dumps(result,ensure_ascii=False))
