function renderPublicEvents() {
  setView(`<section class="research-home"><h1>公开资产动态</h1>
    <p>跟踪公开演示名单中的公司申报、临床登记及 PubMed 登记号关联。公司申报按公司归档，已核实的资产事件列出对应药物。</p>
    ${hashParams().get('event')?'<p>正在查看关注列表保存的来源记录。<a href="#/public-events">查看全部动态</a></p>':''}
    <form id="public-event-filters" class="card">
    <label>资产或公司<input name="asset" placeholder="ivonescimab 或 Summit"></label>
    <label>历史截面（台北日期）<input type="date" name="as_of"><small>查看截至当天已披露且已收录的每个字段的最后记录。留空可看全部变化。</small></label>
    <label>地区<input name="region" placeholder="例如：中东"></label>
    <label>来源披露起始日期<input type="date" name="since"></label>
    <label>首次发现起始日（台北）<input type="date" name="discovered_from"></label>
    <label>首次发现结束日（台北）<input type="date" name="discovered_to"></label>
    <label>记录类型<select name="kind"><option value="">全部</option><option value="change">字段变化</option>
    <option value="reviewed">原文核实</option><option value="baseline">首次收录</option></select></label>
    <label><input type="checkbox" name="publication_status" value="true"> 仅看论文出版状态</label>
    <button class="btn primary">筛选</button><button class="btn" type="button" id="public-event-week">最近七天发现</button></form>
    <a id="public-event-report" hidden>下载当前筛选的完整简报</a>
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
      const eventId = hashParams().get('event');
      if (eventId) query.set('event_id', eventId);
      offset = 0; generation++; list.replaceChildren();
    }
    const request = generation;
    query.set('offset', String(offset));
    const download = $('#public-event-report');
    download.hidden = true;
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
          <p>${esc(r.date_label || '来源披露')} ${esc(r.source_date)} · 首次发现 ${esc(fmtDate(r.observed))}</p>
          ${r.effective?'<p>事件发生或生效 '+esc(r.effective)+'</p>':''}
          ${r.region?'<p>地区 '+esc(r.region)+'</p>':''}
          <a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.source)} · ${esc(r.record)}</a>`;
        const noticeIds = [];
        if (r.category==='publication-status') {
          const notices = new Set(String(r.value).match(/https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/\d+\//g) || []);
          for (const url of notices) {
            const link = document.createElement('a');
            link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
            const pmid = url.split('/').at(-2);
            if (pmid !== r.record) noticeIds.push(pmid);
            link.textContent = '核对关联记录 · PMID '+pmid;
            const paragraph = document.createElement('p');
            paragraph.append(link); card.append(paragraph);
          }
        }
        const trialId = r.source==='ClinicalTrials.gov' ? r.record : r.trial;
        if (/^NCT[0-9]{8}$/.test(trialId)) {
          const button = document.createElement('button');
          button.className = 'btn'; button.type = 'button'; button.textContent = '核对登记并加入本机关注';
          const feedback = document.createElement('p');
          feedback.setAttribute('role','status');
          button.onclick = async ()=>{
            button.disabled = true;
            feedback.textContent = '正在核对 '+trialId+'…';
            try {
              const item = await lookupWatchedTrial(trialId);
              const value = (r.kind==='change'?display(r.previous)+' → ':'')+display(r.value);
              await followTrial(item,new Date().toISOString(),{type:'public-event',snapshot:r.id,
                field:r.field,value,created:r.observed,disclosed:r.source_date,dateLabel:r.date_label});
              feedback.innerHTML = '已保存这条动态与试验的关联。<a href="#/watch">查看关注列表</a>';
            } catch (err) {
              feedback.textContent = '保存失败：'+err.message;
              button.disabled = false;
            }
          };
          card.append(button,feedback);
        }
        if (r.source==='PubMed' && /^[0-9]+$/.test(r.record) && /^NCT[0-9]{8}$/.test(r.trial)) {
          const pair = document.createElement('button');
          pair.type = 'button'; pair.className = 'btn';
          pair.textContent = r.category==='publication-status' ? '复核出版状态与试验登记' : '核对论文与登记，建立底稿';
          let noticeSelect;
          if (noticeIds.length > 1) {
            const label = document.createElement('label');
            label.textContent = '本次底稿要核对的关联记录';
            noticeSelect = document.createElement('select');
            for (const id of noticeIds) noticeSelect.add(new Option('PMID '+id,id));
            label.append(noticeSelect); card.append(label);
          }
          if (noticeIds.length) pair.textContent = '核对论文、关联通知与试验登记';
          pair.onclick = ()=>{
            const notice = noticeSelect?.value || noticeIds[0];
            briefSeed = {question:r.category==='publication-status' ? 'PMID '+r.record+' 的出版状态是否影响对试验 '+r.trial+' 的判断？' : '论文 PMID '+r.record+' 与试验 '+r.trial+' 的报告内容是否一致？',
              pubmed:[r.record,notice].filter(Boolean).map(id=>id+'[pmid]').join(' OR '), trials:r.trial, publicEvent:r.id, startSearch:true};
            nav('brief');
          };
          card.append(pair);
        }
        list.append(card);
      }
      const reportQuery = new URLSearchParams(query);
      reportQuery.delete('offset');
      download.href = '/api/public-events/report.txt?' + reportQuery;
      download.hidden = result.total === 0;
      offset += result.items.length;
      status.textContent = (query.has('as_of')?'截至 '+query.get('as_of')+' 的历史截面 · ':'')+'已显示 '+offset+' / '+result.total+' 条记录';
      more.hidden = offset >= result.total;
    } catch (err) {
      if (generation===request) { status.textContent='读取失败：'+err.message; more.hidden=false; }
    }
  }
  form.onsubmit = event=>{event.preventDefault();load(true);};
  $('#public-event-week').onclick = ()=>{
    const day = new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit'});
    const now = Date.now();
    form.elements.as_of.value = '';
    form.elements.discovered_from.value = day.format(now - 6 * 86400000);
    form.elements.discovered_to.value = day.format(now);
    form.requestSubmit();
  };
  more.onclick = ()=>load(false);
  load(true);
}
