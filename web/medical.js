'use strict';

let medicalBriefId;
const meetingPrefix = 'hijenny.meeting.';
const meetingFields = {expert:'讨论对象或专业方向', question:'外部医学问题', answer:'拟答复要点',
  discussion:'需要专家讨论的问题', action:'会后行动', owner:'行动负责人', due:'预计完成日期', reviewer:'医学复核人'};

function medicalSources(run) {
  return run.steps.flatMap(step => (step.result?.items || []).filter(item => run.review[item.id] === '纳入')
    .map(item => ({id:item.id, title:item.title, notices:item.notices || [], date:item.date, authors:item.authors || [], author_affiliations:item.author_affiliations || [], passages:item.passages || [], retrieved:item.retrieved || step.retrieved})));
}

function meetingText(v) {
  const lines = ['医学交流简报', '版本 '+v.id, '底稿版本 '+v.briefId, '医学复核确认时间 '+v.created];
  if (v.revisionOf) lines.push('修订自已确认简报 '+v.revisionOf,'出版状态变化对答复的影响',v.revisionReason || '待填写');
  for (const [key,label] of Object.entries(meetingFields)) lines.push('',label,v[key] || '待补充');
  lines.push('', '支持本次答复的证据');
  for (const ref of v.refs) {
    lines.push('',ref.title,sourceLink(ref), '来源日期 '+(ref.date || '未提供'), '检索时间 '+ref.retrieved,ref.label,ref.quote,...(ref.notices || []));
  }
  for (const c of v.conflicts || []) {
    lines.push('', '相关证据分歧 · '+c.status, '底稿复核人 '+c.reviewer,c.note,
      c.left+' · '+sourceLink({id:c.left}),c.right+' · '+sourceLink({id:c.right}));
  }
  return lines.join('\n');
}

function latestPublicationChecks(runs) {
  const checks=new Map();
  for (const run of runs) {
    if (!run.publicationCheck) continue;
    const missing=new Set(run.unavailablePublications || run.publicationCheck.flatMap(line=>{
      const match=/^(\d+) · 未能核实，保留原记录$/.exec(line);
      return match ? [match[1]] : [];
    }));
    for (const step of run.steps.filter(s=>s.source==='pubmed')) {
      for (const item of step.result?.items || []) {
        if (!checks.has(item.id) || run.created>checks.get(item.id).created) {
          checks.set(item.id,{version:run.id,created:run.created,notices:item.notices || [],missing:missing.has(item.id)});
        }
      }
    }
  }
  return checks;
}

function meetingPublicationAlerts(version, checks, briefDate) {
  return version.refs.flatMap(ref=>{
    const check=checks.get(ref.id);
    if (!check || check.version===version.briefId || check.created <= (briefDate || ref.retrieved || '')) return [];
    const changed=JSON.stringify([...(ref.notices || [])].sort())!==JSON.stringify([...check.notices].sort());
    return check.missing || changed ? [{id:ref.id,...check}] : [];
  });
}

function confirmedMeetingHistory(saved) {
  const index=new Map(saved.map(v=>[v.id,v]));
  const superseded=new Set();
  for (const version of saved.filter(v=>v.confirmed)) {
    let parent=index.get(version.parent || version.revisionOf);
    const seen=new Set([version.id]);
    while (parent && !seen.has(parent.id)) {
      seen.add(parent.id);
      if (parent.confirmed) superseded.add(parent.id);
      parent=index.get(parent.parent || parent.revisionOf);
    }
  }
  return superseded;
}

async function renderMedical() {
  setView('<section id="medical-meeting" class="research-home"><h1>准备专家会简报</h1><div id="meeting-body">正在读取本机底稿…</div></section>');
  const body = $('#meeting-body');
  let runs, saved, checks, briefDates, meetingRuns;
  try {
    const allRuns=await briefStore('readonly', store=>store.getAll());
    meetingRuns = allRuns.filter(r=>r.purpose==='medical-meeting').sort((a,b)=>b.created.localeCompare(a.created));
    checks=latestPublicationChecks(allRuns);
    briefDates=new Map(allRuns.map(r=>[r.id,r.created]));
    runs = allRuns.filter(r=>medicalSources(r).length)
      .sort((a,b)=>b.created.localeCompare(a.created));
    saved = Object.keys(localStorage).filter(k=>k.startsWith(meetingPrefix))
      .map(k=>JSON.parse(localStorage.getItem(k))).sort((a,b)=>b.created.localeCompare(a.created));
  } catch (err) { body.textContent = '读取资料失败：'+err.message; return; }
  if (!body.isConnected) return;
  const fromBrief = runs.find(r=>r.id===medicalBriefId);
  medicalBriefId = null;
  let version = fromBrief ? null : saved[0];
  let run = fromBrief || runs.find(r=>r.id===version?.briefId) || runs[0];
  let revisionBase=null;
  let dirty = false;
  let answerQuery='', includeEarlier=false;
  function draw() {
    dirty = false;
    const sourceRecords = version?.sourceRecords || (run ? medicalSources(run) : []);
    const authorPapers = sourceRecords.filter(record=>record.authors?.length);
    const conflictPool = version ? version.conflictPool || [] : run?.conflicts || [];
    const relevantConflicts = ids => conflictPool.filter(c=>ids.has(c.left) || ids.has(c.right));
    const selected = new Map((version?.refs || []).map(ref=>[ref.id,ref]));
    const affected=saved.filter(v=>v.confirmed).map(v=>({version:v,
      alerts:meetingPublicationAlerts(v,checks,briefDates.get(v.briefId))})).filter(row=>row.alerts.length);
    const savedBriefIds = new Set(saved.map(v=>v.briefId));
    const pending = meetingRuns.filter(r=>!savedBriefIds.has(r.id));
    const alerts=affected.find(row=>row.version.id===version?.id)?.alerts || [];
    const selectedIndex = r => Math.max(0,r.passages.findIndex(p=>p.text===selected.get(r.id)?.quote && p.label===selected.get(r.id)?.label));
    body.innerHTML = `<p>从已纳入的来源中挑选支持答复的原文，记录专家会要讨论的问题、医学复核意见和后续行动。</p>
      <details class="card" ${runs.length || saved.length ? '' : 'open'}><summary>从新的医学问题准备会议</summary>
      <form id="meeting-search"><label>本次专家会要回答什么问题？
        <input name="question" required maxlength="400" placeholder="例如：会上需要讨论哪些已发表的临床证据？"></label>
      <label>公开检索词（药物英文名或主题）
        <input name="query" required maxlength="400" placeholder="例如 imatinib"></label>
      <p>会议问题保存在本浏览器。仅将公开检索词提交给 PubMed 和 ClinicalTrials.gov。核对来源并标记「纳入」后，即可带回原问题和引文。</p>
      <button class="btn primary" type="submit">检索证据并建立底稿</button></form></details>
      ${pending.length ? `<section class="card"><h2>继续准备中的会议（${pending.length}）</h2>
      ${pending.map(r=>{
        const completed = r.steps.filter(step=>step.result);
        const count = completed.reduce((n,step)=>n+step.result.items.length,0);
        return `<p><a data-resume-meeting href="#/brief?version=${encodeURIComponent(r.id)}">${esc(r.question)}</a><br>
          ${esc(fmtDate(r.created))} · 已完成 ${completed.length}/${r.steps.length} 个来源 · 取回 ${count} 条 · 已纳入 ${medicalSources(r).length} 条</p>`;
      }).join('')}</section>` : ''}
      <button class="btn" data-nav="brief">打开研究底稿</button>
      ${saved.some(v=>v.confirmed)?`<details id="meeting-answer-library" class="card"><summary>查找已确认的医学答复</summary>
        <label>问题、答复、复核人、待办或来源编号<input id="meeting-answer-query" type="search" value="${esc(answerQuery)}"></label>
        <label><input id="meeting-answer-earlier" type="checkbox" ${includeEarlier?'checked':''}> 包含已有后续确认版的历史答复</label>
        <div id="meeting-answer-hits" aria-live="polite"></div></details>`:''}
      ${affected.length ? `<section class="card"><h2>引文需要复查的已确认简报（${affected.length}）</h2>
      ${affected.map(row=>`<p><button type="button" class="btn" data-affected="${esc(row.version.id)}">${esc(row.version.question)} · ${esc(fmtDate(row.version.created))}</button> · 涉及 ${row.alerts.map(a=>esc(a.id)).join('、')}</p>`).join('')}</section>` : ''}
      ${alerts.length ? `<section class="card"><h3>本版引文的后续复查</h3>
      ${alerts.map(a=>`<p>PMID ${esc(a.id)} · ${a.missing?'最近一次未能核实':'出版标记与本版不一致'} · ${esc(fmtDate(a.created))}</p>
      ${a.missing?'':publicationNotices(a)}<a href="#/brief?version=${encodeURIComponent(a.version)}">打开对应复查底稿</a>
      ${runs.some(r=>r.id===a.version) ? `<button type="button" class="btn" data-revise="${esc(a.version)}">用此底稿修订答复</button>` : '<p>先在复查底稿中核对并纳入可支持答复的来源，再回此页修订。</p>'}`).join('')}</section>` : ''}
      <label>历史简报 <select id="meeting-history"><option value="">新草稿</option>
        ${saved.map(v=>option(v.id,version?.id,fmtDate(v.created)+' · '+v.question)).join('')}</select></label>
      <label>选用底稿并新建简报 <select id="meeting-source"><option value="">选择有纳入记录的研究底稿</option>
        ${runs.map(r=>option(r.id,version ? '' : run?.id,r.question+' · '+fmtDate(r.created))).join('')}</select></label>
      ${sourceRecords.length ? `<form id="meeting-form">
      ${revisionBase || version?.revisionOf ? `<label>出版状态变化对答复的影响及修订依据<textarea id="meeting-revision-reason" maxlength="8000">${esc(version?.revisionReason || '')}</textarea></label>` : ''}
      <fieldset class="card"><legend>支持本次答复的原文</legend>
      ${sourceRecords.map((r,i)=>`<section><label><input type="checkbox" data-cite="${i}" ${selected.has(r.id)?'checked':''}> ${esc(r.title)}</label>
        <a href="${esc(sourceLink(r))}" target="_blank" rel="noopener noreferrer">${esc(r.id)} · 核对原始记录</a>
        ${publicationNotices(r)}
        ${r.authors?.length ? `<details><summary>论文署名作者（${r.authors.length}）</summary><p>${esc(r.authors.join('，'))}</p></details>` : ''}
        ${r.passages.length ? `<label>引文片段<select data-passage="${i}">
          ${r.passages.map((p,j)=>option(String(j),String(selectedIndex(r)),p.label+' · '+p.text.slice(0,95))).join('')}</select></label>
          <blockquote data-excerpt="${i}">${esc(r.passages[selectedIndex(r)].text)}</blockquote>` : '<p>此条记录缺少摘要，暂不能用作答复引文。</p>'}</section>`).join('')}</fieldset>
      <section class="card" id="meeting-conflicts" hidden></section>
      ${authorPapers.length ? `<details class="card" id="meeting-authors"><summary>从纳入论文查找讨论对象</summary>
        <label>按姓名、论文署名单位或题名筛选<input type="search" id="meeting-author-query"></label>
        <div id="meeting-author-papers"></div></details>` : ''}
      ${Object.entries(meetingFields).map(([key,label])=>`<label>${label}
        <textarea rows="${['answer','discussion','action'].includes(key)?3:1}" maxlength="8000" data-meeting="${key}">${esc(version?.[key] || (key!=='reviewer' && revisionBase?.[key]) || (key==='question'?run?.question:'') || '')}</textarea></label>`).join('')}
      <label><input id="meeting-confirm" type="checkbox"> 我已核对本版答复和所选来源</label>
      <div><button class="btn" type="submit">保存草稿版本</button>
      <button class="btn primary" type="button" id="meeting-release">确认并导出简报</button>
      ${version?.confirmed ? '<button class="btn" type="button" id="meeting-original">下载已确认原版</button><button class="btn" type="button" id="meeting-slides">下载确认版 PowerPoint</button><button class="btn" type="button" id="meeting-study">用此确认版起草研究</button>' : ''}</div>
      <p role="status" id="meeting-status">${version ? esc((version.confirmed?'已确认':'草稿')+' · '+fmtDate(version.created)) : ''}</p></form>`
      : '<p>请先在研究底稿标记并保存至少一项纳入记录。</p>'}`;
    function switchAllowed() { return !dirty || confirm('放弃本页未保存的修改？'); }
    for (const link of $$('[data-resume-meeting]',body)) link.onclick=event=>{
      if (!switchAllowed()) event.preventDefault();
    };
    $('#meeting-search',body).onsubmit = event => {
      event.preventDefault();
      const fields = event.currentTarget.elements;
      const question = fields.question.value.trim();
      const query = fields.query.value.trim();
      if (!question || !query) { toast('请填写会议问题和公开检索词。'); return; }
      if (!switchAllowed()) return;
      briefSeed = {question, pubmed:query, trials:query, purpose:'medical-meeting', startSearch:true};
      nav('brief');
    };
    for (const button of $$('[data-revise]',body)) button.onclick=()=>{
      if (!switchAllowed()) return;
      revisionBase=version;
      run=runs.find(r=>r.id===button.dataset.revise);
      version=null;draw();
      $('#meeting-revision-reason',body)?.focus();
    };
    function openSaved(id) {
      if (!switchAllowed()) return;
      revisionBase=null;version=saved.find(v=>v.id===id);
      run=runs.find(r=>r.id===version.briefId);draw();
      $('#meeting-history',body).focus();
    }
    for (const button of $$('[data-affected]',body)) button.onclick=()=>openSaved(button.dataset.affected);
    const library=$('#meeting-answer-library',body);
    if (library) {
      const hits=$('#meeting-answer-hits',library), superseded=confirmedMeetingHistory(saved);
      const affectedIds=new Set(affected.map(row=>row.version.id));
      function findAnswers() {
        answerQuery=$('#meeting-answer-query',library).value;
        includeEarlier=$('#meeting-answer-earlier',library).checked;
        const query=answerQuery.trim().toLowerCase();
        const found=saved.filter(v=>v.confirmed && (includeEarlier || !superseded.has(v.id)) &&
          [v.question,v.answer,v.expert,v.reviewer,v.action,v.owner,v.due,...v.refs.flatMap(r=>[r.id,r.title,r.quote])]
            .some(value=>String(value || '').toLowerCase().includes(query)));
        hits.innerHTML=found.map(v=>`<article><h3>${esc(v.question)}</h3><p>${esc(v.answer)}</p>
          <p>复核人 ${esc(v.reviewer)} · ${esc(fmtDate(v.created))}${superseded.has(v.id)?' · 已有后续确认版':''}${affectedIds.has(v.id)?' · 引文需复查':''}</p>
          ${v.action?`<p>会后行动 · ${esc(v.action)} · ${esc(v.owner || '负责人未填')} · ${esc(v.due || '期限未填')}</p>`:''}
          <p>${v.refs.map(r=>`<a href="${esc(sourceLink(r))}" target="_blank" rel="noopener noreferrer">${esc(r.id)}</a>`).join(' · ')}</p>
          <button type="button" class="btn" data-open-answer="${esc(v.id)}">查看此确认版与原文片段</button></article>`).join('') || '<p>未找到匹配的确认版。</p>';
      }
      library.oninput=findAnswers;
      hits.onclick=event=>{
        const button=event.target.closest('[data-open-answer]');
        if (button) openSaved(button.dataset.openAnswer);
      };
      findAnswers();
    }
    $('#meeting-history',body).onchange = event => {
      if (!switchAllowed()) { event.target.value=version?.id || ''; return; }
      revisionBase=null;version = saved.find(v=>v.id===event.target.value);
      run = runs.find(r=>r.id===version?.briefId) || runs[0]; draw();
    };
    $('#meeting-source',body).onchange = event => {
      if (!switchAllowed()) { event.target.value=version?'':run?.id || ''; return; }
      revisionBase=null;version = null; run = runs.find(r=>r.id===event.target.value); draw();
    };
    const form = $('#meeting-form',body);
    if (!form) return;
    const authorPanel = $('#meeting-authors',form);
    if (authorPanel) {
      const search = $('#meeting-author-query',authorPanel);
      const results = $('#meeting-author-papers',authorPanel);
      function showAuthors() {
        const query = search.value.trim().toLowerCase();
        const papers = authorPapers.map((paper,index)=>{
          const titleMatches = paper.title.toLowerCase().includes(query);
          const authors = paperByline(paper).filter(author=>titleMatches || [author.name,author.affiliation].some(value=>value.toLowerCase().includes(query)));
          if (!authors.length) return '';
          return `<section><p><a href="${esc(sourceLink(paper))}" target="_blank" rel="noopener noreferrer">${esc(paper.title)} · PMID ${esc(paper.id)}</a></p>
            <label>此文署名作者<select>${authors.map(author=>`<option value="${author.position}">${esc(author.name)} · ${esc(author.displayAffiliation || '本底稿无署名单位')}</option>`).join('')}</select></label>
            <button class="btn" type="button" data-add-author="${index}">加入讨论对象</button></section>`;
        });
        results.innerHTML = papers.join('') || '<p>本次纳入论文中没有匹配的署名、单位或题名。</p>';
      }
      search.oninput = showAuthors;
      results.onclick = event=>{
        const button = event.target.closest('[data-add-author]');
        if (!button) return;
        const paper = authorPapers[Number(button.dataset.addAuthor)];
        const position = Number(button.closest('section').querySelector('select').value);
        const affiliation = paperByline(paper)[position].displayAffiliation;
        const entry = paper.authors[position]+'（PMID '+paper.id+'）'+(affiliation?' · 论文署名单位：'+affiliation:'');
        const expert = form.querySelector('[data-meeting="expert"]');
        if (!expert.value.split('\n').includes(entry)) {
          const next = [expert.value.trimEnd(),entry].filter(Boolean).join('\n');
          if (next.length > expert.maxLength) { toast('讨论对象已达字数上限。'); return; }
          expert.value = next;
          expert.dispatchEvent(new Event('input',{bubbles:true}));
        }
        expert.focus();
      };
      showAuthors();
    }
    const conflictPanel=$('#meeting-conflicts',form);
    function showConflicts() {
      const checked=new Set($$('[data-cite]:checked',form).map(box=>sourceRecords[box.dataset.cite].id));
      const rows=relevantConflicts(checked);
      conflictPanel.hidden=!rows.length;
      conflictPanel.innerHTML=rows.length ? '<h3>所选引文涉及的证据分歧</h3>'+rows.map(c=>`<p><b>${esc(c.status)}</b> · 底稿复核人 ${esc(c.reviewer)}</p><p>${esc(c.note)}</p>
        <p>${[c.left,c.right].map(id=>`<a href="${esc(sourceLink({id}))}" target="_blank" rel="noopener noreferrer">${esc(id)}</a>`).join(' · ')}</p>`).join('') : '';
    }
    showConflicts();
    form.oninput = event => {
      if (authorPanel?.contains(event.target)) return;
      if (event.target.matches('[data-cite]')) showConflicts();
      if (event.target.id !== 'meeting-confirm') $('#meeting-confirm',form).checked=false;
      dirty=true; $('#meeting-status',body).textContent='有未保存的修改'; };
    for (const select of $$('[data-passage]',form)) select.onchange = () => {
      const index=select.dataset.passage;
      form.querySelector('[data-excerpt="'+index+'"]').textContent=sourceRecords[index].passages[Number(select.value)].text;
    };
    function download(v) {
      const url=URL.createObjectURL(new Blob([meetingText(v)],{type:'text/plain;charset=utf-8'}));
      const a=document.createElement('a'); a.href=url; a.download='HiJenny-专家会-'+v.id+'.txt'; a.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
    function save(confirmed) {
      const fields=Object.fromEntries($$('[data-meeting]',form).map(el=>[el.dataset.meeting,el.value.trim()]));
      const refs=[];
      for (const box of $$('[data-cite]:checked',form)) {
        const index=box.dataset.cite, r=sourceRecords[index];
        const selected=form.querySelector('[data-passage="'+index+'"]');
        if (!selected) continue;
        const passage=r.passages[Number(selected.value)];
        refs.push({id:r.id,title:r.title,notices:r.notices || [],date:r.date,retrieved:r.retrieved,label:passage.label,quote:passage.text});
      }
      const status=$('#meeting-status',body);
      if (!fields.question || !refs.length) { status.textContent='请填写医学问题，并选择至少一个有原文的来源。'; return; }
      if (confirmed && (!fields.answer || !fields.reviewer || !$('#meeting-confirm',form).checked)) {
        status.textContent='导出前请填写答复、医学复核人，并确认已核对本版来源。'; return;
      }
      const revisionOf=revisionBase?.id || version?.revisionOf || null;
      const revisionReason=$('#meeting-revision-reason',form)?.value.trim() || '';
      if (confirmed && revisionOf && !revisionReason) {
        status.textContent='请记录出版状态变化对答复的影响及修订依据。';return;
      }
      const v={...fields,revisionOf,revisionReason,id:crypto.randomUUID(),parent:version?.id || revisionBase?.id || null,briefId:version?.briefId || run.id,
        created:new Date().toISOString(),sourceRecords,refs,confirmed,conflictPool,conflicts:relevantConflicts(new Set(refs.map(r=>r.id)))};
      try { localStorage.setItem(meetingPrefix+v.id,JSON.stringify(v)); }
      catch { status.textContent='无法保存本机版本，请检查浏览器存储空间。'; return; }
      saved.unshift(v); revisionBase=null;version=v; if (confirmed) download(v); draw();
    }
    form.onsubmit=event=>{event.preventDefault();save(false);};
    $('#meeting-release',body).onclick=()=>save(true);
    const original=$('#meeting-original',body);
    if (original) {
      original.onclick=()=>download(version);
      const slides = $('#meeting-slides',body);
      slides.onclick = async () => {
        const {sourceRecords, conflictPool, ...snapshot} = version;
        slides.disabled = true;
        try {
          const response = await fetch('/api/medical/meeting.pptx', {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(snapshot)
          });
          if (!response.ok) throw new Error('导出失败（'+response.status+'）');
          const url = URL.createObjectURL(await response.blob());
          const a = document.createElement('a');
          a.href=url; a.download='HiJenny-专家会-'+snapshot.id+'.pptx'; a.click();
          setTimeout(()=>URL.revokeObjectURL(url),1000);
        } catch (err) { $('#meeting-status',form).textContent=err.message; }
        finally { slides.disabled=false; }
      };
      $('#meeting-study',body).onclick=()=>{ if (!switchAllowed()) return; studySeed=version; nav('study'); };
    }
  }
  draw();
}
