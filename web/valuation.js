'use strict';

const scenarioNames = ['基准','替代'];
function priceScenario(flows, rate) {
  if (!Number.isFinite(rate) || rate < 0 || rate > 100) throw new Error('折现率应在 0% 至 100% 之间。');
  const presentValues = flows.map(({year, amount, probability}) => {
    if (!Number.isInteger(year) || year < 0 || year > 100 || !Number.isFinite(amount) ||
        !Number.isFinite(probability) || probability < 0 || probability > 100) throw new Error('请检查金额、概率及年份。');
    return amount * (probability / 100) / (1 + rate / 100) ** year;
  });
  const total = presentValues.reduce((sum, pv) => sum + pv, 0);
  if (!Number.isFinite(total)) throw new Error('估值超出可计算范围。');
  return {presentValues,total};
}

function valuationWorkbook(plan, project, parent) {
  const xml=value=>esc(String(value ?? '').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g,'')).replace(/&#39;/g,'&apos;');
  const label=value=>`<Cell><Data ss:Type="String">${xml(value)}</Data></Cell>`;
  const number=(value,style='input',formula='')=>`<Cell ss:StyleID="${style}"${formula?' ss:Formula="'+xml(formula)+'"':''}><Data ss:Type="Number">${Number(value)}</Data></Cell>`;
  const row=cells=>'<Row>'+cells.join('')+'</Row>';
  function schedule(version) {
    const end=6+version.flows.length*2;
    const rows=[
      row([label('内部估值复算 · '+version.name),label('百万 '+version.currency),label(version.asOf)]),
      row([label('年折现率（%）'),number(version.rate),label(version.basis)]),
      row([label('项目'),label(project),label('版本'),label(version.id)]),
      row([label('基准净现值'),label('替代净现值'),label('替代减基准')]),
      row(version.outputs.map((out,j)=>number(out.total,'calc',`=SUMIF(R7C2:R${end}C2,"${scenarioNames[j]}",R7C6:R${end}C6)`))
        .concat(number(version.outputs[1].total-version.outputs[0].total,'calc','=RC[-1]-RC[-2]'))),
      row(['现金流','情景','年份','有符号金额（百万）','无条件概率（%）','折现现值（百万）','假设依据','引用的披露编号'].map(label))
    ];
    version.flows.forEach((flow,i)=>flow.cases.forEach((c,j)=>{
      rows.push(row([label(flow.label),label(scenarioNames[j]),number(c.year),number(c.amount),number(c.probability),
        number(version.outputs[j].presentValues[i],'calc','=IF(AND(ISNUMBER(RC[-2]),ISNUMBER(RC[-1]),RC[-1]>=0,RC[-1]<=100,ISNUMBER(RC[-3]),RC[-3]=INT(RC[-3]),RC[-3]>=0,RC[-3]<=100,ISNUMBER(R2C2),R2C2>=0,R2C2<=100),RC[-2]*(RC[-1]/100)/(1+R2C2/100)^RC[-3],NA())'),
        label(flow.basis),label((c.disclosureIds || []).join(' · '))]));
    }));
    if (version.comparable) rows.push(row([label('参照交易及限制'),label(version.comparable)]));
    for (const warning of laterDisclosures(version)) rows.push(row([label('晚于估值日的依据'),label(warning)]));
    return rows;
  }
  const sheet=(name,widths,data)=>`<Worksheet ss:Name="${name}"><Table>${widths.map(w=>'<Column ss:Width="'+w+'"/>').join('')}${data.join('')}</Table></Worksheet>`;
  function evidence(version,prefix) {
    const disclosures=version.disclosures || [], deals=version.deals || [];
    const q=version.question;
    const replies=q ? [
      row(['估值版本',version.id,'问题编号',q.question_id].map(label)),
      row(['卖方问题',q.question,'保存时状态',q.status].map(label)),
      row(['跟进版本',q.revision,'已登记答复数',Array.isArray(q.replies)?q.replies.length:'旧版未记录'].map(label)),
      row(['答复编号','登记时间','登记人','答复内容','文件名','原件位置'].map(label)),
      ...(q.replies || []).map(r=>row([r.event_id,r.ts,r.actor,r.note,r.filename,r.locator].map(label)))
    ] : [];
    return (q?sheet(prefix+'卖方问题与答复',[220,165,140,340,180,200],replies):'')+
      (disclosures.length?sheet(prefix+'引用的公开披露',[230,170,90,240,260],[
      row(['披露编号','字段','披露日','值','来源网址'].map(label)),
      ...disclosures.map(r=>row([r.id,r.field,r.source_date,r.value,r.url].map(label))) ]):'')+
      (deals.length?sheet(prefix+'可比交易',dealFields.map(()=>150),[row(dealFields.map(([,title])=>label(title))),
        ...deals.map(deal=>row(dealFields.map(([key])=>label(deal[key] ?? ''))))]):'');
  }
  return '<?xml version="1.0" encoding="UTF-8"?><?mso-application progid="Excel.Sheet"?>'+
    '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'+
    '<Styles><Style ss:ID="Default"><Alignment ss:Vertical="Top" ss:WrapText="1"/></Style>'+
    '<Style ss:ID="input"><Font ss:Color="#174FA3"/><Interior ss:Color="#EDF5FF" ss:Pattern="Solid"/></Style>'+
    '<Style ss:ID="calc"><NumberFormat ss:Format="#,##0.0000"/></Style></Styles>'+
    sheet('情景复算',[150,100,170,155,125,130,260,220],schedule(plan))+
    (parent?sheet('原版情景复算',[150,100,170,155,125,130,260,220],schedule(parent)):'')+
    (plan.parent?sheet('保存时的版本比较',[460],(parent ? versionComparison(parent,plan) :
      ['未找到原版 '+plan.parent]).map(line=>row([label(line)]))):'')+
    evidence(plan,'')+(parent?evidence(parent,'原版'):'')+'</Workbook>';
}

function laterDisclosures(plan) {
  return [
    ...(plan.deals || []).filter(d=>d.disclosed>plan.asOf).map(d=>'手工可比交易「'+d.name+'」 · '+d.disclosed),
    ...(plan.disclosures || []).filter(r=>r.source_date>plan.asOf).map(r=>'公开依据「'+r.field+'」 · '+r.source_date)
  ];
}

function scenarioDifferences(plan) {
  return plan.flows.map((flow,index)=>{
    const [base,alternate]=flow.cases;
    const assumptions=[];
    for (const [key,label,suffix] of [['amount','金额',''],['year','年份',''],['probability','概率','%']]) {
      if (Number(base[key]) !== Number(alternate[key])) {
        assumptions.push(label+' '+base[key]+suffix+' → '+alternate[key]+suffix);
      }
    }
    const delta=plan.outputs[1].presentValues[index]-plan.outputs[0].presentValues[index];
    const evidenceChanges=[];
    const references=flow.cases.map(c=>new Set(c.disclosureIds || []));
    references.forEach((ids,j)=>{
      for (const id of ids) {
        if (references[1-j].has(id)) continue;
        const record=(plan.disclosures || []).find(r=>r.id===id);
        const label=record ? [record.field,record.source_date,id].filter(Boolean).join(' · ') : id;
        evidenceChanges.push('仅'+scenarioNames[j]+'引用：'+label);
      }
    });
    return {index,delta,assumptions,evidenceChanges};
  }).sort((a,b)=>Math.abs(b.delta)-Math.abs(a.delta) || a.index-b.index);
}

function valuationDraft(plan) {
  const draft=structuredClone(plan);
  draft.flows.forEach((flow,i)=>{flow.id ||= plan.id ? 'legacy:'+plan.id+':'+i : crypto.randomUUID();});
  return draft;
}

function versionComparison(parent, current) {
  const comparable=parent.currency===current.currency && parent.asOf===current.asOf;
  const totals=plan=>plan.outputs.map(o=>o.total.toLocaleString('zh-CN',{maximumFractionDigits:4})).join(' / ');
  const lines=[`原版 ${parent.id} · ${parent.asOf} · 百万 ${parent.currency} · 基准 / 替代 ${totals(parent)}`,
    `本版 ${current.id} · ${current.asOf} · 百万 ${current.currency} · 基准 / 替代 ${totals(current)}`];
  const format=n=>n.toLocaleString('zh-CN',{maximumFractionDigits:4});
  if (comparable) lines.push('净现值变化（百万 '+current.currency+'） · '+scenarioNames.map((s,j)=>
    s+' '+format(current.outputs[j].total-parent.outputs[j].total)).join(' · '));
  else lines.push('估值日或币种不同，请先统一口径再比较金额。');
  if (+parent.rate!==+current.rate) lines.push('年折现率 '+parent.rate+'% → '+current.rate+'%');
  if (parent.basis!==current.basis) lines.push('估值口径依据：'+parent.basis+' → '+current.basis);
  const beforeQuestion=parent.question, afterQuestion=current.question;
  if (beforeQuestion?.question_id!==afterQuestion?.question_id) {
    const describe=q=>q ? q.question_id+' · '+q.question : '未关联';
    lines.push('关联卖方问题：'+describe(beforeQuestion)+' → '+describe(afterQuestion));
  } else if (afterQuestion) {
    if (beforeQuestion.status!==afterQuestion.status) lines.push('卖方问题状态：'+beforeQuestion.status+' → '+afterQuestion.status);
    if (Array.isArray(beforeQuestion.replies) && Array.isArray(afterQuestion.replies)) {
      const earlier=new Set(beforeQuestion.replies.map(reply=>reply.event_id));
      for (const reply of afterQuestion.replies) {
        if (reply.event_id && !earlier.has(reply.event_id)) {
          lines.push('本版新收录卖方答复 · '+[reply.note,reply.filename,reply.locator,reply.actor,reply.ts,reply.event_id].filter(Boolean).join(' · '));
        }
      }
    } else {
      lines.push('历史版本缺少答复清单，无法逐条比较。');
    }
  }
  const flowValues=(plan,i)=>scenarioNames.map((name,j)=>name+'现值 '+format(plan.outputs[j].presentValues[i])).join(' · ')+' 百万 '+plan.currency;
  const old=valuationDraft(parent).flows, used=new Set();
  for (const [i,flow] of current.flows.entries()) {
    const matches=old.map((f,k)=>({f,k})).filter(({f,k})=>!used.has(k) && (flow.id ?
      f.id===flow.id : f.label===flow.label && old.filter(x=>x.label===flow.label).length===1 &&
      current.flows.filter(x=>x.label===flow.label).length===1));
    const previous=matches[0];
    if (!previous) {lines.push('本版列入：'+flow.label+' · '+flowValues(current,i));continue;}
    used.add(previous.k);
    const before=previous.f;
    if (before.label!==flow.label) lines.push('现金流名称：'+before.label+' → '+flow.label);
    if (before.basis!==flow.basis) lines.push(flow.label+' · 依据：'+before.basis+' → '+flow.basis);
    flow.cases.forEach((c,j)=>{
      const prior=before.cases[j], changes=[];
      for (const [key,name,suffix] of [['amount','金额',''],['year','年份',''],['probability','概率','%']]) {
        if (+c[key]!==+prior[key]) changes.push(name+' '+prior[key]+suffix+' → '+c[key]+suffix);
      }
      const ids=new Set(c.disclosureIds || []), oldIds=new Set(prior.disclosureIds || []);
      for (const id of oldIds) if (!ids.has(id)) changes.push('移除引用 '+id);
      for (const id of ids) if (!oldIds.has(id)) changes.push('新增引用 '+id);
      const delta=current.outputs[j].presentValues[i]-parent.outputs[j].presentValues[previous.k];
      if (comparable && delta) changes.push('现值变化 '+format(delta)+' 百万 '+current.currency);
      if (changes.length) lines.push(flow.label+' · '+scenarioNames[j]+' · '+changes.join(' · '));
    });
  }
  old.forEach((f,k)=>{if (!used.has(k)) lines.push('仅原版列入：'+f.label+' · '+flowValues(parent,k));});
  return lines;
}

const dealFields = [
  ['name','交易双方与资产','text'], ['disclosed','披露日','date'],
  ['scope','授权地区、权利范围及资产阶段','text'], ['currency','披露币种','text'],
  ['upfront','已披露首付款（百万）','number'], ['milestones','或有里程碑上限（百万）','number'],
  ['source','披露来源及页码或链接','text'], ['limits','与本资产的可比性限制','text']
];

function projectValuations(project) {
  const prefix='hijenny.valuation.'+project+'.';
  const versions=[];
  for (const key of Object.keys(localStorage).filter(k=>k.startsWith(prefix))) {
    let value;
    try {value=JSON.parse(localStorage.getItem(key));} catch {continue;}
    if (value?.id && key===prefix+value.id && typeof value.created==='string') versions.push(value);
  }
  return versions.sort((a,b)=>b.created.localeCompare(a.created));
}

async function renderValuation(seq) {
  const project = state.projectId;
  const prefix = 'hijenny.valuation.' + project + '.';
  const projectPath = '/api/projects/' + encodeURIComponent(project);
  const [response, detail] = await Promise.all([api(projectPath+'/questions'), api(projectPath)]);
  if (seq !== state.renderSeq) return;
  const versions = () => projectValuations(project);
  const newFlow = () => ({id:crypto.randomUUID(),label:'',basis:'',cases:[{},{}]});
  const newPlan = () => ({name:detail.project.asset_name || '',origin:detail.project.meta?.opportunity || null,deals:[],asOf:'',currency:'USD',rate:'',basis:'',comparable:'', flows:[newFlow()],question:null});
  const requested = response.questions.find(q=>q.question_id===hashParams().get('question'));
  const previous = versions();
  const requestedVersion=hashParams().get('version');
  const exact=previous.find(v=>v.id===requestedVersion);
  if (requestedVersion && (!exact || (hashParams().has('question') && exact.question?.question_id!==hashParams().get('question')))) {
    setView(`<section class="card"><h1>当前浏览器找不到对应的项目估值版本</h1>
      <a href="#/valuation?project=${encodeURIComponent(project)}">查看本项目的估值</a></section>`);return;
  }
  let prior = exact || (requested ? previous.find(v=>v.question?.question_id===requested.question_id) : previous[0]) || null;
  let draft = valuationDraft(prior || newPlan());
  if (requested && !prior) draft.question=requested;
  function rememberVersion() {
    const params=new URLSearchParams({project});
    if (prior) params.set('version',prior.id);
    if (draft.question) params.set('question',draft.question.question_id);
    window.history.replaceState(null,'','#/valuation?'+params);
  }
  let dirty = false;
  const number = value => Number(value).toLocaleString('zh-CN',{maximumFractionDigits:4});
  const searchTerms={asset:'',keyword:''};
  const documents=new Map((detail.documents || []).map(doc=>[doc.document_id,doc]));
  function replySource(reply) {
    const url=documents.get(reply.document_id)?.file_url;
    return url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(reply.filename)}</a>` : esc(reply.filename);
  }
  function disclosureText(r) {
    return [r.asset || r.subject,r.field,r.value,r.region,'披露 '+r.source_date,
      r.effective?'发生或生效 '+r.effective:''].filter(Boolean).join(' · ');
  }
  function archiveUrl(r) {
    return location.origin+location.pathname+'#/public-events?'+new URLSearchParams({event:r.id});
  }
  function disclosureLinks(r) {
    return `<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">SEC 原文</a> · <a href="${esc(archiveUrl(r))}" target="_blank" rel="noopener noreferrer">HiJenny 单条归档</a>`;
  }
  function scenarioSources(plan, scenario) {
    return (plan.disclosures || []).filter(r=>scenario.disclosureIds?.includes(r.id));
  }
  function paint() {
    const questions = [...response.questions];
    if (draft.question && !questions.some(q=>q.question_id===draft.question.question_id)) questions.push(draft.question);
    setView(`<section class="research-home" id="valuation"><h1>买方交易情景估值</h1>
      <p>按同一币种录入增量净现金流。收款填正数，付款填负数；发生概率按估值日的无条件概率填写。</p>
      <label>项目内已保存的估值版本<select id="val-history"><option value="">新估值</option>
      ${versions().map(v=>option(v.id,prior?.id,v.name+' · '+fmtDate(v.created))).join('')}</select></label>
      ${opportunityOrigin(draft.origin)}
      <form id="val-form"><fieldset class="card"><legend>估值口径</legend>
      <label>估值名称<input required maxlength="200" data-head="name" value="${esc(draft.name)}"></label>
      <label>估值日<input required type="date" data-head="asOf" value="${esc(draft.asOf)}"></label>
      <label>币种 · 全部金额单位为百万<select data-head="currency">${['USD','CNY','EUR','JPY','GBP'].map(c=>option(c,draft.currency,c)).join('')}</select></label>
      <label>年折现率（%）<input type="number" required min="0" max="100" step="any" data-head="rate" value="${esc(draft.rate)}"></label>
      <label>折现率和估值日的来源或假设<textarea required maxlength="3000" data-head="basis">${esc(draft.basis)}</textarea></label>
      <label>参照交易、披露日期、出处和可比性限制（如有）<textarea maxlength="4000" data-head="comparable">${esc(draft.comparable)}</textarea></label>
      <label>关联卖方问题<select id="val-question"><option value="">无关联</option>${questions.map(q=>option(q.question_id,draft.question?.question_id,q.question)).join('')}</select></label>
      </fieldset><h2>可比交易披露</h2><p>每笔交易按原币种记录。未披露金额留空，已披露为零填 0。里程碑上限单独展示，现金流假设由评审人填写。</p>
      <section class="card"><h3>公开交易依据</h3>
      <label>按资产或公司查已核实的 SEC 披露<input id="val-public-query" data-disclosure-search="asset" value="${esc(searchTerms.asset)}" placeholder="例如 ivonescimab"></label>
      <label>交易条款关键词<input data-disclosure-search="keyword" value="${esc(searchTerms.keyword)}" placeholder="例如 首付款、里程碑"></label>
      <button type="button" class="btn" id="val-public-search">查找估值日已披露的记录</button>
      <div id="val-public-list" aria-live="polite"></div>
      ${(draft.disclosures || []).map((r,i)=>`<p>${esc(disclosureText(r))} · ${disclosureLinks(r)}
      <button type="button" class="btn" data-remove-disclosure="${i}">移除依据</button></p>`).join('')}</section>
      ${(draft.deals || []).map((deal,i)=>`<fieldset class="card" data-deal="${i}"><legend>可比交易 ${i+1}</legend>
      ${dealFields.map(([key,label,type])=>`<label>${label}<input data-term="${key}" type="${type}" ${type==='number'?'min="0" step="any"':'required maxlength="2000"'} value="${esc(deal[key] ?? '')}"></label>`).join('')}
      <button type="button" class="btn" data-remove-deal="${i}">移除此交易</button></fieldset>`).join('')}
      <button type="button" class="btn" id="val-add-deal">增加可比交易</button><h2>现金流与里程碑</h2><p>第 0 年为估值日，第 1 年及以后按估值日周年折现。两种情景可分别调整收付金额、发生概率和时间。</p>
      ${draft.flows.map((row,i)=>`<fieldset class="card" data-row="${i}"><legend>现金流 ${i+1}</legend>
      <label>项目（如首付款、研发成本或产品净现金流）<input data-row-field="label" maxlength="200" required value="${esc(row.label)}"></label>
      ${scenarioNames.map((s,j)=>`<fieldset><legend>${s}情景</legend>
      ${[['year','发生年份','min="0" max="100" step="1"'],['amount','有符号金额（百万）','step="any"'],['probability','无条件发生概率（%）','min="0" max="100" step="any"']].map(([k,label,limits])=>
        `<label>${label}<input required type="number" data-case="${j}" data-field="${k}" ${limits} value="${esc(row.cases[j][k] ?? '')}"></label>`).join('')}
        ${draft.disclosures?.length?`<fieldset><legend>本情景参考的公开披露</legend>
        ${draft.disclosures.map(r=>`<label><input type="checkbox" data-case-source="${j}" value="${esc(r.id)}" ${row.cases[j].disclosureIds?.includes(r.id)?'checked':''}> ${esc(disclosureText(r))}</label>`).join('')}</fieldset>`:''}</fieldset>`).join('')}
      <label>两种情景金额、概率及年份的依据<textarea required data-row-field="basis" maxlength="4000">${esc(row.basis)}</textarea></label>
      <button type="button" class="btn" data-remove-flow="${i}" ${draft.flows.length===1?'disabled':''}>移除此现金流</button></fieldset>`).join('')}
      <div><button type="button" id="val-add" class="btn">增加现金流</button>
      <button type="submit" class="btn primary">计算并保存版本</button></div></form>
      <div id="val-result" aria-live="polite"></div></section>`);
    const form=$('#val-form');
    const result=$('#val-result');
    function collect() {
      for (const el of $$('[data-head]',form)) draft[el.dataset.head]=el.value.trim();
      draft.deals = $$('[data-deal]',form).map(box=>Object.fromEntries(
        $$('[data-term]',box).map(el=>[el.dataset.term,el.value.trim()])));
      const picked=questions.find(q=>q.question_id===$('#val-question').value);
      if (picked) draft.question={question_id:picked.question_id,question:picked.question,status:picked.status};
      else draft.question=null;
      for (const box of $$('[data-row]',form)) {
        const row=draft.flows[Number(box.dataset.row)];
        for (const el of $$('[data-row-field]',box)) row[el.dataset.rowField]=el.value.trim();
        row.cases.forEach((scenario,j)=>{
          scenario.disclosureIds=$$('[data-case-source="'+j+'"]:checked',box).map(el=>el.value);
        });
        for (const el of $$('[data-field]',box)) row.cases[el.dataset.case][el.dataset.field]=el.value;
      }
    }
    function show(v) {
      const totals=v.outputs.map(o=>o.total);
      const differences=scenarioDifferences(v);
      const late=laterDisclosures(v);
      const parent=v.parent && versions().find(item=>item.id===v.parent);
      const revisions=parent ? versionComparison(parent,v) : v.parent ? ['本浏览器未找到原版 '+v.parent] : [];

      result.innerHTML=`<h2>买方风险调整净现值</h2><p>${esc(v.asOf)} · 百万 ${esc(v.currency)} · 年折现率 ${esc(v.rate)}%</p>
        ${late.length?`<section class="card"><h3>本版含估值日后披露的依据</h3><ul>${late.map(line=>`<li>${esc(line)}</li>`).join('')}</ul></section>`:''}
        <table><caption>按现值差额的绝对值排序，差额为替代减基准</caption><thead><tr><th>现金流</th><th>基准现值</th><th>替代现值</th><th>差额</th></tr></thead><tbody>
        ${differences.map(({index:i,delta,assumptions,evidenceChanges})=>`<tr><th>${esc(v.flows[i].label)}<br><small>${esc(assumptions.join(' · ') || '金额、年份和概率相同')}</small>${evidenceChanges.map(line=>`<br><small>${esc(line)}</small>`).join('')}</th>${v.outputs.map((o,j)=>`<td>${number(o.presentValues[i])}${scenarioSources(v,v.flows[i].cases[j]).map(r=>
          `<br>${esc(r.field)} · ${esc(r.source_date)} · ${disclosureLinks(r)}`).join('')}</td>`).join('')}<td>${number(delta)}</td></tr>`).join('')}
        <tr><th>合计</th>${totals.map(t=>`<td>${number(t)}</td>`).join('')}<td>${number(totals[1]-totals[0])}</td></tr></tbody></table>
        <p>替代情景较基准变化 ${number(totals[1]-totals[0])} 百万 ${esc(v.currency)}。</p>
        ${v.deals?.length ? `<h3>本版参照的交易条款</h3><div class="bd-scroll"><table>
        <thead><tr><th>交易与披露日</th><th>授权范围</th><th>原币种</th><th>首付款（百万）</th><th>里程碑上限（百万）</th><th>来源与限制</th></tr></thead>
        <tbody>${v.deals.map(d=>`<tr><th>${esc(d.name)}<br>${esc(d.disclosed)}</th><td>${esc(d.scope)}</td><td>${esc(d.currency)}</td>
        <td>${esc(d.upfront || '未披露')}</td><td>${esc(d.milestones || '未披露')}</td><td>${esc(d.source)}<br>${esc(d.limits)}</td></tr>`).join('')}</tbody></table></div>` : ''}
        ${v.question?.replies ? `<details><summary>保存时已登记的卖方答复（${v.question.replies.length}）</summary>
        ${v.question.replies.map(r=>`<p>${esc(fmtDate(r.ts))} · ${esc(r.actor)} · ${esc(r.note)}<br>${replySource(r)} · ${esc(r.locator)}</p>`).join('')}</details>` : ''}
        ${v.question?`<p><a href="#/followup?project=${encodeURIComponent(project)}&question=${encodeURIComponent(v.question.question_id)}">复核关联卖方问题</a> · 保存时状态 ${esc(v.question.status)}</p>`:''}
        ${revisions.length?`<details id="val-revisions"><summary>与上一个估值版本比较</summary><ul>${revisions.map(line=>'<li>'+esc(line)+'</li>').join('')}</ul></details>`:''}
        <button type="button" id="val-download" class="btn">下载内部估值底稿</button>
        <button type="button" id="val-formulas" class="btn">下载可复算表（Excel XML）</button>`;
      $('#val-formulas').onclick=()=>{
        const url=URL.createObjectURL(new Blob([valuationWorkbook(v,project,parent)],{type:'application/xml;charset=utf-8'}));
        const link=document.createElement('a');link.href=url;link.download='HiJenny-估值复算-'+v.id+'.xml';link.click();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
      };
      $('#val-download').onclick=()=>{
        const lines=['内部交易评审 · '+v.name,'版本 '+v.id,'项目 '+project,'估值日 '+v.asOf+' · 百万 '+v.currency,
          '年折现率 '+v.rate+'% · '+v.basis,'参照交易及限制 '+(v.comparable || '未提供'),
          '关联问题 '+(v.question ? v.question.question_id+' '+v.question.question+' · '+v.question.status : '未关联'),
          '单笔现值 = 有符号金额 × 无条件发生概率 ÷ (1 + 年折现率)^年份'];
        if (revisions.length) lines.push('', '与上一个估值版本比较', ...revisions);
        if (v.question?.replies) {
          lines.push('保存时的跟进版本 '+v.question.revision+' · 已登记卖方答复 '+v.question.replies.length+' 条');
          for (const r of v.question.replies) lines.push(r.ts+' · '+r.actor+' · '+r.note,r.filename+' · '+r.locator+' · '+r.event_id);
        }
        if (late.length) lines.push('', '本版含估值日后披露的依据', ...late);
        if (v.origin) lines.push('', '机会筛选快照 '+v.origin.snapshot_id, v.origin.asset, v.origin.rationale,
          v.origin.trial_title, sourceLink({id:v.origin.nct}), '检索时间 '+v.origin.retrieved);
        for (const r of v.disclosures || []) lines.push('', '公开交易依据 · '+disclosureText(r), r.url, '公开动态编号 '+r.id,archiveUrl(r));
        for (const deal of v.deals || []) {
          lines.push('', '可比交易披露', ...dealFields.map(([key,label])=>label+' · '+(deal[key] || '未披露')));
        }
        v.flows.forEach((f,i)=>{
          lines.push('',f.label);
          f.cases.forEach((c,j)=>{
            lines.push(scenarioNames[j]+' · 第 '+c.year+' 年 · 金额 '+c.amount+' · 概率 '+c.probability+'% · 现值 '+v.outputs[j].presentValues[i]);
            for (const r of scenarioSources(v,c)) lines.push('本情景参照 · '+disclosureText(r), r.url, '公开动态编号 '+r.id,archiveUrl(r));
          });
          lines.push('依据 '+f.basis);
        });
        lines.push('','现金流差额（替代减基准，按绝对值排序）');
        for (const {index,delta,assumptions,evidenceChanges} of differences) {
          lines.push(v.flows[index].label+' · '+delta+' 百万 '+v.currency+' · '+(assumptions.join(' · ') || '金额、年份和概率相同'), ...evidenceChanges);
        }
        lines.push('',...totals.map((t,j)=>scenarioNames[j]+'净现值 '+t),'情景差额 '+(totals[1]-totals[0]));
        const url=URL.createObjectURL(new Blob([lines.join('\n')],{type:'text/plain;charset=utf-8'}));
        const link=document.createElement('a');link.href=url;link.download='HiJenny-内部估值-'+v.id+'.txt';link.click();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
      };
    }
    if (prior && !dirty) show(prior);
    let searchRevision=0;
    form.oninput=e=>{
      const term=e.target.dataset.disclosureSearch;
      if (term || e.target.dataset.head==='asOf') {
        searchRevision++;
        search.disabled=false;
        list.replaceChildren();
      }
      if (term) {searchTerms[term]=e.target.value;return;}
      dirty=true;result.textContent='输入已修改，请重新计算。';
    };
    $('#val-history').onchange=e=>{
      if (dirty && !confirm('放弃未保存的修改并切换版本？')) {e.target.value=prior?.id || '';return;}
      prior=versions().find(v=>v.id===e.target.value) || null;draft=valuationDraft(prior || newPlan());dirty=false;rememberVersion();paint();
    };
    const search=$('#val-public-search',form), list=$('#val-public-list',form);
    search.onclick=async ()=>{
      const asOf=form.querySelector('[data-head="asOf"]').value;
      if (!asOf) {list.textContent='请先填写估值日。';return;}
      const revision=++searchRevision;
      search.disabled=true; list.textContent='正在读取公开披露…';
      const params=new URLSearchParams({asset:searchTerms.asset.trim(),keyword:searchTerms.keyword.trim(),kind:'reviewed',source:'SEC',disclosed_to:asOf});
      let offset=0;
      async function loadPage() {
        params.set('offset',String(offset));
        const page=await api('/api/public-events?'+params);
        if (!list.isConnected || revision!==searchRevision) return;
        if (!offset) list.replaceChildren();
        for (const r of page.items) {
          const entry=document.createElement('p');
          entry.innerHTML=`${esc(disclosureText(r))} · ${disclosureLinks(r)} <button type="button" class="btn">加入估值依据</button>`;
          const add=entry.querySelector('button');
          add.disabled=(draft.disclosures || []).some(d=>d.id===r.id);
          add.onclick=()=>{
            collect();
            if (r.source_date>draft.asOf || !draft.asOf) {list.textContent='估值日已改变，请重新检索。';return;}
            draft.disclosures ||= [];draft.disclosures.push(r);dirty=true;paint();
          };
          list.append(entry);
        }
        offset+=page.items.length;
        if (!page.total) list.textContent='当前范围没有已核实的交易披露。';
        if (offset<page.total) {
          const more=document.createElement('button');more.type='button';more.className='btn';more.textContent='加载更多披露';
          list.append(more);more.onclick=()=>{more.remove();loadPage().catch(err=>{if (revision===searchRevision) list.textContent=err.message;});};
        }
      }
      try {await loadPage();} catch(err) {if (revision===searchRevision) list.textContent='读取失败：'+err.message;}
      finally {if (revision===searchRevision) search.disabled=false;}
    };
    for (const el of $$('[data-remove-disclosure]',form)) el.onclick=()=>{
      collect();
      const index=Number(el.dataset.removeDisclosure), id=draft.disclosures[index].id;
      const linked=draft.flows.flatMap(row=>row.cases).filter(scenario=>scenario.disclosureIds.includes(id));
      if (linked.length && !confirm('这条披露已被 '+linked.length+' 项情景引用。移除披露及对应引用？')) return;
      for (const scenario of linked) scenario.disclosureIds=scenario.disclosureIds.filter(ref=>ref!==id);
      draft.disclosures.splice(index,1);dirty=true;paint();
    };
    $('#val-add-deal').onclick=()=>{collect();draft.deals.push({});dirty=true;paint();};
    for (const el of $$('[data-remove-deal]',form)) el.onclick=()=>{
      collect();draft.deals.splice(Number(el.dataset.removeDeal),1);dirty=true;paint();
    };
    $('#val-add').onclick=()=>{collect();draft.flows.push(newFlow());dirty=true;paint();};
    for (const el of $$('[data-remove-flow]',form)) el.onclick=()=>{
      collect();draft.flows.splice(Number(el.dataset.removeFlow),1);dirty=true;paint();
    };
    form.onsubmit=async e=>{
      e.preventDefault();
      if (form.inert) return;
      collect();
      const late=laterDisclosures(draft);
      if (late.length) {
        result.textContent='以下披露晚于估值日 '+draft.asOf+'，请核对披露日、移除记录或调整估值日：'+late.join('；');
        return;
      }
      if (!draft.name || !draft.basis || draft.flows.some(f=>!f.basis || !f.label)) {result.textContent='请填写现金流名称和假设依据。';return;}
      if (draft.deals.some(deal=>dealFields.some(([key,,type])=>type!=='number' && !deal[key]))) {
        result.textContent='请填写可比交易的披露来源、币种和可比性限制等必填项。';return;
      }
      form.inert=true;
      try {
        if (draft.question) {
          const fresh=await api(projectPath+'/followup/'+encodeURIComponent(draft.question.question_id)+'/valuation-context');
          if (!form.isConnected) return;
          draft.question=fresh;
        }
        if (!dirty && prior && JSON.stringify(draft.question)===JSON.stringify(prior.question)) {show(prior);return;}
        const outputs=scenarioNames.map((_,j)=>priceScenario(draft.flows.map(f=>({
          year:Number(f.cases[j].year),amount:Number(f.cases[j].amount),probability:Number(f.cases[j].probability)
        })),Number(draft.rate)));
        const next={...draft, outputs,id:crypto.randomUUID(),parent:prior?.id || null,created:new Date().toISOString()};
        localStorage.setItem(prefix+next.id,JSON.stringify(next));
        prior=next;draft=structuredClone(next);dirty=false;rememberVersion();paint();
      } catch(error) {result.textContent='未保存：'+error.message;} finally {form.inert=false;}
    };
  }
  paint();
}
