import json
from pathlib import Path
p=Path('frontend/eval-round1/dashboard.html'); s=p.read_text(encoding='utf-8'); report=json.loads(Path('docs/interview/eval no 3/eval3-reviewed-report.json').read_text(encoding='utf-8')); m=report['summary']['metrics']; cases=report['cases']
rows=[]
for c in cases:
 d=c.get('dimensions',{}); rows.append(f"<tr><td>{c['id']}</td><td>{'待复核' if d.get('outcome') is None else ('通过' if d.get('outcome') else '未通过')}</td><td>{d.get('process',{}).get('score','—') if isinstance(d.get('process'),dict) else '—'}</td><td>{c.get('metrics',{}).get('total_tokens',0):,}</td><td>{c.get('metrics',{}).get('tool_errors',0)}</td></tr>")
sec=f'''<section id="eval3-final" class="section" style="margin-top:40px"><div class="section-kicker">第三轮评测</div><h2>Eval3 · Luna 20题复核结果</h2><p class="section-lede">沿用前两轮结构：结果交给 LLM judge，过程、效率、安全、可用性由程序评分；429 和网络中断单独记录。</p><div class="kpi-grid"><article class="card kpi"><div class="kpi-label">执行题数</div><div class="kpi-value">{m.get('runs',0)}<small>/ 20</small></div></article><article class="card kpi"><div class="kpi-label">总 tokens</div><div class="kpi-value">{m.get('total_tokens',0):,}</div></article><article class="card kpi"><div class="kpi-label">缓存占比</div><div class="kpi-value">{m.get('cache_ratio',0)*100:.1f}%</div></article><article class="card kpi"><div class="kpi-label">工具错误</div><div class="kpi-value">{m.get('tool_errors',0)}</div></article></div><div class="card" style="margin-top:18px;overflow:auto"><table><thead><tr><th>题目</th><th>结果</th><th>过程</th><th>Tokens</th><th>工具错误</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><p style="margin-top:12px"><a href="../docs/interview/eval%20no%203/eval3-reviewed-report.json">下载完整复核报告</a></p></section>'''
# replace prior simple section
start=s.find('<section id="eval3"'); end=s.find('</section>',start)+len('</section>') if start>=0 else -1
if start>=0:s=s[:start]+sec+s[end:]
else:s=s.replace('</body>',sec+'</body>')
p.write_text(s,encoding='utf-8')
