'use strict';

// Study proposals retain the exact source excerpt selected for the meeting.
let studySeed = null;
const studyPrefix = 'hijenny.study.';
const studyCommon = {
  question:'未解决的研究问题', population:'目标人群和入选条件', exposure:'暴露或干预及对照',
  outcome:'结局定义与测量', followup:'随访起点、窗口与终止规则', data:'可用数据、覆盖范围与获取条件',
  confounding:'混杂因素与处理', missing:'缺失数据处理', analysis:'分析方法与敏感性分析',
};
const studySpecific = {
  RWE:{index:'索引日期与暴露分组时点', linkage:'数据关联与结局捕获', selection:'选择偏倚与时间相关偏倚'},
  IIT:{recruitment:'研究中心与招募可行性', allocation:'分组与分配依据', monitoring:'研究监测与实施责任'},
};
const biasFields={risk:'主要偏倚及发生环节',impact:'可能的影响方向与依据',mitigation:'拟采取的控制措施',residual:'剩余不确定性与验证方式'};
const feasibilityItems = ['人群可识别','暴露或干预可测量','结局可捕获','随访窗口可覆盖','关键混杂变量可获得'];
const feasibilityStates = ['待核实','可用','部分可用','不可用'];
function studySummary(v) {
  const fields = {...studyCommon,...studySpecific[v.kind]};
  return [v.kind === 'IIT' ? '研究者发起研究概念书' : '真实世界研究方案',
    '团队评审草稿 · '+v.created,'版本 '+v.id,'前一版本 '+(v.parent || '首版'),
    ...Object.entries(fields).flatMap(([k,label])=>['',label,v.values[k] || '待填写']),
    '', '偏倚评估', ...Object.entries(biasFields).map(([k,label])=>label+' · '+(v.bias?.[k] || '待评估')),
    '', '数据可行性评估', ...feasibilityItems.map((label,i)=>label+' · '+v.checks[i].status+' · '+(v.checks[i].note || '依据待补充')),
    '', '仍需解决', ...(v.gaps.length?v.gaps:['已填完结构化项目，需专业人员评判研究质量']),
    '', '流行病学复核记录',v.epi || '未记录','统计复核记录',v.stats || '未记录',
    '', '本版修改',...(v.changes.length?v.changes:['首版']),
    '', '来源会议版本 '+(v.meetingId || '独立起草'),
    ...v.refs.flatMap(r=>[r.title,sourceLink(r),r.label,r.quote,''])].join('\n');
}
function renderStudy() {
  const versions = () => Object.keys(localStorage).filter(k=>k.startsWith(studyPrefix))
    .map(k=>JSON.parse(localStorage.getItem(k))).sort((a,b)=>b.created.localeCompare(a.created));
  const blank = () => ({id:null,kind:'RWE',values:{},checks:feasibilityItems.map(()=>({status:'待核实',note:''})),
    epi:'',stats:'',refs:[],meetingId:null});
  let base, draft;
  const reset = v => {base=v.id?v:null; draft=structuredClone(v);};
  try {
    if (studySeed) {
      const src=studySeed; studySeed=null;
      const v=blank(); v.meetingId=src.id; v.refs=src.refs;
      v.values.question=src.discussion || src.question; reset(v);
    } else reset(versions()[0] || blank());
  } catch (error) { setView(errorPanel(error)); return; }
  let dirty=false;
  function paint() {
    const fields={...studyCommon,...studySpecific[draft.kind]};
    setView(`<section class="research-home" id="study-workspace" lang="zh-CN"><h1>IIT 与真实世界研究设计</h1>
      <label>历史版本<select id="study-history"><option value="">当前新草稿</option>
        ${versions().map(v=>option(v.id,base?.id,v.kind+' · '+v.values.question+' · '+fmtDate(v.created))).join('')}</select></label>
      <button type="button" id="study-new" class="btn">新建研究</button>
      <form id="study-form"><label>交付形式<select id="study-kind">${option('RWE',draft.kind,'真实世界研究方案')}${option('IIT',draft.kind,'研究者发起研究概念书')}</select></label>
      ${draft.refs.length?`<details><summary>继承自会议的引文（${draft.refs.length}）</summary>${draft.refs.map(r=>`<p><a href="${esc(sourceLink(r))}" target="_blank" rel="noopener noreferrer">${esc(r.title)}</a></p><blockquote>${esc(r.quote)}</blockquote>`).join('')}</details>`:''}
      ${Object.entries(fields).map(([k,label])=>`<label>${label}<textarea data-field="${k}" rows="2" maxlength="8000">${esc(draft.values[k] || '')}</textarea></label>`).join('')}
      <fieldset class="card"><legend>偏倚评估</legend>
      ${Object.entries(biasFields).map(([key,label])=>`<label>${label}<textarea data-bias="${key}" maxlength="8000">${esc(draft.bias?.[key] || '')}</textarea></label>`).join('')}</fieldset>
      <fieldset class="card"><legend>数据可行性评估</legend>${feasibilityItems.map((label,i)=>`<label>${label}
        <select data-check="${i}">${feasibilityStates.map(s=>option(s,draft.checks[i].status,s)).join('')}</select></label>
        <label>依据或补救措施<textarea rows="1" maxlength="2000" data-reason="${i}">${esc(draft.checks[i].note)}</textarea></label>`).join('')}</fieldset>
      <label>流行病学复核人、意见与日期<textarea id="study-epi" maxlength="4000">${esc(draft.epi)}</textarea></label>
      <label>统计复核人、意见与日期<textarea id="study-stats" maxlength="4000">${esc(draft.stats)}</textarea></label>
      <button class="btn primary" type="submit">保存并检查缺项</button>
      <button class="btn" id="study-export" type="button">导出当前方案</button> <button class="btn" id="study-word" type="button">下载可编辑 Word 提案</button></form>
      <div id="study-review" aria-live="polite"></div></section>`);
    const form=$('#study-form');
    function collect() {
      for (const el of $$('[data-field]',form)) draft.values[el.dataset.field]=el.value.trim();
      for (const el of $$('[data-check]',form)) draft.checks[el.dataset.check].status=el.value;
      for (const el of $$('[data-reason]',form)) draft.checks[el.dataset.reason].note=el.value.trim();
      draft.bias=Object.fromEntries($$('[data-bias]',form).map(el=>[el.dataset.bias,el.value.trim()]));
      draft.epi=$('#study-epi').value.trim(); draft.stats=$('#study-stats').value.trim();
    }
    function showReview(v) {
      $('#study-review').innerHTML='<h2>仍需解决</h2><ul>'+v.gaps.map(g=>'<li>'+esc(g)+'</li>').join('')+'</ul>'+
        (v.gaps.length?'':'结构化项目已填写。')+'<h2>本版修改</h2><p>'+esc(v.changes.join('、') || '首版')+'</p>';
    }
    if (base && !dirty) showReview(base);
    form.oninput=()=>{dirty=true;$('#study-review').textContent='有未保存的修改';};
    $('#study-kind').onchange=e=>{collect();draft.kind=e.target.value;dirty=true;paint();};
    $('#study-new').onclick=()=>{if (!dirty || confirm('放弃未保存的修改？')) {reset(blank());dirty=false;paint();}};
    $('#study-history').onchange=e=>{
      if (dirty && !confirm('放弃未保存的修改？')) {e.target.value=base?.id || '';return;}
      reset(versions().find(v=>v.id===e.target.value) || blank());dirty=false;paint();
    };
    function save() {
      collect();
      if (!draft.values.question) {$('#study-review').textContent='请填写未解决的研究问题。';return null;}
      if (base && !dirty) return base;
      const gaps=Object.entries(fields).filter(([k])=>!draft.values[k]).map(([,label])=>label+'待填写');
      draft.checks.forEach((c,i)=>{
        if (c.status!=='可用' || !c.note) gaps.push(feasibilityItems[i]+' · '+c.status+(c.note?'':'，依据待补充'));
      });
      for (const [key,label] of Object.entries(biasFields)) if (!draft.bias[key]) gaps.push(label+'待评估');
      if (!draft.epi) gaps.push('待流行病学复核');
      if (!draft.stats) gaps.push('待统计复核');
      const changes=[];
      if (base) {
        if(base.kind!==draft.kind) changes.push('交付形式');
        for(const [key,label] of Object.entries(fields)) if((base.values[key] || '')!==(draft.values[key] || '')) changes.push(label);
        draft.checks.forEach((c,i)=>{if(JSON.stringify(c)!==JSON.stringify(base.checks[i])) changes.push(feasibilityItems[i]);});
        for (const [key,label] of Object.entries(biasFields))
          if ((base.bias?.[key] || '')!==draft.bias[key]) changes.push(label);
        const designChanged=changes.length>0;
        for (const [key,label] of [['epi','流行病学'],['stats','统计']]) {
          const renewal=label+'复核记录沿用上一版，需确认本版';
          if (draft[key] && draft[key]===base[key] && (designChanged || base.gaps.includes(renewal))) gaps.push(renewal);
        }
        if (base.epi!==draft.epi) changes.push('流行病学复核记录');
        if (base.stats!==draft.stats) changes.push('统计复核记录');
        if (!changes.length) {dirty=false;showReview(base);return base;}
      }
      const next={...structuredClone(draft),values:Object.fromEntries(Object.keys(fields).map(k=>[k,draft.values[k] || ''])),
        id:crypto.randomUUID(),parent:base?.id || null,created:new Date().toISOString(),gaps,changes};
      try {localStorage.setItem(studyPrefix+next.id,JSON.stringify(next));}
      catch {$('#study-review').textContent='无法保存版本，请检查浏览器存储空间。';return null;}
      reset(next);dirty=false;paint();return next;
    }
    form.onsubmit=e=>{e.preventDefault();save();};
    $('#study-word').onclick=async ()=>{
      const v=save();if (!v) return;
      try {
        const res=await fetch('/api/medical/study.docx',jsonOptions('POST',v));
        if (!res.ok) throw new Error('Word 导出失败（'+res.status+'），可重新导出本版。');
        const url=URL.createObjectURL(await res.blob());
        const a=document.createElement('a');a.href=url;a.download='HiJenny-'+v.kind+'-'+v.id+'.docx';a.click();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
      } catch(err) {toast(err.message,'err');}
    };
    $('#study-export').onclick=()=>{
      const v=save();if (!v) return;
      const url=URL.createObjectURL(new Blob([studySummary(v)],{type:'text/plain;charset=utf-8'}));
      const a=document.createElement('a');a.href=url;a.download='HiJenny-'+v.kind+'-'+v.id+'.txt';a.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
  }
  paint();
}
