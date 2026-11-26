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

function validBackupRun(run) {
  if (!run || typeof run.id!=='string' || !run.id || typeof run.question!=='string' ||
      typeof run.created!=='string' || !Number.isFinite(Date.parse(run.created)) ||
      !run.review || typeof run.review!=='object' || Array.isArray(run.review) || !Array.isArray(run.steps)) return false;
  return run.steps.every(s=>s && Object.hasOwn(briefSources,s.source) && typeof s.query==='string' &&
    Number.isInteger(s.attempts) && (!s.result || (Number.isInteger(s.result.total) &&
    Array.isArray(s.result.items) && s.result.items.every(item=>item && typeof item.id==='string' &&
      /^(NCT[0-9]{8}|[0-9]+)$/.test(item.id) && typeof item.title==='string' && Array.isArray(item.passages) &&
      item.passages.every(p=>p && typeof p.text==='string' && typeof p.label==='string')))));
}

async function restoreBriefBackup(backup) {
  if (backup?.format!=='HiJenny-research-briefs' || backup.version!==1 || !Array.isArray(backup.runs) ||
      backup.sha256!==await browserBackupDigest(backup.runs)) throw new Error('底稿备份格式或文件校验失败');
  const runs=backup.runs;
  if (!runs.every(validBackupRun) || new Set(runs.map(r=>r.id)).size!==runs.length) throw new Error('底稿记录格式无效');
  const db=await openBriefDatabase();
  return new Promise((resolve,reject)=>{
    const tx=db.transaction('runs','readwrite'), store=tx.objectStore('runs');
    const result={added:0, identical:0, conflicts:[]};
    tx.oncomplete=()=>resolve(result);
    tx.onabort=()=>reject(tx.error || new Error('恢复失败'));
    for (const run of runs) {
      const req=store.get(run.id);
      req.onsuccess=()=>{
        if (!req.result) {store.add(run);result.added++;}
        else if (JSON.stringify(req.result)===JSON.stringify(run)) result.identical++;
        else result.conflicts.push(run.question+'（'+run.id+'）');
      };
    }
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
      try { update(latest); } catch (err) { tx.abort(); reject(err); return; }
      store.put(latest);
    };
    tx.oncomplete = () => resolve(latest);
    tx.onabort = () => reject(tx.error || new Error('无法更新版本'));
  });
}

async function recheckBriefPublications(original) {
  const next=structuredClone(original);
  const items=next.steps.filter(step=>step.source==='pubmed').flatMap(step=>step.result?.items || []);
  const ids=[...new Set(items.map(row=>row.id))];
  const current=new Map();
  for (let i=0;i<ids.length;i+=10) {
    const query=ids.slice(i,i+10).map(id=>id+'[pmid]').join(' OR ');
    const result=await api('/api/research/search',jsonOptions('POST',{source:'pubmed',query}));
    for (const row of result.items) current.set(row.id,row);
  }
  const checked=new Date().toISOString();
  const unavailable=[];
  const log=['出版状态复查 · '+checked,'原底稿 '+original.id+' · 核查 '+ids.length+' 条 PMID'];
  for (const item of items) {
    const row=current.get(item.id);
    const before=item.notices;
    const after=row?.notices;
    const changed=Array.isArray(after) && JSON.stringify([...(before || [])].sort())!==JSON.stringify([...after].sort());
    let finding;
    if (!Array.isArray(after)) {unavailable.push(item.id);finding='未能核实，保留原记录';}
    else if (changed) finding=Array.isArray(before)?'出版标记变化':'首次查到出版标记，旧版状态未知';
    else finding=Array.isArray(before)?'与原版一致':'首次核查，未见标记';
    log.push(item.id+' · '+finding);
    if (changed) {
      log.push('  原标记：'+(before?.join(' / ') || (before?'无':'旧版未记录')));
      log.push('  本次标记：'+(after.join(' / ') || '无'));
    }
    if (!Array.isArray(after) || changed) {
      next.review[item.id]='待复核';
      if (next.reasons) delete next.reasons[item.id];
    }
    if (Array.isArray(after)) item.notices=after;
  }
  return {...next,id:crypto.randomUUID(),created:checked,publicationCheck:log,unavailablePublications:unavailable};
}

function missingExactPublications(step) {
  if (!step.result || step.source!=='pubmed' || !/^\d+\[pmid\](?: OR \d+\[pmid\])*$/i.test(step.query)) return [];
  const returned = new Set(step.result.items.map(item=>item.id));
  return step.query.match(/\d+/g).filter(id=>!returned.has(id));
}

function retrievalHistory(step) {
  return [step.retrieved, ...(step.recoveries || []).map(r=>'补查 PMID '+r.pmid+' · '+r.time+' · 源底稿 '+r.parent)]
    .filter(Boolean).join('；');
}

function briefText(run) {
  const lines = [run.question, '研究底稿 · 待专业复核', '版本 ' + run.id, '创建时间 ' + run.created];
  if (run.publicEvent) lines.push('发起底稿的公开动态编号 '+run.publicEvent);
  lines.push(...(run.publicationCheck || []));
  for (const step of run.steps) {
    lines.push('', briefSources[step.source], '检索式 ' + step.query, '查询尝试 ' + step.attempts);
    if (!step.result) {
      lines.push(step.error || '尚未完成检索');
      continue;
    }
    for (const id of missingExactPublications(step)) lines.push('本次未取回指定 PMID '+id);
    const items = step.result.items;
    lines.push('检索时间 ' + retrievalHistory(step), '原检索命中 ' + step.result.total + '，已取回 ' + items.length);
    for (const item of items) {
      const decision = run.review[item.id] || '待复核';
      lines.push('', '[' + item.id + '] ' + item.title, decision, sourceLink(item));
      lines.push(...(item.notices || []));
      if (run.reasons?.[item.id]) lines.push('筛选理由 '+run.reasons[item.id]);
      if (decision === '排除') continue;
      lines.push([item.date, item.detail].filter(Boolean).join(' · '));
      for (const passage of item.passages) lines.push(passage.label, passage.text);
      if (!item.passages.length) lines.push('缺少来源片段');
    }
  }
  lines.push('', '审核意见与证据冲突', run.note || '尚未填写');
  for (const c of run.conflicts || []) {
    lines.push('', '来源分歧 · '+c.status+' · '+c.reviewer, c.note);
    for (const id of [c.left,c.right]) {
      const item=run.steps.flatMap(step=>step.result?.items || []).find(row=>row.id===id);
      if (item) lines.push('['+id+'] '+item.title,sourceLink(item));
    }
  }
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
    <details class="card"><summary>备份与恢复本机研究底稿</summary>
    <p>包含此浏览器保存的所有底稿版本、检索记录和审核意见。JSON 文件可读，请保存在授权位置。</p>
    <button type="button" class="btn" id="brief-backup">下载全部底稿备份</button>
    <label>从备份恢复缺少的版本<input id="brief-restore" type="file" accept=".json,application/json"></label>
    <p role="status" id="brief-backup-status"></p></details>
    <div id="brief-report" aria-live="polite"></div></section>`);
  const home = $('#brief-home');
  const history = $('#brief-history', home);
  const report = $('#brief-report', home);
  const form = $('#brief-plan', home);
  const startSearch = briefSeed?.startSearch === true;
  let meetingPlan = briefSeed?.purpose === 'medical-meeting';
  let publicEvent = briefSeed?.publicEvent;
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
  const backupStatus=$('#brief-backup-status',home);
  $('#brief-backup',home).onclick=async ()=>{
    try {
      await saveReview();
      const runs=await briefStore('readonly',store=>store.getAll());
      const backup={format:'HiJenny-research-briefs',version:1,created:new Date().toISOString(),
        sha256:await browserBackupDigest(runs),runs};
      const url=URL.createObjectURL(new Blob([JSON.stringify(backup)],{type:'application/json'}));
      const a=document.createElement('a');a.href=url;a.download='HiJenny-研究底稿-'+backup.created.slice(0,10)+'.json';a.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
      backupStatus.textContent='已导出 '+runs.length+' 个版本。';
    } catch(err) {backupStatus.textContent='备份失败：'+err.message;}
  };
  const restore=$('#brief-restore',home);
  restore.onchange=async ()=>{
    const file=restore.files[0];if (!file) return;
    restore.disabled=true;
    try {
      await saveReview();
      const result=await restoreBriefBackup(JSON.parse(await file.text()));
      const versions=await refreshHistory();
      if (!run && versions.length) {run=versions[0];history.value=run.id;rememberVersion();paint();}
      backupStatus.textContent='已恢复 '+result.added+' 个版本，已有相同版本 '+result.identical+' 个。'+
        (result.conflicts.length?'以下版本与备份有差异，已保留本机内容：'+result.conflicts.join('、'):'');
    } catch(err) {backupStatus.textContent='导入失败：'+err.message;}
    finally {restore.value='';restore.disabled=false;}
  };
  function rememberVersion() {
    if (!home.isConnected) return;
    const params = hashParams();
    if (run) params.set('version', run.id);
    else params.delete('version');
    const query = params.toString();
    window.history.replaceState(null, '', '#/brief' + (query ? '?' + query : ''));
  }
  function paint() {
    if (!run) { report.replaceChildren(); return; }
    history.disabled = busy;
    form.querySelector('button').disabled = busy;
    const items=new Map(run.steps.flatMap(step=>(step.result?.items || []).map(row=>[row.id,row])));
    const choices=[...items.values()].map(row=>option(row.id,'',row.id+' · '+row.title)).join('');
    const conflictLink=id=>`<a href="${esc(sourceLink(items.get(id)))}" target="_blank" rel="noopener noreferrer">${esc(id)} · ${esc(items.get(id).title)}</a>`;
    report.innerHTML = `<h2>${esc(run.question)}</h2><p>来源快照 · ${esc(new Date(run.created).toLocaleString())}</p>
      ${run.publicEvent?`<a href="#/public-events?event=${encodeURIComponent(run.publicEvent)}">查看发起底稿的公开动态</a>`:''}
      ${run.steps.map(step => `<section class="card"><h3>${briefSources[step.source]}</h3>
      <span>${esc(step.query)} · 已尝试 ${step.attempts} 次</span>
      ${step.result ? `<p>原检索命中 ${step.result.total} 条，本版收录 ${step.result.items.length} 条；其中 ${step.result.items.filter(i=>!i.passages.length).length} 条缺少来源片段。</p>
        ${(step.recoveries || []).map(r=>`<p>单独补查 PMID ${esc(r.pmid)} · ${esc(fmtDate(r.time))} · <a href="#/brief?version=${encodeURIComponent(r.parent)}">查看补查前的底稿</a></p>`).join('')}
        ${missingExactPublications(step).map(id=>`<p>本次未取回指定 PMID ${esc(id)} · <a href="${esc(sourceLink({id}))}" target="_blank" rel="noopener noreferrer">核对 PubMed</a> <button class="btn" data-recover-pmid="${esc(id)}" ${busy?'disabled':''}>单独补查并保存新版</button></p>`).join('')}
        ${step.result.items.map(item => `<details><summary>${esc(item.title)}</summary>
        <a href="${esc(sourceLink(item))}" target="_blank" rel="noopener noreferrer">${esc(item.id)} · 核对原始记录</a>
        ${publicationNotices(item)}<p>${esc(item.date)} · ${esc(item.detail)}</p>
        ${item.passages.map(p=>`<p><b>${esc(p.label)}</b><br>${esc(p.text)}</p>`).join('')}
        <label>筛选判断 <select data-review="${esc(item.id)}" ${busy?'disabled':''}>
        ${['待复核','纳入','排除'].map(v=>option(v,run.review[item.id] || '待复核',v)).join('')}</select></label><label>筛选理由（可选）<textarea data-reason="${esc(item.id)}" maxlength="2000" ${busy?'disabled':''}>${esc(run.reasons?.[item.id] || '')}</textarea></label></details>`).join('')}`
        : `<p>${esc(step.error || (busy ? '正在执行检索计划…' : '等待检索'))}</p>`}</section>`).join('')}
      ${items.size>=2 ? `<section class="card"><h3>来源分歧复核记录</h3>
        ${(run.conflicts || []).map(c=>`<article><p>${conflictLink(c.left)} · ${conflictLink(c.right)}</p><p>${esc(c.status)} · ${esc(c.reviewer)}</p><p>${esc(c.note)}</p></article>`).join('')}
        <details><summary>登记两条来源的分歧</summary><form id="brief-conflict">
        <label>第一条来源<select name="left" required><option value="">选择记录</option>${choices}</select></label>
        <label>第二条来源<select name="right" required><option value="">选择记录</option>${choices}</select></label>
        <label>复核判断<select name="status">${['待核对','口径差异可解释','仍有分歧'].map(v=>option(v,'',v)).join('')}</select></label>
        <label>分歧内容、判断依据或待查事项<textarea name="note" required maxlength="2000"></textarea></label>
        <label>复核人<input name="reviewer" required maxlength="120"></label>
        <button class="btn" ${busy?'disabled':''}>保存分歧记录</button></form></details></section>` : ''}
      ${run.publicationCheck ? `<section class="card"><h3>出版状态复查记录</h3>${run.publicationCheck.map(line=>'<p>'+esc(line)+'</p>').join('')}</section>` : ''}
      ${run.steps.some(s=>s.source==='pubmed' && s.result?.items?.length) ? `<button id="brief-recheck" type="button" class="btn" ${busy?'disabled':''}>复查文献出版状态并保存新版</button>` : ''}
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
    for (const button of report.querySelectorAll('[data-recover-pmid]')) {
      button.onclick = ()=>recoverPublication(button.dataset.recoverPmid).catch(fail);
    }
    const conflictForm=$('#brief-conflict',home);
    if (conflictForm) conflictForm.onsubmit=async event=>{
      event.preventDefault();
      const entry=Object.fromEntries(new FormData(conflictForm));
      entry.note=entry.note.trim();entry.reviewer=entry.reviewer.trim();
      if (entry.left===entry.right || !entry.note || !entry.reviewer) {
        toast('请选择两条不同的来源，并填写判断依据和复核人。');return;
      }
      const id=run.id;
      const button=conflictForm.querySelector('button');
      button.disabled=true;
      try {
        await saveReview();
        const saved=await patchBrief(id,record=>{
          record.conflicts ||= [];
          if (record.conflicts.length < 200) record.conflicts.push(entry);
          else throw new Error('本版底稿已达到 200 条分歧记录。');
        });
        if (run?.id===id) {run=saved;paint();}
      } catch(err) {fail(err);} finally {button.disabled=false;}
    };
    $('#brief-medical', home).onclick = async () => {
      try {
        await saveReview();
        if (!medicalSources(run).length) { toast('请先将相关记录标为「纳入」。'); return; }
        medicalBriefId = run.id;
        nav('medical');
      } catch (err) { fail(err); }
    };
    const recheck=$('#brief-recheck',home);
    if (recheck) recheck.onclick=async ()=>{
      if (busy) return;
      busy=true;
      try {
        await saveReview(true);
        paint();
        const source=await briefStore('readonly',store=>store.get(run.id));
        const checked=await recheckBriefPublications(source);
        await put(checked);
        run=checked;
        rememberVersion();
        await refreshHistory();
      } catch(err) {fail(err);}
      finally {busy=false;if (home.isConnected) paint();}
    };
    $('#brief-copy', home).onclick = () => {
      publicEvent = run.publicEvent;
      meetingPlan = run.purpose === 'medical-meeting';
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
        const payload={id,publicEvent:saved.publicEvent,question:saved.question,created:saved.created,note:saved.note,publicationCheck:saved.publicationCheck || [],conflicts:saved.conflicts || [],steps:saved.steps.map(step=>({
          source:step.source,query:step.query,retrieved:retrievalHistory(step),total:step.result?.total ?? null,error:step.error || '',
          records:(step.result?.items || []).map(item=>({
            id:item.id,title:item.title,date:item.date || '',notices:item.notices || [],decision:saved.review[item.id] || '待复核',
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
  async function recoverPublication(pmid) {
    if (busy) return;
    busy = true;
    try {
      await saveReview(true);
      paint();
      const parent = run.id;
      const result = await api('/api/research/search',jsonOptions('POST',{source:'pubmed',query:pmid+'[pmid]'}));
      const found = result.items.find(item=>item.id===pmid);
      if (!found) throw new Error('PubMed 仍未返回 PMID '+pmid+'。原版已保留，可稍后重试。');
      const next = await briefStore('readonly',store=>store.get(parent));
      const step = next.steps.find(s=>missingExactPublications(s).includes(pmid));
      if (!step) return;
      next.id = crypto.randomUUID();
      next.created = new Date().toISOString();
      step.result.items.push({...found,retrieved:next.created});
      step.recoveries ||= [];
      step.recoveries.push({pmid,time:next.created,parent});
      await put(next);
      run = next;
      if (home.isConnected) { rememberVersion(); await refreshHistory(); }
    } finally { busy=false; if (home.isConnected) paint(); }
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
      rememberVersion();
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
      if (meetingPlan) run.purpose = 'medical-meeting';
      if (publicEvent) run.publicEvent = publicEvent;
      await put(run);
      publicEvent = null;
      meetingPlan = false;
      rememberVersion();
      await refreshHistory();
      paint();
      await execute();
    } catch (err) { fail(err); } finally { creating = false; }
  };
  try {
    const runs = await refreshHistory();
    if (startSearch) {
      if (home.isConnected) form.requestSubmit();
      return;
    }
    const requested=hashParams().get('version');
    run=requested ? runs.find(r=>r.id===requested) : runs[0];
    if (run) {history.value=run.id;paint();}
    else if (requested) report.textContent='当前浏览器找不到该底稿版本。';
  } catch (err) { fail(err); }
}
