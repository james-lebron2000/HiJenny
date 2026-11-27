const fdaDocumentTypes = {Label:'药品标签',Letter:'批准函',Review:'审评资料','Summary Review':'审评摘要','Medication Guide':'用药指南'};

function publicScheduleStatus(schedule) {
  const p=document.createElement('p');
  p.id='public-schedule';
  const history=document.createElement('a');
  history.href='https://github.com/james-lebron2000/HiJenny/actions/workflows/public-events.yml?query=event%3Aschedule';
  history.target='_blank';history.rel='noopener noreferrer';history.textContent='定时运行历史';
  const prefix='GitHub 每日计划采集 · 台北 08:17 · ';
  if (!schedule) p.textContent=prefix+'等候服务器核对。';
  else if (schedule.state==='found') {
    const labels={success:'成功',failure:'失败',cancelled:'已取消',in_progress:'运行中',queued:'排队中',timed_out:'超时'};
    const link=document.createElement('a');
    const state=schedule.conclusion || schedule.status;
    link.textContent='最近定时运行 '+fmtDate(schedule.created_utc)+' · '+(labels[state] || state);
    link.href='https://github.com/james-lebron2000/HiJenny/actions/runs/'+encodeURIComponent(schedule.run_id);
    link.target='_blank';link.rel='noopener noreferrer';
    p.append(prefix,link);
  } else p.textContent=prefix+(schedule.state==='no_runs'?'运行历史中尚无定时触发记录。':'暂时无法读取定时运行历史。');
  if (schedule) p.append(' · 核对 '+fmtDate(schedule.checked_utc));
  p.append(' · ',history);
  return p;
}

const fdaUsePolicy = ['https://www.fda.gov/about-fda/about-website/website-policies',
  'FDA 网站自有内容通常可复用，另有版权标注的材料须逐件核对。'];
const publicUsePolicies = {
  'Drugs@FDA':fdaUsePolicy, 'FDA Orange Book':fdaUsePolicy,
  'ClinicalTrials.gov':['https://clinicaltrials.gov/terms-conditions','发布登记数据应署名、保持更新、标明来源处理日期，并说明对数据的修改。部分材料涉及第三方权利。'],
  PubMed:['https://pubmed.ncbi.nlm.nih.gov/disclaimer/','摘要和原文依权利人条件复用；请逐篇检查版权与许可。'],
  EMA:['https://www.ema.europa.eu/en/about-us/about-website/legal-notice','EMA 自有材料可用于商业或非商业用途，每份副本均须注明 EMA。第三方材料另行取得权利人许可。'],
  jRCT:['https://jrct.mhlw.go.jp/agreement','采用 PDL 1.0 并设例外；全面收集须申请许可。站方要求避免自动巡回采集，HiJenny 仅保留历史归档供人工核对。']
};

function publicCoverage(receipt, timing) {
  // Region refers to the source institution, independent of study sites and patient location.
  const sources = {
    nmpa_index: ['中国','监管','NMPA'],
    nmpa: ['中国','监管','NMPA'],
    trials: ['美国','临床试验','ClinicalTrials.gov'],
    publications: ['美国','文献','PubMed'],
    orange_book: ['美国','药品专利','FDA Orange Book'],
    orange_book_uses: ['美国','药品专利','FDA Orange Book'],
    fda: ['美国','监管','Drugs@FDA'],
    jrct: ['日本','临床试验','jRCT'],
    ema: ['欧盟','监管','EMA']
  };
  const table = document.createElement('table');
  table.createCaption().textContent='公开关注名单的来源覆盖';
  const header=table.createTHead().insertRow();
  const kinds=['临床试验','文献','药品专利','监管'];
  for (const label of ['来源所在地区',...kinds]) {
    const th=document.createElement('th');
    th.scope='col'; th.textContent=label; header.append(th);
  }
  const body=table.createTBody();
  for (const region of ['中国','美国','日本','欧盟']) {
    const row=body.insertRow();
    const th=document.createElement('th');
    th.scope='row'; th.textContent=region; row.append(th);
    for (const kind of kinds) {
      const cell=row.insertCell();
      const checks=receipt.checks.filter(r=>sources[r.source]?.[0]===region && sources[r.source][1]===kind);
      if (!checks.length) {cell.textContent='本轮无核对目标';continue;}
      const name=sources[checks[0].source][2];
      const ok=checks.filter(r=>r.status==='ok');
      const dates=checks.map(r=>r.last_success_utc || (r.status==='ok'?receipt.finished_utc:null)).filter(Boolean).sort();
      cell.append(name,document.createElement('br'),'核对成功 '+ok.length+' / '+checks.length);
      if (dates.length) cell.append(document.createElement('br'),'最近成功 '+fmtDate(dates.at(-1)));
      const sample=timing?.[name];
      if (sample) {
        const link=document.createElement('a');
        link.href='#/public-events?'+new URLSearchParams({event:sample.event_id});
        link.textContent='来源日期 '+sample.source_date+' · 收录 '+sample.observed_day+
          (sample.days>=0?' · 相隔 '+sample.days+' 天':' · 日期顺序待核');
        cell.append(document.createElement('br'),link);
      }
      const details=document.createElement('details');
      const summary=document.createElement('summary');
      summary.textContent='查看 '+checks.length+' 项核对明细';
      details.append(summary);
      const policy=publicUsePolicies[name];
      const terms=document.createElement('p');
      terms.textContent=policy?policy[1]+' ':'该来源的复用条件待核。';
      if (policy) {
        const link=document.createElement('a');
        link.href=policy[0]; link.target='_blank'; link.rel='noopener noreferrer';
        link.textContent='官方使用说明';
        terms.append(link);
      }
      details.append(terms);
      const list=document.createElement('ul');
      for (const check of checks) {
        const item=document.createElement('li');
        const section={orange_book:'专利与独占期',orange_book_uses:'专利用途释义',nmpa_index:'公告目录'}[check.source];
        item.append([check.subject,check.target,section].filter(Boolean).join(' · '));
        if (check.status==='ok') item.append(' · 命中 '+check.records+' 条来源记录');
        else if (check.status==='manual_review') {
          item.append(' · 待人工复核');
          if (check.source==='jrct' && /^jRCT[0-9]{10}$/.test(check.target)) {
            const link=document.createElement('a');
            link.href='https://jrct.mhlw.go.jp/en-latest-detail/'+check.target;
            link.textContent='打开 jRCT 原登记'; link.target='_blank'; link.rel='noopener noreferrer';
            item.append(' · ',link);
          }
        }
        else item.append(' · 核对失败'+(check.error?' · '+check.error:''));
        const last=check.last_success_utc || (check.status==='ok'?receipt.finished_utc:null);
        item.append(document.createElement('br'),last?'最近成功 '+fmtDate(last):'尚无成功记录');
        if (check.status==='ok' && check.records>0) {
          const params=new URLSearchParams({source:name});
          if (['fda','orange_book','orange_book_uses'].includes(check.source)) params.set('application',check.target);
          if (['publications','trials','jrct'].includes(check.source)) params.set('trial',check.target);
          if (check.source==='nmpa') params.set('notice_id',check.target);
          if (check.source==='ema') params.set('source_record',check.target);
          const link=document.createElement('a');
          link.textContent=check.source==='nmpa_index'?'查看该来源归档':'查看样本 '+check.target;
          link.href='#/public-events?'+params;
          item.append(document.createElement('br'),link);
        }
        list.append(item);
      }
      details.append(list);
      cell.append(details);
    }
  }
  return table;
}

function recentPublicWeek(params, now=new Date()) {
  const day=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit'});
  params.delete('as_of');
  params.set('discovered_from',day.format(now.getTime()-6*86400000));
  params.set('discovered_to',day.format(now));
}

function bindSavedPublicFilters(onApply) {
  const panel=$('#public-saved-filters'), form=panel.querySelector('form');
  const list=panel.querySelector('ul'), message=panel.querySelector('[role=status]');
  const prefix='hijenny.public-filter.';
  let applied=null, generation=0;
  const refresh=panel.querySelector('[data-refresh-counts]');
  function draw() {
    const version=++generation, pending=[];
    list.replaceChildren();
    try {
      for (const key of Object.keys(localStorage).filter(k=>k.startsWith(prefix)).sort()) {
        const entry=JSON.parse(localStorage.getItem(key));
        const li=document.createElement('li'), open=document.createElement('button'), remove=document.createElement('button');
        open.type=remove.type='button'; open.className=remove.className='btn';
        open.textContent=entry.name+(entry.week?' · 最近七天':'');
        open.onclick=()=>{
          const params=new URLSearchParams(entry.query);
          if (entry.week) recentPublicWeek(params);
          onApply(params);
        };
        remove.textContent='移除'; remove.setAttribute('aria-label','移除 '+entry.name);
        remove.onclick=()=>{
          try {localStorage.removeItem(key); draw(); message.textContent='已移除 '+entry.name;}
          catch {message.textContent='本浏览器无法移除此筛选。';}
        };
        const mark=document.createElement('button');
        mark.type='button'; mark.className='btn'; mark.textContent=entry.checkpoint?'更新核对时间':'记录核对起点';
        mark.onclick=async()=>{
          mark.disabled=true;
          try {
            const {receipt}=await api('/api/public-events/collection');
            if (!receipt?.finished_utc) throw new Error('尚无采集回执');
            const current=JSON.parse(localStorage.getItem(key));
            if (!current || current.query!==entry.query) return;
            current.checkpoint=receipt.finished_utc;
            localStorage.setItem(key,JSON.stringify(current)); draw();
            message.textContent='已按最新入库回执记录核对时间：'+fmtDate(current.checkpoint);
          } catch {message.textContent='无法记录核对时间，请稍后重试。';}
          finally {mark.disabled=false;}
        };
        li.append(open);
        if (entry.checkpoint) {
          const updates=document.createElement('button');
          updates.type='button'; updates.className='btn'; updates.textContent='查看核对后新增';
          function updateQuery() {
            const params=new URLSearchParams(entry.query);
            if (entry.week) recentPublicWeek(params);
            params.set('observed_after',entry.checkpoint);
            return params;
          }
          updates.onclick=()=>onApply(updateQuery());
          pending.push({button:updates,query:updateQuery()});
          li.append(updates,' 核对起点 '+fmtDate(entry.checkpoint)+' ');
        }
        li.append(mark,remove); list.append(li);
      }
      refresh.hidden=!pending.length;
      if (panel.open) showCounts();
      async function showCounts() {
        for (const {button,query} of pending) {
          if (generation!==version || !panel.isConnected || !panel.open) return;
          if (query.has('event')) {
            query.set('event_id',query.get('event')); query.delete('event');
          }
          try {
            const {total}=await api('/api/public-events/count?'+query);
            button.textContent='查看核对后新增 · '+total+' 条';
          } catch {
            button.textContent='查看核对后新增 · 数量暂不可用';
          }
        }
      }
    } catch {message.textContent='本浏览器暂时无法读取已存筛选。';}
  }
  panel.ontoggle=()=>{if (panel.open) draw();};
  refresh.onclick=draw;
  form.onsubmit=event=>{
    event.preventDefault();
    const name=form.elements.label.value.trim();
    if (!name || !applied) return;
    try {
      const key=prefix+encodeURIComponent(name);
      if (localStorage.getItem(key)!==null) {message.textContent='此名称已使用，请更换名称。';return;}
      const saved=new URLSearchParams(applied); saved.delete('observed_after');
      localStorage.setItem(key,JSON.stringify({name,query:saved.toString(),week:form.elements.week.checked}));
      form.reset(); draw(); message.textContent='已保存 '+name;
    } catch {message.textContent='本浏览器无法保存筛选。';}
  };
  draw();
  return params=>{
    applied=params;
    form.querySelector('button').disabled=!params;
  };
}

function bindArchivedComparison(panel) {
  const form=panel.querySelector('form'), output=panel.querySelector('[data-comparison]');
  const today=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Taipei'}).format(new Date());
  form.elements.cutoff.value=hashParams().get('as_of') || today;
  form.elements.cutoff.max=today;
  let generation=0;
  form.oninput=()=>{generation++; output.replaceChildren();};
  form.onsubmit=async event=>{
    event.preventDefault();
    const request=++generation, cutoff=form.elements.cutoff.value;
    const ids=[form.elements.first.value,form.elements.second.value].map(id=>id.toUpperCase());
    if (ids[0]===ids[1]) {output.textContent='请选择两个不同的试验。';return;}
    const queries=ids.map(id=>new URLSearchParams({source:'ClinicalTrials.gov',source_record:id,as_of:cutoff}));
    output.textContent='正在读取两项试验的归档…';
    try {
      const results=await Promise.all(queries.map(q=>api('/api/public-events?limit=100&'+q)));
      if (request!==generation || !panel.isConnected) return;
      const measures=results.map(result=>result.items.find(r=>r.category==='primary-outcomes')?.value?.primary_outcomes || []);
      const fields=['登记状态','登记研究阶段','登记研究疾病','登记年龄组','牵头申办方','试验中心所在国家或地区','方案登记主要终点'];
      const design=fields.map(field=>{
        const values=results.map(result=>result.items.find(r=>r.field===field)?.value);
        return [field,...values,values.some(v=>!v)?'资料不足':values[0]===values[1]?'原报文字相同':'原报文字不同'];
      });
      output.innerHTML='<p>归档截至 '+esc(cutoff)+'（台北）。逐项核对原报口径后，再判断结果能否比较。</p>'+
        ids.map((id,i)=>`<p><a href="#/public-events?${esc(queries[i].toString())}">${esc(id)} · ${esc(results[i].items[0]?.asset || '无归档')} · 查看当时记录</a></p>`+
          (measures[i].length?`<label>${esc(id)} 的主要终点<select data-side="${i}">${measures[i].map((m,n)=>`<option value="${n}">${esc(m.title)}</option>`).join('')}</select></label>`:
          '<p>截至所选日期，尚无该试验的主要终点结果归档。</p>')).join('')+'<h3>登记方案对照</h3>'+registryTable(['登记字段',...ids,'口径核对'],design)+'<h3>已公布主要终点对照</h3><div data-dimensions></div>';
      function draw() {
        const selected=measures.map((all,i)=>all[Number(output.querySelector(`[data-side="${i}"]`)?.value || 0)]);
        const dimensions=[['终点名称',m=>m.title],['分析人群',m=>m.populationDescription],
          ['报告组',m=>(m.groups || []).map(g=>g.title).join(' / ')],['评价时间',m=>m.timeFrame],
          ['统计量',m=>m.paramType],['计量单位',m=>m.unitOfMeasure],
          ['分析分母',m=>measureRows(m).denoms.map(row=>row.join(' · ')).join(' / ')]];
        const rows=dimensions.map(([label,read])=>{
          const values=selected.map(m=>m?read(m):null);
          return [label,...values,values.some(v=>!v)?'资料不足':values[0]===values[1]?'原报文字相同':'原报文字不同'];
        });
        output.querySelector('[data-dimensions]').innerHTML=registryTable(['核对项',...ids,'口径核对'],rows)+
          selected.map((m,i)=>m?`<details><summary>${esc(ids[i])} · 查看各组原报结果与统计分析</summary>
            ${registryTable(outcomeHeaders,measureRows(m).rows)}
            ${m.analyses===undefined?'<p>此版归档尚未采集统计分析。</p>':registryTable(analysisHeaders,analysisRows(m))}</details>`:'').join('');
        const canDraft=results.every(result=>result.items.length);
        const target=output.querySelector('[data-dimensions]');
        if (canDraft) {
          const draft=document.createElement('button');
          draft.type='button'; draft.className='btn primary'; draft.textContent='用这两项历史快照起草研究设计';
          draft.onclick=()=>{
            clinicalSeed=results.map((result,i)=>{
              const records=result.items, lines=['HiJenny 归档截止日（台北） '+cutoff];
              const cite=r=>'来源更新 '+r.source_date+' · 首次观察 '+r.observed+' · 归档编号 '+r.id;
              for (const field of fields) {
                const r=records.find(entry=>entry.field===field);
                lines.push(field+' · '+(r?registryValue(r.value)+'（'+cite(r)+'）':'截至当日未收录'));
              }
              const m=selected[i];
              if (m) {
                lines.push('所选公布终点 · '+m.title, cite(records.find(r=>r.category==='primary-outcomes')));
                for (const row of rows.slice(1)) lines.push(row[0]+' · '+registryValue(row[i+1]));
                lines.push(registryTableText(outcomeHeaders,measureRows(m).rows));
                lines.push(m.analyses?.length?registryTableText(analysisHeaders,analysisRows(m)):
                  m.analyses===undefined?'此版归档尚未采集统计分析。':'本终点未列登记统计分析。');
              } else lines.push('截至当日未收录已公布主要终点结果。');
              const latest=records.map(r=>r.source_date).filter(Boolean).sort().pop();
              const excerpts=records.filter(r=>fields.includes(r.field) && r.value).map(r=>({label:r.field,text:String(r.value),event:r.id}));
              return {id:ids[i],title:records[0].asset || ids[i],date:latest || '',archive_cutoff:cutoff,excerpts,snapshot:lines.join('\n')};
            });
            nav('clinical');
          };
          target.append(draft);
        }
      }
      output.querySelectorAll('select').forEach(select=>{select.onchange=draw;}); draw();
    } catch (err) {if (request===generation) output.textContent='归档读取失败，可重新提交。'+err.message;}
  };
}

const archiveTrialSelection={cutoff:null,ids:new Set()};

async function showAssetEvidence(panel, asset, cutoff) {
  const params=new URLSearchParams({asset});
  if (cutoff) params.set('as_of',cutoff);
  panel.dataset.query=params.toString();
  const current=()=>panel.isConnected && panel.dataset.query===params.toString();
  panel.hidden=!asset;
  if (!asset) return;
  panel.textContent='正在核对资产证据…';
  try {
    const data=await api('/api/public-events/asset-evidence?'+params);
    if (!current()) return;
    if (archiveTrialSelection.cutoff!==data.as_of) {
      archiveTrialSelection.cutoff=data.as_of;
      archiveTrialSelection.ids.clear();
    }
    const picked=archiveTrialSelection.ids;
    const trialLink=r=>`<a href="#/public-events?event=${encodeURIComponent(r.id)}&amp;as_of=${esc(data.as_of)}">${esc(r.record)}</a>`;
    const section=(label,rows)=>`<h3>${label}（${rows.length}）</h3>`+
      (rows.length?'<ul>'+rows.map(row=>'<li>'+row+'</li>').join('')+'</ul>':'<p>此范围内暂无归档。</p>');
    panel.innerHTML=`<h2>${esc(asset)} · 资产证据</h2><p>按药名及台北截止日 ${esc(data.as_of)} 汇总。试验范围为公开关注名单。</p>`+
      '<form data-trial-filters><fieldset><legend>筛选试验与结果</legend></fieldset></form><div data-trial-matches></div><div data-picked-trials></div>'+
      section('指南与技术评估',data.guidance.map(g=>`<a href="${esc(g.url)}" target="_blank" rel="noopener noreferrer">${esc(g.publisher)} ${esc(g.reference)}</a> · ${esc(g.type)} · ${esc(g.topic)}
        <br>${esc(g.region)} · 来源页面标注版本日期 ${esc(g.version_date)} · 人工收录 ${esc(g.observed)}`));
    const filters=panel.querySelector('fieldset'), matches=panel.querySelector('[data-trial-matches]');
    const facets={phase:'研究阶段',sponsor:'牵头申办方',countries:'中心所在国家或地区',ages:'登记年龄组'};
    const values=(r,key)=>key==='sponsor'?[r.sponsor].filter(Boolean):(r[key] || '').split(' / ').filter(Boolean);
    for (const [key,label] of Object.entries(facets)) {
      const choices=[...new Set(data.trials.flatMap(r=>values(r,key)))].sort();
      filters.insertAdjacentHTML('beforeend',`<label>${label}<select name="${key}"><option value="">全部</option>${choices.map(v=>option(v,'',v)).join('')}</select></label>`);
    }
    filters.insertAdjacentHTML('beforeend','<label>疾病关键词<input name="disease" type="search" placeholder="按登记原文"></label><label>主要终点关键词<input name="endpoint" type="search" placeholder="匹配方案或已公布结果"></label><button type="reset" class="btn">清除试验筛选</button>');
    const resultsByTrial=new Map(data.outcomes.map(r=>[r.record,r.endpoints]));
    function draw() {
      const selected=filters.form.elements;
      const disease=selected.disease.value.trim().toLowerCase(), endpoint=selected.endpoint.value.trim().toLowerCase();
      const trials=data.trials.filter(r=>Object.keys(facets).every(key=>!selected[key].value || values(r,key).includes(selected[key].value)) &&
        (r.conditions || '').toLowerCase().includes(disease) &&
        [r.planned_endpoints || '',...(resultsByTrial.get(r.record) || [])].some(v=>v.toLowerCase().includes(endpoint)));
      const ids=new Set(trials.map(r=>r.record));
      const outcomes=data.outcomes.filter(r=>ids.has(r.record));
      matches.innerHTML=section('管线试验',trials.map(r=>{
        const params=new URLSearchParams({source:'ClinicalTrials.gov',source_record:r.record,as_of:data.as_of});
        return `<label><input type="checkbox" data-compare-id="${esc(r.record)}" aria-label="选择比较 ${esc(r.record)}">选择比较</label>`+trialLink(r)+' · '+esc(r.asset)+' · '+esc(r.status)+' · '+esc(r.phase || '阶段未收录')+' · '+esc(r.conditions || '疾病未收录')+
          '<br>牵头申办方 · '+esc(r.sponsor || '未收录')+' · 年龄组 · '+esc(r.ages || '未收录')+
          `<details><summary>中心地区及方案登记终点</summary><p>${esc(r.countries || '地区未收录')}</p><p style="white-space:pre-line">${esc(r.planned_endpoints || '终点未收录')}</p>
          <a href="#/public-events?${esc(params.toString())}">核对这项试验当日的登记字段</a></details>`;
      }))+section('已公布主要终点的试验',outcomes.map(r=>trialLink(r)+' · '+esc(r.endpoints.join('、'))));
      showSelection();
    }
    const selection=panel.querySelector('[data-picked-trials]');
    function showSelection() {
      for (const box of matches.querySelectorAll('[data-compare-id]')) {
        box.checked=picked.has(box.dataset.compareId);
        box.disabled=!box.checked && picked.size===2;
      }
      selection.innerHTML='<p>已选 '+picked.size+' / 2 项 · '+esc(data.as_of)+'</p>'+
        [...picked].map(id=>`<button class="btn" type="button" data-remove="${esc(id)}">移除 ${esc(id)}</button>`).join('')+
        `<button type="button" class="btn" data-open-comparison ${picked.size===2?'':'disabled'}>比较所选试验</button>`;
    }
    matches.onchange=e=>{
      const id=e.target.dataset.compareId;
      if (!id) return;
      if (e.target.checked && picked.size<2) picked.add(id);
      else picked.delete(id);
      showSelection();
    };
    selection.onclick=e=>{
      if (e.target.dataset.remove) {picked.delete(e.target.dataset.remove);showSelection();return;}
      if (!e.target.hasAttribute('data-open-comparison') || picked.size!==2) return;
      const comparison=$('#archived-comparison'), form=comparison.querySelector('form');
      [form.elements.first.value,form.elements.second.value]=[...picked];
      form.elements.cutoff.value=data.as_of;
      comparison.open=true;
      comparison.querySelector('summary').focus();
      comparison.scrollIntoView({block:'start'});
      form.requestSubmit();
    };
    filters.form.oninput=draw;
    filters.form.onreset=()=>setTimeout(draw,0);
    filters.form.onsubmit=e=>e.preventDefault();
    draw();
  } catch (err) {if (current()) panel.textContent='资产证据暂时无法读取，请重新筛选。'+err.message;}
}

function appendPublicCitation(card, event) {
  const link=document.createElement('a');
  link.href=location.pathname+'#/public-events?'+new URLSearchParams({event:event.id});
  link.textContent='此条归档的固定链接';
  // Capture only the archived heading and facts, before any live lookup controls are attached.
  const facts=Array.from(card.querySelectorAll('h2,p'),node=>node.textContent);
  const citation=[...facts,'归档时间 '+event.observed,event.source+' · '+event.record,event.url,'HiJenny 归档 '+link.href].join('\n');
  const actions=document.createElement('p'), copy=document.createElement('button');
  const status=document.createElement('span');
  status.setAttribute('role','status');
  copy.type='button'; copy.className='btn'; copy.textContent='复制带出处的引用';
  copy.onclick=async()=>{
    try {
      await navigator.clipboard.writeText(citation);
      status.textContent='已复制引用和原文链接';
    } catch {
      let field=actions.querySelector('textarea');
      if (!field) {
        field=document.createElement('textarea'); field.readOnly=true;
        field.setAttribute('aria-label','单条归档引用'); field.rows=5; field.value=citation;
        actions.append(field);
      }
      status.textContent='请选择并复制下方引用';
      field.focus(); field.select();
    }
  };
  actions.append(link,' · ',copy,' ',status);
  card.append(actions);
}

function renderPublicEvents() {
  setView(`<section class="research-home"><h1>公开资产动态</h1>
    <p>跟踪公开演示名单中的公司申报、中国 NMPA 公告、美国 FDA 批准记录、欧盟 EMA 人用药集中程序、日本 jRCT 登记、临床登记及 PubMed 登记号关联。公司申报按公司归档，已核实的资产事件列出对应药物。</p>
    ${hashParams().get('event')?'<p>正在查看关注列表保存的来源记录。<a href="#/public-events">查看全部动态</a></p>':''}
    <details class="card" id="public-collection"><summary>最近一次公开采集</summary><div aria-live="polite">正在读取核对记录…</div></details>
    <details class="card" id="public-coverage"><summary>四地区数据覆盖与缺口</summary>
    <p>按来源机构所在地区列出本轮核对的目标。跨国试验和文献可涉及多个地区。成功数按关注目标计算；最近成功时间取该格内最近一次成功核对。日期样本取该来源归档中最新的完整来源日期，与首次收录该日期记录的台北日历日比较。历史补录也计入，间隔仅描述该样本。</p>
    <div style="overflow-x:auto" aria-live="polite">正在读取覆盖范围…</div></details>
    <details class="card" id="archived-comparison"><summary>比较两项试验的登记方案和已归档主要终点</summary>
    <form><datalist id="archive-trial-ids"></datalist>
    <label>第一项试验<input name="first" required list="archive-trial-ids" pattern="[Nn][Cc][Tt][0-9]{8}" placeholder="NCT 编号"></label>
    <label>第二项试验<input name="second" required list="archive-trial-ids" pattern="[Nn][Cc][Tt][0-9]{8}" placeholder="NCT 编号"></label>
    <label>归档截止日期（台北）<input name="cutoff" type="date" required></label>
    <button class="btn">对照归档终点</button></form><div aria-live="polite" data-comparison></div></details>
    <form id="public-event-filters" class="card">
    <label>资产或公司<input name="asset" placeholder="依达方、奥希替尼或 Summit"></label>
    <label>国家药监局公告编号<input name="notice_id" inputmode="numeric" pattern="[0-9]+" placeholder="1073516"></label>
    <label>国家药监局栏目标题关键词<input name="notice_title" placeholder="例如 Good Clinical Practice"></label>
    <label>当前归档含 FDA 文件<select name="document_type"><option value="">全部</option>${Object.entries(fdaDocumentTypes).map(([value,label])=>option(value,'',label)).join('')}</select></label>
    <label>FDA 申请号<input name="application" placeholder="NDA208065" title="NDA、ANDA 或 BLA 加六位数字"></label>
    <label>试验登记号<input name="trial" placeholder="NCT 或 jRCT 编号" pattern="[Nn][Cc][Tt][0-9]{8}|[Jj][Rr][Cc][Tt][0-9]{10}" title="NCT 加八位数字或 jRCT 加十位数字"></label>
    <label>历史截面（台北日期）<input type="date" name="as_of"><small>查看截至当天已披露且已收录的每个字段的最后记录。留空可看全部变化。</small></label>
    <label>数据来源<select name="source"><option value="">全部来源</option><option>NMPA</option><option>Drugs@FDA</option><option>EMA</option><option>FDA Orange Book</option><option>jRCT</option><option>ClinicalTrials.gov</option><option>PubMed</option><option>SEC</option></select></label>
    <label>来源记录编号（完整）<input name="source_record" placeholder="例如 BLA761234/ORIG/1"></label>
    <label>地区<input name="region" placeholder="例如：中东"></label>
    <label>来源披露起始日期<input type="date" name="since"></label>
    <label>来源披露结束日期<input type="date" name="disclosed_to"></label>
    <label>首次发现起始日（台北）<input type="date" name="discovered_from"></label>
    <label>首次发现结束日（台北）<input type="date" name="discovered_to"></label>
    <div id="public-observed-after" hidden><label>首次观察晚于<input name="observed_after" readonly></label><button type="button" class="btn" data-clear>取消核对起点</button></div>
    <label>记录类型<select name="kind"><option value="">全部</option><option value="change">字段变化</option>
    <option value="reviewed">原文核实</option><option value="baseline">首次收录</option></select></label>
    <label><input type="checkbox" name="publication_status" value="true"> 仅看论文出版状态</label>
    <label><input type="checkbox" name="study_interruption" value="true"> 仅看试验暂停、终止或撤回说明</label>
    <button class="btn primary">筛选</button><button class="btn" type="button" id="public-event-week">最近七天发现</button></form>
    <p><a href="#/public-events?asset=奥希替尼">查看奥希替尼的管线、结果和指南</a></p>
    <section class="card" id="asset-evidence" aria-live="polite" hidden></section>
    <details class="card" id="public-saved-filters"><summary>我的常用筛选（本浏览器）</summary>
    <p>记录核对起点使用数据库最新采集回执的时间。查看新增沿用原筛选条件；更新核对时间后，从新起点计算。</p>
    <button type="button" class="btn" data-refresh-counts hidden>刷新新增数量</button><ul></ul><form><label>筛选名称<input name="label" required maxlength="100" placeholder="例如：依达方监管动态"></label>
    <label><input type="checkbox" name="week"> 每次打开时查看最近七天发现，替换已选的历史截面和发现日期</label>
    <button class="btn" disabled>保存当前结果的筛选条件</button></form><p role="status"></p></details>
    <p id="public-event-feed" hidden><a>订阅当前筛选（Atom）</a> · <a data-week>订阅滚动七天</a><br><small>滚动订阅每次读取时按台北日期重算最近七天，替换历史截面和发现日期。两种订阅均保留其他条件，最多列出最近 100 条。</small></p>
    <a id="public-event-report" hidden>下载当前筛选的完整简报</a>
    <p id="public-event-status" role="status"></p><nav id="public-source-counts" aria-label="按来源查看归档"></nav><div id="public-event-list"></div>
    <button type="button" class="btn" id="public-event-more" hidden>加载更多</button></section>`);
  const form = $('#public-event-filters'), status = $('#public-event-status');
  const list = $('#public-event-list'), more = $('#public-event-more');
  bindArchivedComparison($('#archived-comparison'));
  const collection=$('#public-collection');
  api('/api/public-events/collection').then(({receipt,verification,source_timing})=>{
    if (!collection.isConnected) return;
    const body=collection.querySelector('div');
    const coverage=$('#public-coverage div');
    const syncStatus=document.createElement('p');
    syncStatus.id='public-verification';
    if (verification?.status==='ok') {
      const same=receipt && verification.collection_finished_utc===receipt.finished_utc &&
        (verification.collection_run || '')===(receipt.run_id || '');
      syncStatus.textContent=(same?'本批次生产 API 对账通过':'上批次生产 API 对账通过，当前回执待核')+
        ' · '+fmtDate(verification.finished_utc)+' · '+verification.events_verified+' 条归档 · '+verification.sources_verified+' 个来源';
    } else if (verification?.status==='failed') {
      syncStatus.textContent='最近一次服务器同步或对账失败 · '+fmtDate(verification.finished_utc)+' · '+verification.error_type;
    } else if (verification?.status==='running') {
      syncStatus.textContent='服务器已开始同步对账 · '+fmtDate(verification.started_utc)+' · 等待完成回执';
    } else {
      syncStatus.textContent='服务器尚无生产 API 对账记录。';
    }
    if (!receipt) {
      body.textContent=coverage.textContent='服务器尚未同步采集回执。';body.prepend(syncStatus,publicScheduleStatus(verification?.scheduled_collection));return;
    }
    coverage.replaceChildren(publicCoverage(receipt,source_timing));
    const checks=receipt.checks, ok=checks.filter(r=>r.status==='ok').length;
    for (const check of checks.filter(r=>r.source==='trials')) {
      $('#archive-trial-ids').append(new Option(check.subject || check.target,check.target));
    }
    collection.querySelector('summary').textContent='最近一次公开采集 · '+fmtDate(receipt.finished_utc)+' · 成功 '+ok+' / '+checks.length;
    const names={nmpa_index:'NMPA 药品栏目目录',nmpa:'国家药监局 NMPA',orange_book_uses:'FDA 专利用途代码释义',orange_book:'FDA Orange Book',jrct:'jRCT',ema:'EMA',sec:'SEC',fda:'Drugs@FDA',trials:'ClinicalTrials.gov',publications:'PubMed'};
    body.innerHTML='<p>公开关注名单全量核对 · 新增 '+esc(receipt.new_events)+' 条归档事件</p>'+
      registryTable(['来源','资产或公司','目标编号','核对结果','最近成功核对','来源记录数','新增事件'],checks.map(r=>[
        names[r.source] || r.source,r.subject || '旧回执未记录',r.target,r.status==='ok'?'成功':r.status==='manual_review'?'待人工复核':'待更新 · '+r.error,
        r.last_success_utc?fmtDate(r.last_success_utc):r.status==='ok'?fmtDate(receipt.finished_utc):'未记录',r.records ?? '待核',r.new_events ?? '待核']));
    body.prepend(syncStatus,publicScheduleStatus(verification?.scheduled_collection));
    if (/^[0-9]+$/.test(receipt.run_id)) {
      const link=document.createElement('a');
      link.href='https://github.com/james-lebron2000/HiJenny/actions/runs/'+receipt.run_id;
      link.textContent=({schedule:'查看定时采集运行',workflow_dispatch:'查看手动采集运行'})[receipt.trigger] || '查看采集运行';
      body.append(link);
    }
  }).catch(()=>{
    if (collection.isConnected) collection.querySelector('div').textContent=$('#public-coverage div').textContent='暂时无法读取采集回执。';
  });
  const initial = hashParams();
  for (const el of form.elements) {
    if (!el.name || !initial.has(el.name)) continue;
    if (el.type==='checkbox') el.checked = initial.get(el.name)==='true';
    else el.value = initial.get(el.name);
  }
  const setSavedQuery=bindSavedPublicFilters(params=>{
    const hash='#/public-events'+(params.size?'?'+params:'');
    if (location.hash!==hash) {location.hash=hash;return;}
    form.reset();
    for (const [key,value] of params) {
      const el=form.elements.namedItem(key);
      if (!el) continue;
      if (el.type==='checkbox') el.checked=value==='true';
      else el.value=value;
    }
    load(true);
  });
  let query, offset, generation = 0;
  function trialTrail(id) {
    const params = new URLSearchParams({trial:id});
    if (query.get('as_of')) params.set('as_of', query.get('as_of'));
    const link = document.createElement('a');
    link.href = '#/public-events?' + params;
    link.textContent = '查看此试验的跨来源记录';
    return link;
  }
  function display(value) {
    if (value == null) return '未披露';
    if (value.safety_summary) return (value.safety_summary.eventGroups || []).length+' 个安全性报告组';
    if (value.primary_outcomes) return value.primary_outcomes.map(m=>m.title).join('、');
    if (typeof value === 'object') return [value.date, {ACTUAL:'实际', ESTIMATED:'预计'}[value.type] || value.type].filter(Boolean).join(' · ');
    return String(value);
  }
  async function load(reset) {
    if (reset) {
      query = new URLSearchParams([...new FormData(form)].filter(([,value])=>value));
      const eventId = hashParams().get('event');
      if (eventId) query.set('event_id', eventId);
      showAssetEvidence($('#asset-evidence'),query.get('asset')?.trim() || '',query.get('as_of'));
      setSavedQuery(null); offset = 0; generation++; list.replaceChildren();
    }
    $('#public-observed-after').hidden=!query.has('observed_after');
    const request = generation;
    query.set('offset', String(offset));
    const counts = $('#public-source-counts');
    if (reset) counts.replaceChildren();
    const download = $('#public-event-report');
    const feed=$('#public-event-feed');
    feed.hidden=true;
    download.hidden = true;
    more.hidden = true; status.textContent = '正在读取…';
    try {
      const result = await api('/api/public-events?' + query);
      if (generation !== request || !form.isConnected) return;
      counts.replaceChildren();
      if (query.has('event_id')) {
        const exit=document.createElement('a');
        exit.href='#/public-events'; exit.textContent='返回全部公开动态';
        counts.append(exit);
      }
      if (result.total) {
        const heading=document.createElement('p');
        heading.textContent='当前筛选的来源分布（编号在各来源内去重）';
        counts.append(heading);
        for (const row of result.source_counts) {
          const button=document.createElement('button');
          button.type='button'; button.className='btn';
          button.textContent=row.source+' · '+row.events+' 条归档 · '+row.records+' 个来源编号';
          button.disabled=query.get('source')===row.source;
          button.onclick=()=>{form.elements.source.value=row.source; form.requestSubmit();};
          counts.append(button);
        }
      }
      if (query.has('source')) {
        const all=document.createElement('button');
        all.type='button'; all.className='btn'; all.textContent='查看全部来源';
        all.onclick=()=>{form.elements.source.value=''; form.requestSubmit();};
        counts.append(all);
      }
      for (const r of result.items) {
        const card = document.createElement('article');
        card.className = 'card';
        const kind = {baseline:'首次收录',change:'字段变化',reviewed:'原文核实'}[r.kind];
        const documentUpdate = r.kind==='change' && r.source==='Drugs@FDA' && r.previous===r.value && r.previous_date===r.source_date;
        card.innerHTML = `<h2>${esc(r.asset || r.subject)} · ${esc(r.field)}</h2>
          <p>${r.label_trials_changed?'标签试验关联更新':documentUpdate?'配套文件更新':esc(kind)} · ${r.kind==='change'&&!documentUpdate?esc(display(r.previous))+' → ':''}${esc(display(r.value))}</p>
          <p>${esc(r.date_label || '来源披露')} ${esc(r.source_date)} · 首次发现 ${esc(fmtDate(r.observed))}</p>
          ${r.effective?'<p>事件发生或生效 '+esc(r.effective)+'</p>':''}
          ${r.region?'<p>地区 '+esc(r.region)+'</p>':''}
          <a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.source)} · ${esc(r.record)}</a>`;
        if (r.source==='FDA Orange Book') {
          card.querySelector('p').style.whiteSpace='pre-line';
          const note=document.createElement('p');
          note.textContent='各行按 FDA 产品编号与用途代码列示。专利和独占期分别归档；这些日期不能单独确定仿制药上市时间。';
          card.append(note);
        }
        if (r.category==='publication-mention') {
          const note=document.createElement('p');
          note.textContent='文献在题名或摘要中提到此登记号；是否报告该试验的结果仍需核对原文。';
          card.append(note);
        }
        appendPublicCitation(card,r);
        if (!query.has('source_record') || !query.has('source') ||
            [...query.keys()].some(key=>!['source_record','source','offset','disclosed_to','discovered_to'].includes(key))) {
          const params=new URLSearchParams({source:r.source,source_record:r.record});
          for (const end of ['disclosed_to','discovered_to']) {
            const dates=[query.get('as_of'),query.get(end)].filter(Boolean).sort();
            if (dates.length) params.set(end,dates[0]);
          }
          const trail=document.createElement('p'), link=document.createElement('a');
          link.href='#/public-events?'+params;
          link.textContent='查看此来源编号的归档历史';
          trail.append(link); card.append(trail);
        }
        if (r.source==='ClinicalTrials.gov' && r.asset) {
          const link=document.createElement('a'), p=document.createElement('p');
          link.href='#/discovery?event='+encodeURIComponent(r.id);
          link.textContent='以此资产筛选候选试验'; p.append(link); card.append(p);
        }
        if (r.asset) {
          const params=new URLSearchParams({asset:r.asset});
          for (const field of ['as_of','since','disclosed_to','discovered_from','discovered_to','observed_after']) {
            if (query.has(field)) params.set(field,query.get(field));
          }
          const atAsset=query.get('asset')===r.asset &&
            [...query.keys()].every(key=>key==='offset' || params.has(key));
          if (!atAsset) {
            const p=document.createElement('p'), link=document.createElement('a');
            link.href='#/public-events?'+params;
            link.textContent='查看该药物的跨来源记录';
            p.append(link); card.append(p);
          }
        }
        if (r.source==='NMPA' && !query.has('notice_id')) {
          const params = new URLSearchParams({notice_id:r.record});
          if (query.has('as_of')) params.set('as_of',query.get('as_of'));
          const link = document.createElement('a');
          link.href = '#/public-events?' + params;
          link.textContent = '查看同一公告的全部归档';
          const p = document.createElement('p');
          p.append(link); card.append(p);
        }
        if (['FDA Orange Book','Drugs@FDA'].includes(r.source)) {
          const application = r.record.split('/')[0];
          if (!query.has('application') || query.has('source')) {
            const params = new URLSearchParams({application});
            if (query.has('as_of')) params.set('as_of', query.get('as_of'));
            const link = document.createElement('a');
            link.href = '#/public-events?' + params;
            link.textContent = '查看同一 FDA 申请的全部归档';
            const p = document.createElement('p');
            p.append(link);
            card.append(p);
          }
        }
        if (r.category==='safety-summary') {
          for (const [label,version] of [['本次归档',r.value],['上次归档',r.previous]]) {
            const safety=version?.safety_summary;
            if (!safety) continue;
            const detail=document.createElement('details');
            detail.innerHTML=`<summary>${label} · 安全性报告</summary>
              <p>报告期 · ${esc(registryValue(safety.timeFrame))}</p>
              <p>其他不良事件报告阈值（%） · ${esc(registryValue(safety.frequencyThreshold))}</p>
              ${safety.description?'<p>'+esc(safety.description)+'</p>':''}
              ${registryTable(safetyHeaders,safetySummary(safety))}`;
            card.append(detail);
          }
        }
        if (r.category==='primary-outcomes') {
          for (const [label, version] of [['本次归档',r.value],['上次归档',r.previous]]) {
            for (const measure of version?.primary_outcomes || []) {
              const {rows,denoms}=measureRows(measure);
              const detail=document.createElement('details');
              detail.innerHTML=`<summary>${label} · ${esc(measure.title)}</summary>
                <p>分析人群 · ${esc(registryValue(measure.populationDescription))}</p>
                <p>评价时间 · ${esc(registryValue(measure.timeFrame))}</p>
                <p>统计量 · ${esc(registryValue(measure.paramType))} · ${esc(registryValue(measure.dispersionType))}</p>
                ${registryTable(denomHeaders,denoms)}${registryTable(outcomeHeaders,rows)}
                <h4>登记统计分析</h4>${measure.analyses===undefined ? '<p>此版归档尚未采集统计分析。</p>'
                  : registryTable(analysisHeaders,analysisRows(measure))}`;
              card.append(detail);
            }
          }
        }
        const fileChanges=result.document_changes?.[r.id] || {};
        if (r.documents?.length || fileChanges.removed?.length) {
          const links = document.createElement('ul');
          const current=(r.documents || []).map(doc=>{
            const added=fileChanges.added?.some(d=>d.type===doc.type && d.url===doc.url);
            return {doc, label:added?'本次新增归档链接 · ':''};
          });
          const removed=(fileChanges.removed || []).map(doc=>({doc,label:'仅上次归档列出 · '}));
          for (const {doc,label} of [...current,...removed]) {
            if (!doc.url.startsWith('https://www.accessdata.fda.gov/drugsatfda_docs/')) continue;
            const item = document.createElement('li');
            const link = document.createElement('a');
            link.href=doc.url; link.target='_blank'; link.rel='noopener noreferrer';
            link.textContent=fdaDocumentTypes[doc.type] || doc.type;
            if (query.get('document_type')===doc.type && !label.startsWith('仅上次')) link.style.fontWeight='bold';
            item.append(label,link); links.append(item);
          }
          card.append(links);
        }
        for (const trial of r.label_trials || []) {
          const p = document.createElement('p');
          const label = r.documents?.find(doc=>doc.type==='Label' && doc.url===trial.label);
          if (!label || !/^NCT[0-9]{8}$/.test(trial.id)) continue;
          p.innerHTML = `标签所列试验 · ${esc(trial.name)} · <a href="https://clinicaltrials.gov/study/${esc(trial.id)}" target="_blank" rel="noopener noreferrer">${esc(trial.id)}</a>
            · <a href="${esc(label.url)}#page=${Number(trial.page)}" target="_blank" rel="noopener noreferrer">${esc(trial.section)} · 第 ${Number(trial.page)} 页</a>`;
          if (!query.has('trial') || query.has('source')) p.append(' · ', trialTrail(trial.id));
          card.append(p);
        }
        const noticeIds = [];
        if (r.category==='publication-status') {
          const notices = new Set(String(r.value).match(/https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/\d+\//g) || []);
          for (const url of notices) {
            const link = document.createElement('a');
            link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
            const pmid = url.split('/').at(-2);
            if (pmid !== r.record) noticeIds.push(pmid);
            link.textContent = '核对关联记录 · PMID '+pmid;
            const paragraph = document.createElement('p');
            paragraph.append(link); card.append(paragraph);
          }
        }
        if (r.source==='ClinicalTrials.gov' && r.field==='结果首次公布' && r.value?.date) {
          const current = document.createElement('section');
          current.innerHTML = `<p>登记结果首次公布于 ${esc(r.value.date)}。下方按需读取登记库当前版本，历史简报保留当时的公布日期。</p>
            <button type="button" class="btn" data-load-results="${esc(r.record)}">核对当前登记终点与安全性</button>
            <section class="trial-posted" aria-live="polite"></section>`;
          card.append(current);
          bindTrialResultButtons(current,{event_id:r.id, first_posted:r.value.date,
            observed:r.observed, as_of:query.get('as_of') || null});
        }
        const trialId = r.source==='ClinicalTrials.gov' ? r.record : r.trial;
        if (/^NCT[0-9]{8}$/.test(trialId)) {
          if (!query.has('trial') || query.has('source')) card.append(trialTrail(trialId));
          const button = document.createElement('button');
          button.className = 'btn'; button.type = 'button'; button.textContent = '核对登记并加入本机关注';
          const feedback = document.createElement('p');
          feedback.setAttribute('role','status');
          button.onclick = async ()=>{
            button.disabled = true;
            feedback.textContent = '正在核对 '+trialId+'…';
            try {
              const item = await lookupWatchedTrial(trialId);
              const value = (r.kind==='change'?display(r.previous)+' → ':'')+display(r.value);
              await followTrial(item,new Date().toISOString(),{type:'public-event',snapshot:r.id,
                field:r.field,value,created:r.observed,disclosed:r.source_date,dateLabel:r.date_label});
              feedback.innerHTML = '已保存这条动态与试验的关联。<a href="#/watch">查看关注列表</a>';
            } catch (err) {
              feedback.textContent = '保存失败：'+err.message;
              button.disabled = false;
            }
          };
          card.append(button,feedback);
        }
        if (r.source==='PubMed' && /^[0-9]+$/.test(r.record) && /^NCT[0-9]{8}$/.test(r.trial)) {
          const pair = document.createElement('button');
          pair.type = 'button'; pair.className = 'btn';
          pair.textContent = r.category==='publication-status' ? '复核出版状态与试验登记' : '核对论文与登记，建立底稿';
          let noticeSelect;
          if (noticeIds.length > 1) {
            const label = document.createElement('label');
            label.textContent = '本次底稿要核对的关联记录';
            noticeSelect = document.createElement('select');
            for (const id of noticeIds) noticeSelect.add(new Option('PMID '+id,id));
            label.append(noticeSelect); card.append(label);
          }
          if (noticeIds.length) pair.textContent = '核对论文、关联通知与试验登记';
          pair.onclick = ()=>{
            const notice = noticeSelect?.value || noticeIds[0];
            briefSeed = {question:r.category==='publication-status' ? 'PMID '+r.record+' 的出版状态是否影响对试验 '+r.trial+' 的判断？' : '论文 PMID '+r.record+' 与试验 '+r.trial+' 的报告内容是否一致？',
              pubmed:[r.record,notice].filter(Boolean).map(id=>id+'[pmid]').join(' OR '), trials:r.trial, publicEvent:r.id, startSearch:true};
            nav('brief');
          };
          card.append(pair);
        }
        list.append(card);
      }
      const reportQuery = new URLSearchParams(query);
      reportQuery.delete('offset');
      download.href = '/api/public-events/report.txt?' + reportQuery;
      feed.querySelector('a').href='/api/public-events/feed.atom'+(reportQuery.size?'?'+reportQuery:'');
      const rollingQuery=new URLSearchParams(reportQuery);
      for (const field of ['as_of','discovered_from','discovered_to']) rollingQuery.delete(field);
      rollingQuery.set('rolling_week','true');
      feed.querySelector('[data-week]').href='/api/public-events/feed.atom?'+rollingQuery;
      feed.hidden=false;
      const savedQuery=new URLSearchParams(reportQuery);
      if (savedQuery.has('event_id')) {
        savedQuery.set('event',savedQuery.get('event_id')); savedQuery.delete('event_id');
      }
      setSavedQuery(savedQuery);
      download.hidden = result.total === 0;
      offset += result.items.length;
      status.textContent = (query.has('as_of')?'截至 '+query.get('as_of')+' 的历史截面 · ':'')+'已显示 '+offset+' / '+result.total+' 条记录';
      if (result.asset_match) {
        const link = document.createElement('a');
        link.href = result.asset_match.source;
        link.target = '_blank'; link.rel = 'noopener noreferrer';
        link.textContent = '名称来源';
        status.append(' · 核对药名 '+result.asset_match.name+' · ', link);
      } else if (query.get('asset')?.trim()) {
        status.append(' · 按资产或主体名称中的关键词匹配，结果可包含组合疗法。');
      }
      more.hidden = offset >= result.total;
    } catch (err) {
      if (generation===request) { status.textContent='读取失败：'+err.message; more.hidden=false; }
    }
  }
  form.onsubmit = event=>{
    event.preventDefault();
    const params = new URLSearchParams([...new FormData(form)].filter(([,value])=>value));
    const eventId = hashParams().get('event');
    if (eventId) params.set('event',eventId);
    history.replaceState(history.state,'','#/public-events'+(params.size?'?'+params:''));
    load(true);
  };
  $('#public-observed-after [data-clear]').onclick=()=>{
    form.elements.observed_after.value=''; form.requestSubmit();
  };
  $('#public-event-week').onclick = ()=>{
    const params=new URLSearchParams();
    recentPublicWeek(params);
    form.elements.as_of.value='';
    form.elements.discovered_from.value=params.get('discovered_from');
    form.elements.discovered_to.value=params.get('discovered_to');
    form.requestSubmit();
  };
  more.onclick = ()=>load(false);
  load(true);
}
