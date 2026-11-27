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
  zh:{pilot:'就此资产申请试点报价',title:'公开资产证据简报',download:'下载可打印简报',scope:'范围为 HiJenny 公开关注名单和已收录指南。终点按登记名称列出。',source:'原始来源',date:'登记更新',version:'来源版本',observed:'首次收录',none:'此截面暂无记录',phase:'阶段',status:'登记状态',sponsor:'牵头申办方',conditions:'疾病',sites:'中心所在地区'},
  en:{pilot:'Request a pilot quote for this asset',title:'Public asset evidence brief',download:'Download printable brief',scope:'Coverage includes the HiJenny public watchlist and indexed guidance. Endpoints use registry titles.',source:'Original source',date:'Registry update',version:'Source version',observed:'First indexed',none:'No records at this cutoff',phase:'Phase',status:'Registry status',sponsor:'Lead sponsor',conditions:'Conditions',sites:'Site countries or regions'},
  ja:{pilot:'この医薬品の試行導入見積もり',title:'医薬品の公開エビデンス概要',download:'印刷用の概要をダウンロード',scope:'HiJenny の公開ウォッチリストと収録済みの指針が対象です。評価項目は登録名で表示します。',source:'原資料',date:'登録更新日',version:'資料の版',observed:'初回収録日',none:'この時点の収録記録なし',phase:'試験相',status:'登録状況',sponsor:'主たる依頼者',conditions:'対象疾患',sites:'実施国・地域'}
};

const homeUpdates={
  zh:{title:'公开动态（按来源日期）',open:'查看动态、下载简报或订阅更新',empty:'该名称暂无匹配动态。',error:'暂时无法读取公开动态。'},
  en:{title:'Public updates by source date',open:'View updates, download a digest or subscribe',empty:'No archived updates match this name.',error:'Archived updates are temporarily unavailable.'},
  ja:{title:'公開情報（情報源の日付順）',open:'更新一覧・レポートのダウンロード・購読',empty:'この名称に該当する更新はありません。',error:'現在、更新情報を読み込めません。'}
};

async function showHomeUpdates(panel,asset,lang) {
  const copy=homeUpdates[lang];
  const query=new URLSearchParams({asset});
  const archiveLink=`<a href="#/public-events?${esc(query.toString())}">${copy.open}</a>`;
  try {
    query.set('limit','3');
    const result=await api('/api/public-events?'+query);
    if (!panel.isConnected) return;
    const items=result.items.map(r=>{
      const href='#/public-events?'+new URLSearchParams({event:r.id});
      return `<li><a href="${esc(href)}">${esc(r.field)}</a> · ${esc(r.source)} · ${esc([r.date_label,r.source_date].filter(Boolean).join(' '))}</li>`;
    });
    panel.innerHTML=`<h3>${copy.title} (${result.total})</h3>`+
      (items.length?'<ul>'+items.join('')+'</ul>':'<p>'+copy.empty+'</p>')+archiveLink;
  } catch {
    if (panel.isConnected) panel.innerHTML='<p>'+copy.error+'</p>'+archiveLink;
  }
}

function downloadHomeEvidence(asset,result,lang) {
  const copy=homeReport[lang], headers=homeEvidence[lang];
  const link=url=>`<p>${copy.source} · <a href="${esc(url)}">${esc(url)}</a></p>`;
  const details=pairs=>'<dl>'+pairs.filter(([,v])=>v).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')+'</dl>';
  const section=(name,items,render)=>`<section><h2>${name} (${items.length})</h2>`+
    (items.length?items.map(r=>'<article>'+render(r)+'</article>').join(''):`<p>${copy.none}</p>`)+'</section>';
  const registry=r=>'https://clinicaltrials.gov/study/'+encodeURIComponent(r.record);
  const report=`<!doctype html><html lang="${lang}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(asset)} · ${copy.title}</title>
    <style>body{font:16px/1.55 system-ui,sans-serif;max-width:54rem;margin:3rem auto;padding:0 1.2rem;color:#172b35}h1,h2,h3{line-height:1.25}h2{border-bottom:1px solid #66838b;padding-bottom:.4rem}article{margin:1.4rem 0}dt{font-weight:600}dd{margin:0 0 .4rem}a{color:#075f70;overflow-wrap:anywhere}@media print{body{margin:0;font-size:10pt}h2,h3{break-after:avoid}article{break-inside:avoid}}</style>
    <main><p>HiJenny · ${copy.title}</p><h1>${esc(asset)}</h1><p>${headers.cutoff} ${esc(result.as_of)}</p><p>${copy.scope}</p>`+
    section(headers.trials,result.trials,r=>`<h3>${esc(r.record)} · ${esc(r.asset)}</h3>`+
      details([[copy.phase,r.phase],[copy.status,r.status],[copy.sponsor,r.sponsor],[copy.conditions,r.conditions],[copy.sites,r.countries],[copy.date,r.source_date]])+link(registry(r)))+
    section(headers.results,result.outcomes,r=>`<h3>${esc(r.record)}</h3><ul>${r.endpoints.map(v=>'<li>'+esc(v)+'</li>').join('')}</ul>`+
      details([[copy.date,r.source_date]])+link(registry(r)+'?tab=results'))+
    section(headers.guidance,result.guidance,g=>`<h3>${esc(g.publisher)} · ${esc(g.reference)}</h3><p>${esc(g.type)} · ${esc(g.topic)} · ${esc(g.region)}</p>`+
      details([[copy.version,g.version_date],[copy.observed,g.observed]])+link(g.url))+'</main></html>';
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
  setView(`<section class="research-home task-home" lang="${lang}">
    <header><h1>${copy.title}</h1><nav aria-label="Language">${[['zh','中文'],['en','English'],['ja','日本語']].map(([code,name])=>
      `<a href="#/home?${esc(new URLSearchParams({lang:code,asset}).toString())}" lang="${code}" ${lang===code?'aria-current="page"':''}>${name}</a>`).join(' · ')}</nav></header>
    <section class="card home-evidence"><h2>${evidence.heading}</h2>
    <form id="home-asset-search"><label>${evidence.label}<input name="asset" type="search" required maxlength="400" value="${esc(asset)}"></label><button class="btn primary">${evidence.search}</button></form>
    <div id="home-evidence-results" aria-live="polite"></div><div id="home-asset-updates" aria-live="polite"></div></section>
    <div class="task-groups">${copy.groups.map(([role,first,second],i)=>`<section class="card"><h2>${role}</h2>
      <a href="#/${homeRoutes[i][0]}">${first}</a><a href="#/${homeRoutes[i][1]}">${second}</a></section>`).join('')}</div>
    <a class="btn" href="#/pilot?lang=${lang}">${copy.sample}</a></section>`);
  const form=$('#home-asset-search'), output=$('#home-evidence-results');
  form.onsubmit=event=>{
    event.preventDefault();
    const query=form.elements.asset.value.trim();
    if (!query) {form.elements.asset.focus();return;}
    trackHomeEvidence('search_submitted');
    const hash='#/home?'+new URLSearchParams({lang,asset:query});
    if (location.hash===hash) renderHome(); else location.hash=hash;
  };
  const researchLink=`<a href="#/research?${esc(new URLSearchParams({query:asset}).toString())}">${evidence.research}</a>`;
  showHomeUpdates($('#home-asset-updates'),asset,lang);
  output.textContent=evidence.loading;
  try {
    const result=await api('/api/public-events/asset-evidence?'+new URLSearchParams({asset}));
    if (!output.isConnected) return;
    if (!result.trials.length && !result.outcomes.length && !result.guidance.length) {
      output.innerHTML=`<p>${esc(asset)} · ${evidence.empty}</p>${researchLink}`;return;
    }
    const qs=new URLSearchParams({asset,as_of:result.as_of});
    const listed=(label,items,render)=>`<div><h3>${label} (${items.length})</h3>${items.length?'<ul>'+items.slice(0,2).map(r=>'<li>'+render(r)+'</li>').join('')+'</ul>':''}</div>`;
    const trialLink=r=>`<a href="https://clinicaltrials.gov/study/${encodeURIComponent(r.record)}" target="_blank" rel="noopener noreferrer">${esc(r.record)}</a>`;
    output.innerHTML=`<p><strong>${esc(asset)}</strong> · ${evidence.cutoff} ${esc(result.as_of)}</p><div class="home-source-grid">`+
      listed(evidence.trials,result.trials,r=>trialLink(r)+' · '+esc(r.phase || r.status))+
      listed(evidence.results,result.outcomes,r=>trialLink(r)+' · '+esc(r.endpoints.join(' / ')))+
      listed(evidence.guidance,result.guidance,g=>`<a href="${esc(g.url)}" target="_blank" rel="noopener noreferrer">${esc(g.publisher)} ${esc(g.reference)}</a> · ${esc(g.version_date)}`)+
      `</div><a href="#/public-events?${esc(qs.toString())}">${evidence.more}</a> <button class="btn" type="button" data-asset-report>${homeReport[lang].download}</button> <a data-asset-pilot href="#/pilot?${esc(new URLSearchParams({lang,asset,as_of:result.as_of}).toString())}">${homeReport[lang].pilot}</a>`;
    trackHomeEvidence('evidence_shown');
    output.querySelector('[data-asset-report]').onclick=()=>{
      downloadHomeEvidence(asset,result,lang);
      trackHomeEvidence('brief_requested');
    };
    output.querySelector('[data-asset-pilot]').onclick=()=>trackHomeEvidence('quote_link_clicked');
  } catch {
    if (output.isConnected) output.innerHTML=`<p>${evidence.error}</p>${researchLink}`;
  }
}
