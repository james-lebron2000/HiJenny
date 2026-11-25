'use strict';

// Each run is a separately keyed snapshot. Individual source checkpoints survive reloads.
const briefSources = { pubmed: 'PubMed', trials: 'ClinicalTrials.gov' };
let briefSeed = '';
let briefDatabase;

function openBriefDatabase() {
  if (!briefDatabase) briefDatabase = new Promise((resolve, reject) => {
    const req = indexedDB.open('hijenny-public-research', 1);
    req.onupgradeneeded = () => req.result.createObjectStore('runs', {keyPath:'id'});
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => { briefDatabase = null; reject(req.error); };
  });
  return briefDatabase;
}

async function briefStore(mode, work) {
  const db = await openBriefDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('runs', mode);
    const req = work(tx.objectStore('runs'));
    tx.oncomplete = () => resolve(req.result);
    tx.onabort = () => reject(tx.error || new Error('无法保存底稿'));
  });
}

// Patch one part of a version without replacing reviews written in another tab.
async function patchBrief(id, update) {
  const db = await openBriefDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('runs', 'readwrite');
    const store = tx.objectStore('runs');
    const request = store.get(id);
    let latest;
    request.onsuccess = () => {
      latest = request.result;
      if (!latest) { tx.abort(); return; }
      update(latest);
      store.put(latest);
    };
    tx.oncomplete = () => resolve(latest);
    tx.onabort = () => reject(tx.error || new Error('无法更新版本'));
  });
}

function briefText(run) {
  const lines = [run.question, '研究底稿 · 待专业复核', '版本 ' + run.id, '创建时间 ' + run.created];
  for (const step of run.steps) {
    lines.push('', briefSources[step.source], '检索式 ' + step.query, '查询尝试 ' + step.attempts);
    if (!step.result) {
      lines.push(step.error || '尚未完成检索');
      continue;
    }
    const items = step.result.items;
    lines.push('检索时间 ' + step.retrieved, '数据库命中 ' + step.result.total + '，已取回 ' + items.length);
    for (const item of items) {
      const decision = run.review[item.id] || '待复核';
      lines.push('', '[' + item.id + '] ' + item.title, decision, sourceLink(item));
      if (run.reasons?.[item.id]) lines.push('筛选理由 '+run.reasons[item.id]);
      if (decision === '排除') continue;
      lines.push([item.date, item.detail].filter(Boolean).join(' · '));
      for (const passage of item.passages) lines.push(passage.label, passage.text);
      if (!item.passages.length) lines.push('缺少来源片段');
    }
  }
  lines.push('', '审核意见与证据冲突', run.note || '尚未填写');
  return lines.join('\n');
}

async function renderBrief() {
  setView(`<section class="research-home" lang="zh-CN" id="brief-home">
    <h1>整理研究底稿</h1><p>分别检索文献与试验登记，逐条核对来源，再导出会议或研究用的证据底稿。</p>
    <form id="brief-plan" class="card"><label for="brief-question">你希望回答什么问题？</label>
    <input id="brief-question" required maxlength="400" placeholder="例如：伊马替尼有哪些已发表的临床研究？">
    <label for="brief-pubmed">PubMed 检索式</label><input id="brief-pubmed" required maxlength="800" placeholder="imatinib AND clinical trial">
    <label for="brief-trials">ClinicalTrials.gov 检索词</label><input id="brief-trials" required maxlength="400" placeholder="imatinib">
    <small>两条检索式分别发送至对应数据库。研究问题、来源快照与审核意见保存在本浏览器。</small>
    <button class="btn primary" type="submit">运行并保存新版本</button></form>
    <label for="brief-history">打开已有底稿</label><select id="brief-history"><option value="">请选择版本</option></select>
    <div id="brief-report" aria-live="polite"></div></section>`);
  const home = $('#brief-home');
  const history = $('#brief-history', home);
  const report = $('#brief-report', home);
  const form = $('#brief-plan', home);
  if (briefSeed) {
    for (const input of form.querySelectorAll('input')) input.value = typeof briefSeed==='string' ? briefSeed : briefSeed[input.id.slice(6)];
    briefSeed = '';
  }
  let run;
  let busy = false;
  let creating = false;
  const put = value => briefStore('readwrite', store => store.put(value));
  async function refreshHistory() {
    const runs = await briefStore('readonly', store => store.getAll());
    runs.sort((a,b) => b.created.localeCompare(a.created));
    history.innerHTML = '<option value="">请选择版本</option>' + runs.map(r =>
      `<option value="${esc(r.id)}">${esc(new Date(r.created).toLocaleString())} · ${esc(r.question)}</option>`).join('');
    if (run) history.value = run.id;
    return runs;
  }
  function paint() {
    if (!run) { report.replaceChildren(); return; }
    history.disabled = busy;
    form.querySelector('button').disabled = busy;
    report.innerHTML = `<h2>${esc(run.question)}</h2><p>来源快照 · ${esc(new Date(run.created).toLocaleString())}</p>
      ${run.steps.map(step => `<section class="card"><h3>${briefSources[step.source]}</h3>
      <span>${esc(step.query)} · 已尝试 ${step.attempts} 次</span>
      ${step.result ? `<p>共命中 ${step.result.total} 条，已取回 ${step.result.items.length} 条；其中 ${step.result.items.filter(i=>!i.passages.length).length} 条缺少来源片段。</p>
        ${step.result.items.map(item => `<details><summary>${esc(item.title)}</summary>
        <a href="${esc(sourceLink(item))}" target="_blank" rel="noopener noreferrer">${esc(item.id)} · 核对原始记录</a>
        <p>${esc(item.date)} · ${esc(item.detail)}</p>
        ${item.passages.map(p=>`<p><b>${esc(p.label)}</b><br>${esc(p.text)}</p>`).join('')}
        <label>筛选判断 <select data-review="${esc(item.id)}" ${busy?'disabled':''}>
        ${['待复核','纳入','排除'].map(v=>option(v,run.review[item.id] || '待复核',v)).join('')}</select></label><label>筛选理由（可选）<textarea data-reason="${esc(item.id)}" maxlength="2000" ${busy?'disabled':''}>${esc(run.reasons?.[item.id] || '')}</textarea></label></details>`).join('')}`
        : `<p>${esc(step.error || (busy ? '正在执行检索计划…' : '等待检索'))}</p>`}</section>`).join('')}
      <button id="brief-copy" type="button" class="btn" ${busy?'disabled':''}>复制计划并调整检索式</button>
      <label for="brief-note">审核意见、相互矛盾的证据与未解决问题</label>
      <textarea id="brief-note" rows="4" maxlength="16000" ${busy?'disabled':''}>${esc(run.note)}</textarea>
      <div><button id="brief-save" class="btn" ${busy?'disabled':''}>保存审核意见</button>
      <button id="brief-medical" class="btn" ${busy?'disabled':''}>用纳入证据准备专家会</button>
      <button id="brief-export" class="btn" ${busy?'disabled':''}>导出当前底稿</button>
      <button id="brief-word" class="btn" ${busy?'disabled':''}>下载 Word 研究报告</button>
      <small>生成 Word 时，本版检索记录、纳入的来源片段及审核意见会发送至本项目服务器。</small>
      ${run.steps.some(s=>!s.result) ? '<button class="btn primary" id="brief-resume">继续未完成的检索</button>' : ''}
      </div>`;
    $('#brief-medical', home).onclick = async () => {
      try {
        await saveReview();
        if (!medicalSources(run).length) { toast('请先将相关记录标为「纳入」。'); return; }
        medicalBriefId = run.id;
        nav('medical');
      } catch (err) { fail(err); }
    };
    $('#brief-copy', home).onclick = () => {
      $('#brief-question', form).value = run.question;
      for (const step of run.steps) $('#brief-' + step.source, form).value = step.query;
      $('#brief-question', form).focus();
    };
    const resume = $('#brief-resume', home);
    if (resume) { resume.disabled = busy; resume.onclick = () => execute().catch(fail); }
    $('#brief-export', home).onclick = async () => {
      try {
        await saveReview();
        const url = URL.createObjectURL(new Blob([briefText(run)], {type:'text/plain;charset=utf-8'}));
        const link = document.createElement('a');
        link.href = url; link.download = 'HiJenny-研究底稿-' + run.id + '.txt'; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (err) { fail(err); }
    };
    const word = $('#brief-word',home);
    word.onclick=async ()=>{
      word.disabled=true;
      try {
        const id=run.id;
        await saveReview();
        const saved=await briefStore('readonly',store=>store.get(id));
        const payload={id,question:saved.question,created:saved.created,note:saved.note,steps:saved.steps.map(step=>({
          source:step.source,query:step.query,retrieved:step.retrieved || '',total:step.result?.total ?? null,error:step.error || '',
          records:(step.result?.items || []).map(item=>({
            id:item.id,title:item.title,date:item.date || '',decision:saved.review[item.id] || '待复核',
            reason:saved.reasons?.[item.id] || '',postedResult:!!item.postedResult,
            passages:saved.review[item.id]==='纳入'?item.passages:[]
          }))
        }))};
        const res=await fetch('/api/research/brief.docx',jsonOptions('POST',payload));
        if (!res.ok) throw new Error('Word 导出失败（'+res.status+'）');
        const url=URL.createObjectURL(await res.blob());
        const a=document.createElement('a');a.href=url;
        a.download=res.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || 'HiJenny-研究报告.docx';
        a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      } catch(err) {fail(err);}
      finally {word.disabled=false;}
    };
    $('#brief-save', home).onclick = () => saveReview().then(() => toast('已保存审核意见。')).catch(fail);
  }
  function fail(error) { toast('底稿操作失败：' + error.message, 'err'); }
  async function saveReview(whileStarting = false) {
    const note = $('#brief-note', home);
    if (!note || (busy && !whileStarting)) return;
    const id = run.id;
    // Merge review fields into the latest snapshot, preserving retrieval checkpoints.
    const changes = [...report.querySelectorAll('[data-review]')]
      .filter(el => el.value !== (run.review[el.dataset.review] || '待复核'))
      .map(el => [el.dataset.review, el.value]);
    const value = note.value;
    const changedNote = value !== run.note;
    const reasons=[...report.querySelectorAll('[data-reason]')]
      .filter(el=>el.value !== (run.reasons?.[el.dataset.reason] || ''))
      .map(el=>[el.dataset.reason,el.value]);
    if (!changes.length && !changedNote && !reasons.length) return;
    const latest = await patchBrief(id, record => {
      if (changedNote) record.note = value;
      record.reasons=Object.assign(record.reasons || {},Object.fromEntries(reasons));
      Object.assign(record.review, Object.fromEntries(changes));
    });
    if (run?.id === id) run = latest;
  }
  async function execute() {
    if (busy) return;
    busy = true;
    try { await saveReview(true); } catch (err) { busy = false; throw err; }
    paint();
    try {
      await navigator.locks.request('hijenny-brief-' + run.id, async () => {
        run = await briefStore('readonly', store => store.get(run.id));
        for (const step of run.steps) {
          if (step.result || !home.isConnected) continue;
          step.attempts++;
          await patchBrief(run.id, record => Object.assign(record.steps.find(s => s.source === step.source), step));
          try {
            step.result = await api('/api/research/search', jsonOptions('POST',{source:step.source,query:step.query}));
            step.retrieved = new Date().toISOString();
            step.error = '';
          } catch (err) { step.error = err.message; }
          await patchBrief(run.id, record => Object.assign(record.steps.find(s => s.source === step.source), step));
          if (home.isConnected) paint();
        }
        run = await briefStore('readonly', store => store.get(run.id));
      });
    } finally { busy = false; if (home.isConnected) paint(); }
  }
  history.onchange = async () => {
    try {
      await saveReview();
      run = history.value ? await briefStore('readonly',store=>store.get(history.value)) : null;
      paint();
    } catch (err) { fail(err); }
  };
  form.onsubmit = async event => {
    event.preventDefault();
    if (busy || creating) return;
    const question = $('#brief-question', form).value.trim();
    const steps = Object.keys(briefSources).map(source => ({source, query:$('#brief-'+source,form).value.trim(), attempts:0}));
    if (!question || steps.some(s=>!s.query)) { toast('请填写研究问题和两条公开检索式。'); return; }
    creating = true;
    try {
      await saveReview();
      run = {id:crypto.randomUUID(), created:new Date().toISOString(), question, steps, review:{}, note:''};
      await put(run);
      await refreshHistory();
      paint();
      await execute();
    } catch (err) { fail(err); } finally { creating = false; }
  };
  try {
    const runs = await refreshHistory();
    if (runs.length) { run = runs[0]; history.value = run.id; paint(); }
  } catch (err) { fail(err); }
}
