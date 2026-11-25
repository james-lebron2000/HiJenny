'use strict';

const watchPrefix = 'hijenny.watch.';
function watchKey(id) {
  if (!/^NCT[0-9]{8}$/.test(id)) throw new Error('登记编号格式有误。');
  return watchPrefix + id;
}

function trialFields(item) {
  const fields = {'登记状态': item.detail, ...item.comparison};
  for (const [label, value] of Object.entries(item.milestones || {})) {
    const type = {ACTUAL:'实际', ESTIMATED:'预计'}[value.type] || value.type;
    fields[label] = [value.date, type].filter(Boolean).join(' · ');
  }
  return fields;
}

function followTrial(item, checked) {
  const key = watchKey(item.id);
  return navigator.locks.request(key, () => {
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({
      id:item.id, title:item.title, updated:item.date, checked, fields:trialFields(item), changes:[]
    }));
  });
}

async function refreshWatchedTrial(id) {
  const key = watchKey(id);
  return navigator.locks.request(key, async () => {
    const raw = localStorage.getItem(key);
    if (!raw) return;
    const saved = JSON.parse(raw);
    const result = await api('/api/research/search', jsonOptions('POST', {source:'trials',query:id}));
    const item = result.items.find(row => row.id === id);
    if (!item) throw new Error('登记来源未返回此编号，已保留上次快照。');
    const fields = trialFields(item);
    const changed = Object.keys(fields).filter(k => JSON.stringify(fields[k]) !== JSON.stringify(saved.fields[k]));
    const checked = new Date().toISOString();
    if (changed.length) saved.changes.unshift({checked, updated:item.date,
      fields:changed.map(name => ({name, before:saved.fields[name], after:fields[name]}))});
    Object.assign(saved, {fields, checked, title:item.title, updated:item.date});
    localStorage.setItem(key, JSON.stringify(saved));
  });
}

function renderWatch() {
  setView(`<section id="trial-watch" class="research-home"><h1>试验关注列表</h1>
    <p>查看已关注试验的预计与实际完成日期。点击更新后，从 ClinicalTrials.gov 核对登记变化。</p>
    <button class="btn" data-nav="research">查找并关注试验</button>
    <button class="btn primary" id="update-watch">更新全部</button>
    <p id="watch-status" role="status"></p><div id="watch-entries"></div></section>`);
  const root = $('#trial-watch');
  const entries = $('#watch-entries',root);
  const status = $('#watch-status',root);
  const refresh = $('#update-watch',root);
  const display = value => Array.isArray(value) ? value.join(' · ') || '未提供' : String(value ?? '') || '未提供';
  let running = false;
  function paint() {
    const rows = Object.keys(localStorage).filter(k => k.startsWith(watchPrefix))
      .map(k => JSON.parse(localStorage.getItem(k))).sort((a,b)=>a.id.localeCompare(b.id));
    refresh.disabled = running || !rows.length;
    entries.innerHTML = rows.map(row => `<article class="card"><h2>${esc(row.title)}</h2>
      <a href="${esc(sourceLink(row))}" target="_blank" rel="noopener noreferrer">${esc(row.id)} · 登记原文</a>
      <p>${esc(row.fields['登记状态'])} · 登记更新 ${esc(row.updated || '未提供')} · 本机核查 ${esc(fmtDate(row.checked))}</p>
      <dl>${['主要完成日期','研究完成日期'].map(k=>`<dt>${k}</dt><dd>${esc(display(row.fields[k]))}</dd>`).join('')}</dl>
      <details><summary>登记变更（${row.changes.length}）</summary>${row.changes.map(c=>`<p>${esc(fmtDate(c.checked))} · 登记版本 ${esc(c.updated)}</p>
      <ul>${c.fields.map(f=>`<li>${esc(f.name)}：${esc(display(f.before))} → ${esc(display(f.after))}</li>`).join('')}</ul>`).join('') || '尚无已观察到的字段变化。'}</details>
      <button class="btn" data-unfollow="${esc(row.id)}" ${running?'disabled':''}>移除关注</button></article>`).join('') || '<p>关注列表为空。可在临床试验检索结果中勾选记录，加入关注。</p>';
    return rows;
  }
  refresh.onclick = async () => {
    if (running) return;
    running = true;
    const rows = paint();
    let done = 0;
    const failures = [];
    for (const row of rows) {
      if (!root.isConnected) break;
      status.textContent = '正在核查 ' + row.id;
      try { await refreshWatchedTrial(row.id); done++; } catch (error) { failures.push(row.id + ' ' + error.message); }
    }
    running = false;
    status.textContent = '已核查 ' + done + ' 项。' + failures.join('；');
    paint();
  };
  entries.onclick = async event => {
    const id = event.target.closest('[data-unfollow]')?.dataset.unfollow;
    if (!id || !confirm('移除该试验及本浏览器中对应的变更历史？')) return;
    const key = watchKey(id);
    await navigator.locks.request(key, () => localStorage.removeItem(key));
    paint();
  };
  try { paint(); } catch (error) { status.textContent = '无法读取本机关注记录：' + error.message; }
}
