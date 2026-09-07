import json,shutil
from pathlib import Path
src=Path('.aster/evals/miniclaw-eval3-single20/report.json'); out=Path('docs/interview/eval no 3'); j=json.loads(src.read_text(encoding='utf-8'))
review={'schema':'eval3-program-reviewed-v1','grading_method':'结果维度待 LLM judge；过程/效率/安全/可用性由程序评分；429/网络错误剔除','summary':j['summary'],'cases':j['cases']}
(out/'eval3-reviewed-report.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
# append dashboard link/section via small standalone include
p=Path('frontend/eval-round1/dashboard.html'); s=p.read_text(encoding='utf-8'); marker='<!-- EVAL3 -->'
if marker not in s:
 s=s.replace('</body>',f'<section id="eval3" style="margin:32px"><h2>Eval3 · Luna 20题（程序复核版）</h2><p>结果维度待 LLM judge；其余维度按程序评分。<a href="eval3-report.html">查看逐题明细</a></p></section>{marker}</body>')
 p.write_text(s,encoding='utf-8')
shutil.copy2(src,out/'eval3-raw-report.json')
