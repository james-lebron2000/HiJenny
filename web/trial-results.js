'use strict';

function registryValue(value) {
  return value === null || value === undefined || value === '' ? '未提供' : String(value);
}
function registryTable(headers, rows) {
  if (!rows.length) return '<p>登记未提供此表的数据行。</p>';
  return `<div class="comparison-scroll" tabindex="0"><table><thead><tr>${headers.map(h=>'<th scope="col">'+esc(h)+'</th>').join('')}</tr></thead>
    <tbody>${rows.map(row=>'<tr>'+row.map(value=>'<td>'+esc(registryValue(value))+'</td>').join('')+'</tr>').join('')}</tbody></table></div>`;
}

function measureRows(measure) {
  const groups = new Map((measure.groups || []).map(g=>[g.id,g.title]));
  const name = id => [id,groups.get(id)].filter(Boolean).join(' · ');
  const rows = [], denoms = [];
  function collectDenoms(section, category) {
    for (const denom of section.denoms || []) for (const count of denom.counts || []) {
      denoms.push([category, name(count.groupId), count.value, denom.units]);
    }
  }
  collectDenoms(measure,'整个终点');
  for (const section of measure.classes || []) {
    const cls=section.title || '';
    collectDenoms(section,cls || '分层');
    for (const category of section.categories || []) {
      const label=[cls,category.title].filter(Boolean).join(' / ') || '总体';
      collectDenoms(category,label);
      for (const obs of category.measurements || []) rows.push([
        label,name(obs.groupId),obs.value,measure.unitOfMeasure,obs.spread,obs.lowerLimit,obs.upperLimit,obs.comment
      ]);
    }
  }
  return {rows,denoms};
}

const outcomeHeaders=['分层或类别','结果组','登记值','单位','离散量','下限','上限','原注'];
const denomHeaders=['适用范围','结果组','分析分母','分母单位'];
const safetyHeaders=['类别','报告组','受影响人数','风险人数'];
function safetySummary(section) {
  const rows=[];
  for (const g of section.eventGroups || []) {
    for (const [label,key] of [['全因死亡','deaths'],['严重不良事件','serious'],['其他不良事件','other']]) {
      if (g[key+'NumAffected']!=null || g[key+'NumAtRisk']!=null)
        rows.push([label,[g.id,g.title].filter(Boolean).join(' · '),g[key+'NumAffected'],g[key+'NumAtRisk']]);
    }
  }
  return rows;
}

function showPostedResults(panel, snapshot) {
  const outcomes=snapshot.outcomes;
  const safety=snapshot.safety;
  const source=sourceLink(snapshot)+'?tab=results';
  const summary=safetySummary(safety);
  panel.innerHTML=`<h3>登记结果 · ${esc(snapshot.id)}</h3>
    <p><a href="${esc(source)}" target="_blank" rel="noopener noreferrer">核对 ClinicalTrials.gov 结果页</a> · 登记更新 ${esc(registryValue(snapshot.updated))}</p>
    ${!snapshot.has_results?'<p>此登记记录当前未发布试验结果。</p>':''}
    ${outcomes.length?`<label>公布的终点（${outcomes.length}）<select data-outcome>${outcomes.map((m,i)=>option(String(i),'0',(m.type || '')+' · '+m.title)).join('')}</select></label>
      <div data-outcome-view></div>` : '<p>无已发布的终点测量表。</p>'}
    ${Object.keys(safety).length?`<details><summary>不良事件报告</summary>
      <p>报告期 · ${esc(registryValue(safety.timeFrame))}；其他不良事件报告阈值（%）· ${esc(registryValue(safety.frequencyThreshold))}</p>
      ${safety.description?'<p>'+esc(safety.description)+'</p>':''}
      ${registryTable(safetyHeaders,summary)}
      <label>事件明细<select data-event-type><option value="seriousEvents">严重不良事件</option><option value="otherEvents">其他不良事件</option></select></label>
      <div data-event-table></div></details>`:''}
    <button type="button" class="btn" data-json>下载本次登记结果快照</button>
    ${outcomes.length?'<button type="button" class="btn" data-brief-result>将当前终点存入研究底稿</button>':''}
    <p data-result-status role="status"></p>`;
  const choose=panel.querySelector('[data-outcome]');
  function active() { return outcomes[Number(choose.value)]; }
  function details(m) {
    return [['登记说明',m.description],['评价时间',m.timeFrame],['分析人群',m.populationDescription],
      ['统计量类型',m.paramType],['离散量或区间类型',m.dispersionType],['报告状态',m.reportingStatus],
      ...(m.groups || []).map(g=>['组别 · '+(g.title || g.id),g.description])];
  }
  if (choose) {
    function renderMeasure() {
      const m=active(), {rows,denoms}=measureRows(m);
      panel.querySelector('[data-outcome-view]').innerHTML=details(m).map(([label,v])=>'<p><strong>'+esc(label)+'</strong> '+esc(registryValue(v))+'</p>').join('')+
        registryTable(denomHeaders,denoms)+registryTable(outcomeHeaders,rows);
    }
    choose.onchange=renderMeasure;renderMeasure();
  }
  const eventType=panel.querySelector('[data-event-type]');
  if (eventType) {
    function events() {
      const groups=new Map((safety.eventGroups || []).map(g=>[g.id,g.title]));
      const rows=(safety[eventType.value] || []).flatMap(e=>(e.stats || []).map(s=>[
        e.term,e.organSystem,[s.groupId,groups.get(s.groupId)].filter(Boolean).join(' · '),s.numEvents,s.numAffected,s.numAtRisk,e.assessmentType
      ]));
      panel.querySelector('[data-event-table]').innerHTML=registryTable(['事件','器官系统','报告组','事件次数','受影响人数','风险人数','评估方式'],rows);
    }
    eventType.onchange=events;
    eventType.closest('details').ontoggle=e=>{if(e.target.open)events();};
  }
  panel.querySelector('[data-json]').onclick=()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify(snapshot,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download=snapshot.id+'-results.json';a.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  const add=panel.querySelector('[data-brief-result]');
  if (add) add.onclick=async ()=>{
    add.disabled=true;
    try {
      const m=active(), {rows,denoms}=measureRows(m);
      const toText=(headers,values)=>[headers,...values].map(row=>row.map(registryValue).join(' | ')).join('\n');
      const item={id:snapshot.id,postedResult:true,title:snapshot.title,date:snapshot.updated || '',detail:'登记结果 · '+m.title,
        url:source,passages:[{label:m.title,text:details(m).map(([k,v])=>k+'：'+registryValue(v)).join('\n')},
          {label:'登记分析分母',text:toText(denomHeaders,denoms)},
          {label:'登记测量值',text:toText(outcomeHeaders,rows)}]};
      const run={id:crypto.randomUUID(),created:new Date().toISOString(),question:m.title,review:{},note:'',
        steps:[{source:'trials',query:snapshot.id,attempts:1,retrieved:snapshot.retrieved,
          result:{total:1,items:[item]}}]};
      await briefStore('readwrite',store=>store.put(run));
      nav('brief');
    } catch(err) {panel.querySelector('[data-result-status]').textContent='底稿保存失败：'+err.message;}
    finally {add.disabled=false;}
  };
}

function bindTrialResultButtons(output) {
  for (const button of output.querySelectorAll('[data-load-results]')) button.onclick=async ()=>{
    const panel=button.nextElementSibling;
    button.disabled=true;panel.textContent='正在读取登记结果…';
    try {
      const snapshot=await api('/api/research/trials/'+encodeURIComponent(button.dataset.loadResults)+'/results');
      if (panel.isConnected) {showPostedResults(panel,snapshot);button.textContent='重新读取登记结果';}
    } catch(err) {panel.textContent=err.message;}
    finally {button.disabled=false;}
  };
}
