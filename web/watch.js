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

function followTrial(item, checked, origin) {
  const key = watchKey(item.id);
  return navigator.locks.request(key, () => {
    const stored = localStorage.getItem(key);
    const row = stored ? JSON.parse(stored) : {
      id:item.id, title:item.title, updated:item.date, checked, fields:trialFields(item), changes:[]
    };
    if (origin) {
      row.origins ||= [];
      if (!row.origins.some(entry => entry.snapshot === origin.snapshot)) row.origins.push(origin);
    }
    localStorage.setItem(key, JSON.stringify(row));
  });
}

async function lookupWatchedTrial(id) {
  const result = await api('/api/research/search', jsonOptions('POST', {source:'trials',query:id}));
  const item = result.items.find(row => row.id === id);
  if (!item) throw new Error('登记来源未返回此编号。');
  return item;
}

async function refreshWatchedTrial(id) {
  const key = watchKey(id);
  return navigator.locks.request(key, async () => {
    const raw = localStorage.getItem(key);
    if (!raw) return;
    const saved = JSON.parse(raw);
    const item = await lookupWatchedTrial(id);
    const fields = trialFields(item);
    const changed = Object.keys(fields).filter(k => JSON.stringify(fields[k]) !== JSON.stringify(saved.fields[k]));
    const checked = new Date().toISOString();
    if (changed.length) saved.changes.unshift({checked, updated:item.date,
      fields:changed.map(name => ({name, before:saved.fields[name], after:fields[name]}))});
    Object.assign(saved, {fields, checked, title:item.title, updated:item.date});
    localStorage.setItem(key, JSON.stringify(saved));
  });
}

function milestoneDate(field) {
  const [date, kind] = String(field || '').split(' · ');
  if (!/^\d{4}-(0[1-9]|1[0-2])(-\d{2})?$/.test(date)) return null;
  const precise = date.length===10;
  if (precise) {
    const parsed = new Date(date+'T00:00:00Z');
    if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0,10)!==date) return null;
  }
  return {date,kind:kind || '类型未提供',precise};
}

function watchedMilestones(rows) {
  const events=[];
  for (const row of rows) {
    for (const [key,label] of [['primary','主要完成日期'],['completion','研究完成日期']]) {
      const when=milestoneDate(row.fields[label]);
      if (!when) continue;
      const history=row.changes.flatMap(c=>c.fields.filter(f=>f.name===label));
      const before=milestoneDate(history[0]?.before);
      // A month-only date and a day within that month are not evidence of a delay.
      const precision=before ? Math.min(before.date.length,when.date.length) : 0;
      const delayed=when.kind==='预计' && before?.kind==='预计' &&
        when.date.slice(0,precision)>before.date.slice(0,precision);
      events.push({...when,id:row.id,title:row.title,key,label,updated:row.updated,
        revision:history.length,status:delayed?'预计延期':when.kind,previous:before?.date});
    }
  }
  return events.sort((a,b)=>a.date.localeCompare(b.date) || a.id.localeCompare(b.id) || a.key.localeCompare(b.key));
}

const watchReminderOptions = [1,7,30];

function watchReminderTime(event, days, now) {
  if (!watchReminderOptions.includes(days) || !event.precise || event.kind !== '预计') return null;
  const [year,month,day] = event.date.split('-').map(Number);
  const reminder = new Date(year,month-1,day-days,9);
  return reminder > now ? reminder : null;
}

function watchCalendar(events, now=new Date(), reminderDays=0) {
  const text=value=>String(value).replace(/\\/g,'\\\\').replace(/\r\n|\r|\n/g,'\\n')
    .replace(/,/g,'\\,').replace(/;/g,'\\;').replace(/[\x00-\x08\x0b-\x1f\x7f]/g,'');
  const stamp=date=>date.toISOString().replace(/[-:]/g,'').replace(/\.\d{3}/,'');
  const lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//HiJenny//Trial Watch//ZH'];
  for (const e of events.filter(e=>e.precise)) {
    lines.push('BEGIN:VEVENT','UID:'+e.id+'-'+e.key+'@hijenny','DTSTAMP:'+stamp(now),'SEQUENCE:'+e.revision,
      'DTSTART;VALUE=DATE:'+e.date.replaceAll('-',''),'TRANSP:TRANSPARENT',
      'SUMMARY:'+text(e.status+' · '+e.label+' · '+e.id),
      'DESCRIPTION:'+text(e.title+'\n登记更新 '+(e.updated || '未提供')+(e.previous?'\n上次登记日期 '+e.previous:'')),
      'URL:https://clinicaltrials.gov/study/'+e.id);
    const reminder=watchReminderTime(e,reminderDays,now);
    if (reminder) lines.push('BEGIN:VALARM','ACTION:DISPLAY','TRIGGER;VALUE=DATE-TIME:'+stamp(reminder),
      'DESCRIPTION:'+text(e.id+' · '+e.label+' · '+e.date),'END:VALARM');
    lines.push('END:VEVENT');
  }
  lines.push('END:VCALENDAR');
  const encoder=new TextEncoder();
  return lines.map(line=>{
    let folded='', bytes=0;
    for (const ch of line) {
      const size=encoder.encode(ch).length;
      if (bytes+size>75) {folded+='\r\n ';bytes=1;}
      folded+=ch;bytes+=size;
    }
    return folded;
  }).join('\r\n')+'\r\n';
}

function renderWatch() {
  setView(`<section id="trial-watch" class="research-home"><h1>试验关注列表</h1>
    <p>查看已关注试验的预计与实际完成日期。点击更新后，从 ClinicalTrials.gov 核对登记变化。</p>
    <button class="btn" data-nav="public-events">查看公开资产动态</button>
    <button class="btn" data-nav="research">查找并关注试验</button>
    <button class="btn primary" id="update-watch">更新全部</button>
    <p id="watch-status" role="status"></p><section id="watch-calendar"></section><div id="watch-entries"></div></section>`);
  const root = $('#trial-watch');
  const entries = $('#watch-entries',root);
  const status = $('#watch-status',root);
  const refresh = $('#update-watch',root);
  const display = value => Array.isArray(value) ? value.join(' · ') || '未提供' : String(value ?? '') || '未提供';
  let running = false;
  let reminderDays = 0;
  function paint() {
    const rows = Object.keys(localStorage).filter(k => k.startsWith(watchPrefix))
      .map(k => JSON.parse(localStorage.getItem(k))).sort((a,b)=>a.id.localeCompare(b.id));
    refresh.disabled = running || !rows.length;
    const events=watchedMilestones(rows);
    const calendar=$('#watch-calendar',root);
    calendar.innerHTML=events.length?`<h2>试验里程碑日历</h2>
      <p>按登记日期排序。日期精确到日的项目可导入日历；仅披露月份的项目在此保留月份。</p>
      <ol>${events.map(e=>`<li><time datetime="${esc(e.date)}">${esc(e.date)}</time> · ${esc(e.status)} · ${esc(e.label)}
        <a href="${esc(sourceLink(e))}" target="_blank" rel="noopener noreferrer">${esc(e.id)} · ${esc(e.title)}</a>
        ${e.status==='预计延期'?'<small>上次预计 '+esc(e.previous)+'</small>':''}</li>`).join('')}</ol>
      <label>预计里程碑的日历提醒<select id="watch-reminder">
        ${option('0',String(reminderDays),'不添加提醒')}${watchReminderOptions.map(days=>option(String(days),String(reminderDays),'提前 '+days+' 天')).join('')}</select></label>
      <p id="watch-reminder-summary" role="status"></p>
      <button class="btn" id="watch-export" ${running || !events.some(e=>e.precise)?'disabled':''}>下载日历（${events.filter(e=>e.precise).length} 个全天事件）</button>`:'';
    const download=$('#watch-export',root);
    const select=$('#watch-reminder',root);
    if (select) {
      const describe=()=>{
        reminderDays=Number(select.value);
        const now=new Date();
        const count=events.filter(e=>watchReminderTime(e,reminderDays,now)).length;
        $('#watch-reminder-summary',root).textContent=reminderDays ?
          `导入日历后，由日历应用按其通知设置提醒。文件包含 ${count} 个提醒，时间为本地上午 9 点（${Intl.DateTimeFormat().resolvedOptions().timeZone}）。已过提醒时间的项目会跳过。` : '';
      };
      select.onchange=describe;describe();
    }
    if (download) download.onclick=()=>{
      const url=URL.createObjectURL(new Blob([watchCalendar(events,new Date(),reminderDays)],{type:'text/calendar;charset=utf-8'}));
      const a=document.createElement('a');a.href=url;a.download='HiJenny-试验里程碑.ics';a.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
    entries.innerHTML = rows.map(row => `<article class="card"><h2>${esc(row.title)}</h2>
      <a href="${esc(sourceLink(row))}" target="_blank" rel="noopener noreferrer">${esc(row.id)} · 登记原文</a>
      <p>${esc(row.fields['登记状态'])} · 登记更新 ${esc(row.updated || '未提供')} · 本机核查 ${esc(fmtDate(row.checked))}</p>
      ${(row.origins || []).map(origin => origin.type==='public-event'
        ? `<p>关注来源 · <a href="#/public-events?event=${encodeURIComponent(origin.snapshot)}">${esc(origin.field)} · ${esc(origin.value)}</a>
          · 来源披露 ${esc(origin.disclosed)} · 首次发现 ${esc(fmtDate(origin.created))}</p>`
        : `<p>候选来源 · <a href="#/discovery?snapshot=${encodeURIComponent(origin.snapshot)}">${esc(origin.condition)} · ${esc(fmtDate(origin.created))} 筛选快照</a></p>`).join('')}
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
