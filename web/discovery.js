'use strict';

function opportunityOrigin(record) {
  if (!record || !/^NCT[0-9]{8}$/.test(record.nct)) return '';
  return `<section class="card"><h2>机会来源与入选理由</h2>
    <p>${esc(record.rationale)}</p><a href="${esc(sourceLink({id:record.nct}))}" target="_blank" rel="noopener noreferrer">${esc(record.nct)} · ${esc(record.trial_title)}</a>
    <p>登记申办方 ${esc(record.sponsor || '未披露')} · 登记更新 ${esc(record.updated || '未提供')} · 检索于 ${esc(fmtDate(record.retrieved))}</p>
    <p>筛选条件 · ${esc([record.criteria?.condition,record.criteria?.phase,record.criteria?.sponsor].filter(Boolean).join(' / '))}</p>
    <small>筛选快照 ${esc(record.snapshot_id)}</small></section>`;
}

function renderDiscovery() {
  const prefix = 'hijenny.opportunity.';
  const history = () => Object.keys(localStorage).filter(k=>k.startsWith(prefix))
    .map(k=>JSON.parse(localStorage.getItem(k))).sort((a,b)=>b.created.localeCompare(a.created));
  let snapshot = history()[0];
  setView(`<section class="research-home" id="opportunities"><h1>筛选候选资产与潜在伙伴</h1>
    <form id="screen-form" class="card"><label>适应症（英文）<input name="condition" required maxlength="200" placeholder="例如 melanoma"></label>
    <label>登记的临床阶段<select name="phase">${['','EARLY_PHASE1','PHASE1','PHASE2','PHASE3','PHASE4'].map(v=>option(v,'',v || '全部阶段')).join('')}</select></label>
    <label>主要申办方（可选）<input name="sponsor" maxlength="200" placeholder="英文机构名"></label>
    <small>筛选词将发送至 ClinicalTrials.gov。登记申办方与候选干预的交易权属需分别核实。</small>
    <button class="btn primary" type="submit">筛选并保存快照</button></form>
    <label>已保存的候选集<select id="screen-history"></select></label>
    <p id="screen-status" role="status"></p><div id="screen-results"></div></section>`);
  const root=$('#opportunities'), form=$('#screen-form',root), select=$('#screen-history',root);
  const results=$('#screen-results',root), status=$('#screen-status',root);
  let busy=false;
  function lock(value) {
    busy=value;
    for (const el of root.querySelectorAll('button, select, input, textarea')) el.disabled=value;
  }
  function paint() {
    select.innerHTML='<option value="">选择快照</option>'+history().map(s=>option(s.id,snapshot?.id,
      s.criteria.condition+' · '+s.items.length+' 条 · '+fmtDate(s.created))).join('');
    if (!snapshot) { results.replaceChildren(); return; }
    for (const [key,value] of Object.entries(snapshot.criteria)) form.elements[key].value=value;
    results.innerHTML=`<h2>${esc(snapshot.criteria.condition)} · 候选试验</h2>
      <p>筛选时数据库命中 ${esc(snapshot.total)} 条；本快照保存 ${snapshot.items.length} 条。检索时间 ${esc(fmtDate(snapshot.created))}。</p>
      ${snapshot.items.map((r,i)=>`<article class="card"><h3><a href="${esc(sourceLink(r))}" target="_blank" rel="noopener noreferrer">${esc(r.title)}</a></h3>
      <p>${esc([r.id,r.sponsor,r.phases.join(' / '),r.status].filter(Boolean).join(' · '))}</p>
      <p>登记干预 · ${esc(r.interventions.join(' / ') || '未披露')}</p>
      <details><summary>将候选资产转入尽调</summary><form data-candidate="${i}">
      <label>拟评估的具体资产<input name="asset" maxlength="200" required list="assets-${i}"></label>
      <datalist id="assets-${i}">${r.interventions.map(name=>`<option value="${esc(name)}"></option>`).join('')}</datalist>
      <label>入选理由及仍需核对的权属或数据问题<textarea required maxlength="3000" name="rationale"></textarea></label>
      <button type="submit" class="btn">建立或打开尽调项目</button></form></details></article>`).join('') ||
      '<p>没有匹配记录。可放宽阶段或申办方筛选。</p>'}
      ${snapshot.cursor?'<button type="button" class="btn" id="screen-next">继续取下一页并保存新版</button>':''}`;
    const more=$('#screen-next',root);
    if (more) more.onclick=()=>retrieve(snapshot);
  }
  async function retrieve(previous) {
    if (busy) return;
    const criteria=previous?.criteria || Object.fromEntries(new FormData(form));
    lock(true); status.textContent='正在读取登记数据…';
    try {
      const data=await api('/api/discovery/screen',jsonOptions('POST',{criteria,cursor:previous?.cursor || ''}));
      const byId=new Map((previous?.items || []).map(item=>[item.id,item]));
      for (const row of data.items) byId.set(row.id,{...row,retrieved:data.retrieved});
      const next={id:crypto.randomUUID(),criteria,created:data.retrieved,total:data.total ?? previous?.total,cursor:data.cursor,
        items:[...byId.values()],parent:previous?.id || null};
      localStorage.setItem(prefix+next.id,JSON.stringify(next));
      snapshot=next;paint();status.textContent='候选集已保存在本浏览器。';
    } catch (err) {status.textContent='筛选未保存：'+err.message;}
    finally {lock(false);}
  }
  form.onsubmit=e=>{e.preventDefault();retrieve(null);};
  select.onchange=()=>{snapshot=history().find(s=>s.id===select.value);paint();status.textContent='';};
  results.onsubmit=async e=>{
    e.preventDefault();
    const candidate=e.target.closest('form[data-candidate]');
    if (!candidate || busy) return;
    const entry=snapshot.items[Number(candidate.dataset.candidate)];
    const values=Object.fromEntries(new FormData(candidate));
    lock(true);
    try {
      const res=await api('/api/discovery/projects',jsonOptions('POST',{...values,snapshot_id:snapshot.id,
        criteria:snapshot.criteria,nct:entry.id,trial_title:entry.title,sponsor:entry.sponsor,
        phase:entry.phases.join(' / '),updated:entry.updated,retrieved:entry.retrieved}));
      if (!root.isConnected) return;
      window.history.pushState(null,'','#/overview?project='+encodeURIComponent(res.project.project_id));
      location.reload();
    } catch (err) {status.textContent='项目未打开：'+err.message;}
    finally {lock(false);}
  };
  paint();
}
