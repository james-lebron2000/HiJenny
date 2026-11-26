function renderPublicEvents() {
  setView(`<section class="research-home"><h1>公开资产动态</h1>
    <p>跟踪公开演示名单中的公司申报与临床登记。公司申报按公司归档，已核实的资产事件列出对应药物。</p>
    <form id="public-event-filters" class="card">
    <label>资产或公司<input name="asset" placeholder="ivonescimab 或 Summit"></label>
    <label>地区<input name="region" placeholder="例如：中东"></label>
    <label>来源披露起始日期<input type="date" name="since"></label>
    <button class="btn primary">筛选</button></form>
    <p id="public-event-status" role="status"></p><div id="public-event-list"></div>
    <button type="button" class="btn" id="public-event-more" hidden>加载更多</button></section>`);
  const form = $('#public-event-filters'), status = $('#public-event-status');
  const list = $('#public-event-list'), more = $('#public-event-more');
  let query, offset, generation = 0;
  function display(value) {
    if (value == null) return '未披露';
    if (typeof value === 'object') return [value.date, {ACTUAL:'实际', ESTIMATED:'预计'}[value.type] || value.type].filter(Boolean).join(' · ');
    return String(value);
  }
  async function load(reset) {
    if (reset) {
      query = new URLSearchParams([...new FormData(form)].filter(([,value])=>value));
      offset = 0; generation++; list.replaceChildren();
    }
    const request = generation;
    query.set('offset', String(offset));
    more.hidden = true; status.textContent = '正在读取…';
    try {
      const result = await api('/api/public-events?' + query);
      if (generation !== request || !form.isConnected) return;
      for (const r of result.items) {
        const card = document.createElement('article');
        card.className = 'card';
        const kind = {baseline:'首次收录',change:'字段变化',reviewed:'原文核实'}[r.kind];
        card.innerHTML = `<h2>${esc(r.asset || r.subject)} · ${esc(r.field)}</h2>
          <p>${esc(kind)} · ${r.kind==='change'?esc(display(r.previous))+' → ':''}${esc(display(r.value))}</p>
          <p>来源披露 ${esc(r.source_date)} · 首次发现 ${esc(fmtDate(r.observed))}</p>
          ${r.effective?'<p>事件发生或生效 '+esc(r.effective)+'</p>':''}
          ${r.region?'<p>地区 '+esc(r.region)+'</p>':''}
          <a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.source)} · ${esc(r.record)}</a>`;
        list.append(card);
      }
      offset += result.items.length;
      status.textContent = '已显示 '+offset+' / '+result.total+' 条记录';
      more.hidden = offset >= result.total;
    } catch (err) {
      if (generation===request) { status.textContent='读取失败：'+err.message; more.hidden=false; }
    }
  }
  form.onsubmit = event=>{event.preventDefault();load(true);};
  more.onclick = ()=>load(false);
  load(true);
}
