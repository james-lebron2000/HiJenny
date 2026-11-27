'use strict';

function registryValue(value) {
  return value === null || value === undefined || value === '' ? '未提供' : String(value);
}
function registryTableText(headers,values) {
  return [headers,...values].map(row=>row.map(registryValue).join(' | ')).join('\n');
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

const analysisHeaders=['登记分析序号','字段','原报内容'];
function analysisRows(measure) {
  const groups=new Map((measure.groups || []).map(g=>[g.id,g.title]));
  const labels={groupIds:'纳入分析的组',paramType:'效应量类型',paramValue:'效应估计值',ciPctValue:'置信水平（%）',
    ciNumSides:'区间侧数',ciLowerLimit:'区间下限',ciUpperLimit:'区间上限',pValue:'P 值',
    statisticalMethod:'检验方法',statisticalComment:'方法说明',nonInferiorityType:'比较类型',
    groupDescription:'组别说明',estimateComment:'估计值说明',pValueComment:'P 值说明'};
  return (measure.analyses || []).flatMap((analysis,index)=>Object.entries(analysis).map(([field,value])=>{
    const text=field==='groupIds' ? value.map(id=>[id,groups.get(id)].filter(Boolean).join(' · ')).join(' / ') : value;
    return [index+1,labels[field] || field,text];
  }));
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
    ${snapshot.origin? `<p>本次读取 ${esc(snapshot.retrieved)} · <a href="#/public-events?event=${encodeURIComponent(snapshot.origin.event_id)}">查看发起核对的公开记录</a></p>` : ''}
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
        registryTable(denomHeaders,denoms)+registryTable(outcomeHeaders,rows)+
        (m.analyses?.length ? '<h4>登记统计分析</h4>'+registryTable(analysisHeaders,analysisRows(m)) : '');
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
      const item={id:snapshot.id,postedResult:true,title:snapshot.title,date:snapshot.updated || '',detail:'登记结果 · '+m.title,
        url:source,passages:[{label:m.title,text:details(m).map(([k,v])=>k+'：'+registryValue(v)).join('\n')},
          {label:'登记分析分母',text:registryTableText(denomHeaders,denoms)},
          {label:'登记测量值',text:registryTableText(outcomeHeaders,rows)}]};
      if (m.analyses?.length) item.passages.push({label:'登记统计分析',text:registryTableText(analysisHeaders,analysisRows(m))});
      const run={id:crypto.randomUUID(),created:new Date().toISOString(),question:m.title,review:{},note:'',
        steps:[{source:'trials',query:snapshot.id,attempts:1,retrieved:snapshot.retrieved,
          result:{total:1,items:[item]}}]};
      if (snapshot.origin) run.publicEvent=snapshot.origin.event_id;
      await briefStore('readwrite',store=>store.put(run));
      nav('brief');
    } catch(err) {panel.querySelector('[data-result-status]').textContent='底稿保存失败：'+err.message;}
    finally {add.disabled=false;}
  };
}

function bindTrialResultButtons(output, origin) {
  for (const button of output.querySelectorAll('[data-load-results]')) button.onclick=async ()=>{
    const panel=button.nextElementSibling;
    button.disabled=true;panel.textContent='正在读取登记结果…';
    try {
      const snapshot=await api('/api/research/trials/'+encodeURIComponent(button.dataset.loadResults)+'/results');
      if (origin) snapshot.origin=origin;
      if (panel.isConnected) {showPostedResults(panel,snapshot);button.textContent='重新读取登记结果';}
    } catch(err) {panel.textContent=err.message;}
    finally {button.disabled=false;}
  };
}

// Outcome indices belong to a specific registry snapshot, even when two titles match.
function comparisonFields(measure) {
  const {rows, denoms}=measureRows(measure);
  return [
    ['登记终点',measure.title],['终点类别',measure.type],['终点定义',measure.description],
    ['评价时间',measure.timeFrame],['分析人群',measure.populationDescription],
    ['统计量',measure.paramType],['单位',measure.unitOfMeasure],['离散量或区间类型',measure.dispersionType],
    ['组别定义',(measure.groups || []).map(g=>[g.id,g.title,g.description].filter(Boolean).join(' · ')).join('\n')],
    ['分析分母',[denomHeaders,...denoms]],['测量结果',[outcomeHeaders,...rows]],
    ['登记统计分析',[analysisHeaders,...analysisRows(measure)]]
  ];
}

function comparePostedResults(panel, records) {
  if (records.length<2 || records.length>4) {panel.textContent='请选择 2 至 4 项试验。';return;}
  panel.innerHTML='<h2>公布终点对照</h2><p>为每项试验选取要讨论的终点，核对定义、人群、时间窗与分母。跨试验数据仅作描述性对照。</p><div data-pickers></div><div data-matrix></div>';
  const controls=panel.querySelector('[data-pickers]'), matrix=panel.querySelector('[data-matrix]');
  const columns=records.map(item=>({item, snapshot:null, measure:null}));
  function draw() {
    const selected=columns.filter(c=>c.measure);
    if (!selected.length) {matrix.replaceChildren();return;}
    const fieldSets=selected.map(c=>[['登记更新',c.snapshot.updated],['检索时间',c.snapshot.retrieved],...comparisonFields(c.measure)]);
    matrix.innerHTML=`<p>已选择终点的试验：${selected.length} / ${records.length}</p>
      <div class="comparison-scroll" tabindex="0"><table><thead><tr><th scope="col">对照项</th>${selected.map(c=>`<th scope="col"><a href="${esc(sourceLink({id:c.item.id,postedResult:true}))}" target="_blank" rel="noopener noreferrer">${esc(c.item.id)}</a></th>`).join('')}</tr></thead>
      <tbody>${fieldSets[0].map(([label],i)=>`<tr><th scope="row">${esc(label)}</th>${fieldSets.map(fields=>{
        const value=fields[i][1];
        const content=Array.isArray(value)?registryTable(value[0],value.slice(1)):esc(registryValue(value)).replaceAll('\n','<br>');
        return '<td>'+content+'</td>';
      }).join('')}</tr>`).join('')}</tbody></table></div>
      ${selected.length===columns.length?'<button class="btn" type="button" data-export-comparison>下载所选终点与来源</button>':''}`;
    const download=matrix.querySelector('[data-export-comparison]');
    if (download) download.onclick=()=>{
      const payload=selected.map(({snapshot,measure, index})=>({id:snapshot.id,title:snapshot.title,
        updated:snapshot.updated,retrieved:snapshot.retrieved,url:sourceLink({id:snapshot.id,postedResult:true}),
        outcome_index:index,outcome:measure}));
      const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
      const a=document.createElement('a');a.href=url;a.download='HiJenny-trial-outcomes.json';a.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
  }
  for (const col of columns) {
    const box=document.createElement('fieldset');
    controls.appendChild(box);
    const heading='<legend>'+esc(col.item.id)+' · '+esc(col.item.title)+'</legend>';
    async function load() {
      box.innerHTML=heading+'<p role="status">正在读取登记结果…</p>';
      try {
        const snapshot=await api('/api/research/trials/'+encodeURIComponent(col.item.id)+'/results');
        if (!box.isConnected) return;
        col.snapshot=snapshot;
        box.innerHTML=heading+'<p>检索时间 '+esc(fmtDate(snapshot.retrieved))+'</p>'+
          (snapshot.outcomes.length?`<label>选择公布的终点<select><option value="">请选择</option>${snapshot.outcomes.map((m,i)=>option(String(i),'',[m.type,m.title].filter(Boolean).join(' · '))).join('')}</select></label>`:
            '<p>'+ (snapshot.has_results?'此记录无已发布的终点测量表。':'此记录尚未公布试验结果。')+'</p>');
        const choice=box.querySelector('select');
        if (choice) choice.onchange=()=>{
          col.index=choice.value===''?null:Number(choice.value);
          col.measure=snapshot.outcomes[col.index] || null;draw();
        };
      } catch(err) {
        box.innerHTML=heading+'<p role="status">'+esc(err.message)+'</p><button class="btn" type="button">重试此试验</button>';
        box.querySelector('button').onclick=load;
      }
    }
    load();
  }
}
