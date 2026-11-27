'use strict';

const homeTasks = {
  zh: {title:'选择今天要做的工作', sample:'查看单资产尽调样例与试点报价', groups:[
    ['商务拓展与尽调','筛选候选试验','打开尽调项目'],
    ['临床开发','比较研究设计、起草方案','跟踪试验里程碑'],
    ['医学事务','准备专家会答复','起草 IIT 或真实世界研究提案'],
    ['文献与证据','检索文献、指南与临床试验','整理多来源研究底稿']]},
  en: {title:'What are you working on?', sample:'View a sample diligence report and request a pilot quote', groups:[
    ['Business development','Screen candidate trials','Open diligence projects'],
    ['Clinical development','Compare study designs and draft a protocol','Track trial milestones'],
    ['Medical affairs','Prepare an expert meeting brief','Draft an IIT or real-world study proposal'],
    ['Evidence research','Search literature, guidelines and trials','Review sources for a research brief']]},
  ja: {title:'取り組む業務を選択', sample:'デューデリジェンスの納品見本と試行導入の見積もり', groups:[
    ['事業開発','候補試験を絞り込む','デューデリジェンス案件を開く'],
    ['臨床開発','試験デザインを比較し、計画書を作成する','試験のマイルストーンを追跡する'],
    ['メディカルアフェアーズ','専門家会議の資料を準備する','医師主導研究・リアルワールド研究を計画する'],
    ['文献・エビデンス','文献・ガイドライン・臨床試験を検索する','複数の情報源から調査資料をまとめる']]}
};
const homeRoutes=[['discovery','projects'],['clinical','watch'],['medical','study'],['research','brief']];

const homeEvidence={
  zh:{heading:'查药物的公开证据',label:'药物名、商品名或公司名',search:'查询',example:'奥希替尼',
    cutoff:'归档截面（台北）',trials:'登记试验',results:'有主要终点结果的试验',guidance:'指南与技术评估',
    more:'查看完整资产证据与比较工具',research:'带入医药检索',loading:'正在读取资产归档…',empty:'试验、主要终点和指南暂无匹配记录。',error:'暂时无法读取资产归档。'},
  en:{heading:'Find evidence for a drug',label:'Drug, brand or company name',search:'Search',example:'osimertinib',
    cutoff:'Archive as of (Taipei)',trials:'Registered trials',results:'Trials with primary results',guidance:'Guidelines and appraisals',
    more:'Open asset evidence and comparison tools',research:'Continue in research search',loading:'Loading archived evidence…',empty:'No matching trials, primary results or guidance.',error:'The archive is temporarily unavailable.'},
  ja:{heading:'医薬品の公開資料を調べる',label:'一般名・製品名・企業名',search:'検索',example:'osimertinib',
    cutoff:'収録基準日（台北）',trials:'登録試験',results:'主要評価項目の結果を収録した試験',guidance:'ガイドライン・技術評価',
    more:'資料一覧と試験比較を開く',research:'文献・試験検索へ',loading:'収録データを読み込み中…',empty:'該当する試験・主要評価項目・指針はありません。',error:'現在、収録データを読み込めません。'}
};

const homeReport={
  zh:{pilot:'就当前查询申请试点报价',title:'公开资产证据简报',download:'下载可打印简报',scope:'范围为 HiJenny 公开关注名单和已收录指南。终点按登记名称列出。',archive:'核对当时归档',source:'原始来源',date:'登记更新',version:'来源版本',observed:'首次收录',none:'此截面暂无记录',phase:'阶段',status:'登记状态',sponsor:'牵头申办方',conditions:'疾病',sites:'中心所在地区',timeFrame:'评价时间',population:'分析人群',missing:'该字段未收录',planned:'方案登记的主要终点',unavailable:'该截面未收录方案主要终点。',posted:'查看本试验已归档的主要终点结果条目',noPosted:'该截面未收录本试验的主要终点结果条目。'},
  en:{pilot:'Request a pilot quote for this search',title:'Public asset evidence brief',download:'Download printable brief',scope:'Coverage includes the HiJenny public watchlist and indexed guidance. Endpoints use registry titles.',archive:'View archived evidence',source:'Original source',date:'Registry update',version:'Source version',observed:'First indexed',none:'No records at this cutoff',phase:'Phase',status:'Registry status',sponsor:'Lead sponsor',conditions:'Conditions',sites:'Site countries or regions',timeFrame:'Assessment time frame',population:'Analysis population',missing:'Field unavailable in archive',planned:'Protocol primary endpoints',unavailable:'Protocol primary endpoints are unavailable at this cutoff.',posted:'View archived primary outcome entries for this trial',noPosted:'No primary outcome entries for this trial are archived at this cutoff.'},
  ja:{pilot:'この検索対象で試行導入の見積もりを依頼',title:'医薬品の公開エビデンス概要',download:'印刷用の概要をダウンロード',scope:'HiJenny の公開ウォッチリストと収録済みの指針が対象です。評価項目は登録名で表示します。',archive:'当時の収録記録',source:'原資料',date:'登録更新日',version:'資料の版',observed:'初回収録日',none:'この時点の収録記録なし',phase:'試験相',status:'登録状況',sponsor:'主たる依頼者',conditions:'対象疾患',sites:'実施国・地域',timeFrame:'評価期間',population:'解析対象集団',missing:'この項目は未収録',planned:'計画書に登録された主要評価項目',unavailable:'この時点で計画書の主要評価項目は収録されていません。',posted:'この試験の収録済み主要評価項目の結果を見る',noPosted:'この時点でこの試験の主要評価項目の結果は収録されていません。'}
};

const homeUpdates={
  zh:{title:'公开动态（按来源日期）',source:'查看原文',filed:'申报日',open:'查看完整动态与订阅',download:'下载全部动态简报（中文 TXT）',empty:'当前查询条件下没有匹配的归档动态。',error:'暂时无法读取公开动态。'},
  en:{title:'Public updates by source date',source:'Open original source',filed:'Filed',open:'View all updates and subscriptions',download:'Download all matching updates (Chinese TXT)',empty:'No archived updates match the current filters.',error:'Archived updates are temporarily unavailable.'},
  ja:{title:'公開情報（情報源の日付順）',source:'原資料を開く',filed:'提出日',open:'更新一覧と購読',download:'全件のレポートをダウンロード（中国語 TXT）',empty:'この検索条件に該当する収録記録はありません。',error:'現在、更新情報を読み込めません。'}
};

function homeDisclosure(row,lang,cutoff) {
  if (row.source!=='NMPA' && row.source!=='SEC') return '';
  const labels={
    zh:{notice:'同一公告的归档',filing:'同一申报的归档'},
    en:{notice:'Archived entries for this notice',filing:'Archived entries for this filing'},
    ja:{notice:'この公告の収録記録',filing:'この提出書類の収録記録'}
  }[lang];
  const isNotice=row.source==='NMPA';
  const value=typeof row.value==='string' && row.field!=='公司申报' ? row.value : '';
  const summary=value ? `<p>${esc(value)}</p>` : '';
  if (!row.record) return summary;
  const params=new URLSearchParams({source:row.source,source_record:row.record});
  if (cutoff) params.set('as_of',cutoff);
  const label=isNotice ? labels.notice : labels.filing;
  return summary+`<div><a data-home-disclosure href="#/public-events?${esc(params.toString())}">${label} · ${esc(row.record)}</a></div>`;
}

function homeMonitoring(receipt,lang) {
  const copy={
    zh:{title:'最近一批监测',success:'成功目标',none:'本批关注名单没有匹配的具名目标',unknown:'未标注对象的目标',noReceipt:'服务器尚无采集回执',manual_review:'待人工复核',failed:'采集失败',pending:'待核对'},
    en:{title:'Latest monitoring run',success:'Successful targets',none:'No named watchlist target matched this scope',unknown:'Targets without a subject label',noReceipt:'No collection receipt available',manual_review:'Manual review',failed:'Collection failed',pending:'Pending review'},
    ja:{title:'直近の収集状況',success:'収集成功',none:'この範囲に一致する名称付き監視対象はありません',unknown:'対象名が未登録の監視項目',noReceipt:'収集記録はまだありません',manual_review:'手動確認待ち',failed:'収集失敗',pending:'要確認'}
  }[lang];
  if (!receipt) return `<p>${copy.noReceipt}</p>`;
  const reviewLabel={zh:'查看采集明细',en:'Review collection details',ja:'収集の詳細を確認'}[lang];
  const gaps=receipt.pending.map(c=>{
    const params=new URLSearchParams({check_source:c.source,check_target:c.target,check_batch:receipt.finished_utc});
    return `<li>${esc(copy[c.status] || copy.pending)} · ${esc(c.source)} · ${esc(c.target)} · <a data-home-check href="#/public-events?${esc(params.toString())}">${reviewLabel}</a></li>`;
  }).join('');
  const count=receipt.total ? `${copy.success} ${receipt.ok}/${receipt.total}` : copy.none;
  const unknown=receipt.unlabeled ? ` · ${copy.unknown} ${receipt.unlabeled}` : '';
  return `<div data-home-monitoring><p>${copy.title} · ${esc(receipt.finished_utc)} · ${count}${unknown}</p>${gaps ? '<ul>'+gaps+'</ul>' : ''}</div>`;
}

async function showHomeUpdates(panel,filters,lang) {
  const copy=homeUpdates[lang];
  const query=new URLSearchParams(filters);
  const reportUrl='/api/public-events/report.txt?'+query;
  const archiveLink=`<a href="#/public-events?${esc(query.toString())}">${copy.open}</a>`;
  try {
    query.set('limit','3');
    query.set('include_monitoring','true');
    const result=await api('/api/public-events?'+query);
    if (!panel.isConnected) return;
    const items=result.items.map(r=>{
      const href='#/public-events?'+new URLSearchParams({event:r.id,...(filters.as_of ? {as_of:filters.as_of} : {})});
      const filing=r.source==='SEC';
      const title=filing && r.field==='公司申报' ? [r.subject,r.value].filter(Boolean).join(' · ') : r.field;
      const dateLabel=r.date_label || (filing ? copy.filed : '');
      const when=[dateLabel,r.source_date].filter(Boolean).join(' ');
      const sourceLink=/^https?:\/\//i.test(r.url || '')
        ? ` · <a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${copy.source}</a>` : '';
      const measures=r.category==='primary-outcomes' ? r.value?.primary_outcomes : null;
      const outcomes=Array.isArray(measures) ? measures.map(m=>
        `<details class="home-outcome"><summary>${esc(r.record)} · ${esc(m.title || homeEvidence[lang].results)}</summary>${homeOutcomeDetails(m,lang)}</details>`).join('') : '';
      return `<li><a href="${esc(href)}">${esc(title)}</a> · ${esc(r.source)} · ${esc(when)}${sourceLink}${homeDisclosure(r,lang,filters.as_of)}${outcomes}</li>`;
    });
    panel.innerHTML=`<h3>${copy.title} (${result.total})</h3>`+
      (items.length?'<ul>'+items.join('')+'</ul>':'<p>'+copy.empty+'</p>')+homeMonitoring(result.latest_monitoring,lang)+archiveLink+
      (result.total ? ` <a class="btn" data-home-update-report href="${esc(reportUrl)}" download>${copy.download}</a>` : '');
    return result.total;
  } catch {
    if (panel.isConnected) panel.innerHTML='<p>'+copy.error+'</p>'+archiveLink;
  }
}

function homeOutcomeDetails(m,lang) {
  const copy=homeReport[lang];
  const tableLabels={
    zh:{denoms:denomHeaders,values:outcomeHeaders,scopes:['整个终点','分层','总体'],stat:'统计量',spread:'离散量类型'},
    en:{denoms:['Scope','Group','Denominator','Denominator unit'],values:['Category','Group','Reported value','Unit','Dispersion','Lower limit','Upper limit','Comment'],scopes:['Entire endpoint','Stratum','Overall'],stat:'Statistic',spread:'Dispersion type'},
    ja:{denoms:['適用範囲','群','解析分母','分母の単位'],values:['区分','群','報告値','単位','ばらつき','下限','上限','注記'],scopes:['評価項目全体','層','全体'],stat:'統計量',spread:'ばらつきの種類'}
  }[lang];
  const analyses={
    zh:{title:'登记统计分析',headers:analysisHeaders,fields:analysisFields,empty:'本终点归档列出的统计分析为 0 项。',missing:'此版归档缺少统计分析字段。'},
    en:{title:'Registered statistical analyses',headers:['Analysis','Field','Reported content'],empty:'This endpoint archive lists zero analyses.',missing:'The analyses field is unavailable in this archive version.',
      fields:{groupIds:'Groups',paramType:'Effect measure',paramValue:'Estimate',ciPctValue:'Confidence level (%)',ciNumSides:'Interval sides',ciLowerLimit:'Lower limit',ciUpperLimit:'Upper limit',pValue:'P value',statisticalMethod:'Statistical method',statisticalComment:'Method comment',nonInferiorityType:'Comparison type',nonInferiorityComment:'Comparison design comment',groupDescription:'Group description',estimateComment:'Estimate comment',pValueComment:'P value comment'}},
    ja:{title:'登録された統計解析',headers:['解析番号','項目','報告内容'],empty:'この評価項目の収録記録に記載された統計解析は0件です。',missing:'この版には統計解析の項目が収録されていません。',
      fields:{groupIds:'解析対象群',paramType:'効果指標',paramValue:'推定値',ciPctValue:'信頼水準（%）',ciNumSides:'区間の片側・両側',ciLowerLimit:'下限',ciUpperLimit:'上限',pValue:'P値',statisticalMethod:'統計手法',statisticalComment:'手法の注記',nonInferiorityType:'比較の種類',nonInferiorityComment:'比較デザインの注記',groupDescription:'群の説明',estimateComment:'推定値の注記',pValueComment:'P値の注記'}}
  }[lang];
  const {denoms,rows}=measureRows(m,tableLabels.scopes);
  const facts=[[copy.timeFrame,m.timeFrame || copy.missing],
    [copy.population,m.populationDescription || copy.missing],[tableLabels.stat,m.paramType],[tableLabels.spread,m.dispersionType]];
  return '<dl>'+facts.filter(([,value])=>value).map(([label,value])=>
    `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join('')+'</dl>'+
    registryTable(tableLabels.denoms,denoms,copy.missing,copy.missing)+
    registryTable(tableLabels.values,rows,copy.missing,copy.missing)+
    `<h4>${analyses.title}</h4>`+(Array.isArray(m.analyses)
      ? registryTable(analyses.headers,analysisRows(m,analyses.fields),analyses.empty,copy.missing)
      : '<p>'+analyses.missing+'</p>');
}

function downloadHomeEvidence(asset,result,lang) {
  const copy=homeReport[lang], headers=homeEvidence[lang];
  const link=url=>`<p>${copy.source} · <a href="${esc(url)}">${esc(url)}</a></p>`;
  const citations=new Map();
  const archive=id=>{
    if (!id) return '';
    const href=location.origin+location.pathname+'#/public-events?'+new URLSearchParams({event:id,as_of:result.as_of});
    if (!citations.has(id)) citations.set(id,{number:citations.size+1,href});
    return ` · <a href="${esc(href)}">${copy.archive} [${citations.get(id).number}]</a>`;
  };
  const details=pairs=>'<dl>'+pairs.filter(([,v])=>v).map(([k,v,id])=>`<dt>${esc(k)}</dt><dd>${esc(v)}${archive(id)}</dd>`).join('')+'</dl>';
  const section=(name,items,render)=>`<section><h2>${name} (${items.length})</h2>`+
    (items.length?items.map(r=>'<article>'+render(r)+'</article>').join(''):`<p>${copy.none}</p>`)+'</section>';
  const registry=r=>'https://clinicaltrials.gov/study/'+encodeURIComponent(r.record);
  const postedTrials=new Set(result.outcomes.map(r=>r.record));
  const protocol=r=>{
    const endpoints=(r.planned_endpoints || '').split('\n').filter(Boolean);
    const items=endpoints.map(title=>'<li>'+esc(title)+'</li>').join('');
    const posted=postedTrials.has(r.record)
      ? `<a href="#outcome-${esc(r.record)}">${copy.posted}</a>` : copy.noPosted;
    return `<h4>${copy.planned}</h4>`+(items?'<ul>'+items+'</ul>'+archive(r.field_events?.planned_endpoints):'<p>'+copy.unavailable+'</p>')+
      '<p>'+posted+'</p>';
  };
  const citationIndex=()=>{
    if (!citations.size) return '';
    const title={zh:'归档编号索引',en:'Archive reference index',ja:'収録記録番号一覧'}[lang];
    const hint={zh:'在「公开资产动态」按归档编号和本简报截止日查找原记录。',en:'In Public updates, search by archive ID and the cutoff date of this brief.',ja:'公開情報画面で収録記録番号とこの資料の基準日を指定して検索できます。'}[lang];
    const labels={zh:['来源日期','归档收录时间'],en:['Source date','Indexed at'],ja:['情報源の日付','収録日時']}[lang];
    const reference=([id,{href}])=>{
      const dates=result.reference_dates?.[id];
      const timing=dates ? [dates.source_date,dates.observed].map((v,i)=>v ? labels[i]+' '+esc(v) : '').filter(Boolean).join(' · ') : '';
      return `<li><a href="${esc(href)}"><code>${esc(id)}</code></a>${timing?'<br>'+timing:''}</li>`;
    };
    return `<section><h2>${title}</h2><p>${hint}</p><ol>`+
      [...citations].map(reference).join('')+'</ol></section>';
  };
  const report=`<!doctype html><html lang="${lang}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(asset)} · ${copy.title}</title>
    <style>body{font:16px/1.55 system-ui,sans-serif;max-width:54rem;margin:3rem auto;padding:0 1.2rem;color:#172b35}h1,h2,h3{line-height:1.25}h2{border-bottom:1px solid #66838b;padding-bottom:.4rem}article{margin:1.4rem 0}dt{font-weight:600}dd{margin:0 0 .4rem}a{color:#075f70;overflow-wrap:anywhere}.comparison-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;margin:.8rem 0;font-size:.85em}th,td{padding:.25rem;text-align:left;vertical-align:top;border:1px solid #aab8be;overflow-wrap:anywhere}
    @page{margin:15mm 13mm 17mm;@bottom-right{content:counter(page) " / " counter(pages);font:9pt system-ui;color:#47565d}}
    @media print{
      body{margin:0;padding:0;max-width:none;font-size:10pt}
      .comparison-scroll{overflow:visible}
      h2,h3,h4,dt{break-after:avoid}
      thead{display:table-header-group;break-inside:avoid}
      tr{break-inside:avoid}
      article{break-inside:avoid}
      article:has(table){break-inside:auto}
      table{font-size:9pt}
    }</style>
    <main><p>HiJenny · ${copy.title}</p><h1>${esc(asset)}</h1><p>${headers.cutoff} ${esc(result.as_of)}</p><p>${copy.scope}</p>`+
    section(headers.trials,result.trials,r=>`<h3>${esc(r.record)} · ${esc(r.asset)}</h3>`+
      details([['phase',copy.phase],['status',copy.status],['sponsor',copy.sponsor],['conditions',copy.conditions],['countries',copy.sites]]
        .map(([key,label])=>[label,r[key],r.field_events?.[key]]).concat([[{zh:'登记状态的来源日期',en:'Source date for registry status',ja:'登録状況の情報源の日付'}[lang],r.source_date,r.id]]))+protocol(r)+link(registry(r)))+
    section(headers.results,result.outcomes,r=>`<h3 id="outcome-${esc(r.record)}">${esc(r.record)}</h3><ul>${(r.measures || r.endpoints.map(title=>({title}))).map(m=>
      `<li><strong>${esc(m.title)}</strong>${homeOutcomeDetails(m,lang)}</li>`).join('')}</ul>`+
      details([[copy.date,r.source_date,r.id]])+link(registry(r)+'?tab=results'))+
    section(headers.guidance,result.guidance,g=>`<h3>${esc(g.publisher)} · ${esc(g.reference)}</h3><p>${esc(g.type)} · ${esc(g.topic)} · ${esc(g.region)}</p>`+
      details([[copy.version,g.version_date],[copy.observed,g.observed]])+link(g.url))+citationIndex()+'</main></html>';
  const url=URL.createObjectURL(new Blob([report],{type:'text/html;charset=utf-8'}));
  const a=document.createElement('a');a.href=url;a.download='HiJenny-'+asset.replace(/[^\p{L}\p{N}._-]+/gu,'-').slice(0,60)+'-'+result.as_of+'-'+lang+'.html';a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function trackHomeEvidence(event) {
  try {
    const payload={session_id:pilotSessionId(),sample_version:'asset-home-v1',event};
    fetch('/api/pilot/events',{...jsonOptions('POST',payload),keepalive:true}).catch(()=>{});
  } catch { /* Browsers without session storage can still use the archive. */ }
}

async function renderHome() {
  trackHomeEvidence('home_open');
  const requested=hashParams().get('lang');
  const lang=Object.hasOwn(homeTasks,requested)?requested:'zh';
  const copy=homeTasks[lang], evidence=homeEvidence[lang];
  const asset=hashParams().get('asset')?.trim().slice(0,400) || evidence.example;
  const cutoff=hashParams().get('as_of');
  const filters=cutoff ? {asset,as_of:cutoff} : {asset};
  const scopes={
    results:{source:'ClinicalTrials.gov',primary_results:'true'},
    regulatory:{source:'NMPA'},
    filings:{source:'SEC'}
  };
  const task=hashParams().get('task');
  const scoped=Object.hasOwn(scopes,task);
  const scopeLabels={
    zh:{results:'登记主要终点结果',regulatory:'监管公告',filings:'公司申报'},
    en:{results:'Registered primary outcome results',regulatory:'Regulatory notices',filings:'Company filings'},
    ja:{results:'登録された主要評価項目の結果',regulatory:'規制当局の公表情報',filings:'企業提出資料'}
  }[lang];
  const navigation=scoped ? {...filters,task} : filters;
  const examples={
    zh:[['results','奥希替尼','查临床读出：奥希替尼'],['regulatory','依达方','查监管动态：依达方'],['filings','Summit Therapeutics','查公司披露：Summit Therapeutics']],
    en:[['results','osimertinib','Trial results · osimertinib'],['regulatory','ivonescimab','Regulatory updates · ivonescimab'],['filings','Summit Therapeutics','Company disclosures · Summit Therapeutics']],
    ja:[['results','osimertinib','試験結果 · osimertinib'],['regulatory','ivonescimab','規制動向 · ivonescimab'],['filings','Summit Therapeutics','企業開示 · Summit Therapeutics']]
  }[lang];
  const exampleLinks=examples.map(([task,name,label])=>{
    const params=new URLSearchParams({...filters,asset:name,lang,task});
    return `<a href="#/home?${esc(params.toString())}">${esc(label)}</a>`;
  }).join(' · ');
  setView(`<section class="research-home task-home" lang="${lang}">
    <header><h1>${copy.title}</h1><nav aria-label="Language">${[['zh','中文'],['en','English'],['ja','日本語']].map(([code,name])=>
      `<a href="#/home?${esc(new URLSearchParams({lang:code,...navigation}).toString())}" lang="${code}" ${lang===code?'aria-current="page"':''}>${name}</a>`).join(' · ')}</nav></header>
    <section class="card home-evidence"><h2>${evidence.heading}</h2>
    <form id="home-asset-search"><label>${evidence.label}<input name="asset" type="search" required maxlength="400" value="${esc(asset)}"></label>
    <label>${evidence.cutoff}<input name="as_of" type="date" value="${esc(cutoff || '')}"></label><button class="btn primary">${evidence.search}</button></form>
    <p>${exampleLinks}</p>
    ${scoped ? `<p><strong>${scopeLabels[task]} · ${scopes[task].source}</strong> · <a href="#/home?${esc(new URLSearchParams({lang,...filters}).toString())}">${{zh:'查看该名称的全部动态',en:'All updates for this name',ja:'この名称の更新をすべて表示'}[lang]}</a></p><div id="home-asset-updates" aria-live="polite"></div>` : ''}
    <div id="home-evidence-results" aria-live="polite"></div>${scoped ? '' : '<div id="home-asset-updates" aria-live="polite"></div>'}</section>
    <div class="task-groups">${copy.groups.map(([role,first,second],i)=>`<section class="card"><h2>${role}</h2>
      <a href="#/${homeRoutes[i][0]}">${first}</a><a href="#/${homeRoutes[i][1]}">${second}</a></section>`).join('')}</div>
    <a class="btn" href="#/pilot?lang=${lang}">${copy.sample}</a></section>`);
  const form=$('#home-asset-search'), output=$('#home-evidence-results');
  form.onsubmit=event=>{
    event.preventDefault();
    const query=form.elements.asset.value.trim();
    if (!query) {form.elements.asset.focus();return;}
    trackHomeEvidence('search_submitted');
    const params=new URLSearchParams({lang,asset:query});
    if (scoped && query===asset) params.set('task',task);
    if (form.elements.as_of.value) params.set('as_of',form.elements.as_of.value);
    const hash='#/home?'+params;
    if (location.hash===hash) renderHome(); else location.hash=hash;
  };
  const researchLink=`<a href="#/research?${esc(new URLSearchParams({query:asset}).toString())}">${evidence.research}</a>`;
  const archiveMatches=showHomeUpdates($('#home-asset-updates'),{...filters,...(scoped ? scopes[task] : {})},lang);
  output.textContent=evidence.loading;
  try {
    const result=await api('/api/public-events/asset-evidence?'+new URLSearchParams(filters));
    if (!output.isConnected) return;
    const qs=new URLSearchParams({asset,as_of:result.as_of});
    const pilot=document.createElement('a');
    pilot.dataset.assetPilot='';
    pilot.href='#/pilot?'+new URLSearchParams({lang,asset,as_of:result.as_of});
    pilot.textContent=homeReport[lang].pilot;
    pilot.onclick=()=>trackHomeEvidence('quote_link_clicked');
    if (!result.trials.length && !result.outcomes.length && !result.guidance.length) {
      output.innerHTML=`<p>${esc(asset)} · ${evidence.empty}</p>${researchLink}`;
      if (await archiveMatches && output.isConnected) output.append(' ',pilot);
      return;
    }
    const listed=(label,items,render)=>`<div><h3>${label} (${items.length})</h3>${items.length?'<ul>'+items.slice(0,2).map(r=>'<li>'+render(r)+'</li>').join('')+'</ul>':''}</div>`;
    const trialLink=r=>`<a href="https://clinicaltrials.gov/study/${encodeURIComponent(r.record)}" target="_blank" rel="noopener noreferrer">${esc(r.record)}</a>`;
    output.innerHTML=`<p><strong>${esc(asset)}</strong> · ${evidence.cutoff} ${esc(result.as_of)}</p><div class="home-source-grid">`+
      listed(evidence.trials,result.trials,r=>trialLink(r)+' · '+esc(r.phase || r.status))+
      listed(evidence.results,result.outcomes,r=>trialLink(r)+' · '+esc(r.endpoints.join(' / ')))+
      listed(evidence.guidance,result.guidance,g=>`<a href="${esc(g.url)}" target="_blank" rel="noopener noreferrer">${esc(g.publisher)} ${esc(g.reference)}</a> · ${esc(g.version_date)}`)+
      `</div><a href="#/public-events?${esc(qs.toString())}">${evidence.more}</a> <button class="btn" type="button" data-asset-report>${homeReport[lang].download}</button>`;
    trackHomeEvidence('evidence_shown');
    output.querySelector('[data-asset-report]').onclick=()=>{
      downloadHomeEvidence(asset,result,lang);
      trackHomeEvidence('brief_requested');
    };
    output.append(' ',pilot);
  } catch {
    if (output.isConnected) output.innerHTML=`<p>${evidence.error}</p>${researchLink}`;
  }
}
