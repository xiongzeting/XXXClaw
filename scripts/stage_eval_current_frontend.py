"""Prepare the replacement offline report without publishing until UI checks pass."""
import json
from pathlib import Path
import re
import shutil
from build_eval_current_round2 import build_current

ROOT=Path(__file__).resolve().parents[1]
FRONT=ROOT/'frontend/eval-round1'
STAGE=ROOT/'.aster/frontend-v1-v10-stage'


def main():
    STAGE.mkdir(exist_ok=True)
    for name in ['index.html','styles.css','improvements.css','app.js','data.js','round1-data.json']:
        shutil.copy2(FRONT/name,STAGE/name)
    data=build_current(ROOT,STAGE)
    shutil.copy2(ROOT/'scripts/ui/eval-current-rounds.js',STAGE/'round2.js')
    css=(FRONT/'round2.css').read_text(encoding='utf-8')
    css=re.sub(r'#round2(?![-\w])',':is(#round2,#round3)',css)
    css=css.replace('#round2-empty',':is(#round2-empty,#round3-empty)').replace('#round2-detail',':is(#round2-detail,#round3-detail)')
    css+='\n:is(#round2,#round3) .review-note{max-width:380px;line-height:1.6} :is(#round2,#round3) .compact-kpis strong{overflow-wrap:anywhere;font-size:24px} :is(#round2-detail,#round3-detail) pre{white-space:pre-wrap;overflow-wrap:anywhere} :is(#round2-detail,#round3-detail) details{margin:12px 0}\n'
    css+='\n@media(min-width:1100px){:is(#round2,#round3) .filters{display:grid;grid-template-columns:minmax(220px,2fr) repeat(3,minmax(110px,1fr)) auto auto;align-items:center} :is(#round2,#round3) .filters .search-field{width:auto;grid-column:auto} :is(#round2,#round3) .filters select{width:100%}}\n'
    (STAGE/'round2.css').write_text(css,encoding='utf-8')
    html=(STAGE/'index.html').read_text(encoding='utf-8')
    html=html.replace('<title>MiniClaw · Eval 第一轮与第二轮</title>','<title>MiniClaw · 第一轮、v1 重跑与新版 20 题</title>')
    html=html.replace('aria-label="第二轮 Eval 报告"','aria-label="v1 重跑 20 题报告"')
    html=html.replace('Evaluation / Round 01 + 02','Evaluation / Round 01 + v1 + v10')
    (STAGE/'index.html').write_text(html,encoding='utf-8')
    for name in ['styles.css','improvements.css','round2.css']:
        html=html.replace(f'<link rel="stylesheet" href="{name}">','<style>'+(STAGE/name).read_text(encoding='utf-8')+'</style>')
    for name in ['data.js','round2-data.js','round2.js','app.js']:
        html=html.replace(f'<script src="{name}"></script>','<script>'+(STAGE/name).read_text(encoding='utf-8')+'</script>')
    (STAGE/'dashboard.html').write_text(html,encoding='utf-8')
    print(json.dumps({b['version']:{'counts':b['counts'],'passed':b['passed'],'tokens':b['totals']['total_tokens']} for b in data['batches']}))


if __name__=='__main__':main()
