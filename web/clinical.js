'use strict';

let clinicalSeed = null;
const designFields = {
  population:'目标人群', include:'入选标准', exclude:'排除标准', control:'对照与分配',
  endpoint:'主要终点与评价时间', feasibility:'可行性与限制',
};

const sapFields = {
  estimand:'估计目标与伴发事件处理', sets:'分析集与主要分析人群', method:'主要分析模型、比较与效应量',
  sampleSize:'样本量依据与参数来源', multiplicity:'多重性与检验顺序', missing:'缺失数据处理',
  sensitivity:'敏感性与亚组分析', interim:'期中分析与停止规则', outputs:'预定表图与数据锁定要求'
};

function changedSections(before, after) {
  if (!before) return [];
  const changes = [];
  after.options.forEach((opt, i) => {
    const label = i === 0 ? 'A' : 'B';
    for (const key of Object.keys(designFields)) {
      if (opt[key] === before.options[i][key]) continue;
      const impact = ['population','include','exclude'].includes(key)
        ? '复核招募可行性、分析集与亚组定义'
        : key === 'endpoint' ? '复核访视安排、估计目标与统计分析计划'
        : key === 'control' ? '复核分配流程、对照假设与统计比较' : '复核实施计划';
      changes.push('设计 ' + label + ' · ' + designFields[key] + '已更改，' + impact);
    }
  });
  for (const [key,label] of Object.entries({question:'研究问题',choice:'倾向方案',rationale:'选择依据',pending:'待复核事项'})) {
    if (before[key] !== after[key]) changes.push(label + '已更改');
  }
  for (const [key,label] of Object.entries(sapFields)) {
    if ((before.sap?.[key] || '') !== (after.sap?.[key] || '')) changes.push('SAP · '+label+'已更改，需统计复核');
  }
  return changes;
}

function renderClinical() {
  const prefix = 'hijenny.clinical.';
  const blank = () => ({id:null, parent:null, created:'', question:'', options:[{},{}], choice:'',
    rationale:'', pending:'', references:[], changes:[]});
  let version = blank();
  const versions = () => Object.keys(localStorage).filter(k=>k.startsWith(prefix))
    .map(k=>JSON.parse(localStorage.getItem(k))).sort((a,b)=>b.created.localeCompare(a.created));
  try {
    if (clinicalSeed) { version.references = clinicalSeed; clinicalSeed = null; }
    else version = versions()[0] || version;
  } catch (err) { toast('无法读取本机设计版本。', 'err'); }
  function reviewsFor(id) {
    const reviewPrefix='hijenny.design-review.'+id+'.';
    return Object.keys(localStorage).filter(key=>key.startsWith(reviewPrefix))
      .map(key=>JSON.parse(localStorage.getItem(key))).sort((a,b)=>a.created.localeCompare(b.created));
  }
  function draw() {
    const refs = version.references;
    setView(`<section class="research-home" lang="zh-CN"><h1>比较两种临床研究设计</h1>
      <p>用同一研究问题比较人群、对照与主要终点，记录选择依据，导出供团队评审的 Word 摘要。</p>
      <label>已保存版本 <select id="design-history"><option value="">当前新草稿</option>
      ${versions().map(v=>option(v.id,version.id,fmtDate(v.created)+' · '+v.question)).join('')}</select></label>
      <button type="button" id="design-new" class="btn">新建空白设计</button>
      <details><summary>关联登记试验（${refs.length}）</summary>
      ${refs.map(r=>`<article><a href="${esc(sourceLink(r))}" target="_blank" rel="noopener noreferrer">${esc(r.id)} · ${esc(r.title)}</a>
        <p>登记更新 ${esc(r.date)}</p><pre class="source-snapshot">${esc(r.snapshot)}</pre></article>`).join('') ||
      '<p>在医药检索中选择试验并打开设计对照，可使用选区创建新草稿。</p>'}</details>
      <form id="design-form"><label>研究问题 <input id="design-question" maxlength="400" required value="${esc(version.question)}"></label>
      <div class="design-options">${version.options.map((opt,i)=>`<fieldset class="card"><legend>候选设计 ${i?'B':'A'}</legend>
      ${Object.entries(designFields).map(([k,label])=>`<label>${label}<textarea rows="2" maxlength="6000" data-opt="${i}" data-key="${k}">${esc(opt[k] || '')}</textarea></label>`).join('')}</fieldset>`).join('')}</div>
      <label>倾向方案 <select id="design-choice">${[['','待讨论'],['A','设计 A'],['B','设计 B']].map(([v,label])=>option(v,version.choice,label)).join('')}</select></label>
      <label>选择依据与取舍<textarea rows="3" maxlength="6000" id="design-rationale">${esc(version.rationale)}</textarea></label>
      <label>待临床与统计复核<textarea rows="3" maxlength="6000" id="design-pending">${esc(version.pending)}</textarea></label>
      <details id="sap-editor"><summary>起草统计分析计划（SAP）</summary>
      <p>从倾向方案带入人群、对照、终点和评价时间。以下内容由统计人员填写，空缺会标入 Word 草稿。</p>
      ${Object.entries(sapFields).map(([key,label])=>`<label>${label}<textarea data-sap="${key}" maxlength="6000" rows="2">${esc(version.sap?.[key] || '')}</textarea></label>`).join('')}
      <button type="button" class="btn" id="sap-export">保存并导出 SAP 草稿</button></details>
      <button type="submit" class="btn primary">保存设计版本</button>
      <button type="button" class="btn" id="design-export">保存并导出 Word 草稿</button></form>
      ${version.id?`<section class="card"><h2>本版专业复核</h2>
      <p>意见保存在本浏览器，按设计版本归档。修改设计后，临床和统计人员可分别复核新版。</p>
      <div id="design-reviews"></div><form id="design-review-form">
      <label>专业角色<select name="role"><option>临床</option><option>统计</option></select></label>
      <label>复核人<input name="reviewer" required maxlength="120"></label>
      <label>复核意见<select name="decision"><option>需修改</option><option>同意当前设计</option><option>需补充材料</option></select></label>
      <label>理由与待处理事项<textarea name="note" required maxlength="6000"></textarea></label>
      <button class="btn" type="submit">把意见记入此版本</button></form></section>`:''}
      <div id="design-status" role="status"></div>
      ${version.changes.length ? '<h2>本版变更与影响</h2><ul>'+version.changes.map(c=>'<li>'+esc(c)+'</li>').join('')+'</ul>' : ''}</section>`);
    const form = $('#design-form');
    let dirty = false;
    let reviewDirty = false;
    const reviewForm=$('#design-review-form');
    function paintReviews() {
      const reviews=reviewsFor(version.id);
      $('#design-reviews').innerHTML=['临床','统计'].map(role=>{
        const entries=reviews.filter(r=>r.role===role);
        return '<h3>'+role+'复核 · '+esc(entries.at(-1)?.decision || '待复核')+'</h3>'+
          entries.map(r=>'<p>'+esc(r.reviewer)+' · '+esc(fmtDate(r.created))+' · '+esc(r.decision)+'<br>'+esc(r.note)+'</p>').join('');
      }).join('');
    }
    if (reviewForm) {
      paintReviews();
      reviewForm.oninput=()=>{reviewDirty=true;};
      reviewForm.onsubmit=event=>{
        event.preventDefault();
        const status=$('#design-status');
        if (dirty) {status.textContent='请先保存设计修改，再复核新版本。';return;}
        const data=Object.fromEntries(new FormData(reviewForm));
        const entry={...data,reviewer:data.reviewer.trim(),note:data.note.trim(),version:version.id,created:new Date().toISOString()};
        if (!entry.reviewer || !entry.note) {status.textContent='请填写复核人和具体意见。';return;}
        try {
          localStorage.setItem('hijenny.design-review.'+version.id+'.'+crypto.randomUUID(),JSON.stringify(entry));
          paintReviews();reviewForm.reset();reviewDirty=false;status.textContent='已保存本版复核意见';
        } catch {status.textContent='复核意见保存失败，请检查本机存储空间。';}
      };
    }
    form.oninput = () => { dirty = true; $('#design-status').textContent = '有未保存的修改'; };
    $('#design-history').onchange = event => {
      if ((dirty || reviewDirty) && !confirm('放弃未保存的修改并切换版本？')) { event.target.value = version.id || ''; return; }
      version = versions().find(v=>v.id===event.target.value) || blank(); draw();
    };
    $('#design-new').onclick = () => {
      if ((dirty || reviewDirty) && !confirm('放弃当前未保存的修改并新建？')) return;
      version = blank(); draw();
    };
    function save() {
      if (!form.reportValidity()) return false;
      if (reviewDirty && !confirm('复核表还有未登记的意见。继续仅使用已保存的复核记录？')) return false;
      if (version.id && !dirty) return true;
      const question = $('#design-question').value.trim();
      if (!question) { $('#design-status').textContent = '请填写研究问题。'; return false; }
      const next = {...version,id:crypto.randomUUID(),parent:version.id,created:new Date().toISOString(),
        question,options:[{},{}],choice:$('#design-choice').value,rationale:$('#design-rationale').value,pending:$('#design-pending').value};
      for (const el of $$('textarea[data-opt]',form)) next.options[Number(el.dataset.opt)][el.dataset.key] = el.value;
      next.sap=Object.fromEntries($$('[data-sap]',form).map(el=>[el.dataset.sap,el.value]));
      next.changes = changedSections(version.id ? version : null,next);
      try { localStorage.setItem(prefix+next.id,JSON.stringify(next)); }
      catch { $('#design-status').textContent = '本机存储失败，请保留此页并检查可用空间。'; return false; }
      version = next; draw(); $('#design-status').textContent = '已保存新版本'; return true;
    }
    form.onsubmit = event => { event.preventDefault(); save(); };
    async function exportDocument(sap) {
      if (sap && !$('#design-choice').value) {$('#design-status').textContent='请先选择用于 SAP 的候选设计 A 或 B。';return;}
      if (!save()) return;
      const id = version.id;
      try {
        const res = await fetch(sap?'/api/clinical/sap.docx':'/api/clinical/synopsis.docx',jsonOptions('POST',{...version,reviews:reviewsFor(id)}));
        if (!res.ok) throw new Error('导出失败，已保存的版本可重新导出。');
        const url = URL.createObjectURL(await res.blob());
        const link = document.createElement('a'); link.href = url; link.download = (sap?'HiJenny-SAP-':'HiJenny-方案摘要-')+id+'.docx'; link.click();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
      } catch (err) { toast(err.message,'err'); }
    }
    $('#design-export').onclick=()=>exportDocument(false);
    $('#sap-export').onclick=()=>exportDocument(true);
  }
  draw();
}
