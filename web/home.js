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

function renderHome() {
  const requested=hashParams().get('lang');
  const lang=Object.hasOwn(homeTasks,requested)?requested:'zh';
  const copy=homeTasks[lang];
  setView(`<section class="research-home task-home" lang="${lang}">
    <header><h1>${copy.title}</h1><nav aria-label="Language">${[['zh','中文'],['en','English'],['ja','日本語']].map(([code,name])=>
      `<a href="#/home?lang=${code}" lang="${code}" ${lang===code?'aria-current="page"':''}>${name}</a>`).join(' · ')}</nav></header>
    <div class="task-groups">${copy.groups.map(([role,first,second],i)=>`<section class="card"><h2>${role}</h2>
      <a href="#/${homeRoutes[i][0]}">${first}</a><a href="#/${homeRoutes[i][1]}">${second}</a></section>`).join('')}</div>
    <a class="btn" href="#/pilot?lang=${lang}">${copy.sample}</a></section>`);
}
