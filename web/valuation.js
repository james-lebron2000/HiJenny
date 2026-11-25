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

const dealFields = [
  ['name','交易双方与资产','text'], ['disclosed','披露日','date'],
  ['scope','授权地区、权利范围及资产阶段','text'], ['currency','披露币种','text'],
  ['upfront','已披露首付款（百万）','number'], ['milestones','或有里程碑上限（百万）','number'],
  ['source','披露来源及页码或链接','text'], ['limits','与本资产的可比性限制','text']
];

async function renderValuation(seq) {
  const project = state.projectId;
  const prefix = 'hijenny.valuation.' + project + '.';
  const projectPath = '/api/projects/' + encodeURIComponent(project);
  const [response, detail] = await Promise.all([api(projectPath+'/questions'), api(projectPath)]);
  if (seq !== state.renderSeq) return;
  const versions = () => Object.keys(localStorage).filter(k=>k.startsWith(prefix))
    .map(k=>JSON.parse(localStorage.getItem(k))).sort((a,b)=>b.created.localeCompare(a.created));
  const newFlow = () => ({label:'',basis:'',cases:[{},{}]});
  const newPlan = () => ({name:detail.project.asset_name || '',origin:detail.project.meta?.opportunity || null,deals:[],asOf:'',currency:'USD',rate:'',basis:'',comparable:'', flows:[newFlow()],question:null});
  const requested = response.questions.find(q=>q.question_id===hashParams().get('question'));
  const previous = versions();
  let prior = (requested ? previous.find(v=>v.question?.question_id===requested.question_id) : previous[0]) || null;
  let draft = structuredClone(prior || newPlan());
  if (requested && !prior) draft.question=requested;
  let dirty = false;
  const number = value => Number(value).toLocaleString('zh-CN',{maximumFractionDigits:4});
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
      ${(draft.deals || []).map((deal,i)=>`<fieldset class="card" data-deal="${i}"><legend>可比交易 ${i+1}</legend>
      ${dealFields.map(([key,label,type])=>`<label>${label}<input data-term="${key}" type="${type}" ${type==='number'?'min="0" step="any"':'required maxlength="2000"'} value="${esc(deal[key] ?? '')}"></label>`).join('')}
      <button type="button" class="btn" data-remove-deal="${i}">移除此交易</button></fieldset>`).join('')}
      <button type="button" class="btn" id="val-add-deal">增加可比交易</button><h2>现金流与里程碑</h2><p>第 0 年为估值日，第 1 年及以后按估值日周年折现。两种情景可分别调整收付金额、发生概率和时间。</p>
      ${draft.flows.map((row,i)=>`<fieldset class="card" data-row="${i}"><legend>现金流 ${i+1}</legend>
      <label>项目（如首付款、研发成本或产品净现金流）<input data-row-field="label" maxlength="200" required value="${esc(row.label)}"></label>
      ${scenarioNames.map((s,j)=>`<fieldset><legend>${s}情景</legend>
      ${[['year','发生年份','min="0" max="100" step="1"'],['amount','有符号金额（百万）','step="any"'],['probability','无条件发生概率（%）','min="0" max="100" step="any"']].map(([k,label,limits])=>
        `<label>${label}<input required type="number" data-case="${j}" data-field="${k}" ${limits} value="${esc(row.cases[j][k] ?? '')}"></label>`).join('')}</fieldset>`).join('')}
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
        for (const el of $$('[data-field]',box)) row.cases[el.dataset.case][el.dataset.field]=el.value;
      }
    }
    function show(v) {
      const totals=v.outputs.map(o=>o.total);
      result.innerHTML=`<h2>买方风险调整净现值</h2><p>${esc(v.asOf)} · 百万 ${esc(v.currency)} · 年折现率 ${esc(v.rate)}%</p>
        <table><thead><tr><th>现金流</th><th>基准现值</th><th>替代现值</th></tr></thead><tbody>
        ${v.flows.map((f,i)=>`<tr><th>${esc(f.label)}</th>${v.outputs.map(o=>`<td>${number(o.presentValues[i])}</td>`).join('')}</tr>`).join('')}
        <tr><th>合计</th>${totals.map(t=>`<td>${number(t)}</td>`).join('')}</tr></tbody></table>
        <p>替代情景较基准变化 ${number(totals[1]-totals[0])} 百万 ${esc(v.currency)}。</p>
        ${v.deals?.length ? `<h3>本版参照的交易条款</h3><div class="bd-scroll"><table>
        <thead><tr><th>交易与披露日</th><th>授权范围</th><th>原币种</th><th>首付款（百万）</th><th>里程碑上限（百万）</th><th>来源与限制</th></tr></thead>
        <tbody>${v.deals.map(d=>`<tr><th>${esc(d.name)}<br>${esc(d.disclosed)}</th><td>${esc(d.scope)}</td><td>${esc(d.currency)}</td>
        <td>${esc(d.upfront || '未披露')}</td><td>${esc(d.milestones || '未披露')}</td><td>${esc(d.source)}<br>${esc(d.limits)}</td></tr>`).join('')}</tbody></table></div>` : ''}
        ${v.question?`<p><a href="#/followup?project=${encodeURIComponent(project)}&question=${encodeURIComponent(v.question.question_id)}">复核关联卖方问题</a> · 保存时状态 ${esc(v.question.status)}</p>`:''}
        <button type="button" id="val-download" class="btn">下载内部估值底稿</button>`;
      $('#val-download').onclick=()=>{
        const lines=['内部交易评审 · '+v.name,'版本 '+v.id,'项目 '+project,'估值日 '+v.asOf+' · 百万 '+v.currency,
          '年折现率 '+v.rate+'% · '+v.basis,'参照交易及限制 '+(v.comparable || '未提供'),
          '关联问题 '+(v.question ? v.question.question_id+' '+v.question.question+' · '+v.question.status : '未关联'),
          '单笔现值 = 有符号金额 × 无条件发生概率 ÷ (1 + 年折现率)^年份'];
        if (v.origin) lines.push('', '机会筛选快照 '+v.origin.snapshot_id, v.origin.asset, v.origin.rationale,
          v.origin.trial_title, sourceLink({id:v.origin.nct}), '检索时间 '+v.origin.retrieved);
        for (const deal of v.deals || []) {
          lines.push('', '可比交易披露', ...dealFields.map(([key,label])=>label+' · '+(deal[key] || '未披露')));
        }
        v.flows.forEach((f,i)=>{
          lines.push('',f.label,...f.cases.map((c,j)=>scenarioNames[j]+' · 第 '+c.year+' 年 · 金额 '+c.amount+' · 概率 '+c.probability+'% · 现值 '+v.outputs[j].presentValues[i]),'依据 '+f.basis);
        });
        lines.push('',...totals.map((t,j)=>scenarioNames[j]+'净现值 '+t),'情景差额 '+(totals[1]-totals[0]));
        const url=URL.createObjectURL(new Blob([lines.join('\n')],{type:'text/plain;charset=utf-8'}));
        const link=document.createElement('a');link.href=url;link.download='HiJenny-内部估值-'+v.id+'.txt';link.click();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
      };
    }
    if (prior && !dirty) show(prior);
    form.oninput=()=>{dirty=true;result.textContent='输入已修改，请重新计算。';};
    $('#val-history').onchange=e=>{
      if (dirty && !confirm('放弃未保存的修改并切换版本？')) {e.target.value=prior?.id || '';return;}
      prior=versions().find(v=>v.id===e.target.value) || null;draft=structuredClone(prior || newPlan());dirty=false;paint();
    };
    $('#val-add-deal').onclick=()=>{collect();draft.deals.push({});dirty=true;paint();};
    for (const el of $$('[data-remove-deal]',form)) el.onclick=()=>{
      collect();draft.deals.splice(Number(el.dataset.removeDeal),1);dirty=true;paint();
    };
    $('#val-add').onclick=()=>{collect();draft.flows.push(newFlow());dirty=true;paint();};
    for (const el of $$('[data-remove-flow]',form)) el.onclick=()=>{
      collect();draft.flows.splice(Number(el.dataset.removeFlow),1);dirty=true;paint();
    };
    form.onsubmit=e=>{
      e.preventDefault();collect();
      if (!draft.name || !draft.basis || draft.flows.some(f=>!f.basis || !f.label)) {result.textContent='请填写现金流名称和假设依据。';return;}
      if (draft.deals.some(deal=>dealFields.some(([key,,type])=>type!=='number' && !deal[key]))) {
        result.textContent='请填写可比交易的披露来源、币种和可比性限制等必填项。';return;
      }
      if (!dirty && prior) {show(prior);return;}
      try {
        const outputs=scenarioNames.map((_,j)=>priceScenario(draft.flows.map(f=>({
          year:Number(f.cases[j].year),amount:Number(f.cases[j].amount),probability:Number(f.cases[j].probability)
        })),Number(draft.rate)));
        const next={...draft, outputs,id:crypto.randomUUID(),parent:prior?.id || null,created:new Date().toISOString()};
        localStorage.setItem(prefix+next.id,JSON.stringify(next));
        prior=next;draft=structuredClone(next);dirty=false;paint();
      } catch(error) {result.textContent='未保存：'+error.message;}
    };
  }
  paint();
}
