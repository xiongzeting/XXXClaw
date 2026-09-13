(() => {
  'use strict';
  const root = document.getElementById('eval3-r1'), data = window.EVAL3_R1;
  if (!root || !data) return;
  const s = data.summary, dims = {outcome:'结果',process:'过程',efficiency:'效率',safety:'安全',reliability:'可用性'};
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = v => v == null ? '未知' : Number(v).toLocaleString('en-US');
  const sec = v => v == null ? '无法准确计算' : Number(v).toFixed(3);
  const pill = ok => `<span class="r1-${ok?'pass':'fail'}">${ok?'通过':'失败'}</span>`;
  const archive = '../../docs/interview/eval%20no%203/2026-09-07-hardened-r1/';
  root.innerHTML = `
    <header class="r1-hero"><div class="r1-eyebrow">EVAL 03 / HARDENED R1 / 2026.09.07</div><h2>Eval3 强化版 · 难点落在真实交付</h2><p>五维全过 <b>${s.overall_passed} / ${s.cases}</b> · 结果通过 <b>${s.dimension_pass_counts.outcome} / ${s.cases}</b> · ${s.model} · ${s.jobs} 路并行 · ${s.phase_count} 个阶段</p><p>结果由助手逐题审查，过程、效率、安全、可用性由程序检查。每题运行一次，任务族均已曝光。</p><div class="r1-run">${esc(data.run_id)}</div></header>
    <div class="r1-grid">${Object.entries(dims).map(([key,label])=>`<article class="r1-stat" data-r1-dimension="${key}">${label}<strong>${s.dimension_pass_counts[key]} / ${s.cases}</strong><small>${key==='outcome'?'助手判断':`程序原分 ${s.original_program_pass_counts[key]} → 复核 ${s.dimension_pass_counts[key]}`}</small><div class="r1-bar"><i style="width:${100*s.dimension_pass_counts[key]/s.cases}%"></i></div></article>`).join('')}</div>
    <div class="r1-callout"><b>下一轮优先：守住阶段权限，再减少契约遗漏与重复上下文。</b><br>9 题出现只读阶段写入；12 题 token 超预算。安全原分 4/20 → 11/20 来自有证据的程序误报修正，真实违规继续保留。</div>
    <article class="r1-panel"><h3>成本与有效耗时</h3><div class="r1-metrics">
      <div>单题有效耗时累计<strong>${sec(s.sum_case_effective_seconds)} s</strong><small>逐题扣除模型等待并集；累计值不是批次墙钟</small></div>
      <div>单题有效耗时中位数<strong>${sec(s.median_case_effective_seconds)} s</strong><small>完整时间记录；缺失 ${s.missing_time_records}</small></div>
      <div>原始批次墙钟<strong>${sec(s.batch_raw_wall_seconds)} s</strong><small>${s.jobs} 路并行，不与不同并发直接比较</small></div>
      <div>总 tokens<strong>${num(s.total_tokens)}</strong><small>输入 ${num(s.input_tokens)} · 输出 ${num(s.output_tokens)}</small></div>
      <div>缓存输入占比<strong>${s.observed_cache_hit_percent.toFixed(2)}%</strong><small>${num(s.cached_input_tokens)} / ${num(s.input_tokens)} 输入 tokens</small></div>
      <div>已上报回复费用小计<strong>$${s.known_cost_subtotal_usd.toFixed(6)}</strong><small>完整账单未知；2 次失败传输尝试用量未上报</small></div></div>
      <p class="r1-note">${num(s.model_requests)} 次逻辑模型请求 · ${num(s.tool_calls)} 次工具调用 / ${num(s.tool_errors)} 次真实错误 · 2 次超时重试恢复，未重放工具副作用。普通输入 $0.20/M、缓存输入 $0.02/M、输出 $1.20/M（用户账单推算）。</p>
      <details><summary>等待区间与计费边界</summary><p class="r1-note">全局模型等待并集 ${sec(s.batch_model_wait_union_seconds)} 秒；剩余 ${sec(s.batch_effective_wall_seconds)} 秒仅为并发覆盖诊断，不能表述为 20 题在 4 秒完成。${esc(s.accounting_note)} 缓存覆盖全部 462 个最终回复；不代表超时尝试没有费用。</p></details></article>
    <article class="r1-panel"><h3>20 题 · 五维判定与失败证据</h3><p class="r1-note">点击题目查看结果理由、程序检查、原分与修正分及证据路径。五维全部通过才算总通过。</p>
      <div class="r1-toolbar"><input id="r1-search" aria-label="搜索 Eval3 题目或判定理由" placeholder="搜索题目或判定理由"><select id="r1-filter" aria-label="按失败维度筛选"><option value="all">全部题目</option><option value="passed">五维全过</option><option value="failed">任一维度失败</option>${Object.entries(dims).map(([k,v])=>`<option value="${k}">${v}失败</option>`).join('')}</select><select id="r1-sort" aria-label="排序"><option value="default">题库顺序</option><option value="tokens">Tokens 从高到低</option><option value="time">有效耗时从高到低</option></select><button id="r1-reset">重置</button><button id="r1-csv">导出筛选 CSV</button><button id="r1-json">导出本批 JSON</button></div>
      <p id="r1-count" class="r1-note" aria-live="polite"></p><div class="r1-scroll"><table><thead><tr><th>题目</th>${Object.values(dims).map(v=>`<th>${v}</th>`).join('')}<th>全过</th><th>Tokens</th><th>有效秒</th><th>工具错误</th></tr></thead><tbody id="r1-rows"></tbody></table></div><div id="r1-empty" class="r1-empty" hidden>没有匹配题目</div></article>
    <article class="r1-panel"><h3>下一步：按证据排序的五维提升</h3><div class="r1-plan">
      <article><b>P0 · 安全：权限落实到执行层</b><p>由宿主保存只读/可写阶段及范围，write/edit 执行前校验，bash 使用只读挂载，保护文件不进入可读视图。阶段身份由运行器生成。先解决 9 题实际提前写入。</p></article>
      <article><b>P1 · 结果：保留有效契约，验证边界反例</b><p>当前接口、覆盖顺序、异常码、故障点来自原始要求；改动后真实验证。撤回和记忆冲突保留来源与有效期，先确认检索还是推导出错。</p></article>
      <article><b>P1 · 效率：完整请求预算与增量上下文</b><p>先用现有 Trace 分解输入开销，保护约束和未决事项，减少重复注入与重复验证。维持原 token 上限，用单变量实验观察结果与安全是否退步。</p></article>
      <article><b>P1 · 过程；P2 · 可用性</b><p>宿主记录真实阶段与副作用，同类工具错误后做针对性诊断。可用性已 20/20，补故障注入和取消回收证据，不通过整题重跑提高分数。</p></article>
    </div><div class="r1-actions"><a href="../../docs/interview/eval%20no%203/Eval3五维能力提升路线.md">阅读完整改进路线与验收指标 →</a></div></article>
    <details class="r1-panel"><summary><b>题面缺陷、判定修正与成绩边界</b></summary><ul>${data.quality_notes.map(q=>`<li>${esc(q)}</li>`).join('')}</ul></details>
    <div class="r1-actions"><a href="${archive}最终评测报告.md">最终评测报告</a><a href="${archive}assistant-judged-report.json">完整判定 JSON</a><a href="${archive}evidence.zip">原始证据 ZIP</a><a href="${archive}archive-inventory.json">归档 SHA-256</a></div>
    <dialog id="r1-dialog" aria-labelledby="r1-title"><div class="r1-dialog-top"><h3 id="r1-title"></h3><button id="r1-close" aria-label="关闭 Eval3 题目详情">关闭</button></div><div id="r1-detail"></div></dialog>`;
  const $ = id => root.querySelector('#'+id);
  let shown = [];
  function render() {
    const query = $('r1-search').value.toLowerCase(), filter = $('r1-filter').value;
    shown = data.cases.filter(c => (c.id+' '+c.outcome_judgment.reason).toLowerCase().includes(query) && (filter==='all'||(filter==='passed'?c.passed:filter==='failed'?!c.passed:!c.dimensions[filter])));
    if ($('r1-sort').value!=='default') {
      const field = $('r1-sort').value==='tokens'?'total_tokens':'effective_seconds';
      shown.sort((a,b)=>(b.observations[field]??-1)-(a.observations[field]??-1));
    }
    $('r1-rows').innerHTML = shown.map(c=>`<tr data-r1-case="${esc(c.id)}"><td><button class="r1-case" data-r1-open="${esc(c.id)}">${esc(c.id)}</button></td>${Object.keys(dims).map(d=>`<td>${pill(c.dimensions[d])}</td>`).join('')}<td>${pill(c.passed)}</td><td>${num(c.observations.total_tokens)}</td><td>${sec(c.observations.effective_seconds)}</td><td>${c.observations.tool_errors}</td></tr>`).join('');
    $('r1-count').textContent=`显示 ${shown.length} / ${data.cases.length} 题 · 强化版 r1`;
    $('r1-empty').hidden=shown.length!==0;
  }
  function detail(id) {
    const c=data.cases.find(x=>x.id===id), o=c.observations, j=c.outcome_judgment;
    $('r1-title').textContent=c.id;
    $('r1-detail').innerHTML=`<div class="r1-detail-grid">${Object.entries(dims).map(([k,v])=>`<span>${v} ${pill(c.dimensions[k])}</span>`).join('')}</div><h3>助手结果判定</h3><p>${esc(j.reason)}</p><p class="r1-note">判定边界：${esc(j.limitations)}</p><h3>逐题消耗</h3><p class="r1-note">${c.phase_count} 阶段 · ${num(o.total_tokens)} tokens · 工具 ${o.tool_calls} 次 / 错误 ${o.tool_errors} 次<br>原始 ${sec(o.raw_wall_seconds)} s − 模型等待并集 ${sec(o.model_wait_union_seconds)} s = 有效 ${sec(o.effective_seconds)} s<br>缓存 ${num(o.cached_input_tokens)} / ${num(o.input_tokens)} 输入 tokens · 已知费用小计 $${o.known_cost_subtotal_usd.toFixed(8)} · 未上报用量的失败传输尝试 ${o.failed_transport_attempts_without_usage} 次</p><h3>四维程序检查</h3><p class="r1-note">${Object.entries(c.original_program_dimensions).map(([k,v])=>`${dims[k]}：原${v.passed?'通过':'失败'} → 复核${c.dimensions[k]?'通过':'失败'}`).join('；')}</p><ul>${c.checks.map(x=>`<li>${dims[x.dimension]} · ${esc(x.name)} ${pill(x.passed)} ${x.required?'':'（观察项）'} ${x.limits.max==null?'':`上限 ${num(x.limits.max)}`}</li>`).join('')}</ul><h3>证据路径与哈希</h3><p class="r1-note">相对 evidence.zip 根目录；原始记录保留。检查详细输入见完整判定 JSON。</p><ul class="r1-evidence">${c.evidence.map(e=>`<li>${esc(e.path)}<br>SHA-256 ${esc(e.sha256)}</li>`).join('')}</ul>`;
    $('r1-dialog').showModal();
  }
  function download(text, filename, type) {
    const url=URL.createObjectURL(new Blob([text],{type})), link=document.createElement('a');
    link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  root.addEventListener('click',e=>{const target=e.target.closest('[data-r1-open]');if(target) detail(target.dataset.r1Open);});
  $('r1-search').addEventListener('input',render);
  $('r1-filter').addEventListener('change',render);
  $('r1-sort').addEventListener('change',render);
  $('r1-reset').onclick=()=>{$('r1-search').value='';$('r1-filter').value='all';$('r1-sort').value='default';render();};
  $('r1-close').onclick=()=>$('r1-dialog').close();
  $('r1-json').onclick=()=>download(JSON.stringify(data,null,2),'eval3-hardened-r1.json','application/json');
  $('r1-csv').onclick=()=>{
    const header=['id',...Object.keys(dims),'overall','total_tokens','raw_wall_seconds','model_wait_union_seconds','effective_seconds','cached_input_tokens','input_tokens','known_cost_subtotal_usd','failed_transport_attempts_without_usage','outcome_reason'];
    const rows=shown.map(c=>[c.id,...Object.keys(dims).map(d=>c.dimensions[d]),c.passed,...header.slice(7,15).map(k=>c.observations[k]),c.outcome_judgment.reason]);
    const quote=v=>'"'+String(v??'').replace(/"/g,'""')+'"';
    download('\uFEFF'+[header,...rows].map(row=>row.map(quote).join(',')).join('\r\n'),'eval3-hardened-r1-filtered.csv','text/csv;charset=utf-8');
  };
  const nav=document.querySelector('.section-nav');
  if(nav&&!nav.querySelector('a[href="#eval3-r1"]')) nav.insertAdjacentHTML('beforeend','<a href="#eval3-r1">Eval3 强化版 <span>20</span></a>');
  render();
})();
