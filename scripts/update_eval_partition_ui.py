"""Apply current partition wording; score data remains generated from frozen evidence."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];F=ROOT/'frontend/eval-round1'
def edit(name, pairs):
    p=F/name;s=p.read_text(encoding='utf-8')
    for a,b in pairs:
        if a not in s and b not in s:raise ValueError((name,a[:100]))
        s=s.replace(a,b)
    p.write_text(s,encoding='utf-8')
def main():
    edit('app.js',[("retained:{name:'保留集',short:'保留',purpose:'比较候选版本和配置，帮助决定选用哪一版。'},",''),
        ('预留给最终报告；看过并用于修改后，需要更换新的封存题。','用于内部测试与回归；已曝光，不作为未来对比盲测。'),
        ("Object.values(splits).map((s,i)=>", "Object.entries(splits).map(([key,s],i)=>"),
        ('${s.name} · 10 道', '${s.name} · ${rows.filter(c=>c.split===key).length} 道')])
    note='<article class="card" id="next-efficiency-policy"><h3>下一轮新增效率观察项</h3><p>缓存命中率（%）与费用估算（USD）进入逐题判定报告，仅观察，不设硬门槛、不计入五维分数。缓存比例按累计缓存输入 / 累计输入计算；费用区分缓存输入、未缓存输入和输出，按配置费率估算。缺少 usage 或价格显示未采集，不显示为免费。旧轮次不倒用新口径。</p></article>'
    edit('index.html',[
        ('三层题集，五种能力','开发与测试，五种能力'),
        ('开发、保留、测试各 10 道；每层的每种能力各 2 道。','开发集 15 道、测试集 15 道；每集的每种能力各 3 道。原保留题已按固定规则分配。'),
        ('本轮结果已被阅读并用于改进，保留集与测试集现已曝光；后续复跑属于回归，不能再当作全新盲测。','两集均已曝光，后续复跑属于开发与内部测试。新的保留集等 MiniClaw / Codex 对比时再创建，目前不生成。'),
        ('<option value="retained">保留集</option>',''),
        (note, '')])
    edit('round2.js',[("retained:'保留集',",''),
        ('新题的开发、保留、测试各 5 道，五类能力各 1 道；旧题全部是长压缩编程题。','当前开发集 8 道、测试集 7 道，另有 5 道旧题回归。均已曝光；新的对比保留集暂不创建。'),
        ('tool_errors ≤ 5 和 600/900 秒门槛不参与本批判分。下一轮加入缓存命中率 % 和费用估算 USD，仅观察、不设硬门槛、不改变五维分数；缺失计费信息显示未采集。','tool_errors ≤ 5 和 600/900 秒门槛不参与本批判分。工具错误和耗时保留展示，不能据此重写本轮成绩。')])
    p=F/'round2.js';s=p.read_text(encoding='utf-8')
    if 'id="round2-updates"' not in s:
        anchor="  document.querySelector('.section-nav').insertAdjacentHTML"
        update='''  $('round2').insertAdjacentHTML('beforeend', `<div id="round2-updates" class="round2-block">${heading('UPDATES','第二轮相关修改','当前配置已更新；原始成绩保留，下一轮启用新增观察项。')}<article class="card"><h3>题集重新划分</h3><p>第二轮开发集 8 道、测试集 7 道，另保留 5 道旧题回归。原保留题已按固定规则加入开发或测试，前端与可执行配置同步。全部属于已曝光材料，新的 MiniClaw / Codex 对比保留集暂不创建。</p></article>'''+note+'''</div>`);
'''
        if anchor not in s:raise ValueError('round2 update insertion point missing')
        s=s.replace(anchor,update+anchor,1)
    p.write_text(s,encoding='utf-8')
if __name__=='__main__':main()
