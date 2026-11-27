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

const protocolFields = {
  objectives:'研究背景、依据与目标', organisation:'申办方、研究中心与职责', intervention:'干预实施与合并治疗',
  visits:'访视安排、评价窗口与随访', discontinuation:'退出、停药与随访处理', safety:'安全监测与报告流程',
  data:'数据采集、质量控制与管理', ethics:'知情同意、伦理与隐私安排'
};

const ibSections={product:'研究用产品概况与制剂信息',nonclinical:'非临床研究摘要',kinetics:'人体药代与药效资料',
  clinical:'既往人体研究与临床经验',safety:'安全性资料与已识别风险',guidance:'供研究者参考的资料与待解问题'};
const csrFields={conduct:'实际实施与偏离方案',cutoff:'数据截止日期',source:'源表名称与版本',
  table:'结果表（CSV 文本）',interpretation:'结果解释与限制'};
const csrHeader='类别,分析集,组别,指标,评价时间,报告值,单位,分母,出处';

function designDifferences(previous, current) {
  if (!previous) return [];
  return current.options.flatMap((opt,index)=>Object.entries(designFields).flatMap(([key,label])=>{
    const before=previous.options[index][key] || '', after=opt[key] || '';
    if (before===after) return [];
    const impact=['population','include','exclude'].includes(key)
      ? '复核招募可行性、分析集与亚组定义'
      : key==='endpoint'?'复核访视安排、估计目标与统计分析计划'
      : key==='control'?'复核分配流程、对照假设与统计比较':'复核实施计划';
    return [{label:'设计 '+(index?'B':'A')+' · '+label,before,after,impact}];
  }));
}

function changedSections(before, after) {
  if (!before) return [];
  const changes=designDifferences(before,after).map(row=>row.label+'已更改，'+row.impact);
  for (const [key,label] of Object.entries({question:'研究问题',choice:'倾向方案',rationale:'选择依据',pending:'待复核事项'})) {
    if (before[key] !== after[key]) changes.push(label + '已更改');
  }
  for (const [key,label] of Object.entries(sapFields)) {
    if ((before.sap?.[key] || '') !== (after.sap?.[key] || '')) changes.push('SAP · '+label+'已更改，需统计复核');
  }
  for (const choice of ['A','B']) {
    for (const [key,label] of Object.entries(protocolFields)) {
      if ((before.protocol?.[choice]?.[key] || '') !== (after.protocol?.[choice]?.[key] || ''))
        changes.push('Protocol '+choice+' · '+label+'已更改，需临床复核');
    }
  }
  for (const choice of ['A','B']) {
    if (Object.keys(csrFields).some(key=>(before.csr?.[choice]?.[key] || '') !== (after.csr?.[choice]?.[key] || '')))
      changes.push('CSR '+choice+' · 报告内容或统计表版本已更改，需核对结果及出处');
  }
  if (['product','cutoff'].some(k=>(before.ib?.[k] || '') !== (after.ib?.[k] || '')))
    changes.push('IB · 产品名称或资料截止日期已更改，需复核资料范围');
  for (const [key,label] of Object.entries(ibSections)) {
    if (['summary','source'].some(k=>(before.ib?.sections?.[key]?.[k] || '') !== (after.ib?.sections?.[key]?.[k] || '')))
      changes.push('IB · '+label+'内容或出处已更改，需复核本章');
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
      .map(key=>({...JSON.parse(localStorage.getItem(key)),id:key.slice(reviewPrefix.length)})).sort((a,b)=>a.created.localeCompare(b.created));
  }
  function parentOf(item) {
    return item.parent?JSON.parse(localStorage.getItem(prefix+item.parent)):null;
  }
  function draw() {
    const previous=parentOf(version);
    const differences=designDifferences(previous,version);
    const refs = version.references;
    setView(`<section class="research-home" lang="zh-CN"><h1>比较两种临床研究设计</h1>
      <p>用同一研究问题比较人群、对照与主要终点，记录选择依据，导出供团队评审的 Word 摘要。</p>
      <label>已保存版本 <select id="design-history"><option value="">当前新草稿</option>
      ${versions().map(v=>option(v.id,version.id,fmtDate(v.created)+' · '+v.question)).join('')}</select></label>
      <button type="button" id="design-new" class="btn">新建空白设计</button>
      <details><summary>关联登记试验（${refs.length}）</summary>
      ${refs.map(r=>`<article><a href="${esc(sourceLink(r))}" target="_blank" rel="noopener noreferrer">${esc(r.id)} · ${esc(r.title)}</a>
        <p>登记更新 ${esc(r.date)}${r.archive_cutoff?' · <a href="#/public-events?'+esc(new URLSearchParams({source:'ClinicalTrials.gov',source_record:r.id,as_of:r.archive_cutoff}).toString())+'">回查 '+esc(r.archive_cutoff)+' 的归档</a>':''}</p><pre class="source-snapshot">${esc(r.snapshot)}</pre></article>`).join('') ||
      '<p>在医药检索中选择试验并打开设计对照，可使用选区创建新草稿。</p>'}</details>
      <form id="design-form"><label>研究问题 <input id="design-question" maxlength="400" required value="${esc(version.question)}"></label>
      ${refs.length?`<details id="design-evidence"><summary>引用登记原文填写候选设计</summary>
      <label>关联试验<select name="reference">${refs.map((r,i)=>option(String(i),'0',r.id+' · '+r.title)).join('')}</select></label>
      <label>来源字段<select name="excerpt"></select></label>
      <label>来源文字（可选取其中一段）<textarea name="quote" rows="5" readonly></textarea></label>
      <label>填入位置<select name="destination">${['A','B'].map((letter,i)=>Object.entries(designFields).map(([key,label])=>option(i+'-'+key,'0-population','设计 '+letter+' · '+label)).join('')).join('')}</select></label>
      <button type="button" class="btn" data-insert-quote>追加引用</button><p role="status"></p></details>`:''}
      <div class="design-options">${version.options.map((opt,i)=>`<fieldset class="card"><legend>候选设计 ${i?'B':'A'}</legend>
      ${Object.entries(designFields).map(([k,label])=>`<label>${label}<textarea rows="2" maxlength="6000" data-opt="${i}" data-key="${k}">${esc(opt[k] || '')}</textarea></label>`).join('')}</fieldset>`).join('')}</div>
      <label>倾向方案 <select id="design-choice">${[['','待讨论'],['A','设计 A'],['B','设计 B']].map(([v,label])=>option(v,version.choice,label)).join('')}</select></label>
      <label>选择依据与取舍<textarea rows="3" maxlength="6000" id="design-rationale">${esc(version.rationale)}</textarea></label>
      <label>待临床与统计复核<textarea rows="3" maxlength="6000" id="design-pending">${esc(version.pending)}</textarea></label>
      <details id="protocol-editor"><summary>起草临床研究方案正文（Protocol）</summary>
      <p>选择倾向方案后填写实施章节。A、B 各自保存正文；统计部分引用本版 SAP。</p>
      ${['A','B'].map(choice=>`<fieldset data-protocol-choice="${choice}"><legend>设计 ${choice} 的方案正文</legend>
      ${Object.entries(protocolFields).map(([key,label])=>`<label>${label}<textarea data-protocol="${key}" maxlength="6000" rows="2">${esc(version.protocol?.[choice]?.[key] || '')}</textarea></label>`).join('')}</fieldset>`).join('')}
      <button type="button" class="btn" id="protocol-export">保存并导出 Protocol 草稿</button></details>
      <details id="ib-editor"><summary>起草研究者手册（IB）</summary>
      <p>按产品汇总截至指定日期的资料。填写章节摘要时，请同时记录资料名称、版本及页码或表号。</p>
      <label>研究用产品名称<input id="ib-product" maxlength="6000" value="${esc(version.ib?.product || '')}"></label>
      <label>资料截止日期<input id="ib-cutoff" type="date" value="${esc(version.ib?.cutoff || '')}"></label>
      ${Object.entries(ibSections).map(([key,title])=>`<fieldset data-ib="${key}"><legend>${title}</legend>
      <label>资料摘要<textarea data-ib-field="summary" maxlength="6000">${esc(version.ib?.sections?.[key]?.summary || '')}</textarea></label>
      <label>资料名称、版本及定位<textarea data-ib-field="source" maxlength="6000">${esc(version.ib?.sections?.[key]?.source || '')}</textarea></label></fieldset>`).join('')}
      <button type="button" class="btn" id="ib-export">保存并导出 IB 草稿</button></details>
      <details id="csr-editor"><summary>起草临床研究报告（CSR）</summary>
      <p>按选中方案粘贴已复核的汇总表。类别可填「受试者处置」「疗效」「安全性」；出处填写原始表号与行号。保留原始数值精度，缺失值留空。</p>
      <button type="button" class="btn" id="csr-template">下载空白结果表（CSV）</button><p>CSV 表头 <code>${csrHeader}</code></p>
      ${['A','B'].map(choice=>`<fieldset data-csr-choice="${choice}"><legend>设计 ${choice} 的研究报告</legend>
      <label>从 UTF-8 CSV 文件填入结果表<input type="file" data-csr-file accept=".csv,text/csv"></label>
      ${Object.entries(csrFields).map(([key,label])=>`<label>${label}${key==='cutoff'?`<input type="date" data-csr="cutoff" value="${esc(version.csr?.[choice]?.cutoff || '')}">`:
        `<textarea data-csr="${key}" maxlength="${key==='table'?120000:6000}" rows="${key==='table'?5:2}">${esc(version.csr?.[choice]?.[key] || '')}</textarea>`}</label>`).join('')}</fieldset>`).join('')}
      <button type="button" class="btn" id="csr-export">保存并导出 CSR 草稿</button></details>
      <details id="sap-editor"><summary>起草统计分析计划（SAP）</summary>
      <p>从倾向方案带入人群、对照、终点和评价时间。以下内容由统计人员填写，空缺会标入 Word 草稿。</p>
      ${Object.entries(sapFields).map(([key,label])=>`<label>${label}<textarea data-sap="${key}" maxlength="6000" rows="2">${esc(version.sap?.[key] || '')}</textarea></label>`).join('')}
      <button type="button" class="btn" id="sap-export">保存并导出 SAP 草稿</button></details>
      <details id="design-replies" hidden><summary>修订时回应本版复核意见</summary><div></div></details>
      <button type="submit" class="btn primary">保存设计版本</button>
      <button type="button" class="btn" id="design-export">保存并导出 Word 草稿</button></form>
      ${version.id?`<section class="card"><h2>本版专业复核</h2>
      <p>意见保存在本浏览器，按设计版本归档。修改设计后，临床和统计人员可分别复核新版。</p>
      ${version.parent?`<details id="design-revision"><summary>与直接上一版逐项核对候选设计（${differences.length}）</summary>
        <p>上一版 ${esc(version.parent)} · 本版 ${esc(version.id)}</p>
        ${previous?(differences.map(row=>`<article><h3>${esc(row.label)}</h3><p>${esc(row.impact)}</p>
        <strong>修订前</strong><pre class="source-snapshot">${esc(row.before || '（空白）')}</pre>
        <strong>修订后</strong><pre class="source-snapshot">${esc(row.after || '（空白）')}</pre></article>`).join('') || '<p>两种候选设计的字段均与上一版相同。</p>'):'<p>本浏览器缺少上一版，无法核对原文。</p>'}</details>`:''}
      ${version.responses?.length?`<details id="saved-replies"><summary>本版对上版复核意见的回应（${version.responses.length}）</summary>
        ${version.responses.map(({review,reply})=>`<article><h3>${esc(review.role)} · ${esc(review.reviewer)} · ${esc(review.decision)}</h3>
        <p>${esc(review.note)}</p><p>修订回应</p><pre class="source-snapshot">${esc(reply)}</pre></article>`).join('')}</details>`:''}
      <div id="design-reviews"></div><form id="design-review-form">
      <label>专业角色<select name="role"><option>临床</option><option>统计</option></select></label>
      <label>复核人<input name="reviewer" required maxlength="120"></label>
      <label>复核意见<select name="decision"><option>需修改</option><option>同意当前设计</option><option>需补充材料</option></select></label>
      <label>理由与待处理事项<textarea name="note" required maxlength="6000"></textarea></label>
      <button class="btn" type="submit">把意见记入此版本</button></form></section>`:''}
      <div id="design-status" role="status"></div>
      ${version.changes.length ? '<h2>本版变更与影响</h2><ul>'+version.changes.map(c=>'<li>'+esc(c)+'</li>').join('')+'</ul>' : ''}</section>`);
    const form = $('#design-form');
    const choiceInput=$('#design-choice',form);
    function displayProtocol() {
      for (const section of $$('[data-protocol-choice],[data-csr-choice]',form)) {
        section.hidden=section.disabled=(section.dataset.protocolChoice || section.dataset.csrChoice)!==choiceInput.value;
      }
    }
    choiceInput.addEventListener('change',displayProtocol);
    displayProtocol();
    let dirty = false;
    const evidence=$('#design-evidence',form);
    if (evidence) {
      const {reference,excerpt,quote,destination}=form.elements;
      const status=evidence.querySelector('[role="status"]');
      const ref=()=>refs[Number(reference.value)];
      const passages=()=>ref().excerpts?.length?ref().excerpts:[{label:'登记快照',text:ref().snapshot}];
      function showQuote() {
        quote.value=passages()[Number(excerpt.value)].text || '';
        quote.setSelectionRange(0,0);
        status.textContent='';
      }
      reference.onchange=()=>{
        excerpt.innerHTML=passages().map((item,i)=>option(String(i),'0',item.label)).join('');
        showQuote();
      };
      excerpt.onchange=showQuote;
      reference.onchange();
      evidence.querySelector('[data-insert-quote]').onclick=()=>{
        const [index,key]=destination.value.split('-');
        const field=form.querySelector(`[data-opt="${index}"][data-key="${key}"]`);
        const text=(quote.selectionStart===quote.selectionEnd?quote.value:quote.value.slice(quote.selectionStart,quote.selectionEnd)).trim();
        if (!text) {status.textContent='此字段没有可引用的文字。';return;}
        const source=ref(), passage=passages()[Number(excerpt.value)];
        const citation=[source.id,passage.label,source.archive_cutoff?'归档截止 '+source.archive_cutoff:'登记更新 '+(source.date || '未提供'),passage.event].filter(Boolean).join(' · ');
        const addition='【'+citation+'】\n'+text;
        const value=[field.value,addition].filter(Boolean).join('\n\n');
        if (value.length>field.maxLength) {status.textContent='超过目标字段字数上限，请选取较短的来源段落。';return;}
        field.value=value;dirty=true;
        status.textContent='已追加到设计 '+(index==='0'?'A':'B')+' 的“'+designFields[key]+'”，保存版本前可继续编辑。';
      };
    }
    $('#csr-template',form).onclick=()=>{
      const url=URL.createObjectURL(new Blob(['\ufeff'+csrHeader+'\r\n'],{type:'text/csv;charset=utf-8'}));
      const a=document.createElement('a');a.href=url;a.download='HiJenny-CSR-结果表模板.csv';a.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
    for (const input of $$('[data-csr-file]',form)) input.onchange=async ()=>{
      const file=input.files[0];
      if (!file) return;
      const section=input.closest('[data-csr-choice]');
      const table=$('[data-csr="table"]',section);
      if (table.value && !confirm('使用文件内容替换当前编辑的结果表？')) {input.value='';return;}
      try {
        if (file.size>480000) throw new Error('结果表超过大小上限');
        const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());
        if (text.length>120000) throw new Error('结果表超过 120000 字符');
        if (!input.isConnected || input.files[0]!==file) return;
        table.value=text;dirty=true;
        $('#design-status').textContent='已填入 '+file.name+'，请核对数据截止日期及源表版本。';
      } catch(err) {toast('CSV 读取失败：'+err.message,'err');}
      finally {input.value='';}
    };
    let reviewDirty = false;
    const reviewForm=$('#design-review-form');
    function paintReviews() {
      const reviews=reviewsFor(version.id);
      const replies=$('#design-replies',form);
      replies.hidden=!reviews.length;
      for (const review of reviews) {
        if ($$('textarea[data-reply]',replies).some(el=>el.dataset.reply===review.id)) continue;
        replies.querySelector('div').insertAdjacentHTML('beforeend',`<label>${esc(review.role)} · ${esc(review.reviewer)} · ${esc(review.decision)}
          <span>${esc(review.note)}</span><textarea data-reply="${esc(review.id)}" rows="2" maxlength="6000" placeholder="记录处理方式和理由，随下一版保存"></textarea></label>`);
      }
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
      next.protocol={};
      for (const section of $$('[data-protocol-choice]',form)) {
        next.protocol[section.dataset.protocolChoice]=Object.fromEntries(
          $$('[data-protocol]',section).map(el=>[el.dataset.protocol,el.value]));
      }
      next.ib={product:$('#ib-product',form).value.trim(),cutoff:$('#ib-cutoff',form).value || null,sections:{}};
      for (const section of $$('[data-ib]',form)) {
        next.ib.sections[section.dataset.ib]=Object.fromEntries(
          $$('[data-ib-field]',section).map(el=>[el.dataset.ibField,el.value]));
      }
      next.csr={};
      for (const section of $$('[data-csr-choice]',form)) {
        next.csr[section.dataset.csrChoice]=Object.fromEntries(
          $$('[data-csr]',section).map(el=>[el.dataset.csr,el.dataset.csr==='cutoff'?(el.value || null):el.value]));
      }
      next.sap=Object.fromEntries($$('[data-sap]',form).map(el=>[el.dataset.sap,el.value]));
      const reviews=new Map(reviewsFor(version.id).map(review=>[review.id,review]));
      next.responses=[];
      for (const el of $$('textarea[data-reply]',form)) {
        const reply=el.value.trim(), review=reviews.get(el.dataset.reply);
        if (reply && !review) {$('#design-status').textContent='来源意见已不可用，请核对后再保存。';return false;}
        if (reply) next.responses.push({review,reply});
      }
      next.changes = changedSections(version.id ? version : null,next);
      try { localStorage.setItem(prefix+next.id,JSON.stringify(next)); }
      catch { $('#design-status').textContent = '本机存储失败，请保留此页并检查可用空间。'; return false; }
      version = next; draw(); $('#design-status').textContent = '已保存新版本'; return true;
    }
    form.onsubmit = event => { event.preventDefault(); save(); };
    async function exportDocument(kind, label) {
      if (!['synopsis','ib'].includes(kind) && !choiceInput.value) {$('#design-status').textContent='请先选择用于 '+label+' 的候选设计 A 或 B。';return;}
      if (kind==='ib') {
        const required=[$('#ib-product',form),$('#ib-cutoff',form)];
        for (const section of $$('[data-ib]',form)) {
          if ($('[data-ib-field="summary"]',section).value.trim()) required.push($('[data-ib-field="source"]',section));
        }
        const missing=required.find(el=>!el.value.trim());
        if (missing) {
          missing.focus();$('#design-status').textContent='请填写产品名称、截止日期和已有摘要的章节出处。';
          missing.setCustomValidity('导出 IB 前请填写此项');missing.reportValidity();missing.setCustomValidity('');return;
        }
      }
      if (!save()) return;
      const id = version.id;
      try {
        const parent=parentOf(version);
        const previous_design=parent?{id:parent.id,options:parent.options}:null;
        const res = await fetch('/api/clinical/'+kind+'.docx',jsonOptions('POST',{...version,previous_design,reviews:reviewsFor(id)}));
        if (!res.ok) {
          const failure=await res.json().catch(()=>({}));
          throw new Error(typeof failure.detail==='string'?failure.detail:'导出失败，已保存的版本可重新导出。');
        }
        const url = URL.createObjectURL(await res.blob());
        const link = document.createElement('a'); link.href = url; link.download = ('HiJenny-'+label+'-')+id+'.docx'; link.click();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
      } catch (err) { toast(err.message,'err'); }
    }
    $('#design-export').onclick=()=>exportDocument('synopsis','方案摘要');
    $('#protocol-export').onclick=()=>exportDocument('protocol','Protocol');
    $('#ib-export').onclick=()=>exportDocument('ib','IB');
    $('#csr-export').onclick=()=>exportDocument('csr','CSR');
    $('#sap-export').onclick=()=>exportDocument('sap','SAP');
  }
  draw();
}
