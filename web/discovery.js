'use strict';

function registryAliasLines(groups) {
  return (groups || []).map(g=>g.name+' · '+[...g.aliases].sort().join(' / ')).sort();
}

function registryAliasesHtml(groups) {
  const lines=registryAliasLines(groups);
  return lines.length ? '<details><summary>核对各项干预的登记别名</summary><ul>'+
    lines.map(line=>'<li>'+esc(line)+'</li>').join('')+'</ul></details>' : '';
}

function registryResultLabel(value) {
  return value===true ? '已公布登记结果' : value===false ? '尚无登记结果' : '结果状态未采集';
}

function registrySearchTerm(input, effective) {
  return effective && effective!==input ? `<p>登记库实际干预检索词 · <code>${esc(effective)}</code></p>` : '';
}

function screeningOriginHtml(origin) {
  if (!origin) return '';
  return `<p>发起筛选的归档 · ${esc(origin.asset)} · ${esc(origin.trial)} · ${esc(origin.field)} · ${esc(origin.disclosed)}
    <a href="#/public-events?event=${encodeURIComponent(origin.event_id)}">回看原动态</a></p>`;
}

function opportunityOrigin(record) {
  if (!record || !/^NCT[0-9]{8}$/.test(record.nct)) return '';
  return `<section class="card"><h2>机会来源与入选理由</h2>
    <p>${esc(record.rationale)}</p><a href="${esc(sourceLink({id:record.nct}))}" target="_blank" rel="noopener noreferrer">${esc(record.nct)} · ${esc(record.trial_title)}</a>
    <p>登记申办方 ${esc(record.sponsor || '未披露')} · 登记更新 ${esc(record.updated || '未提供')} · 检索于 ${esc(fmtDate(record.retrieved))}</p>
    <p>筛选条件 · ${esc([record.criteria?.condition,record.criteria?.intervention,record.criteria?.phase,record.criteria?.sponsor,record.criteria?.country,record.criteria?.results && registryResultLabel(record.criteria.results==='Yes')].filter(Boolean).join(' / '))}</p>
    ${screeningOriginHtml(record.screening_origin)}
    ${registrySearchTerm(record.criteria?.intervention,record.registry_intervention)}
    ${record.countries?.length ? `<p>登记研究中心所在国家或地区 · ${esc(record.countries.join(' / '))}</p>` : ''}
    ${record.has_results!=null ? `<p>${registryResultLabel(record.has_results)} · 首次公布日期 ${esc(record.results_posted || '未提供')}</p>` : ''}
    ${registryAliasesHtml(record.intervention_aliases)}
    <small>筛选快照 ${esc(record.snapshot_id)}</small></section>`;
}

const opportunityFields = {title:'登记标题', sponsor:'申办方', conditions:'适应症', phases:'阶段', countries:'研究中心所在国家或地区',
  interventions:'登记干预', status:'试验状态', updated:'登记更新日'};

function opportunityChanges(before, after) {
  const oldRows = new Map(before.items.map(row => [row.id,row]));
  const newRows = new Map(after.items.map(row => [row.id,row]));
  const format = value => Array.isArray(value) ? [...value].sort().join(' / ') : String(value || '');
  const changes = [];
  for (const [id, row] of newRows) {
    const old = oldRows.get(id);
    if (!old) { changes.push({id, kind:'本次新见', title:row.title}); continue; }
    const fields = [];
    for (const [key,label] of Object.entries(opportunityFields)) {
      if (key === 'countries' && !Object.hasOwn(old,key)) continue;
      const previous = format(old[key]), current = format(row[key]);
      if (previous !== current) fields.push({label, previous, current});
    }
    if (old.has_results!=null && row.has_results!=null && old.has_results!==row.has_results) {
      fields.push({label:'登记结果',previous:registryResultLabel(old.has_results),current:registryResultLabel(row.has_results)});
    }
    if (Object.hasOwn(old,'results_posted') && Object.hasOwn(row,'results_posted') && old.results_posted!==row.results_posted) {
      fields.push({label:'结果首次公布日期',previous:old.results_posted,current:row.results_posted});
    }
    if (Object.hasOwn(old,'intervention_aliases') && Object.hasOwn(row,'intervention_aliases')) {
      const previous=registryAliasLines(old.intervention_aliases), current=registryAliasLines(row.intervention_aliases);
      if (JSON.stringify(previous)!==JSON.stringify(current)) {
        fields.push({label:'登记干预别名',previous:previous.join('；'),current:current.join('；')});
      }
    }
    if (fields.length) changes.push({id, kind:'登记字段变化', title:row.title, fields});
  }
  for (const [id,row] of oldRows) {
    if (!newRows.has(id)) changes.push({id, kind:'本次未见', title:row.title});
  }
  return changes;
}

function opportunityTopic(criteria) {
  return [criteria.condition, criteria.intervention].filter(Boolean).join(' · ');
}

function downloadOpportunityReport(snapshot, baseline, changes) {
  const criteria=snapshot.criteria;
  const lines=['HiJenny 候选试验筛选报告', opportunityTopic(criteria), '',
    '来源 ClinicalTrials.gov', '快照编号 '+snapshot.id, '检索时间 '+snapshot.created,
    '数据库命中 '+(snapshot.total ?? '未记录')+' 条，本快照取回 '+snapshot.items.length+' 条'];
  const origin=snapshot.screening_origin;
  if (origin) lines.push('发起筛选的归档 · '+origin.asset+' · '+origin.trial+' · '+origin.field+' · '+origin.disclosed,
    'HiJenny 动态编号 '+origin.event_id, sourceLink({id:origin.trial}));
  if (snapshot.pages) lines.push('已取页数 '+snapshot.pages);
  for (const [key,label] of Object.entries({condition:'适应症',intervention:'输入的干预名称',phase:'阶段',
    sponsor:'主要申办方',country:'研究中心所在地',results:'登记结果'})) {
    if (criteria[key]) lines.push(label+' · '+(key==='results'?registryResultLabel(criteria.results==='Yes'):criteria[key]));
  }
  if (criteria.intervention) lines.push('登记库实际干预检索词 · '+(snapshot.registry_intervention ?? criteria.intervention));
  if (snapshot.recheckedFrom) {
    lines.push('', '候选集复查', '对照快照 '+snapshot.recheckedFrom);
    if (!baseline) lines.push('本浏览器缺少对照快照，无法计算差异。');
    else {
      lines.push('上次检索 '+baseline.created+'，取回 '+baseline.items.length+' 条。按试验编号对照已取回的记录。');
      if (!changes.length) lines.push('两次取回的候选记录和已核对字段一致。');
      for (const change of changes) {
        lines.push('', change.kind+' · '+change.id+' · '+change.title, sourceLink({id:change.id}));
        for (const field of change.fields || []) lines.push(field.label+' · '+(field.previous || '未披露')+' → '+(field.current || '未披露'));
      }
      if (changes.some(change=>change.kind==='本次未见')) lines.push('排序或本次取回范围可能影响可见记录，撤回状态须核对原登记。');
    }
  }
  lines.push('', '候选试验（'+snapshot.items.length+' 条）');
  for (const row of snapshot.items) {
    lines.push('', row.id+' · '+row.title, sourceLink(row));
    for (const [key,label] of Object.entries(opportunityFields)) {
      if (key==='title') continue;
      const value=row[key];
      lines.push(label+' · '+(value===undefined?'此版未采集':(Array.isArray(value)?value.join(' / '):value) || '未披露'));
    }
    if (row.intervention_aliases) lines.push('登记干预别名 · '+(registryAliasLines(row.intervention_aliases).join('；') || '未披露'));
    lines.push(registryResultLabel(row.has_results));
    if (row.results_posted) lines.push('结果首次公布日期 '+row.results_posted);
    if (row.retrieved) lines.push('本条取回时间 '+row.retrieved);
  }
  const url=URL.createObjectURL(new Blob([lines.join('\n')],{type:'text/plain;charset=utf-8'}));
  const link=document.createElement('a');
  link.href=url; link.download='HiJenny-候选试验-'+snapshot.id+'.txt'; link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function renderDiscovery() {
  const prefix = 'hijenny.opportunity.';
  const history = () => Object.keys(localStorage).filter(k=>k.startsWith(prefix))
    .map(k=>JSON.parse(localStorage.getItem(k))).sort((a,b)=>b.created.localeCompare(a.created));
  const requested = hashParams().get('snapshot');
  const trigger=hashParams().get('event');
  let startingOrigin=null;
  const snapshots = history();
  let snapshot = requested ? snapshots.find(row => row.id === requested) : trigger ? null : snapshots[0];
  setView(`<section class="research-home" id="opportunities"><h1>筛选候选资产与潜在伙伴</h1>
    <div id="screen-start"></div><form id="screen-form" class="card"><label>适应症（英文）<input name="condition" maxlength="200" placeholder="例如 melanoma"></label>
    <label>药物或干预名称<input name="intervention" maxlength="200" placeholder="例如 依达方、泰瑞沙或 pembrolizumab"></label>
    <small>适应症和干预名称至少填写一项。</small>
    <label>登记的临床阶段<select name="phase">${['','EARLY_PHASE1','PHASE1','PHASE2','PHASE3','PHASE4'].map(v=>option(v,'',v || '全部阶段')).join('')}</select></label>
    <label>研究中心所在国家或地区（可选）<input name="country" maxlength="120" list="site-countries" placeholder="例如 Japan"></label>
    <datalist id="site-countries">${['China','United States','Japan','Germany','France','United Kingdom','Canada','Australia'].map(c=>`<option value="${c}"></option>`).join('')}</datalist>
    <label>登记结果<select name="results"><option value="">全部</option><option value="Yes">已公布登记结果</option><option value="No">尚无登记结果</option></select></label>
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
      opportunityTopic(s.criteria)+' · '+s.items.length+' 条 · '+fmtDate(s.created))).join('');
    if (!snapshot) { results.replaceChildren(); return; }
    form.elements.intervention.value='';
    form.elements.country.value='';
    form.elements.results.value='';
    for (const [key,value] of Object.entries(snapshot.criteria)) form.elements[key].value=value;
    const parent = snapshot.parent && history().find(row=>row.id===snapshot.parent);
    const baseline = snapshot.recheckedFrom && history().find(row => row.id === snapshot.recheckedFrom);
    const changes = baseline ? opportunityChanges(baseline, snapshot) : [];
    results.innerHTML=`<h2>${esc(opportunityTopic(snapshot.criteria))} · 候选试验</h2>
      <p>筛选时数据库命中 ${esc(snapshot.total)} 条；本快照保存 ${snapshot.items.length} 条。检索时间 ${esc(fmtDate(snapshot.created))}。</p>
      ${screeningOriginHtml(snapshot.screening_origin)}
      ${registrySearchTerm(snapshot.criteria.intervention,snapshot.registry_intervention)}
      ${parent?`<p><a href="#/discovery?snapshot=${encodeURIComponent(parent.id)}">打开上一版候选快照</a></p>`:''}
      <button type="button" class="btn" id="screen-export">下载候选集报告</button>
      <button type="button" class="btn" id="screen-recheck">按原条件重新检索并比较</button>
      ${baseline ? `<section class="card"><h3>与上次候选集比较</h3><p>上次检索 ${esc(fmtDate(baseline.created))}，取回 ${baseline.items.length} 条；本次取回 ${snapshot.items.length} 条。按试验编号匹配。排序和检索范围会影响出现的记录，“本次未见”不能证明登记撤回。</p>
      ${changes.length ? changes.map(change=>`<details><summary>${esc(change.kind)} · ${esc(change.id)} · ${esc(change.title)}</summary>
        <a href="${esc(sourceLink({id:change.id}))}" target="_blank" rel="noopener noreferrer">查看试验登记</a>
        ${(change.fields || []).map(f=>`<p>${esc(f.label)}：${esc(f.previous || '未披露')} → ${esc(f.current || '未披露')}</p>`).join('')}</details>`).join('') : '<p>两次取回的候选记录和登记字段一致。</p>'}</section>` : ''}
      ${snapshot.items.map((r,i)=>`<article class="card"><h3><a href="${esc(sourceLink(r))}" target="_blank" rel="noopener noreferrer">${esc(r.title)}</a></h3>
      <p>${esc([r.id,r.sponsor,r.phases.join(' / '),r.status].filter(Boolean).join(' · '))}</p>
      <p>研究中心所在国家或地区 · ${esc(r.countries?.join(' / ') || (r.countries?'登记未提供':'旧版快照未采集'))}</p>
      <p>登记适应症 · ${esc(r.conditions?.join(' / ') || '未披露')}</p>
      <p>登记干预 · ${esc(r.interventions.join(' / ') || '未披露')}</p>
      ${registryAliasesHtml(r.intervention_aliases)}
      <p>${registryResultLabel(r.has_results)}${r.results_posted?' · 首次公布 '+esc(r.results_posted):''}</p>
      ${r.has_results===true ? `<a href="${esc(sourceLink({id:r.id,postedResult:true}))}" target="_blank" rel="noopener noreferrer">核对登记结果原文</a>
      <button type="button" class="btn" data-load-results="${esc(r.id)}">查看公布终点与安全性</button><section class="trial-posted" aria-live="polite"></section>` : ''}
      <button type="button" class="btn" data-watch-trial="${esc(r.id)}">关注试验里程碑</button>
      <details><summary>将候选资产转入尽调</summary><form data-candidate="${i}">
      <label>拟评估的具体资产<input name="asset" maxlength="200" required list="assets-${i}"></label>
      <datalist id="assets-${i}">${r.interventions.map(name=>`<option value="${esc(name)}"></option>`).join('')}</datalist>
      <label>入选理由及仍需核对的权属或数据问题<textarea required maxlength="3000" name="rationale"></textarea></label>
      <button type="submit" class="btn">建立或打开尽调项目</button></form></details></article>`).join('') ||
      '<p>没有匹配记录。可放宽阶段或申办方筛选。</p>'}
      ${snapshot.cursor?'<button type="button" class="btn" id="screen-next">继续取下一页并保存新版</button>':''}`;
    bindTrialResultButtons(results);
    $('#screen-export',root).onclick=()=>downloadOpportunityReport(snapshot,baseline,changes);
    $('#screen-recheck',root).onclick=()=>retrieve(snapshot, true);
    const more=$('#screen-next',root);
    if (more) more.onclick=()=>retrieve(snapshot);
  }
  async function retrieve(previous, recheck=false) {
    if (busy) return;
    const criteria=previous?.criteria || Object.fromEntries(new FormData(form));
    lock(true); status.textContent='正在读取登记数据…';
    try {
      const snapshots = new Map(history().map(row => [row.id,row]));
      let depth = previous?.pages || 1;
      if (previous && !previous.pages) {
        for (let ancestor=previous; snapshots.has(ancestor.parent); ancestor=snapshots.get(ancestor.parent)) depth++;
      }
      const byId = new Map((!recheck && previous ? previous.items : []).map(row => [row.id,row]));
      let cursor = recheck ? '' : previous?.cursor || '';
      let pages = recheck || !previous ? 0 : depth;
      let total = recheck ? null : previous?.total;
      // Older snapshots sent the original text directly. Replay that exact input too.
      let registryTerm = previous ? previous.registry_intervention ?? criteria.intervention ?? '' : null;
      let retrieved;
      const seen = new Set();
      do {
        if (seen.has(cursor)) throw Error('登记库重复返回同一分页标记，请稍后重试。');
        seen.add(cursor);
        const requestCriteria = registryTerm===null ? criteria : {...criteria,intervention:registryTerm};
        const data = await api('/api/discovery/screen',jsonOptions('POST',{
          criteria:requestCriteria,cursor,literal_intervention:registryTerm!==null
        }));
        registryTerm=data.registry_intervention ?? requestCriteria.intervention ?? '';
        for (const row of data.items) byId.set(row.id,{...row,retrieved:data.retrieved});
        total = data.total ?? total;
        retrieved = data.retrieved;
        cursor = data.cursor;
        pages++;
      } while (recheck && cursor && pages < depth);
      const next={id:crypto.randomUUID(),criteria,screening_origin:previous ? previous.screening_origin : startingOrigin,registry_intervention:registryTerm,created:retrieved,total,cursor,pages,
        items:[...byId.values()],parent:previous?.id || null,recheckedFrom:recheck ? previous.id : null};
      localStorage.setItem(prefix+next.id,JSON.stringify(next));
      startingOrigin=null; $('#screen-start',root).replaceChildren();
      snapshot=next;window.history.replaceState(null,'','#/discovery?snapshot='+encodeURIComponent(next.id));paint();status.textContent='候选集已保存在本浏览器。';
    } catch (err) {status.textContent='筛选未保存：'+err.message;}
    finally {lock(false);}
  }
  form.onsubmit=e=>{
    e.preventDefault();
    if (!form.elements.condition.value.trim() && !form.elements.intervention.value.trim()) {
      status.textContent='请填写适应症或药物／干预名称。';form.elements.intervention.focus();return;
    }
    retrieve(null);
  };
  select.onchange=()=>{
    startingOrigin=null; $('#screen-start',root).replaceChildren();
    snapshot=history().find(s=>s.id===select.value);
    window.history.replaceState(null,'','#/discovery'+(snapshot?'?snapshot='+encodeURIComponent(snapshot.id):''));
    paint();status.textContent='';
  };
  results.addEventListener('click',async event=>{
    const button=event.target.closest('[data-watch-trial]');
    if (!button || busy) return;
    const id=button.dataset.watchTrial;
    const origin={snapshot:snapshot.id,condition:opportunityTopic(snapshot.criteria),created:snapshot.created};
    lock(true);
    status.textContent='正在核对登记编号和里程碑日期…';
    try {
      const item=await lookupWatchedTrial(id);
      await followTrial(item,new Date().toISOString(),origin);
      if (root.isConnected) nav('watch');
    } catch (err) {status.textContent='关注未保存：'+err.message;}
    finally {lock(false);}
  });
  results.onsubmit=async e=>{
    e.preventDefault();
    const candidate=e.target.closest('form[data-candidate]');
    if (!candidate || busy) return;
    const entry=snapshot.items[Number(candidate.dataset.candidate)];
    const values=Object.fromEntries(new FormData(candidate));
    lock(true);
    try {
      const res=await api('/api/discovery/projects',jsonOptions('POST',{...values,snapshot_id:snapshot.id,screening_origin:snapshot.screening_origin,
        criteria:snapshot.criteria,registry_intervention:snapshot.registry_intervention ?? snapshot.criteria.intervention,nct:entry.id,trial_title:entry.title,has_results:entry.has_results ?? null,results_posted:entry.results_posted || '',countries:entry.countries || [],intervention_aliases:entry.intervention_aliases || [],sponsor:entry.sponsor,
        phase:entry.phases.join(' / '),updated:entry.updated,retrieved:entry.retrieved}));
      if (!root.isConnected) return;
      window.history.pushState(null,'','#/overview?project='+encodeURIComponent(res.project.project_id));
      location.reload();
    } catch (err) {status.textContent='项目未打开：'+err.message;}
    finally {lock(false);}
  };
  paint();
  if (trigger && !requested) {
    lock(true);
    status.textContent='正在核对发起筛选的公开归档…';
    api('/api/public-events?event_id='+encodeURIComponent(trigger)).then(data=>{
      if (!root.isConnected) return;
      const row=data.items[0];
      if (!row || row.id!==trigger || row.source!=='ClinicalTrials.gov' || !row.asset) throw Error('找不到指定的试验归档。');
      startingOrigin={event_id:row.id,trial:row.record,asset:row.asset,field:row.field,disclosed:row.source_date};
      form.elements.intervention.value=row.asset;
      $('#screen-start',root).innerHTML=screeningOriginHtml(startingOrigin);
      status.textContent='可调整药物或登记条件后执行筛选。';
    }).catch(err=>{if (root.isConnected) status.textContent=err.message;})
      .finally(()=>{if (root.isConnected) lock(false);});
  }
  if (requested && !snapshot) status.textContent='当前浏览器找不到此候选集快照。';
}
