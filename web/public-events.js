function publicCoverage(receipt) {
  // Region refers to the source institution, independent of study sites and patient location.
  const sources = {
    nmpa_index: ['中国','监管','NMPA'],
    nmpa: ['中国','监管','NMPA'],
    trials: ['美国','临床试验','ClinicalTrials.gov'],
    publications: ['美国','文献','PubMed'],
    orange_book: ['美国','药品专利','FDA Orange Book'],
    fda: ['美国','监管','Drugs@FDA'],
    jrct: ['日本','临床试验','jRCT'],
    ema: ['欧盟','监管','EMA']
  };
  const table = document.createElement('table');
  table.createCaption().textContent='公开关注名单的来源覆盖';
  const header=table.createTHead().insertRow();
  const kinds=['临床试验','文献','药品专利','监管'];
  for (const label of ['来源所在地区',...kinds]) {
    const th=document.createElement('th');
    th.scope='col'; th.textContent=label; header.append(th);
  }
  const body=table.createTBody();
  for (const region of ['中国','美国','日本','欧盟']) {
    const row=body.insertRow();
    const th=document.createElement('th');
    th.scope='row'; th.textContent=region; row.append(th);
    for (const kind of kinds) {
      const cell=row.insertCell();
      const checks=receipt.checks.filter(r=>sources[r.source]?.[0]===region && sources[r.source][1]===kind);
      if (!checks.length) {cell.textContent='本轮无核对目标';continue;}
      const name=sources[checks[0].source][2];
      const ok=checks.filter(r=>r.status==='ok');
      const dates=checks.map(r=>r.last_success_utc || (r.status==='ok'?receipt.finished_utc:null)).filter(Boolean).sort();
      cell.append(name,document.createElement('br'),'核对成功 '+ok.length+' / '+checks.length);
      if (dates.length) cell.append(document.createElement('br'),'最近成功 '+fmtDate(dates.at(-1)));
      const sample=ok.find(r=>r.records>0);
      if (!sample) continue;
      const params=new URLSearchParams({source:name});
      if (['fda','orange_book'].includes(sample.source)) params.set('application',sample.target);
      else if (['publications','trials','jrct'].includes(sample.source)) params.set('trial',sample.target);
      else if (sample.source==='nmpa') params.set('notice_id',sample.target);
      else params.set('asset',sample.subject || '');
      const link=document.createElement('a');
      link.textContent='查看样本 '+sample.target;
      link.href='#/public-events?'+params;
      cell.append(document.createElement('br'),link);
    }
  }
  return table;
}

function recentPublicWeek(params, now=new Date()) {
  const day=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit'});
  params.delete('as_of');
  params.set('discovered_from',day.format(now.getTime()-6*86400000));
  params.set('discovered_to',day.format(now));
}

function bindSavedPublicFilters(onApply) {
  const panel=$('#public-saved-filters'), form=panel.querySelector('form');
  const list=panel.querySelector('ul'), message=panel.querySelector('[role=status]');
  const prefix='hijenny.public-filter.';
  let applied=null;
  function draw() {
    list.replaceChildren();
    try {
      for (const key of Object.keys(localStorage).filter(k=>k.startsWith(prefix)).sort()) {
        const entry=JSON.parse(localStorage.getItem(key));
        const li=document.createElement('li'), open=document.createElement('button'), remove=document.createElement('button');
        open.type=remove.type='button'; open.className=remove.className='btn';
        open.textContent=entry.name+(entry.week?' · 最近七天':'');
        open.onclick=()=>{
          const params=new URLSearchParams(entry.query);
          if (entry.week) recentPublicWeek(params);
          onApply(params);
        };
        remove.textContent='移除'; remove.setAttribute('aria-label','移除 '+entry.name);
        remove.onclick=()=>{
          try {localStorage.removeItem(key); draw(); message.textContent='已移除 '+entry.name;}
          catch {message.textContent='本浏览器无法移除此筛选。';}
        };
        li.append(open,remove); list.append(li);
      }
    } catch {message.textContent='本浏览器暂时无法读取已存筛选。';}
  }
  form.onsubmit=event=>{
    event.preventDefault();
    const name=form.elements.label.value.trim();
    if (!name || !applied) return;
    try {
      const key=prefix+encodeURIComponent(name);
      if (localStorage.getItem(key)!==null) {message.textContent='此名称已使用，请更换名称。';return;}
      localStorage.setItem(key,JSON.stringify({name,query:applied.toString(),week:form.elements.week.checked}));
      form.reset(); draw(); message.textContent='已保存 '+name;
    } catch {message.textContent='本浏览器无法保存筛选。';}
  };
  draw();
  return params=>{
    applied=params;
    form.querySelector('button').disabled=!params;
  };
}

function renderPublicEvents() {
  setView(`<section class="research-home"><h1>公开资产动态</h1>
    <p>跟踪公开演示名单中的公司申报、中国 NMPA 公告、美国 FDA 批准记录、欧盟 EMA 人用药集中程序、日本 jRCT 登记、临床登记及 PubMed 登记号关联。公司申报按公司归档，已核实的资产事件列出对应药物。</p>
    ${hashParams().get('event')?'<p>正在查看关注列表保存的来源记录。<a href="#/public-events">查看全部动态</a></p>':''}
    <details class="card" id="public-collection"><summary>最近一次公开采集</summary><div aria-live="polite">正在读取核对记录…</div></details>
    <details class="card" id="public-coverage"><summary>四地区数据覆盖与缺口</summary>
    <p>按来源机构所在地区列出本轮核对的目标。跨国试验和文献可涉及多个地区。成功数按关注目标计算；最近成功时间取该格内最近一次成功核对。</p>
    <div style="overflow-x:auto" aria-live="polite">正在读取覆盖范围…</div></details>
    <form id="public-event-filters" class="card">
    <label>资产或公司<input name="asset" placeholder="依达方、奥希替尼或 Summit"></label>
    <label>国家药监局公告编号<input name="notice_id" inputmode="numeric" pattern="[0-9]+" placeholder="1073516"></label>
    <label>国家药监局栏目标题关键词<input name="notice_title" placeholder="例如 Good Clinical Practice"></label>
    <label>FDA 申请号<input name="application" placeholder="NDA208065" title="NDA、ANDA 或 BLA 加六位数字"></label>
    <label>试验登记号<input name="trial" placeholder="NCT 或 jRCT 编号" pattern="[Nn][Cc][Tt][0-9]{8}|[Jj][Rr][Cc][Tt][0-9]{10}" title="NCT 加八位数字或 jRCT 加十位数字"></label>
    <label>历史截面（台北日期）<input type="date" name="as_of"><small>查看截至当天已披露且已收录的每个字段的最后记录。留空可看全部变化。</small></label>
    <label>数据来源<select name="source"><option value="">全部来源</option><option>NMPA</option><option>Drugs@FDA</option><option>EMA</option><option>FDA Orange Book</option><option>jRCT</option><option>ClinicalTrials.gov</option><option>PubMed</option><option>SEC</option></select></label>
    <label>地区<input name="region" placeholder="例如：中东"></label>
    <label>来源披露起始日期<input type="date" name="since"></label>
    <label>首次发现起始日（台北）<input type="date" name="discovered_from"></label>
    <label>首次发现结束日（台北）<input type="date" name="discovered_to"></label>
    <label>记录类型<select name="kind"><option value="">全部</option><option value="change">字段变化</option>
    <option value="reviewed">原文核实</option><option value="baseline">首次收录</option></select></label>
    <label><input type="checkbox" name="publication_status" value="true"> 仅看论文出版状态</label>
    <label><input type="checkbox" name="study_interruption" value="true"> 仅看试验暂停、终止或撤回说明</label>
    <button class="btn primary">筛选</button><button class="btn" type="button" id="public-event-week">最近七天发现</button></form>
    <details class="card" id="public-saved-filters"><summary>我的常用筛选（本浏览器）</summary>
    <ul></ul><form><label>筛选名称<input name="label" required maxlength="100" placeholder="例如：依达方监管动态"></label>
    <label><input type="checkbox" name="week"> 每次打开时查看最近七天发现，替换已选的历史截面和发现日期</label>
    <button class="btn" disabled>保存当前结果的筛选条件</button></form><p role="status"></p></details>
    <a id="public-event-report" hidden>下载当前筛选的完整简报</a>
    <p id="public-event-status" role="status"></p><nav id="public-source-counts" aria-label="按来源查看归档"></nav><div id="public-event-list"></div>
    <button type="button" class="btn" id="public-event-more" hidden>加载更多</button></section>`);
  const form = $('#public-event-filters'), status = $('#public-event-status');
  const list = $('#public-event-list'), more = $('#public-event-more');
  const collection=$('#public-collection');
  api('/api/public-events/collection').then(({receipt})=>{
    if (!collection.isConnected) return;
    const body=collection.querySelector('div');
    const coverage=$('#public-coverage div');
    if (!receipt) {body.textContent=coverage.textContent='服务器尚未同步采集回执。';return;}
    coverage.replaceChildren(publicCoverage(receipt));
    const checks=receipt.checks, ok=checks.filter(r=>r.status==='ok').length;
    collection.querySelector('summary').textContent='最近一次公开采集 · '+fmtDate(receipt.finished_utc)+' · 成功 '+ok+' / '+checks.length;
    const names={nmpa_index:'NMPA 药品栏目目录',nmpa:'国家药监局 NMPA',orange_book_uses:'FDA 专利用途代码释义',orange_book:'FDA Orange Book',jrct:'jRCT',ema:'EMA',sec:'SEC',fda:'Drugs@FDA',trials:'ClinicalTrials.gov',publications:'PubMed'};
    body.innerHTML='<p>公开关注名单全量核对 · 新增 '+esc(receipt.new_events)+' 条归档事件</p>'+
      registryTable(['来源','资产或公司','目标编号','核对结果','最近成功核对','来源记录数','新增事件'],checks.map(r=>[
        names[r.source] || r.source,r.subject || '旧回执未记录',r.target,r.status==='ok'?'成功':'待更新 · '+r.error,
        r.last_success_utc?fmtDate(r.last_success_utc):r.status==='ok'?fmtDate(receipt.finished_utc):'未记录',r.records ?? '待核',r.new_events ?? '待核']));
    if (/^[0-9]+$/.test(receipt.run_id)) {
      const link=document.createElement('a');
      link.href='https://github.com/james-lebron2000/HiJenny/actions/runs/'+receipt.run_id;
      link.textContent=({schedule:'查看定时采集运行',workflow_dispatch:'查看手动采集运行'})[receipt.trigger] || '查看采集运行';
      body.append(link);
    }
  }).catch(()=>{
    if (collection.isConnected) collection.querySelector('div').textContent=$('#public-coverage div').textContent='暂时无法读取采集回执。';
  });
  const initial = hashParams();
  for (const el of form.elements) {
    if (!el.name || !initial.has(el.name)) continue;
    if (el.type==='checkbox') el.checked = initial.get(el.name)==='true';
    else el.value = initial.get(el.name);
  }
  const setSavedQuery=bindSavedPublicFilters(params=>{
    const hash='#/public-events'+(params.size?'?'+params:'');
    if (location.hash!==hash) {location.hash=hash;return;}
    form.reset();
    for (const [key,value] of params) {
      const el=form.elements.namedItem(key);
      if (!el) continue;
      if (el.type==='checkbox') el.checked=value==='true';
      else el.value=value;
    }
    load(true);
  });
  let query, offset, generation = 0;
  function trialTrail(id) {
    const params = new URLSearchParams({trial:id});
    if (query.get('as_of')) params.set('as_of', query.get('as_of'));
    const link = document.createElement('a');
    link.href = '#/public-events?' + params;
    link.textContent = '查看此试验的跨来源记录';
    return link;
  }
  function display(value) {
    if (value == null) return '未披露';
    if (value.safety_summary) return (value.safety_summary.eventGroups || []).length+' 个安全性报告组';
    if (value.primary_outcomes) return value.primary_outcomes.map(m=>m.title).join('、');
    if (typeof value === 'object') return [value.date, {ACTUAL:'实际', ESTIMATED:'预计'}[value.type] || value.type].filter(Boolean).join(' · ');
    return String(value);
  }
  async function load(reset) {
    if (reset) {
      query = new URLSearchParams([...new FormData(form)].filter(([,value])=>value));
      const eventId = hashParams().get('event');
      if (eventId) query.set('event_id', eventId);
      setSavedQuery(null); offset = 0; generation++; list.replaceChildren();
    }
    const request = generation;
    query.set('offset', String(offset));
    const counts = $('#public-source-counts');
    if (reset) counts.replaceChildren();
    const download = $('#public-event-report');
    download.hidden = true;
    more.hidden = true; status.textContent = '正在读取…';
    try {
      const result = await api('/api/public-events?' + query);
      if (generation !== request || !form.isConnected) return;
      counts.replaceChildren();
      if (result.total) {
        const heading=document.createElement('p');
        heading.textContent='当前筛选的来源分布（编号在各来源内去重）';
        counts.append(heading);
        for (const row of result.source_counts) {
          const button=document.createElement('button');
          button.type='button'; button.className='btn';
          button.textContent=row.source+' · '+row.events+' 条归档 · '+row.records+' 个来源编号';
          button.disabled=query.get('source')===row.source;
          button.onclick=()=>{form.elements.source.value=row.source; form.requestSubmit();};
          counts.append(button);
        }
      }
      if (query.has('source')) {
        const all=document.createElement('button');
        all.type='button'; all.className='btn'; all.textContent='查看全部来源';
        all.onclick=()=>{form.elements.source.value=''; form.requestSubmit();};
        counts.append(all);
      }
      for (const r of result.items) {
        const card = document.createElement('article');
        card.className = 'card';
        const kind = {baseline:'首次收录',change:'字段变化',reviewed:'原文核实'}[r.kind];
        const documentUpdate = r.kind==='change' && r.source==='Drugs@FDA' && r.previous===r.value && r.previous_date===r.source_date;
        card.innerHTML = `<h2>${esc(r.asset || r.subject)} · ${esc(r.field)}</h2>
          <p>${r.label_trials_changed?'标签试验关联更新':documentUpdate?'配套文件更新':esc(kind)} · ${r.kind==='change'&&!documentUpdate?esc(display(r.previous))+' → ':''}${esc(display(r.value))}</p>
          <p>${esc(r.date_label || '来源披露')} ${esc(r.source_date)} · 首次发现 ${esc(fmtDate(r.observed))}</p>
          ${r.effective?'<p>事件发生或生效 '+esc(r.effective)+'</p>':''}
          ${r.region?'<p>地区 '+esc(r.region)+'</p>':''}
          <a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.source)} · ${esc(r.record)}</a>`;
        if (r.source==='NMPA' && r.asset && (!query.has('asset') || query.has('source') || query.has('notice_id'))) {
          const params = new URLSearchParams({asset:r.asset});
          if (query.has('as_of')) params.set('as_of',query.get('as_of'));
          const p=document.createElement('p'), link=document.createElement('a');
          link.href='#/public-events?'+params;
          link.textContent='查看该药物的跨来源记录';
          p.append(link); card.append(p);
        }
        if (r.source==='NMPA' && !query.has('notice_id')) {
          const params = new URLSearchParams({notice_id:r.record});
          if (query.has('as_of')) params.set('as_of',query.get('as_of'));
          const link = document.createElement('a');
          link.href = '#/public-events?' + params;
          link.textContent = '查看同一公告的全部归档';
          const p = document.createElement('p');
          p.append(link); card.append(p);
        }
        if (['FDA Orange Book','Drugs@FDA'].includes(r.source)) {
          const application = r.record.split('/')[0];
          if (!query.has('application') || query.has('source')) {
            const params = new URLSearchParams({application});
            if (query.has('as_of')) params.set('as_of', query.get('as_of'));
            const link = document.createElement('a');
            link.href = '#/public-events?' + params;
            link.textContent = '查看同一 FDA 申请的全部归档';
            const p = document.createElement('p');
            p.append(link);
            card.append(p);
          }
        }
        if (r.source==='FDA Orange Book') {
          card.querySelector('p').style.whiteSpace='pre-line';
          const note=document.createElement('p');
          note.textContent='各行按 FDA 产品编号与用途代码列示。专利和独占期分别归档；这些日期不能单独确定仿制药上市时间。';
          card.append(note);
        }
        if (r.category==='publication-mention') {
          const note=document.createElement('p');
          note.textContent='文献在题名或摘要中提到此登记号；是否报告该试验的结果仍需核对原文。';
          card.append(note);
        }
        if (r.category==='safety-summary') {
          for (const [label,version] of [['本次归档',r.value],['上次归档',r.previous]]) {
            const safety=version?.safety_summary;
            if (!safety) continue;
            const detail=document.createElement('details');
            detail.innerHTML=`<summary>${label} · 安全性报告</summary>
              <p>报告期 · ${esc(registryValue(safety.timeFrame))}</p>
              <p>其他不良事件报告阈值（%） · ${esc(registryValue(safety.frequencyThreshold))}</p>
              ${safety.description?'<p>'+esc(safety.description)+'</p>':''}
              ${registryTable(safetyHeaders,safetySummary(safety))}`;
            card.append(detail);
          }
        }
        if (r.category==='primary-outcomes') {
          for (const [label, version] of [['本次归档',r.value],['上次归档',r.previous]]) {
            for (const measure of version?.primary_outcomes || []) {
              const {rows,denoms}=measureRows(measure);
              const detail=document.createElement('details');
              detail.innerHTML=`<summary>${label} · ${esc(measure.title)}</summary>
                <p>分析人群 · ${esc(registryValue(measure.populationDescription))}</p>
                <p>评价时间 · ${esc(registryValue(measure.timeFrame))}</p>
                <p>统计量 · ${esc(registryValue(measure.paramType))} · ${esc(registryValue(measure.dispersionType))}</p>
                ${registryTable(denomHeaders,denoms)}${registryTable(outcomeHeaders,rows)}
                <h4>登记统计分析</h4>${measure.analyses===undefined ? '<p>此版归档尚未采集统计分析。</p>'
                  : registryTable(analysisHeaders,analysisRows(measure))}`;
              card.append(detail);
            }
          }
        }
        if (r.documents?.length) {
          const types = {Label:'药品标签',Letter:'批准函',Review:'审评资料','Medication Guide':'用药指南'};
          const links = document.createElement('ul');
          for (const doc of r.documents) {
            const item = document.createElement('li');
            const link = document.createElement('a');
            link.href=doc.url; link.target='_blank'; link.rel='noopener noreferrer';
            link.textContent=types[doc.type] || doc.type;
            item.append(link); links.append(item);
          }
          card.append(links);
        }
        for (const trial of r.label_trials || []) {
          const p = document.createElement('p');
          const label = r.documents?.find(doc=>doc.type==='Label' && doc.url===trial.label);
          if (!label || !/^NCT[0-9]{8}$/.test(trial.id)) continue;
          p.innerHTML = `标签所列试验 · ${esc(trial.name)} · <a href="https://clinicaltrials.gov/study/${esc(trial.id)}" target="_blank" rel="noopener noreferrer">${esc(trial.id)}</a>
            · <a href="${esc(label.url)}#page=${Number(trial.page)}" target="_blank" rel="noopener noreferrer">${esc(trial.section)} · 第 ${Number(trial.page)} 页</a>`;
          if (!query.has('trial') || query.has('source')) p.append(' · ', trialTrail(trial.id));
          card.append(p);
        }
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
        if (r.source==='ClinicalTrials.gov' && r.field==='结果首次公布' && r.value?.date) {
          const current = document.createElement('section');
          current.innerHTML = `<p>登记结果首次公布于 ${esc(r.value.date)}。下方按需读取登记库当前版本，历史简报保留当时的公布日期。</p>
            <button type="button" class="btn" data-load-results="${esc(r.record)}">核对当前登记终点与安全性</button>
            <section class="trial-posted" aria-live="polite"></section>`;
          card.append(current);
          bindTrialResultButtons(current,{event_id:r.id, first_posted:r.value.date,
            observed:r.observed, as_of:query.get('as_of') || null});
        }
        const trialId = r.source==='ClinicalTrials.gov' ? r.record : r.trial;
        if (/^NCT[0-9]{8}$/.test(trialId)) {
          if (!query.has('trial') || query.has('source')) card.append(trialTrail(trialId));
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
      const savedQuery=new URLSearchParams(reportQuery);
      if (savedQuery.has('event_id')) {
        savedQuery.set('event',savedQuery.get('event_id')); savedQuery.delete('event_id');
      }
      setSavedQuery(savedQuery);
      download.hidden = result.total === 0;
      offset += result.items.length;
      status.textContent = (query.has('as_of')?'截至 '+query.get('as_of')+' 的历史截面 · ':'')+'已显示 '+offset+' / '+result.total+' 条记录';
      if (result.asset_match) {
        const link = document.createElement('a');
        link.href = result.asset_match.source;
        link.target = '_blank'; link.rel = 'noopener noreferrer';
        link.textContent = '名称来源';
        status.append(' · 核对药名 '+result.asset_match.name+' · ', link);
      }
      more.hidden = offset >= result.total;
    } catch (err) {
      if (generation===request) { status.textContent='读取失败：'+err.message; more.hidden=false; }
    }
  }
  form.onsubmit = event=>{
    event.preventDefault();
    const params = new URLSearchParams([...new FormData(form)].filter(([,value])=>value));
    const eventId = hashParams().get('event');
    if (eventId) params.set('event',eventId);
    history.replaceState(history.state,'','#/public-events'+(params.size?'?'+params:''));
    load(true);
  };
  $('#public-event-week').onclick = ()=>{
    const params=new URLSearchParams();
    recentPublicWeek(params);
    form.elements.as_of.value='';
    form.elements.discovered_from.value=params.get('discovered_from');
    form.elements.discovered_to.value=params.get('discovered_to');
    form.requestSubmit();
  };
  more.onclick = ()=>load(false);
  load(true);
}
