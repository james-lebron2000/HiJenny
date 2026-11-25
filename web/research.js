'use strict';

const researchSession = { query: '', source: 'pubmed', response: null };

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

function researchResults(data) {
  if (!data.items.length) return '<p>没有找到记录。可尝试药物通用名、英文适应症或更短的检索式。</p>';
  return `<h2>${esc(data.query)}</h2><p>${data.source === 'guidelines' ? 'PubMed 指南文献' : (data.source === 'pubmed' ? 'PubMed' : 'ClinicalTrials.gov')} · 共 ${esc(data.total)} 条，展示 ${data.items.length} 条</p>
    ${data.source === 'guidelines' ? '<p>范围为 PubMed 标注的指南及实践指南。适用地区、更新状态和推荐级别请核对发布方原文。</p>' : ''}
    <button id="save-search" class="btn" type="button">下载检索记录</button>
    <ol class="research-results">${data.items.map(item => `<li><label><input type="checkbox" class="pick-record" value="${esc(item.id)}"> 选择</label> <a href="${esc(sourceLink(item))}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>
    <p>${esc([item.id, item.detail, item.date].filter(Boolean).join(' · '))}</p>
    ${data.source === 'guidelines' ? '<p>文献类型 · '+esc((item.publication_types || []).join(' / ') || '未提供')+'</p>' : ''}
    <details><summary>查看来源片段</summary>${item.passages.length ? item.passages.map(p =>
      `<p><strong>${esc(p.label)}</strong><br>${esc(p.text)}</p>`).join('') : '<p>该记录未提供摘要。</p>'}</details>
    ${data.source === 'trials' ? `<button type="button" class="btn" data-load-results="${esc(item.id)}">查看公布结果与安全性</button><section class="trial-posted" aria-live="polite"></section>` : ''}</li>`).join('')}</ol><button id="keep-records" type="button" class="btn">收藏勾选记录</button>
    ${data.source === 'trials' ? '<button id="follow-trials" class="btn" type="button">关注选中的试验</button><button id="compare-trials" type="button" class="btn">并排比较选中的试验</button><div id="trial-comparison"></div>' : ''}`;
}

function renderResearch() {
  setView(`<section class="research-home" lang="zh-CN"><h1>查文献与临床试验</h1>
    <p>输入公开药物名、适应症或检索式，查看数据库记录与原始来源。</p>
    <form id="research-form" class="card"><label for="research-query">检索内容</label>
    <input id="research-query" required maxlength="400" placeholder="例如 imatinib 或 NCT00115739" value="${esc(researchSession.query)}">
    <label for="research-source">数据来源</label><select id="research-source"><option value="pubmed">PubMed 文献</option>
    <option value="guidelines">PubMed 指南文献</option><option value="trials">ClinicalTrials.gov 临床试验</option></select>
    <p>点击检索后，仅检索词会发送至所选公共数据库。请使用公开信息。</p>
    <button class="btn primary" type="submit">检索公开记录</button></form>
    <button class="btn" type="button" id="make-brief">用这个主题整理多来源底稿 →</button>
    <div id="research-output" aria-live="polite"></div>
    <details id="public-library"><summary>我的公开资料收藏（本浏览器）</summary>
    <label>筛选收藏 <input id="library-filter" type="search"></label><ul id="library-list"></ul></details>
    <button data-nav="projects" class="btn" type="button">进入资料包尽调工作区 →</button></section>`);
  $('#make-brief').onclick = () => { briefSeed = $('#research-query').value.trim(); nav('brief'); };
  const source = $('#research-source');
  source.value = researchSession.source;
  const output = $('#research-output');
  function renderLibrary() {
    const filter = $('#library-filter').value.trim().toLowerCase();
    const list = $('#library-list');
    const entries = readPublicLibrary().filter(item =>
      [item.title, item.detail, item.query, item.id].join(' ').toLowerCase().includes(filter));
    list.innerHTML = entries.map(item => `<li><a href="${esc(sourceLink(item))}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>
      <button class="btn sm" type="button" data-remove="${esc(item.id)}">移除</button></li>`).join('') || '<li>暂无匹配记录</li>';
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
    if (id) persist(readPublicLibrary().filter(item => item.id !== id));
  };
  renderLibrary();
  function show(data) {
    output.innerHTML = researchResults(data);
    bindTrialResultButtons(output);
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
        <button type="button" id="design-from-trials" class="btn primary">用这组试验起草研究设计</button>`;
      $('#design-from-trials', panel).onclick = () => {
        clinicalSeed = records.map(item => ({
          id:item.id, title:item.title, date:item.date,
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
        entries.set(item.id, {id:item.id, title:item.title, detail:item.detail, query:data.query});
      }
      if (persist([...entries.values()])) toast('记录已加入本浏览器收藏。');
    };
    const save = $('#save-search', output);
    if (save) save.onclick = () => {
      const lines = [data.query, data.source, data.retrieved, '', ...data.items.map(i =>
        [i.title, [i.id, i.date, i.detail].filter(Boolean).join(' · '), i.url, ''].join('\n'))];
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
    researchSession.query = query;
    researchSession.source = source.value;
    researchSession.response = null;
    button.disabled = true;
    output.textContent = '正在读取数据库…';
    try {
      const result = await api('/api/research/search', jsonOptions('POST', {query, source:source.value}));
      if (!form.isConnected) return;
      result.retrieved = new Date().toISOString();
      researchSession.response = result;
      show(result);
    } catch (error) {
      output.textContent = error.message;
    } finally { button.disabled = false; }
  };
}
