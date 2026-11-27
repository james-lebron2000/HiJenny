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
  zh:{heading:'查药物的公开证据',label:'药物名或商品名',search:'查询',example:'奥希替尼',
    cutoff:'归档截面（台北）',trials:'登记试验',results:'有主要终点结果的试验',guidance:'指南与技术评估',
    more:'查看完整资产证据与比较工具',research:'带入医药检索',loading:'正在读取资产归档…',empty:'公开关注名单暂无匹配记录。',error:'暂时无法读取资产归档。'},
  en:{heading:'Find evidence for a drug',label:'Drug or brand name',search:'Search',example:'osimertinib',
    cutoff:'Archive as of (Taipei)',trials:'Registered trials',results:'Trials with primary results',guidance:'Guidelines and appraisals',
    more:'Open asset evidence and comparison tools',research:'Continue in research search',loading:'Loading archived evidence…',empty:'No matching records in the public watchlist.',error:'The archive is temporarily unavailable.'},
  ja:{heading:'医薬品の公開資料を調べる',label:'一般名または製品名',search:'検索',example:'osimertinib',
    cutoff:'収録基準日（台北）',trials:'登録試験',results:'主要評価項目の結果を収録した試験',guidance:'ガイドライン・技術評価',
    more:'資料一覧と試験比較を開く',research:'文献・試験検索へ',loading:'収録データを読み込み中…',empty:'公開ウォッチリストに該当する記録がありません。',error:'現在、収録データを読み込めません。'}
};

async function renderHome() {
  const requested=hashParams().get('lang');
  const lang=Object.hasOwn(homeTasks,requested)?requested:'zh';
  const copy=homeTasks[lang], evidence=homeEvidence[lang];
  const asset=hashParams().get('asset')?.trim().slice(0,400) || evidence.example;
  setView(`<section class="research-home task-home" lang="${lang}">
    <header><h1>${copy.title}</h1><nav aria-label="Language">${[['zh','中文'],['en','English'],['ja','日本語']].map(([code,name])=>
      `<a href="#/home?${esc(new URLSearchParams({lang:code,asset}).toString())}" lang="${code}" ${lang===code?'aria-current="page"':''}>${name}</a>`).join(' · ')}</nav></header>
    <section class="card home-evidence"><h2>${evidence.heading}</h2>
    <form id="home-asset-search"><label>${evidence.label}<input name="asset" type="search" required maxlength="400" value="${esc(asset)}"></label><button class="btn primary">${evidence.search}</button></form>
    <div id="home-evidence-results" aria-live="polite"></div></section>
    <div class="task-groups">${copy.groups.map(([role,first,second],i)=>`<section class="card"><h2>${role}</h2>
      <a href="#/${homeRoutes[i][0]}">${first}</a><a href="#/${homeRoutes[i][1]}">${second}</a></section>`).join('')}</div>
    <a class="btn" href="#/pilot?lang=${lang}">${copy.sample}</a></section>`);
  const form=$('#home-asset-search'), output=$('#home-evidence-results');
  form.onsubmit=event=>{
    event.preventDefault();
    const query=form.elements.asset.value.trim();
    if (!query) {form.elements.asset.focus();return;}
    const hash='#/home?'+new URLSearchParams({lang,asset:query});
    if (location.hash===hash) renderHome(); else location.hash=hash;
  };
  const researchLink=`<a href="#/research?${esc(new URLSearchParams({query:asset}).toString())}">${evidence.research}</a>`;
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
      `</div><a href="#/public-events?${esc(qs.toString())}">${evidence.more}</a>`;
  } catch {
    if (output.isConnected) output.innerHTML=`<p>${evidence.error}</p>${researchLink}`;
  }
}
