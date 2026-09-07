import json,shutil
from pathlib import Path
src=Path('.aster/evals/miniclaw-eval3-single20/report.json'); dst=Path('docs/interview/eval no 3/eval3-report.json'); shutil.copy2(src,dst)
j=json.loads(src.read_text(encoding='utf-8')); s=j['summary']; m=s['metrics']; rows=[]
for c in j.get('cases',[]): rows.append(f"<tr><td>{c['id']}</td><td>{c.get('outcome_status','pending')}</td><td>{c.get('program_grading_status','pending')}</td><td>{c.get('error','')}</td></tr>")
html=f'''<!doctype html><meta charset="utf-8"><title>MiniClaw Eval3</title><style>body{{font:14px system-ui;margin:32px;background:#f6f7fb}}.card{{display:inline-block;background:white;padding:16px;margin:6px;border-radius:10px}}table{{background:white;border-collapse:collapse;width:100%}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}</style><h1>MiniClaw Eval3 · Luna 20题</h1><div class="card">执行题数：{m.get('runs',0)}</div><div class="card">模型请求：{m.get('model_requests',0)}</div><div class="card">总 tokens：{m.get('total_tokens',0):,}</div><div class="card">缓存占比：{m.get('cache_ratio',0)*100:.1f}%</div><div class="card">工具错误：{m.get('tool_errors',0)}</div><div class="card">模型错误：{m.get('model_errors',0)}</div><h2>逐题结果</h2><table><tr><th>题目</th><th>结果</th><th>程序评分</th><th>错误</th></tr>{''.join(rows)}</table>'''
Path('frontend/eval-round1/eval3-report.html').write_text(html,encoding='utf-8')
