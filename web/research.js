'use strict';

const researchSession = { query: '', source: 'pubmed', articleType:'', yearFrom:'', yearTo:'', response: null };

function readPublicLibrary() {
  try {
    const entries = JSON.parse(localStorage.getItem('hijenny.public-library') || '[]');
    return Array.isArray(entries) ? entries : [];
  } catch { return []; }
}

function sourceLink(item) {
  const id = String(item.id);
  if (/^NCT[0-9]+$/.test(id)) return 'https://clinicaltrials.gov/study/' + id + (item.postedResult ? '?tab=results' : '');
  if (/^[0-9]+$/.test(id)) return 'https://pubmed.ncbi.nlm.nih.gov/' + id + '/';
  return '';
}

function publicationNotices(item) {
  return (item.notices || []).map(notice=>{
    const url=notice.match(/https:\/\/pubmed\.ncbi\.nlm\.nih\.gov\/[0-9]+\/$/)?.[0];
    return `<p><strong>出版状态</strong> · ${esc(url ? notice.slice(0,-url.length) : notice)}${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">核对关联记录</a>` : ''}</p>`;
  }).join('');
}

function researchResults(data) {

  return `<h2>${esc(data.query)}</h2><p>${data.source === 'guidelines' ? 'PubMed 指南文献' : (data.source === 'pubmed' ? 'PubMed' : 'ClinicalTrials.gov')} · 共 ${esc(data.total)} 条，展示 ${data.items.length} 条</p>
    ${data.asset_match ? `<p>核对药名 ${esc(data.asset_match.name)} · <a href="${esc(data.asset_match.source)}" target="_blank" rel="noopener noreferrer">名称来源</a></p>` : ''}
    ${data.source === 'guidelines' ? '<p>范围为 PubMed 标注的指南及实践指南。适用地区、更新状态和推荐级别请核对发布方原文。</p>' : ''}
    ${data.effective_query && data.effective_query!==data.query ? `<p>实际检索式 <code>${esc(data.effective_query)}</code></p><button class="btn" type="button" id="filtered-brief">带入研究底稿</button>`:''}
    <button id="save-search" class="btn" type="button">下载检索记录</button>
    ${!data.items.length?'<p>没有找到记录。可调整年份、文献类型或检索词。</p>':''}
    <ol class="research-results">${data.items.map(item => `<li><label><input type="checkbox" class="pick-record" value="${esc(item.id)}"> 选择</label> <a href="${esc(sourceLink(item))}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>
    <p>${esc([item.id, item.detail, item.date].filter(Boolean).join(' · '))}</p>
    ${publicationNotices(item)}
    ${data.source !== 'trials' ? '<p>文献类型 · '+esc((item.publication_types || []).join(' / ') || '未提供')+'</p>' : ''}
    <details><summary>查看来源片段</summary>${item.passages.length ? item.passages.map(p =>
      `<p><strong>${esc(p.label)}</strong><br>${esc(p.text)}</p>`).join('') : '<p>该记录未提供摘要。</p>'}</details>
    ${data.source === 'trials' ? `<button type="button" class="btn" data-load-results="${esc(item.id)}">查看公布结果与安全性</button><section class="trial-posted" aria-live="polite"></section>` : ''}</li>`).join('')}</ol><button id="keep-records" type="button" class="btn">收藏勾选记录</button>
    ${data.source === 'trials' ? '<button id="follow-trials" class="btn" type="button">关注选中的试验</button><button id="compare-trials" type="button" class="btn">并排比较选中的试验</button><div id="trial-comparison"></div>' : ''}`;
}

function paperByline(paper) {
  const affiliations=(paper.authors || []).map((_,position)=>(paper.author_affiliations?.[position] || []).join(' / '));
  const counts=new Map();
  for (const text of affiliations) counts.set(text,(counts.get(text) || 0)+1);
  return (paper.authors || []).map((name,position)=>{
    const affiliation=affiliations[position];
    const count=counts.get(affiliation);
    const displayAffiliation=affiliation && count>1 ? '（'+count+' 位作者共用这段署名信息）'+affiliation : affiliation;
    return {name,position,affiliation,displayAffiliation};
  });
}

function libraryMatches(item, filter) {
  const contains = value => String(value ?? '').toLowerCase().includes(filter);
  const passages = item.passages || [];
  const matching = filter ? passages.filter(p => contains(p.text) || contains(p.label)) : [];
  const byline=paperByline(item);
  const matchedAuthors=filter?byline.filter(author=>contains(author.name) || contains(author.affiliation)):[];
  return {byline,matchedAuthors,visible:matchedAuthors.length > 0 || matching.length > 0 || [item.title,item.detail,item.query,item.inputQuery,item.id,item.date,...(item.notices || [])].some(contains), matching};
}

function libraryEvidence(item, matching) {
  const passages = matching.length ? matching : item.passages || [];
  const empty = item.passages ? '本次检索未返回来源片段。' : '旧收藏仅保存了索引。重新检索并收藏可保存当前来源片段。';
  return `<details ${matching.length?'open':''}><summary>${matching.length?'匹配的来源片段':'收藏时的来源片段'}（${passages.length}）</summary>
    <p>${esc(item.id)} · 来源日期 ${esc(item.date || '未记录')} · 检索时间 ${esc(item.retrieved || '未记录')}</p>
    ${item.inputQuery && item.inputQuery!==item.query?`<p>输入词 ${esc(item.inputQuery)}</p>`:''}
    ${item.query?`<p>检索式 <code>${esc(item.query)}</code></p>`:''}
    ${passages.map(p=>`<p><strong>${esc(p.label)}</strong><br>${esc(p.text)}</p>`).join('') || `<p>${empty}</p>`}</details>`;
}

function libraryBrief(question, entries) {
  const steps=[];
  for (const source of ['pubmed','trials']) {
    const items=entries.filter(item=>item.id.startsWith('NCT')===(source==='trials'))
      .map(item=>({...item,passages:item.passages || []}));
    if (!items.length) continue;
    if (items.length>100) throw Error('每份研究报告每个来源最多收录 100 条，请缩小选区。');
    const query=items.map(item=>source==='pubmed'?item.id+'[pmid]':item.id).join(' OR ');
    steps.push({source,query,attempts:0,fromLibrary:true,result:{total:null,items}});
  }
  return {id:crypto.randomUUID(),created:new Date().toISOString(),question,steps,review:{},note:''};
}

function renderResearch() {
  setView(`<section class="research-home" lang="zh-CN"><h1>查文献与临床试验</h1>
    <p>输入公开药物名、适应症或检索式，查看数据库记录与原始来源。</p>
    <p>准备资产引进评审？<a href="#/pilot?lang=zh">查看单资产尽调交付物与试点报价申请</a> · <a href="#/pilot?lang=en" lang="en">Pilot quote</a> · <a href="#/pilot?lang=ja" lang="ja">見積もり依頼</a></p>
    <form id="research-form" class="card"><label for="research-query">检索内容</label>
    <input id="research-query" required maxlength="400" placeholder="例如 依达方、imatinib 或 NCT00115739" value="${esc(researchSession.query)}">
    <label for="research-source">数据来源</label><select id="research-source"><option value="pubmed">PubMed 文献</option>
    <option value="guidelines">PubMed 指南文献</option><option value="trials">ClinicalTrials.gov 临床试验</option></select>
    <fieldset id="pubmed-filters"><legend>文献筛选</legend>
    <label>文献类型<select id="article-type">${[['','全部类型'],['Clinical Trial','临床试验'],['Randomized Controlled Trial','随机对照试验'],['Systematic Review','系统综述'],['Meta-Analysis','荟萃分析'],['Review','综述']].map(([v,label])=>option(v,researchSession.articleType,label)).join('')}</select></label>
    <label>发表起始年<input type="number" min="1000" max="9999" id="year-from" value="${esc(researchSession.yearFrom)}"></label>
    <label>发表结束年<input type="number" min="1000" max="9999" id="year-to" value="${esc(researchSession.yearTo)}"></label></fieldset>
    <p>检索词和筛选条件将发送至所选公共数据库。请使用公开信息。</p>
    <button class="btn primary" type="submit">检索公开记录</button></form>
    <button class="btn" type="button" id="make-brief">用这个主题整理多来源底稿 →</button>
    <div id="research-output" aria-live="polite"></div>
    <details id="public-library"><summary>我的公开资料收藏（本浏览器）</summary>
    <label>按题名、编号、作者、署名单位、检索式或来源片段筛选 <input id="library-filter" type="search"></label><ul id="library-list"></ul><button class="btn" type="button" id="library-compare">对照已选试验的公布终点</button><form id="library-brief"><label>围绕这些收藏要回答什么问题？<input name="question" required maxlength="400"></label><button class="btn" type="submit">用所选收藏创建研究底稿</button></form><div id="library-outcomes" aria-live="polite"></div></details>
    <button data-nav="projects" class="btn" type="button">进入资料包尽调工作区 →</button></section>`);
  $('#make-brief').onclick = () => { briefSeed = $('#research-query').value.trim(); nav('brief'); };
  const source = $('#research-source');
  source.value = researchSession.source;
  const filters=$('#pubmed-filters'), articleType=$('#article-type'), yearFrom=$('#year-from'), yearTo=$('#year-to');
  function updateFilters() {
    filters.hidden=filters.disabled=source.value==='trials';
    articleType.disabled=source.value!=='pubmed';
    articleType.parentElement.hidden=source.value==='guidelines';
  }
  source.onchange=updateFilters;updateFilters();
  const output = $('#research-output');
  const chosenRecords = new Set();
  function renderLibrary() {
    const filter = $('#library-filter').value.trim().toLowerCase();
    const list = $('#library-list');
    const rows = [];
    for (const item of readPublicLibrary()) {
      const {visible,matching,byline,matchedAuthors} = libraryMatches(item, filter);
      if (!visible) continue;
      const authors=matchedAuthors.length ? matchedAuthors : byline;
      const authorDetails=authors.length ? `<details data-library-authors ${matchedAuthors.length?'open':''}>
        <summary>${matchedAuthors.length?'匹配的署名作者':'论文署名作者'}（${authors.length}）</summary>
        <ol>${authors.map(a=>`<li value="${a.position+1}">${esc(a.name)} · ${esc(a.displayAffiliation || '该署名未列单位')}</li>`).join('')}</ol></details>`
        : (!item.id.startsWith('NCT') && !item.authors ? '<p>旧收藏未保存署名，重新检索并收藏可补入当前署名记录。</p>' : '');
      rows.push(`<li><label><input type="checkbox" data-library-id="${esc(item.id)}" ${chosenRecords.has(item.id)?"checked":""}> ${esc(item.id)}</label>
        <a href="${esc(sourceLink(item))}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>
        ${publicationNotices(item)}${authorDetails}${libraryEvidence(item,matching)}<button class="btn sm" type="button" data-remove="${esc(item.id)}">移除</button></li>`);
    }
    list.innerHTML = rows.join('') || '<li>暂无匹配记录</li>';
  }
  function persist(entries) {
    try {
      localStorage.setItem('hijenny.public-library', JSON.stringify(entries));
      renderLibrary();
      return true;
    } catch { toast('浏览器无法写入收藏。请下载检索记录。', 'err'); return false; }
  }
  $('#library-filter').oninput = renderLibrary;
  $('#library-list').onclick = event => {
    const id = event.target.closest('[data-remove]')?.dataset.remove;
    if (id && persist(readPublicLibrary().filter(item => item.id !== id))) {
      chosenRecords.delete(id);$('#library-compare').textContent='对照已选试验的公布终点（'+[...chosenRecords].filter(id=>id.startsWith('NCT')).length+'）';$('#library-outcomes').replaceChildren();
    }
  };
  $('#library-list').onchange = event=>{
    const input=event.target.closest('[data-library-id]');
    if (!input) return;
    if (input.checked) chosenRecords.add(input.dataset.libraryId); else chosenRecords.delete(input.dataset.libraryId);
    $('#library-outcomes').replaceChildren();
    $('#library-compare').textContent='对照已选试验的公布终点（'+[...chosenRecords].filter(id=>id.startsWith('NCT')).length+'）';
  };
  $('#library-compare').onclick=()=>comparePostedResults($('#library-outcomes'),
    readPublicLibrary().filter(item=>item.id.startsWith('NCT') && chosenRecords.has(item.id)));
  $('#library-brief').onsubmit=async event=>{
    event.preventDefault();
    const form=event.currentTarget, button=form.querySelector('button');
    const question=form.elements.question.value.trim();
    const entries=readPublicLibrary().filter(item=>chosenRecords.has(item.id));
    if (!question || !entries.length) {toast('请填写研究问题并勾选收藏记录。');return;}
    if (button.disabled) return;
    button.disabled=true;
    try {
      const run=libraryBrief(question,entries);
      await briefStore('readwrite',store=>store.add(run));
      if (form.isConnected) location.hash='#/brief?version='+encodeURIComponent(run.id);
    } catch (err) {toast('创建底稿失败：'+err.message,'err');}
    finally {button.disabled=false;}
  };
  renderLibrary();
  function show(data) {
    output.innerHTML = researchResults(data);
    bindTrialResultButtons(output);
    const filteredBrief=$('#filtered-brief',output);
    if (filteredBrief) filteredBrief.onclick=()=>{
      briefSeed={question:data.query,pubmed:data.effective_query,trials:data.asset_match?.name || data.query};nav('brief');
    };
    const follow = $('#follow-trials', output);
    if (follow) follow.onclick = async () => {
      const picked = new Set($$('.pick-record:checked', output).map(el => el.value));
      if (!picked.size) { toast('请先勾选试验。'); return; }
      try {
        for (const item of data.items.filter(item => picked.has(item.id))) await followTrial(item, data.retrieved);
        nav('watch');
      } catch (error) { toast(error.message, 'err'); }
    };
    const compare = $('#compare-trials', output);
    if (compare) compare.onclick = () => {
      const selected = new Set($$('.pick-record:checked', output).map(input => input.value));
      const panel = $('#trial-comparison', output);
      if (selected.size < 2 || selected.size > 4) {
        panel.textContent = '请选择 2 至 4 项试验。'; return;
      }
      const records = data.items.filter(item => selected.has(item.id));
      const fields = Object.keys(records[0].comparison);
      const cell = value => {
        const text = Array.isArray(value) ? value.join(' · ') : value;
        return text === '' || text == null ? '未提供' : String(text);
      };
      panel.innerHTML = `<h2>试验设计对照</h2><p>列中的人数为登记入组人数，主要终点为登记的测量计划。差异按登记原文呈现。</p>
        <div class="comparison-scroll" tabindex="0"><table><thead><tr><th scope="col">登记字段</th>
        ${records.map(item=>`<th scope="col"><a href="${esc(sourceLink(item))}" target="_blank" rel="noopener noreferrer">${esc(item.id)}</a></th>`).join('')}
        </tr></thead><tbody>${fields.map(field=>`<tr><th scope="row">${esc(field)}</th>
        ${records.map(item=>`<td>${esc(cell(item.comparison[field]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
        <button type="button" id="results-from-trials" class="btn">对照公布终点</button><div data-compared-results></div>
        <button type="button" id="design-from-trials" class="btn primary">用这组试验起草研究设计</button>`;
      $('#results-from-trials',panel).onclick=()=>comparePostedResults($('[data-compared-results]',panel),records);
      $('#design-from-trials', panel).onclick = () => {
        clinicalSeed = records.map(item => ({
          id:item.id, title:item.title, date:item.date,
          excerpts:fields.filter(field=>cell(item.comparison[field])!=='未提供').map(field=>({label:field,text:cell(item.comparison[field])})),
          snapshot:fields.map(field => field + '：' + cell(item.comparison[field])).join('\n'),
        }));
        nav('clinical');
      };
      panel.scrollIntoView({block:'nearest'});
    };
    const keep = $('#keep-records', output);
    if (keep) keep.onclick = () => {
      const picked = new Set($$('.pick-record:checked', output).map(input => input.value));
      if (!picked.size) { toast('请先勾选需要收藏的记录。'); return; }
      const entries = new Map(readPublicLibrary().map(item => [item.id, item]));
      for (const item of data.items.filter(item => picked.has(item.id))) {
        entries.set(item.id, {id:item.id, title:item.title, detail:item.detail, date:item.date, retrieved:data.retrieved,
          authors:item.authors || [], author_affiliations:item.author_affiliations || [],
          passages:item.passages || [], notices:item.notices || [], inputQuery:data.query, query:data.effective_query || data.query});
      }
      if (persist([...entries.values()])) toast('记录已加入本浏览器收藏。');
    };
    const save = $('#save-search', output);
    if (save) save.onclick = () => {
      const lines = [data.query, data.source, data.retrieved, '实际检索式 '+(data.effective_query || data.query), ...(data.asset_match?['名称来源 '+data.asset_match.source]:[]), '数据库命中 '+data.total+' · 取回 '+data.items.length, '', ...data.items.map(i =>
        [i.title, [i.id, i.date, i.detail].filter(Boolean).join(' · '), i.url, ...(i.notices || []), ''].join('\n'))];
      const url = URL.createObjectURL(new Blob([lines.join('\n')], {type:'text/plain;charset=utf-8'}));
      const link = document.createElement('a');
      link.href = url; link.download = 'HiJenny-search.txt'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
  }
  if (researchSession.response) show(researchSession.response);
  $('#research-form').onsubmit = async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button');
    const query = $('#research-query').value.trim();
    if (!query || button.disabled) return;
    if (!filters.disabled && yearFrom.value && yearTo.value && +yearFrom.value>+yearTo.value) {
      toast('起始年份应早于或等于结束年份。','err');return;
    }
    Object.assign(researchSession,{articleType:articleType.value,yearFrom:yearFrom.value,yearTo:yearTo.value});
    const payload={query,source:source.value};
    if (!filters.disabled) {
      if (!articleType.disabled) payload.article_type=articleType.value;
      if (yearFrom.value) payload.year_from=Number(yearFrom.value);
      if (yearTo.value) payload.year_to=Number(yearTo.value);
    }
    researchSession.query = query;
    researchSession.source = source.value;
    researchSession.response = null;
    button.disabled = true;
    output.textContent = '正在读取数据库…';
    try {
      const result = await api('/api/research/search', jsonOptions('POST', payload));
      if (!form.isConnected) return;
      result.retrieved = new Date().toISOString();
      researchSession.response = result;
      show(result);
    } catch (error) {
      output.textContent = error.message;
    } finally { button.disabled = false; }
  };
}
