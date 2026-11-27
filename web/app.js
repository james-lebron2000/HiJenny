/* ==========================================================================
   HiJenny — review UI (vanilla JS, no build step)
   Views: Projects | Upload & Analysis | Evidence Workspace | Findings & Questions | Report
   ========================================================================== */
'use strict';

/* ------------------------------------------------------------- constants */
const STAGES = ['INGEST', 'EXTRACT', 'VALIDATE', 'CONSISTENCY', 'GAP', 'QUESTIONS', 'REPORT'];
const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'];
const PRIORITIES = ['critical', 'high', 'medium', 'low'];
const FINDING_STATUSES = ['open', 'needs_review', 'confirmed', 'dismissed', 'resolved'];
const ACTIVE_FINDING_STATUSES = ['open', 'confirmed', 'needs_review'];
const QUESTION_STATUSES = ['draft', 'reviewed', 'approved', 'sent', 'answered', 'closed', 'dismissed'];
const REVIEW_STATUSES = ['pending', 'approved', 'edited', 'rejected'];
const OWNERS = [
  'Clinical Development', 'Safety / Pharmacovigilance', 'Biostatistics', 'Regulatory Affairs',
  'CMC / Manufacturing', 'IP / Legal', 'Commercial', 'Legal', 'Business Development',
];
const DOC_STATUS_CLASS = {
  uploaded: 'grey', parsing: 'amber', parsed: 'green', failed: 'red', superseded: 'purple',
};
const PROJECT_STATUS_CLASS = {
  CREATED: 'grey', UPLOADING: 'blue', ANALYZING: 'amber', REPORT_READY: 'green', FAILED: 'red',
};
const VERIFICATION_CLASS = {
  verified: 'green', pending: 'amber', failed: 'red', unverifiable: 'grey',
};

const state = {
  view: 'projects',
  projectId: null,
  project: null,
  projects: [],
  documents: [],
  evidenceAll: [],
  findings: [],
  questions: [],
  coverage: [],
  reviewEvents: [],
  showEvents: false,
  stats: null,
  outputs: null,
  run: null,
  runFull: null,
  runId: null,
  pollTimer: null,
  renderSeq: 0,
  uploadQueue: [],
  selectedEvidenceId: null,
  previewDocId: null,
  previewPage: 1,
  previewMode: 'pdf',
  pageData: null,
  pendingFocusEvidence: null,
  showCreateForm: false,
  reportMd: null,
  reportUrl: null,
  apiOnline: false,
  health: null,
  llm: null,
  filters: {
    evidence: { q: '', field_name: '', category: '', document_id: '', verification_status: '' },
    findings: { severity: '', status: '', finding_type: '' },
    questions: { priority: '', status: '', owner: '' },
  },
};

/* ------------------------------------------------------------------ utils */
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const normWs = (text) => String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
const debounce = (fn, ms) => {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
};

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function toast(message, kind) {
  const box = $('#toasts');
  if (!box) return;
  const el = document.createElement('div');
  el.className = `toast ${kind || ''}`;
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; }, 4200);
  setTimeout(() => el.remove(), 4600);
}

async function api(path, options) {
  const opts = Object.assign({ headers: { Accept: 'application/json' } }, options || {});
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (err) { data = null; }
  if (!res.ok) {
    let message = res.statusText || `HTTP ${res.status}`;
    if (data && data.detail) {
      if (typeof data.detail === 'string') message = data.detail;
      else if (data.detail.message) message = data.detail.message;
      else message = JSON.stringify(data.detail).slice(0, 400);
    }
    const error = new Error(message);
    error.status = res.status;
    error.data = data;
    throw error;
  }
  return data;
}

function pilotSessionId() {
  const key='hijenny.pilot.session';
  const id=sessionStorage.getItem(key) || crypto.randomUUID();
  sessionStorage.setItem(key,id);
  return id;
}

function jsonOptions(method, body) {
  return { method, headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body || {}) };
}

function chip(text, cls) {
  return `<span class="chip ${cls || 'grey'}">${esc(text)}</span>`;
}

function isActiveFinding(finding) {
  return ACTIVE_FINDING_STATUSES.includes(String((finding && finding.status) || 'open').toLowerCase());
}

function severityChip(sev) {
  return chip(sev || 'info', sev === 'critical' ? 'red' : sev === 'high' ? 'orange' : sev === 'medium' ? 'amber' : sev === 'low' ? 'green' : 'grey');
}

function priorityChip(prio) {
  return chip(prio || 'medium', prio === 'critical' ? 'red' : prio === 'high' ? 'orange' : prio === 'medium' ? 'amber' : 'green');
}

function statusChip(text) {
  const key = String(text || '').toLowerCase();
  return chip(text || '—', VERIFICATION_CLASS[key] || DOC_STATUS_CLASS[key] || 'grey');
}

function projectStatusChip(status) {
  return chip(status || 'CREATED', PROJECT_STATUS_CLASS[status] || 'grey');
}

function evidenceValue(ev) {
  if (ev.value_num != null) {
    const n = Number(ev.value_num);
    const num = Number.isFinite(n) ? String(parseFloat(n.toFixed(6))) : String(ev.value_num);
    if (ev.unit === '%') return `${num}%`;
    return ev.unit ? `${num} ${ev.unit}` : num;
  }
  return ev.value_text || '—';
}

function option(value, selected, label) {
  const text = label == null ? value : label;
  return `<option value="${esc(value)}"${String(selected || '') === String(value) ? ' selected' : ''}>${esc(text)}</option>`;
}

function selectOptions(values, selected, emptyLabel) {
  return (emptyLabel != null ? `<option value="">${esc(emptyLabel)}</option>` : '') +
    values.map((v) => option(v, selected, v)).join('');
}

function relativeFileUrl(path) {
  return '/api/files/' + String(path).split('/').map(encodeURIComponent).join('/');
}

/* ------------------------------------------------------------- navigation */
function currentView() {
  const hash = (location.hash || '').replace(/^#\/?/, '');
  const view = hash.split('?')[0];
  if (!view) return 'home';
  return ['home', 'pilot', 'discovery', 'research', 'brief', 'public-events', 'watch', 'clinical', 'medical', 'study', 'valuation', 'projects', 'overview', 'upload', 'evidence', 'findings', 'followup', 'report'].includes(view) ? view : 'projects';
}

function hashParams() {
  const raw = location.hash || '';
  const idx = raw.indexOf('?');
  return new URLSearchParams(idx >= 0 ? raw.slice(idx + 1) : '');
}

function nav(view) {
  const project = hashParams().get('project') || state.projectId;
  // Leaving a deep-linked document view resets its transient evidence filter.
  state.filters.evidence.document_id = '';
  if (currentView() === view) { renderView(); return; }
  location.hash = `#/${view}${project ? '?project=' + encodeURIComponent(project) : ''}`;
}

function setView(html) {
  const view = $('#view');
  if (view) view.innerHTML = html;
}

function loadingBlock(message) {
  return `<div class="loading-block"><div class="spinner"></div>${esc(message || 'Loading…')}</div>`;
}

function errorPanel(error, retryLabel) {
  return `
    <div class="card"><div class="empty">
      <div class="icon">⚠️</div>
      <h3>Something went wrong</h3>
      <p>${esc(error && error.message ? error.message : String(error))}</p>
      <button class="btn" data-action="refresh-view">${esc(retryLabel || 'Retry')}</button>
    </div></div>`;
}

function emptyPanel(title, message, actionHtml) {
  return `<div class="empty"><div class="icon">▤</div><h3>${esc(title)}</h3><p>${esc(message)}</p>${actionHtml || ''}</div>`;
}

function statStrip(items) {
  return `<div class="stat-strip">${items.map((it) => `
    <div class="stat"><div class="k">${esc(it.k)}</div><div class="v">${it.html != null ? it.html : esc(it.v)}</div></div>`).join('')}</div>`;
}

/* ---------------------------------------------------------------- topbar */
function updateTopbar() {
  const titles = {
    home: ['HiJenny Agent', ''],
    study: ['IIT / RWE', '研究概念书与数据可行性'],
    medical: ['医学交流', '专家会与证据答复'],
    clinical: ['临床设计', '研究方案比较与评审'],
    'public-events': ['公开资产动态', '公司申报与临床登记变化'],
    watch: ['试验关注', '登记日期与变更记录'],
    brief: ['研究底稿', '多来源检索与证据复核'],
    pilot: ['单资产付费试点', '首轮临床尽调报价申请'],
    discovery: ['机会筛选', '公开管线与候选资产'],
    research: ['HiJenny Agent', '公开医药证据检索'],
    projects: ['Projects', 'Due-diligence packages'],
    valuation: ['交易估值', '买方情景与假设'],
    overview: ['Asset 360', '药物资产发现与资产评估 · Discovery & Evaluation'],
    upload: ['Upload & Analysis', 'Documents, parsing and pipeline runs'],
    evidence: ['Evidence Workspace', 'Every number traced to document, page and quote'],
    followup: ['BD 复核', '可比资产、卖方答复与验证判断'],
    findings: ['Findings & Questions', 'Consistency, gaps, seller questions and review'],
    report: ['Report', 'Generated due-diligence report and exports'],
  };
  const [title, sub] = titles[currentView()] || titles.projects;
  const titleEl = $('#topbar-title');
  const subEl = $('#topbar-sub');
  if (titleEl) titleEl.textContent = title;
  if (subEl) subEl.textContent = sub;

  const publicView = ['home', 'pilot', 'discovery', 'research', 'brief', 'public-events', 'watch', 'clinical', 'medical', 'study'].includes(currentView());
  for (const el of $$('.project-select, #project-status, #btn-analyze-topbar')) el.hidden = publicView;
  $('#mobile-nav').value = currentView();
  const select = $('#project-select');
  if (select) {
    const current = state.projectId || '';
    const options = state.projects.map((p) => option(p.project_id, current, `${p.name}${p.asset_name ? ' · ' + p.asset_name : ''}`));
    select.innerHTML = `<option value="">— select project —</option>` + options.join('');
    if (!state.projects.length) select.innerHTML = '<option value="">— no projects —</option>';
  }

  const statusEl = $('#project-status');
  if (statusEl) {
    const project = state.projects.find((p) => p.project_id === state.projectId);
    statusEl.innerHTML = project ? projectStatusChip(project.status) : '';
  }
  const analyzeBtn = $('#btn-analyze-topbar');
  if (analyzeBtn) analyzeBtn.disabled = !state.projectId || Boolean(state.run && state.run.status === 'running');
}

function updateSidebarBadges() {
  const s = state.stats || {};
  const counts = {
    'badge-projects': state.projects.length || 0,
    'badge-docs': state.documents.length || s.documents || 0,
    'badge-evidence': state.evidenceAll.length || s.evidence || 0,
    'badge-findings': state.findings.length
      ? state.findings.filter(isActiveFinding).length
      : s.findings || 0,
  };
  Object.entries(counts).forEach(([id, value]) => {
    const el = document.getElementById(id);
    if (el) el.textContent = String(value);
  });
  const reportBadge = $('#badge-report');
  if (reportBadge) {
    const ready = state.outputs && state.outputs.report_md && state.outputs.report_md.exists;
    reportBadge.textContent = ready ? '✓' : '—';
  }
}

/* ---------------------------------------------------------------- router */
async function renderView() {
  state.view = currentView();
  // Deep-link controls (used by the demo kit and shareable review links):
  //   #/evidence?project=<id>&doc=<document_id>&page=11&preview=text&evidence=<evidence_id>
  const params = hashParams();
  if (params.get('preview') === 'text') state.previewMode = 'text';
  if (state.view === 'evidence') {
    const doc = params.get('doc');
    if (doc) {
      state.previewDocId = doc;
      state.previewPage = Number(params.get('page') || 1) || 1;
      // Focus the evidence list on the linked document (shareable review link).
      state.filters.evidence.document_id = doc;
    }
    if (params.get('evidence')) state.pendingFocusEvidence = params.get('evidence');
  }
  $$('.nav-item[data-nav]').forEach((btn) => btn.classList.toggle('active', btn.dataset.nav === state.view));
  const seq = ++state.renderSeq;
  updateTopbar();

  if (state.view === 'home') { renderHome(); return; }
  if (state.view === 'pilot') { renderPilot(); return; }
  if (state.view === 'discovery') { renderDiscovery(); return; }
  if (state.view === 'study') { renderStudy(); return; }
  if (state.view === 'medical') { await renderMedical(); return; }
  if (state.view === 'clinical') { renderClinical(); return; }
  if (state.view === 'public-events') { renderPublicEvents(); return; }
  if (state.view === 'watch') { renderWatch(); return; }
  if (state.view === 'brief') { await renderBrief(); return; }
  if (state.view === 'research') { renderResearch(); return; }
  if (state.view === 'projects') { await renderProjects(seq); return; }
  if (state.view === 'overview') { await renderOverview(seq); return; }
  if (!state.projectId) {
    setView(`<div class="card">${emptyPanel('No project selected', 'Create or select a project to continue.', '<button class="btn primary" data-nav="projects">Go to Projects</button>')}</div>`);
    return;
  }
  try {
    if (state.view === 'valuation') await renderValuation(seq);
    else if (state.view === 'upload') await renderUpload(seq);
    else if (state.view === 'evidence') await renderEvidence(seq);
    else if (state.view === 'findings') await renderFindings(seq);
    else if (state.view === 'followup') await renderFollowup(seq);
    else if (state.view === 'report') await renderReport(seq);
  } catch (error) {
    if (seq === state.renderSeq) setView(errorPanel(error));
  }
}

async function ensureProjectLoaded() {
  if (!state.projectId) return null;
  if (state.project && state.project.project_id === state.projectId && state.stats) return state.project;
  const data = await api(`/api/projects/${state.projectId}`);
  state.project = data.project;
  state.stats = data.stats;
  state.outputs = data.outputs || null;
  state.projects = state.projects.map((p) => (p.project_id === data.project.project_id ? Object.assign({}, p, data.project) : p));
  updateTopbar();
  updateSidebarBadges();
  return state.project;
}

/* -------------------------------------------------------------- projects */
async function renderProjects(seq) {
  setView(loadingBlock('Loading projects…'));
  try {
    const data = await api('/api/projects');
    if (seq !== state.renderSeq) return;
    state.projects = data.projects || [];
    if (!state.projects.some((p) => p.project_id === state.projectId)) {
      state.projectId = state.projects.length ? state.projects[0].project_id : null;
      if (state.projectId) localStorage.setItem('hijenny.project', state.projectId);
    }
    updateTopbar();
    updateSidebarBadges();
    const agg = state.projects.reduce((acc, p) => {
      const s = p.stats || {};
      acc.docs += s.documents || 0;
      acc.pages += s.pages || 0;
      acc.evidence += s.evidence || 0;
      acc.findings += s.findings || 0;
      acc.questions += s.questions || 0;
      return acc;
    }, { docs: 0, pages: 0, evidence: 0, findings: 0, questions: 0 });

    const createForm = state.showCreateForm ? `
      <div class="card">
        <div class="card-head"><h2>New due-diligence project</h2><span class="sub">One project per asset / package</span></div>
        <div class="card-body">
          <form data-form="create-project">
            <div class="form-grid">
              <div class="field"><label for="np-name">Project name *</label><input id="np-name" name="name" required minlength="1" placeholder="e.g. ABC-101 NSCLC Phase 2" /></div>
              <div class="field"><label for="np-asset">Asset</label><input id="np-asset" name="asset_name" placeholder="e.g. ABC-101" /></div>
              <div class="field"><label for="np-indication">Indication</label><input id="np-indication" name="indication" placeholder="e.g. NSCLC" /></div>
              <div class="field"><label for="np-phase">Phase</label><input id="np-phase" name="phase" placeholder="e.g. Phase 2" /></div>
            </div>
            <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
              <button class="btn ghost" type="button" data-action="toggle-create-project">Cancel</button>
              <button class="btn primary" type="submit">Create project</button>
            </div>
          </form>
        </div>
      </div>` : '';

    const rows = state.projects.map((p) => {
      const s = p.stats || {};
      const outputs = p.outputs || {};
      const ready = outputs.report_md && outputs.report_md.exists;
      return `<tr>
        <td class="title-cell">${esc(p.name)}</td>
        <td>${esc(p.asset_name || '—')}</td>
        <td>${esc(p.indication || '—')}</td>
        <td>${esc(p.phase || '—')}</td>
        <td>${projectStatusChip(p.status)}</td>
        <td class="num">${s.documents || 0}</td>
        <td class="num">${s.evidence || 0}</td>
        <td class="num">${s.findings || 0}</td>
        <td class="num">${s.questions || 0}</td>
        <td>${ready ? chip('report ready', 'green') : chip('no report', 'grey')}</td>
        <td class="muted">${esc(fmtDate(p.updated_at || p.created_at))}</td>
        <td><button class="btn sm primary" data-action="open-project" data-id="${esc(p.project_id)}">Open →</button></td>
      </tr>`;
    }).join('');

    setView(`
      <div class="page-head">
        <div><h1>Projects</h1><div class="sub">Each project holds one due-diligence package, its evidence chain and review state.</div></div>
        <div class="actions">
          <button class="btn" data-action="refresh-view">Refresh</button>
          <button class="btn primary" data-action="toggle-create-project">+ New project</button>
        </div>
      </div>
      ${statStrip([
        { k: 'Projects', v: state.projects.length },
        { k: 'Documents', v: agg.docs },
        { k: 'Pages parsed', v: agg.pages },
        { k: 'Evidence items', v: agg.evidence },
        { k: 'Findings', v: agg.findings },
        { k: 'Seller questions', v: agg.questions },
      ])}
      ${createForm}
      <div class="card">
        <div class="card-head"><h2>All projects</h2><span class="sub">${state.projects.length} project(s)</span></div>
        ${state.projects.length ? `
        <div class="table-wrap"><table class="data">
          <thead><tr>
            <th>Project</th><th>Asset</th><th>Indication</th><th>Phase</th><th>Status</th>
            <th class="num">Docs</th><th class="num">Evidence</th><th class="num">Findings</th><th class="num">Questions</th>
            <th>Report</th><th>Updated</th><th></th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table></div>` : emptyPanel('No projects yet', 'Create a project, upload the due-diligence package, then run the analysis pipeline.',
          '<button class="btn primary" data-action="toggle-create-project">+ Create the first project</button>')}
      </div>`);
  } catch (error) {
    if (seq === state.renderSeq) setView(errorPanel(error));
  }
}

/* --------------------------------------------------------------- overview */
const OV_COLORS = {
  teal: '#1e7d4f', blue: '#2e75b6', amber: '#9a6700', red: '#b3261e',
  grey: '#8a97a8', navy: '#1f3864', line: '#dbe2ec',
};
const OV_SEVERITY = { critical: OV_COLORS.red, high: OV_COLORS.amber, medium: OV_COLORS.blue, low: OV_COLORS.grey, info: '#b8c4d2' };

function ovColor(score) {
  const s = Number(score || 0);
  if (s >= 80) return OV_COLORS.teal;
  if (s >= 65) return OV_COLORS.blue;
  if (s >= 50) return OV_COLORS.amber;
  return OV_COLORS.red;
}

function ovRing(score, size) {
  const s = clamp(Number(score || 0), 0, 100);
  const dim = size || 170;
  const stroke = 14;
  const r = (dim - stroke) / 2;
  const c = 2 * Math.PI * r;
  const dash = (s / 100) * c;
  return `<svg class="ov-ring" width="${dim}" height="${dim}" viewBox="0 0 ${dim} ${dim}" role="img" aria-label="Diligence readiness ${Math.round(s)}">
    <circle cx="${dim / 2}" cy="${dim / 2}" r="${r}" fill="none" stroke="var(--border)" stroke-width="${stroke}"></circle>
    <circle cx="${dim / 2}" cy="${dim / 2}" r="${r}" fill="none" stroke="${ovColor(s)}" stroke-width="${stroke}"
      stroke-linecap="round" stroke-dasharray="${dash} ${c - dash}"
      transform="rotate(-90 ${dim / 2} ${dim / 2})"></circle>
    <text x="${dim / 2}" y="${dim / 2 + 2}" text-anchor="middle" dominant-baseline="middle"
      font-size="36" font-weight="750" fill="${ovColor(s)}">${Math.round(s)}</text>
    <text x="${dim / 2}" y="${dim / 2 + 28}" text-anchor="middle" font-size="10" fill="var(--muted)">/100</text>
  </svg>`;
}

function ovDonut(segments, size) {
  const dim = size || 168;
  const stroke = 22;
  const r = (dim - stroke) / 2;
  const c = 2 * Math.PI * r;
  const total = segments.reduce((sum, seg) => sum + Number(seg.value || 0), 0) || 1;
  let offset = 0;
  const circles = segments.filter((seg) => Number(seg.value) > 0).map((seg) => {
    const value = Number(seg.value || 0);
    const dash = (value / total) * c;
    const el = `<circle class="chart-slice" cx="${dim / 2}" cy="${dim / 2}" r="${r}" fill="none"
      stroke="${seg.color}" stroke-width="${stroke}"
      stroke-dasharray="${dash} ${c - dash}" stroke-dashoffset="${-offset}"
      transform="rotate(-90 ${dim / 2} ${dim / 2})"
      ${seg.key ? `data-action="overview-filter" data-severity="${esc(seg.key)}"` : ''}>
      <title>${esc(seg.label)}: ${value}</title></circle>`;
    offset += dash;
    return el;
  }).join('');
  return `<svg class="ov-donut" width="${dim}" height="${dim}" viewBox="0 0 ${dim} ${dim}" role="img">
    <circle cx="${dim / 2}" cy="${dim / 2}" r="${r}" fill="none" stroke="var(--border)" stroke-width="${stroke}"></circle>
    ${circles}
    <text x="${dim / 2}" y="${dim / 2 - 2}" text-anchor="middle" dominant-baseline="middle"
      font-size="30" font-weight="700" fill="var(--navy)">${total}</text>
    <text x="${dim / 2}" y="${dim / 2 + 22}" text-anchor="middle" font-size="9" fill="var(--muted)">findings</text>
  </svg>`;
}

function ovBars(items, unitLabel) {
  const maxValue = Math.max(1, ...items.map((item) => Number(item.value || 0)));
  return items.map((item) => {
    const pct = Math.max(1, Math.round((Number(item.value || 0) / maxValue) * 100));
    return `<div class="ov-bar-row">
      <div class="ov-bar-label">${esc(item.label)}</div>
      <div class="ov-bar-track"><div class="ov-bar-fill" style="width:${pct}%;background:${item.color || OV_COLORS.blue}"></div></div>
      <div class="ov-bar-value">${Number(item.value || 0).toLocaleString()}${unitLabel ? ' ' + esc(unitLabel) : ''}</div>
    </div>`;
  }).join('');
}

function ovStack(segments) {
  const total = segments.reduce((sum, seg) => sum + Number(seg.value || 0), 0) || 1;
  const bars = segments.filter((seg) => Number(seg.value) > 0).map((seg) => {
    const pct = (Number(seg.value) / total) * 100;
    return `<div class="ov-stack-seg" style="width:${pct}%;background:${seg.color}"
      title="${esc(seg.label)}: ${seg.value} (${pct.toFixed(1)}%)"></div>`;
  }).join('');
  const legend = segments.map((seg) => `<span class="ov-legend-item"><span class="ov-dot" style="background:${seg.color}"></span>${esc(seg.label)} <strong>${seg.value}</strong></span>`).join('');
  return `<div class="ov-stack">${bars}</div><div class="ov-legend">${legend}</div>`;
}

function ovHref(item, projectId) {
  if (!item || !item.evidence_id) return '#';
  const params = new URLSearchParams({ project: projectId, preview: 'text', evidence: item.evidence_id });
  if (item.document_id) params.set('doc', item.document_id);
  if (item.page) params.set('page', item.page);
  return `#/evidence?${params.toString()}`;
}

function ovCard(titleZh, titleEn, body, className) {
  return `<section class="ov-card ${className || ''}">
    <div class="ov-card-head"><h3>${esc(titleZh)}<span>${esc(titleEn)}</span></h3></div>
    ${body}
  </section>`;
}

function ovKpi(label, value, sub, tone) {
  return `<div class="ov-kpi ${tone || ''}">
    <div class="ov-kpi-value">${value}</div>
    <div class="ov-kpi-label">${esc(label)}</div>
    <div class="ov-kpi-sub">${sub || ''}</div>
  </div>`;
}

function overviewHtml(data) {
  const project = data.project || {};
  const projectId = project.project_id;
  const disc = data.discovery || {};
  const ev = data.evaluation || {};
  const overall = ev.overall || {};
  const risk = ev.risk || {};
  const dims = ev.dimensions || [];
  const facts = disc.key_facts || [];
  const docs = disc.documents || {};
  const riskCounts = risk.by_severity || {};
  const consistency = ev.consistency || {};
  const coverageCounts = (ev.coverage || {}).counts || {};
  const asset = (disc.assets || [])[0] || {};
  const trial = (disc.trials || [])[0] || {};
  const versions = data.versions || {};
  const findings = data.findings || [];
  const questions = data.questions || [];

  const efficacy = facts.filter((f) => ['orr', 'dcr', 'pfs', 'os', 'dor'].includes(f.field));
  const safety = facts.filter((f) => ['any_ae', 'grade3_ae', 'sae', 'treatment_related_death', 'discontinuation'].includes(f.field));
  const criticalHigh = (riskCounts.critical || 0) + (riskCounts.high || 0);
  const coverageApplicable = Number((ev.coverage || {}).applicable || 0);
  const coverageSufficient = Number(coverageCounts.sufficient || 0);
  const coveragePct = coverageApplicable ? Math.round((coverageSufficient / coverageApplicable) * 100) : 0;

  const attrRows = (disc.attributes || []).map((attr) => {
    const src = attr.evidence_id
      ? `<a href="${ovHref(attr, projectId)}" title="${esc(attr.quote || '')}">${esc(attr.document || 'evidence')}${attr.page ? ' p.' + attr.page : ''}</a>`
      : (attr.source === 'missing' ? '<span class="muted">缺失</span>' : '<span class="muted">资产登记</span>');
    const value = attr.value === null || attr.value === undefined || attr.value === '' ? '—' : attr.value;
    return `<tr><th>${esc(attr.label_zh)}<span class="en">${esc(attr.label_en)}</span></th>
      <td>${esc(String(value))}</td><td class="ov-src">${src}</td></tr>`;
  }).join('');

  const factCard = (fact) => `<a class="ov-fact" href="${ovHref(fact, projectId)}" title="${esc(fact.quote || '')}">
    <div class="ov-fact-top"><span class="ov-fact-label">${esc(fact.label_zh)}</span>
      ${chip(fact.verification_status === 'verified' ? 'verified' : (fact.verification_status || 'pending'), fact.verification_status === 'verified' ? 'green' : 'amber')}</div>
    <div class="ov-fact-value">${esc(fact.value_display)}</div>
    <div class="ov-fact-meta">${[fact.population, fact.cutoff, fact.assessment_method].filter(Boolean).map(esc).join(' · ') || '—'}</div>
    <div class="ov-fact-src">${esc(fact.document || '')} p.${esc(String(fact.page || ''))}${fact.alternatives ? ' · 另有 ' + fact.alternatives + ' 处报告' : ''}</div>
  </a>`;

  const factCards = [...efficacy, ...safety].map(factCard).join('');
  const expextedChips = (docs.expected || []).map((row) =>
    `<span class="ov-doc-chip ${row.present ? 'ok' : 'miss'}" title="${esc(row.label_en)}">${row.present ? '✓' : '✗'} ${esc(row.doc_type)}</span>`
  ).join('');
  const docBars = (docs.by_type || []).map((row) => ({
    label: `${row.doc_type} · ${row.documents}`, value: row.pages, color: OV_COLORS.blue,
  }));
  const highlights = (disc.document_highlights || []).map((hl) => `<div class="ov-hl">
    <div class="ov-hl-head"><span class="chip blue">${esc(hl.doc_type)}</span><span class="muted">${esc(hl.filename)} p.${hl.page}</span></div>
    <div class="ov-hl-text">${esc(hl.excerpt)}</div>
  </div>`).join('');

  const dimRows = dims.map((dim) => {
    const score = Number(dim.score || 0);
    const missing = (dim.missing || []).length
      ? `<div class="ov-dim-missing">缺失：${dim.missing.map(esc).join('、')}</div>` : '';
    return `<div class="ov-dim">
      <div class="ov-dim-head"><div><strong>${esc(dim.label_zh)}</strong><span class="en">${esc(dim.label_en)}</span></div>
        <div class="ov-dim-score" style="color:${ovColor(score)}">${score.toFixed(1)}</div></div>
      <div class="ov-dim-track"><div class="ov-dim-fill" style="width:${clamp(score, 0, 100)}%;background:${ovColor(score)}"></div></div>
      <div class="ov-dim-basis">${esc(dim.basis)}</div>${missing}
    </div>`;
  }).join('');

  const sevSegments = Object.entries(riskCounts)
    .filter(([key, value]) => Number(value) > 0 && key !== 'info')
    .map(([key, value]) => ({ label: key, value: Number(value), color: OV_SEVERITY[key] || OV_COLORS.grey, key }));
  const riskDonut = ovDonut(sevSegments, 168);

  const consMeta = {
    A: { label: 'A 直接矛盾', color: OV_COLORS.red, type: 'data_inconsistency' },
    B: { label: 'B 版本差异', color: OV_COLORS.grey, type: 'version_difference' },
    C: { label: 'C 定义差异', color: OV_COLORS.amber, type: 'definition_difference' },
    U: { label: 'U 可比性不明', color: '#b8c4d2', type: 'comparability_unclear' },
  };
  const consTiles = Object.entries(consMeta).map(([key, meta]) =>
    `<a class="ov-cons-tile" href="#/findings?project=${encodeURIComponent(projectId)}&type=${meta.type}" style="border-top-color:${meta.color}">
      <div class="ov-cons-key" style="color:${meta.color}">${key}</div>
      <div class="ov-cons-value">${Number(consistency[key] || 0)}</div>
      <div class="ov-cons-label">${esc(meta.label)}</div>
    </a>`).join('');

  const covSegments = [
    { label: 'sufficient', value: Number(coverageCounts.sufficient || 0), color: OV_COLORS.teal },
    { label: 'partial', value: Number(coverageCounts.partial || 0), color: OV_COLORS.amber },
    { label: 'not found', value: Number(coverageCounts.not_found || 0), color: OV_COLORS.red },
    { label: 'not applicable', value: Number(coverageCounts.not_applicable || 0), color: OV_COLORS.grey },
    { label: 'not reviewed', value: Number(coverageCounts.not_reviewed || 0), color: '#c9d3df' },
  ];

  const riskRows = findings.slice(0, 6).map((finding) => `<a class="ov-risk-row" href="#/findings?project=${encodeURIComponent(projectId)}&severity=${encodeURIComponent(finding.severity)}">
    <span class="ov-sev-dot" style="background:${OV_SEVERITY[finding.severity] || OV_COLORS.grey}"></span>
    <div class="ov-risk-main"><div class="ov-risk-title">${esc(finding.title)}</div>
      <div class="muted">${esc(finding.finding_type)} · ${esc(finding.domain || '')} · 证据 ${finding.evidence_count}</div></div>
    ${chip(finding.severity, finding.severity === 'critical' ? 'red' : finding.severity === 'high' ? 'amber' : finding.severity === 'medium' ? 'blue' : 'grey')}
  </a>`).join('');
  const questionRows = questions.slice(0, 5).map((question) => `<div class="ov-q-row">
    <span class="ov-sev-dot" style="background:${OV_SEVERITY[question.priority] || OV_COLORS.grey}"></span>
    <div class="ov-q-main"><div class="ov-q-text">${esc(question.question)}</div>
      <div class="muted">${esc(question.owner)} · ${esc(question.priority)} · ${esc(question.status)}</div></div>
  </div>`).join('');

  const latest = (versions.latest || []).length;
  const superseded = (versions.superseded || []).length;
  const capNote = overall.readiness_cap
    ? `<div class="ov-note">因存在 ${overall.critical_contradictions} 项 critical 跨文件矛盾，尽调就绪度封顶为 ${overall.readiness_cap}。</div>`
    : '';

  return `
  <div class="page-head ov-page-head">
    <div><h1>Asset 360</h1><div class="sub">药物资产发现与资产评估 · Drug asset discovery & evaluation</div></div>
    <div class="actions">
      <a class="btn sm" href="#/valuation?project=${encodeURIComponent(projectId)}">交易估值</a>
      <a class="btn sm" href="#/evidence?project=${encodeURIComponent(projectId)}">Evidence</a>
      <a class="btn sm" href="#/findings?project=${encodeURIComponent(projectId)}">Findings & Questions</a>
      <a class="btn sm" href="#/report?project=${encodeURIComponent(projectId)}">Report</a>
      <button class="btn primary sm" data-action="refresh-view">Refresh</button>
    </div>
  </div>

  ${opportunityOrigin(project.meta?.opportunity)}
  <div class="ov-hero">
    <div class="ov-asset">
      <div class="ov-eyebrow">DRUG ASSET · DISCOVERED FROM PACKAGE</div>
      <h2>${esc(asset.name || project.asset_name || '—')} <span class="ov-phase">${esc(asset.phase || project.phase || '')}</span></h2>
      <div class="ov-asset-sub">${esc(asset.mechanism || '—')} · ${esc(asset.modality || '—')}</div>
      <div class="ov-asset-meta">
        <span><b>靶点</b> ${esc(asset.target || '—')}</span>
        <span><b>适应症</b> ${esc(asset.indication || project.indication || '—')}</span>
        <span><b>试验</b> ${esc(trial.nct_id || '—')}${trial.internal_id ? ' · ' + esc(trial.internal_id) : ''}</span>
        <span><b>样本量</b> ${esc(String(trial.sample_size || '—'))}</span>
        <span><b>数据截止</b> ${esc(trial.data_cutoff || '—')}</span>
      </div>
      <div class="ov-asset-actions">
        <a class="ov-inline-link" href="#/evidence?project=${encodeURIComponent(projectId)}">查看 ${Number((ev.evidence || {}).total || 0)} 条证据 →</a>
        <a class="ov-inline-link" href="#/findings?project=${encodeURIComponent(projectId)}">查看 ${findings.length} 条 active findings →</a>
        <a class="ov-inline-link" href="#/report?project=${encodeURIComponent(projectId)}">报告与导出 →</a>
      </div>
    </div>
    <div class="ov-score-panel">
      <div class="ov-score-ring">${ovRing(overall.score, 168)}</div>
      <div class="ov-score-copy">
        <div class="ov-score-title">${esc(overall.title_zh || '尽调就绪度')}</div>
        <div class="ov-score-level" style="color:${ovColor(overall.score)}">${esc(overall.label_zh || '')} · ${Number(overall.score || 0).toFixed(1)} / 100</div>
        <div class="ov-risk-line">风险暴露：<strong>${esc(risk.label_zh || '—')}</strong> · ${risk.active_findings || 0} 条 active findings</div>
        <div class="ov-score-note">${esc(overall.disclaimer || '')}</div>
        ${capNote}
      </div>
    </div>
  </div>

  <div class="ov-kpis">
    ${ovKpi('证据 / Evidence', Number((ev.evidence || {}).total || 0), `${(ev.evidence || {}).verified || 0} 条已核验（${Number((ev.evidence || {}).verified_pct || 0).toFixed(1)}%）`)}
    ${ovKpi('发现 / Findings', findings.length, `critical ${riskCounts.critical || 0} · high ${riskCounts.high || 0}`, 'danger')}
    ${ovKpi('卖方问题 / Questions', questions.length, '可直接发卖方·可审核')}
    ${ovKpi('检查覆盖 / Coverage', coveragePct + '%', `${coverageSufficient}/${coverageApplicable} 适用项充分`, 'ok')}
    ${ovKpi('资料包 / Package', (docs.total_documents || 0) + ' docs', (docs.total_pages || 0) + ' pages · ' + (docs.present_count || 0) + '/' + (docs.expected_count || 0) + ' 类关键文档')}
    ${ovKpi('版本 / Versions', latest + ' latest', superseded ? `${superseded} 份已被 v2 替代` : '当前无替代版本')}
  </div>

  <div class="ov-section-head"><h2>药物资产发现 <span>Asset Discovery</span></h2>
    <div class="muted">从资料包中识别资产、试验、关键临床事实与文件构成；点击任一卡片回到原文</div></div>

  <div class="ov-grid ov-grid-2">
    ${ovCard('资产档案', 'Asset profile', `<table class="ov-attr-table"><tbody>${attrRows}</tbody></table>`)}
    ${ovCard('关键临床事实', 'Key clinical facts', `<div class="ov-facts">${factCards || '<div class="muted">暂无可展示的临床事实</div>'}</div>`)}
  </div>

  <div class="ov-grid ov-grid-2">
    ${ovCard('文件构成', 'Document inventory', `
      <div class="ov-bars">${ovBars(docBars)}</div>
      <div class="ov-subhead">关键文档覆盖</div>
      <div class="ov-chips">${expextedChips}</div>
      <div class="ov-subhead">非临床 / 监管 / 专利 / 商业文件预览 <span class="muted">（未结构化抽取，仅供参考）</span></div>
      <div class="ov-highlights">${highlights || '<div class="muted">暂无</div>'}</div>`)}
    ${ovCard('试验与版本', 'Trial & versions', `
      <table class="ov-attr-table"><tbody>
        <tr><th>试验编号<span class="en">Trial / NCT</span></th><td>${esc(trial.nct_id || '—')}</td></tr>
        <tr><th>研究编号<span class="en">Internal ID</span></th><td>${esc(trial.internal_id || '—')}</td></tr>
        <tr><th>阶段 / 设计<span class="en">Phase / design</span></th><td>${esc((trial.phase || '') + (trial.design ? ' · ' + trial.design : '')) || '—'}</td></tr>
        <tr><th>主要终点<span class="en">Primary endpoint</span></th><td>${esc(trial.primary_endpoint || '—')}</td></tr>
        <tr><th>文档版本<span class="en">Versions</span></th><td>${latest} 份 latest${superseded ? ` · ${superseded} 份 superseded` : ''}</td></tr>
      </tbody></table>
      <div class="ov-subhead">版本历史 <span class="muted">（v1 → v2 自动替代）</span></div>
      <div class="ov-versions">
        ${(versions.superseded || []).slice(0, 6).map((v) => `<span class="ov-doc-chip miss" title="superseded">↺ ${esc(v.filename)} v${esc(v.version)}</span>`).join('') || '<span class="muted">当前无 superseded 文档</span>'}
      </div>`)}
  </div>

  <div class="ov-section-head"><h2>药物资产评估 <span>Asset Evaluation</span></h2>
    <div class="muted">按证据完整度、可核验性、一致性、覆盖度与成熟度计算的规则化就绪度；不构成投资建议</div></div>

  <div class="ov-grid ov-grid-2">
    ${ovCard('评估维度', 'Evaluation dimensions', `<div class="ov-dims">${dimRows}</div>`)}
    ${ovCard('风险与一致性', 'Risk & consistency', `
      <div class="ov-chart-row">
        <div class="ov-chart-block"><div class="ov-chart-title">发现严重度分布 <span>Severity mix</span></div>${riskDonut}
          <div class="ov-chart-caption">点击扇区筛选 findings</div></div>
        <div class="ov-chart-block ov-cons-block"><div class="ov-chart-title">一致性分类 <span>Comparability classes</span></div>
          <div class="ov-cons-grid">${consTiles}</div></div>
      </div>
      <div class="ov-subhead">检查清单覆盖 <span class="muted">${coverageSufficient} sufficient · ${coverageCounts.partial || 0} partial · ${coverageCounts.not_found || 0} not found</span></div>
      ${ovStack(covSegments)}`)}
  </div>

  <div class="ov-grid ov-grid-2">
    ${ovCard('Top 风险', 'Top open risks', `<div class="ov-risk-list">${riskRows || '<div class="muted">暂无 active findings</div>'}</div>`)}
    ${ovCard('Top 卖方问题', 'Top seller questions', `<div class="ov-q-list">${questionRows || '<div class="muted">暂无问题</div>'}</div>`)}
  </div>

  <div class="ov-foot">
    数据来自项目 <span class="mono">${esc(projectId)}</span> 的最新文档；评估为规则化汇总，需专家复核。
    生成于 ${esc(fmtDate(data.latest_run && data.latest_run.finished_at))}。
  </div>`;
}

async function renderOverview(seq) {
  setView(loadingBlock('Building asset overview…'));
  try {
    const data = await api(`/api/projects/${state.projectId}/overview`);
    if (seq !== state.renderSeq) return;
    state.overview = data;
    state.project = data.project;
    state.stats = data.stats;
    updateSidebarBadges();
    setView(overviewHtml(data));
    const nav = $$('.nav-item[data-nav]');
    nav.forEach((btn) => btn.classList.toggle('active', btn.dataset.nav === 'overview'));
  } catch (error) {
    if (seq === state.renderSeq) setView(errorPanel(error));
  }
}

/* ---------------------------------------------------------------- upload */
function stagePillsHtml(run) {
  const stats = (run && run.stats) || {};
  return STAGES.map((stage) => {
    const info = stats[`stage_${stage.toLowerCase()}`];
    let cls = '';
    if (info && info.state === 'done') cls = 'done';
    else if (info && info.state === 'running') cls = 'running';
    return `<span class="stage-pill ${cls}">${stage}</span>`;
  }).join('');
}

function runProgressHtml() {
  const run = state.run;
  if (!run) {
    return `<div class="card run-panel"><div class="card-head"><h2>Analysis run</h2>
      <span class="sub">No run started yet</span></div>
      <div class="card-body"><p style="margin:0;color:var(--muted)">Upload the package then click <strong>Run analysis</strong>. The pipeline runs INGEST → EXTRACT → VALIDATE → CONSISTENCY → GAP → QUESTIONS → REPORT.</p></div></div>`;
  }
  const statusClass = run.status === 'succeeded' ? 'green' : run.status === 'failed' ? 'red' : 'blue';
  const err = run.error || (state.runFull && state.runFull.error);
  return `<div class="card run-panel" id="run-progress">
    <div class="card-head">
      <h2>Analysis run</h2>
      <span class="sub mono">${esc(run.run_id || '')}</span>
      <div class="right">${chip(run.status || 'running', statusClass)}${chip(run.state || '', 'blue')}</div>
    </div>
    <div class="card-body">
      <div style="display:flex;align-items:baseline;gap:10px">
        <strong style="font-size:18px;color:var(--navy)">${Number(run.percent || 0)}%</strong>
        <span style="color:var(--muted);font-size:12px">${Number(run.stages_done || 0)} / ${Number(run.stages_total || STAGES.length)} stages complete</span>
      </div>
      <div class="progress-track"><div class="progress-fill" style="width:${clamp(Number(run.percent || 0), 0, 100)}%"></div></div>
      <div class="stage-row">${stagePillsHtml(state.runFull || run)}</div>
      ${err ? `<div class="run-error">${esc(err)}</div>` : ''}
    </div>
  </div>`;
}

function updateRunPanels() {
  const host = document.querySelector('#run-progress-host');
  if (host) host.innerHTML = runProgressHtml();
  updateTopbar();
}

async function renderUpload(seq) {
  setView(loadingBlock('Loading documents…'));
  try {
    // The documents endpoint adds evidence_count + file_url used by the table;
    // the project endpoint carries stats/runs/outputs.
    const [data, docsRes] = await Promise.all([
      api(`/api/projects/${state.projectId}`),
      api(`/api/projects/${state.projectId}/documents`),
    ]);
    if (seq !== state.renderSeq) return;
    state.project = data.project;
    state.stats = data.stats;
    state.documents = (docsRes.documents || data.documents || []);
    state.runs = data.runs || [];
    state.outputs = data.outputs || null;
    const latest = data.latest_run;
    if (latest && latest.run && latest.run.status === 'running') {
      startPolling(latest.run.run_id);
    } else if (latest && latest.run) {
      state.run = latest.progress;
      state.runFull = latest.run;
      state.runId = latest.run.run_id;
    }
    updateTopbar();
    updateSidebarBadges();

    const docRows = state.documents.map((doc) => {
      const fileUrl = doc.file_url || '';
      return `<tr>
        <td class="title-cell">${esc(doc.filename)}</td>
        <td>${chip(doc.doc_type || 'OTHER', 'blue')}</td>
        <td>${esc(doc.version || '—')}</td>
        <td class="num">${Number(doc.page_count || 0)}</td>
        <td>${chip(doc.status || 'uploaded', DOC_STATUS_CLASS[doc.status] || 'grey')}</td>
        <td class="num">${Number(doc.evidence_count || 0)}</td>
        <td>${doc.is_latest ? chip('latest', 'green') : chip('superseded', 'purple')}</td>
        <td class="muted">${doc.parse_error ? `<span class="alert-inline" title="${esc(doc.parse_error)}">${esc(String(doc.parse_error).slice(0, 80))}</span>` : '—'}</td>
        <td>${fileUrl ? `<a class="link-btn" href="${esc(fileUrl)}" target="_blank" rel="noopener">open</a>` : '—'}</td>
      </tr>`;
    }).join('');

    const queueHtml = state.uploadQueue.length ? `<div class="upload-queue">${state.uploadQueue.map((item) => `
      <div class="queue-row"><span class="name">${esc(item.name)}</span>
        <span class="muted">${item.size ? Math.round(item.size / 1024) + ' KB' : ''}</span>
        <span class="right">${item.status === 'uploading' ? chip('uploading', 'blue') : item.status === 'error' ? chip('failed', 'red') : item.status === 'registered' ? chip('registered (parser pending)', 'amber') : chip('done', 'green')}</span>
      </div>`).join('')}</div>` : '';

    const ingestionMissing = state.health && state.health.ingestion_available === false;
    const s = state.stats || {};
    setView(`
      <div class="page-head">
        <div><h1>Upload &amp; Analysis</h1>
          <div class="sub">${esc(state.project ? state.project.name : '')} · drag &amp; drop the full due-diligence package, then run the evidence pipeline.</div>
        </div>
        <div class="actions">
          <button class="btn" data-action="refresh-view">Refresh</button>
          <button class="btn" data-action="run-analysis" data-force="1">Force re-run</button>
          <button class="btn primary" data-action="run-analysis">▶ Run analysis</button>
        </div>
      </div>
      ${ingestionMissing ? `<div class="notice warn"><span>⚠️</span><div>The parsing module (<code>hijenny.ingestion</code>) is not installed yet. Uploads are registered and saved; pages/evidence appear once the parser is available and analysis is run.</div></div>` : ''}
      ${statStrip([
        { k: 'Documents', v: s.documents || state.documents.length },
        { k: 'Pages parsed', v: s.pages || 0 },
        { k: 'Evidence', v: s.evidence || 0 },
        { k: 'Findings', v: s.findings || 0 },
        { k: 'Questions', v: s.questions || 0 },
        { k: 'Coverage checks', v: s.coverage || 0 },
      ])}
      <div class="card">
        <div class="card-head"><h2>Package upload</h2><span class="sub">multi-file · PDF, DOCX, XLSX, PPTX</span></div>
        <div class="card-body">
          <div class="dropzone" data-action="browse-files" role="button" tabindex="0">
            <div class="icon">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 16V4m0 0L7 9m5-5 5 5"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>
            </div>
            <h3>Drop the due-diligence package here</h3>
            <p>or click to browse files. All files are registered and parsed in one request.</p>
            <div class="formats">${['CSR / IB / Protocol', 'SAP', 'Safety', 'CMC', 'Patent', 'XLSX', 'PPTX'].map((f) => chip(f, 'outline')).join('')}</div>
          </div>
          <input type="file" id="file-input" multiple class="hidden" data-input="file-upload" accept=".pdf,.docx,.doc,.xlsx,.xls,.pptx,.txt,.md" />
          ${queueHtml}
        </div>
      </div>
      <div id="run-progress-host">${runProgressHtml()}</div>
      <div class="card">
        <div class="card-head"><h2>Documents</h2><span class="sub">${state.documents.length} registered</span>
          <div class="right"><button class="btn sm" data-action="refresh-view">Refresh</button></div>
        </div>
        ${state.documents.length ? `
        <div class="table-wrap"><table class="data">
          <thead><tr><th>Filename</th><th>Type</th><th>Version</th><th class="num">Pages</th><th>Status</th><th class="num">Evidence</th><th>Version state</th><th>Parse error</th><th>File</th></tr></thead>
          <tbody>${docRows}</tbody>
        </table></div>` : emptyPanel('No documents yet', 'Upload the seller package to start building the evidence chain.')}
      </div>`);
  } catch (error) {
    if (seq === state.renderSeq) setView(errorPanel(error));
  }
}

/* -------------------------------------------------------------- evidence */
function filteredEvidence() {
  const f = state.filters.evidence;
  const needle = f.q.trim().toLowerCase();
  return state.evidenceAll.filter((ev) => {
    if (f.field_name && ev.field_name !== f.field_name) return false;
    if (f.category && ev.category !== f.category) return false;
    if (f.document_id && ev.document_id !== f.document_id) return false;
    if (f.verification_status && ev.verification_status !== f.verification_status) return false;
    if (needle) {
      const hay = `${ev.quote || ''} ${ev.value_text || ''} ${ev.filename || ''} ${ev.field_name || ''} ${ev.arm || ''} ${ev.population || ''}`.toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  });
}

function selectedEvidence() {
  return state.evidenceAll.find((ev) => ev.evidence_id === state.selectedEvidenceId) || null;
}

function docTreeHtml() {
  if (!state.documents.length) {
    return `<div class="evidence-hint">No documents. Upload a package first.</div>`;
  }
  const docs = state.documents.slice().sort((a, b) => String(a.doc_type).localeCompare(String(b.doc_type)) || String(a.filename).localeCompare(String(b.filename)));
  return docs.map((doc) => `
    <div class="doc-node ${doc.document_id === state.previewDocId ? 'active' : ''}" data-action="select-doc" data-id="${esc(doc.document_id)}">
      <div class="row1">
        ${chip(doc.doc_type || 'OTHER', 'blue')}
        <span class="fname" title="${esc(doc.filename)}">${esc(doc.filename)}</span>
      </div>
      <div class="meta">
        <span>${Number(doc.page_count || 0)} pages</span>
        <span>${Number(doc.evidence_count || 0)} evidence</span>
        <span>${esc(doc.version || 'v1.0')}</span>
        ${doc.is_latest ? '' : '<span style="color:var(--purple)">superseded</span>'}
      </div>
    </div>`).join('');
}

function evidenceFiltersHtml() {
  const f = state.filters.evidence;
  const fields = [...new Set(state.evidenceAll.map((ev) => ev.field_name))].sort();
  const categories = [...new Set(state.evidenceAll.map((ev) => ev.category))].sort();
  const verifications = ['pending', 'verified', 'failed', 'unverifiable'];
  return `
    <div class="inline-filters">
      <input class="search" type="search" placeholder="Search quote / field / value…" value="${esc(f.q)}" data-input="evidence-search" />
      <select data-change="evidence-filter" data-field="field_name">${selectOptions(fields, f.field_name, 'All fields')}</select>
      <select data-change="evidence-filter" data-field="category">
        <option value="">All categories</option>
        ${categories.map((c) => `<option value="${esc(c)}"${c === f.category ? ' selected' : ''}>${esc(c.replace(/_/g, ' '))}</option>`).join('')}
      </select>
      <select data-change="evidence-filter" data-field="document_id">
        <option value="">All documents</option>
        ${state.documents.map((d) => `<option value="${esc(d.document_id)}"${d.document_id === f.document_id ? ' selected' : ''}>${esc(d.filename)}</option>`).join('')}
      </select>
      <select data-change="evidence-filter" data-field="verification_status">${selectOptions(verifications, f.verification_status, 'All verifications')}</select>
      <button class="btn sm ghost" data-action="clear-evidence-filters">Clear</button>
      <span class="count" id="evidence-count"></span>
    </div>`;
}

function evidenceCardHtml(ev) {
  const dims = [
    ev.population ? `pop ${ev.population}` : '',
    ev.arm ? `arm ${ev.arm}` : '',
    ev.data_cutoff ? `cutoff ${ev.data_cutoff}` : '',
    ev.assessment_method ? ev.assessment_method : '',
    ev.trial_key ? ev.trial_key : '',
  ].filter(Boolean).map((d) => `<span class="dim">${esc(d)}</span>`).join('');
  return `
    <div class="ev-card ${ev.evidence_id === state.selectedEvidenceId ? 'active' : ''}" data-action="select-evidence" data-id="${esc(ev.evidence_id)}">
      <div class="top">
        <span class="field">${esc(ev.field_name)}</span>
        ${ev.category ? chip(ev.category.replace(/_/g, ' '), 'outline') : ''}
        <span class="value">${esc(evidenceValue(ev))}</span>
      </div>
      ${dims ? `<div class="dims">${dims}</div>` : ''}
      <div class="quote">${esc(normWs(ev.quote))}</div>
      <div class="foot">
        <span>📄 ${esc(ev.filename || '')} · p.${Number(ev.page || 0)}</span>
        <span class="right">${statusChip(ev.verification_status)}</span>
      </div>
    </div>`;
}

function renderEvidenceCards() {
  const host = document.querySelector('#evidence-list');
  if (!host) return;
  const rows = filteredEvidence();
  const countEl = document.querySelector('#evidence-count');
  if (countEl) countEl.textContent = `${rows.length} of ${state.evidenceAll.length} evidence items`;
  if (!rows.length) {
    host.innerHTML = `<div class="evidence-hint">${state.evidenceAll.length ? 'No evidence matches the current filters.' : 'No evidence extracted yet — upload a package and run analysis.'}</div>`;
    return;
  }
  host.innerHTML = rows.slice(0, 600).map(evidenceCardHtml).join('');
}

function quotePanelHtml() {
  const ev = selectedEvidence();
  if (!ev) {
    return `<div class="label">Selected evidence</div><div style="color:var(--muted)">Click an evidence card to jump to its document page and see the verbatim quote.</div>`;
  }
  return `<div class="label">${esc(ev.field_name)} = ${esc(evidenceValue(ev))} · ${esc(ev.filename || '')} p.${Number(ev.page || 0)} · ${esc(ev.verification_status || '')}</div>
    <blockquote>${esc(normWs(ev.quote))}</blockquote>`;
}

function updatePreviewToolbar() {
  const host = document.querySelector('#preview-toolbar');
  if (!host) return;
  const doc = state.documents.find((d) => d.document_id === state.previewDocId) || null;
  const total = (state.pageData && Number(state.pageData.total_pages)) || Number(doc && doc.page_count) || 0;
  host.innerHTML = `
    <span class="docname" title="${esc(doc ? doc.filename : '')}">${doc ? esc(doc.filename) : 'No document selected'}</span>
    <span class="spacer"></span>
    <button class="btn sm" data-action="page-prev" ${state.previewPage <= 1 ? 'disabled' : ''}>‹</button>
    <input class="page-input" type="number" min="1" max="${total || 9999}" value="${state.previewPage}" data-change="page-jump" aria-label="Page number" />
    <span style="font-size:11.5px;color:var(--muted)">/ <span id="page-total">${total || '?'}</span></span>
    <button class="btn sm" data-action="page-next" ${total && state.previewPage >= total ? 'disabled' : ''}>›</button>
    <button class="btn sm ${state.previewMode === 'pdf' ? 'navy' : ''}" data-action="preview-mode" data-mode="pdf">PDF</button>
    <button class="btn sm ${state.previewMode === 'text' ? 'navy' : ''}" data-action="preview-mode" data-mode="text">Text</button>
    ${doc && doc.file_url ? `<a class="btn sm" href="${esc(doc.file_url)}" target="_blank" rel="noopener">Open ↗</a>` : ''}`;
}

function selectedQuoteForSearch() {
  const ev = selectedEvidence();
  if (!ev || !ev.quote) return '';
  return normWs(ev.quote).slice(0, 90);
}

function textPageHtml(pageData) {
  const page = pageData.page || {};
  const blocks = (pageData.blocks || page.blocks || []).filter((b) => Array.isArray(b.bbox) && b.bbox.length === 4);
  const W = Number(page.width) || 612;
  const H = Number(page.height) || 792;
  const quote = normWs((selectedEvidence() || {}).quote || '').toLowerCase();
  const evBBoxes = (pageData.evidence || []).map((ev) => ev.bbox).filter((b) => Array.isArray(b) && b.length === 4);

  if (!blocks.length) {
    const text = page.text || pageData.text || '';
    if (!text) return `<div class="preview-empty">This page has no extracted text yet.<br/>Run analysis to parse the package.</div>`;
    let html = esc(text);
    if (quote) {
      const idx = normWs(text).toLowerCase().indexOf(quote);
      if (idx >= 0) html = html.replace(esc(normWs(text).slice(idx, idx + quote.length)), (m) => `<mark style="background:#ffe066">${m}</mark>`);
    }
    return `<div class="preview-text"><pre style="white-space:pre-wrap;font-size:12px;line-height:1.55;margin:0">${html}</pre></div>`;
  }

  const blockHtml = blocks.map((block) => {
    const [x0, y0, x1, y1] = block.bbox.map(Number);
    const text = normWs(block.text);
    const norm = text.toLowerCase();
    const quoteHit = quote.length > 4 && norm && (quote.includes(norm) || norm.includes(quote.slice(0, 40)));
    const evHit = evBBoxes.some((b) => Math.abs(Number(b[0]) - x0) < 4 && Math.abs(Number(b[1]) - y0) < 4);
    const left = clamp((x0 / W) * 100, 0, 100);
    const top = clamp((y0 / H) * 100, 0, 100);
    const width = clamp(((x1 - x0) / W) * 100, 0.5, 100 - left);
    const height = clamp(((y1 - y0) / H) * 100, 0.3, 100 - top);
    const fontPx = clamp((height / 100) * 620 * 0.62, 4.5, 14);
    return `<div class="page-block ${quoteHit || evHit ? 'hl' : ''}" style="left:${left.toFixed(3)}%;top:${top.toFixed(3)}%;width:${width.toFixed(3)}%;height:${height.toFixed(3)}%;font-size:${fontPx.toFixed(1)}px" title="${esc(text.slice(0, 240))}">${esc(text)}</div>`;
  }).join('');

  return `<div class="preview-text"><div class="page-canvas-wrap"><div class="page-canvas" style="width:100%;aspect-ratio:${(W / H).toFixed(4)}">${blockHtml}</div></div></div>`;
}

async function updatePreview() {
  const body = document.querySelector('#preview-body');
  if (!body) return;
  const doc = state.documents.find((d) => d.document_id === state.previewDocId) || null;
  const quoteHost = document.querySelector('#quote-panel');
  if (quoteHost) quoteHost.innerHTML = quotePanelHtml();
  updatePreviewToolbar();
  if (!doc) {
    body.innerHTML = `<div class="preview-empty">Select a document in the left panel to preview pages and evidence.</div>`;
    return;
  }
  state.pageData = null;
  try {
    state.pageData = await api(`/api/projects/${state.projectId}/documents/${doc.document_id}/pages/${state.previewPage}`);
  } catch (error) {
    state.pageData = null;
  }
  if (state.previewMode === 'pdf' && doc.pdf_url) {
    const search = selectedQuoteForSearch();
    const src = `${doc.pdf_url}#page=${state.previewPage}${search ? '&search=' + encodeURIComponent(search) : ''}`;
    body.innerHTML = `<iframe class="preview-frame" title="PDF preview" src="${esc(src)}"></iframe>`;
  } else if (state.pageData) {
    body.innerHTML = textPageHtml(state.pageData);
  } else if (doc.pdf_url) {
    body.innerHTML = `<iframe class="preview-frame" title="PDF preview" src="${esc(doc.pdf_url + '#page=' + state.previewPage)}"></iframe>`;
  } else {
    body.innerHTML = `<div class="preview-empty">Page not parsed yet.<br/>Run analysis, or open the original file.</div>`;
  }
  updatePreviewToolbar();
}

function renderEvidenceView() {
  setView(`
    <div class="page-head">
      <div><h1>Evidence Workspace</h1>
        <div class="sub">${state.evidenceAll.length} evidence items · ${state.documents.length} documents — every value traced to a document, page and verbatim quote.</div>
      </div>
      <div class="actions"><button class="btn" data-action="refresh-view">Refresh</button></div>
    </div>
    ${evidenceFiltersHtml()}
    <div class="ws-grid" style="margin-top:14px">
      <div class="ws-panel">
        <div class="ph">Documents<span class="right">${state.documents.length}</span></div>
        <div class="doc-tree">${docTreeHtml()}</div>
      </div>
      <div class="ws-panel">
        <div class="ph">Page preview<span class="right"><span id="page-total-badge"></span></span></div>
        <div id="preview-toolbar" class="preview-toolbar"></div>
        <div id="preview-body"><div class="preview-empty">Loading preview…</div></div>
        <div class="quote-panel" id="quote-panel"></div>
      </div>
      <div class="ws-panel">
        <div class="ph">Evidence<span class="right"><span id="evidence-count"></span></span></div>
        <div class="evidence-list" id="evidence-list"></div>
      </div>
    </div>`);
}

async function renderEvidence(seq) {
  setView(loadingBlock('Loading evidence…'));
  try {
    const [docsRes, evRes] = await Promise.all([
      api(`/api/projects/${state.projectId}/documents`),
      api(`/api/projects/${state.projectId}/evidence?limit=5000`),
    ]);
    if (seq !== state.renderSeq) return;
    state.documents = docsRes.documents || [];
    state.evidenceAll = evRes.evidence || [];
    const withEvidence = state.documents.find((d) => Number(d.evidence_count || 0) > 0);
    if (!state.previewDocId || !state.documents.some((d) => d.document_id === state.previewDocId)) {
      state.previewDocId = (withEvidence || state.documents[0] || {}).document_id || null;
      state.previewPage = 1;
      state.selectedEvidenceId = null;
    }
    if (state.pendingFocusEvidence) {
      const target = state.evidenceAll.find((ev) => ev.evidence_id === state.pendingFocusEvidence);
      state.pendingFocusEvidence = null;
      if (target) {
        state.selectedEvidenceId = target.evidence_id;
        state.previewDocId = target.document_id;
        state.previewPage = Number(target.page || 1);
      }
    }
    renderEvidenceView();
    renderEvidenceCards();
    if (state.selectedEvidenceId) {
      const selected = document.querySelector(`.ev-card[data-id="${state.selectedEvidenceId}"]`);
      if (selected && selected.scrollIntoView) selected.scrollIntoView({ block: 'center' });
    }
    await updatePreview();
    state.outputs = null;
    updateSidebarBadges();
  } catch (error) {
    if (seq === state.renderSeq) setView(errorPanel(error));
  }
}

async function selectEvidenceById(evidenceId) {
  const ev = state.evidenceAll.find((item) => item.evidence_id === evidenceId);
  if (!ev) return;
  state.selectedEvidenceId = evidenceId;
  state.previewDocId = ev.document_id;
  state.previewPage = Number(ev.page || 1);
  $$('.doc-node').forEach((node) => node.classList.toggle('active', node.dataset.id === state.previewDocId));
  $$('.ev-card').forEach((card) => card.classList.toggle('active', card.dataset.id === evidenceId));
  await updatePreview();
  const card = document.querySelector(`.ev-card[data-id="${evidenceId}"]`);
  if (card && card.scrollIntoView) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

async function selectDocument(docId) {
  state.previewDocId = docId;
  state.previewPage = 1;
  state.selectedEvidenceId = null;
  $$('.doc-node').forEach((node) => node.classList.toggle('active', node.dataset.id === docId));
  $$('.ev-card').forEach((card) => card.classList.remove('active'));
  await updatePreview();
}

/* -------------------------------------------------------------- findings */
function findingsTableHtml() {
  const f = state.filters.findings;
  const rows = state.findings.filter((finding) => {
    if (f.severity && finding.severity !== f.severity) return false;
    if (f.status && finding.status !== f.status) return false;
    if (f.finding_type && finding.finding_type !== f.finding_type) return false;
    return true;
  });
  const body = rows.map((finding) => {
    const count = (finding.evidence_ids || []).length;
    const evId = (finding.evidence_ids || [])[0];
    return `<tr>
      <td>
        <select data-change="patch-finding" data-id="${esc(finding.finding_id)}" data-field="severity" class="sev-${esc(finding.severity)}">
          ${selectOptions(SEVERITIES, finding.severity)}
        </select>
      </td>
      <td><span class="mono" style="font-size:10.5px;color:var(--muted)">${esc(finding.finding_type || '')}</span></td>
      <td>
        <div class="title-cell">${esc(finding.title)}</div>
        <div class="muted" style="margin-top:3px">${esc(finding.explanation || '')}</div>
        ${(finding.missing_information || []).length ? `<div class="muted" style="margin-top:3px">Missing: ${esc((finding.missing_information || []).join('; '))}</div>` : ''}
      </td>
      <td>${esc(finding.domain || '—')}</td>
      <td>
        <select data-change="patch-finding" data-id="${esc(finding.finding_id)}" data-field="status">
          ${selectOptions(FINDING_STATUSES, finding.status)}
        </select>
      </td>
      <td class="num">${count}${evId ? ` <button class="link-btn" data-action="focus-evidence" data-id="${esc(evId)}">view</button>` : ''}</td>
      <td class="muted">${esc(finding.rule_id || '—')}</td>
    </tr>`;
  }).join('');
  return `
    <div class="card">
      <div class="card-head"><h2>Findings</h2><span class="sub">${rows.length} of ${state.findings.length}</span>
        <div class="right">
          <select data-change="finding-filter" data-field="severity">${selectOptions(SEVERITIES, f.severity, 'All severities')}</select>
          <select data-change="finding-filter" data-field="status">${selectOptions(FINDING_STATUSES, f.status, 'All statuses')}</select>
          <select data-change="finding-filter" data-field="finding_type">${selectOptions([...new Set(state.findings.map((x) => x.finding_type))].filter(Boolean).sort(), f.finding_type, 'All types')}</select>
        </div>
      </div>
      ${rows.length ? `<div class="table-wrap"><table class="data">
        <thead><tr><th>Severity</th><th>Type</th><th>Finding</th><th>Domain</th><th>Status</th><th class="num">Ev.</th><th>Rule</th></tr></thead>
        <tbody>${body}</tbody></table></div>` : emptyPanel('No findings', 'Run the analysis pipeline to generate consistency, comparability and evidence-gap findings.')}
    </div>`;
}

function questionsTableHtml() {
  const f = state.filters.questions;
  const rows = state.questions.filter((q) => {
    if (f.priority && q.priority !== f.priority) return false;
    if (f.status && q.status !== f.status) return false;
    if (f.owner && q.owner !== f.owner) return false;
    return true;
  });
  const body = rows.map((q) => `
    <tr>
      <td><select data-change="patch-question" data-id="${esc(q.question_id)}" data-field="priority">${selectOptions(PRIORITIES, q.priority)}</select></td>
      <td><select data-change="patch-question" data-id="${esc(q.question_id)}" data-field="owner">${selectOptions(OWNERS, q.owner)}</select></td>
      <td>${esc(q.category || '—')}</td>
      <td>
        <div class="title-cell">${esc(q.question)}</div><a href="#/followup?project=${esc(state.projectId)}&question=${esc(q.question_id)}">跟踪卖方答复 →</a>
        <div class="muted" style="margin-top:3px">${esc(q.rationale || '')}</div>
        ${(q.required_documents || []).length ? `<div class="muted" style="margin-top:3px">Required: ${esc((q.required_documents || []).join('; '))}</div>` : ''}
      </td>
      <td class="num">${(q.evidence_ids || []).length}</td>
      <td><select data-change="patch-question" data-id="${esc(q.question_id)}" data-field="status">${selectOptions(QUESTION_STATUSES, q.status)}</select></td>
      <td>${statusChip(q.review_status)}</td>
      <td><button class="btn sm danger" data-action="delete-question" data-id="${esc(q.question_id)}">Delete</button></td>
    </tr>`).join('');
  return `
    <div class="card">
      <div class="card-head"><h2>Seller questions</h2><span class="sub">${rows.length} of ${state.questions.length}</span>
        <div class="right">
          <select data-change="question-filter" data-field="priority">${selectOptions(PRIORITIES, f.priority, 'All priorities')}</select>
          <select data-change="question-filter" data-field="status">${selectOptions(QUESTION_STATUSES, f.status, 'All statuses')}</select>
          <select data-change="question-filter" data-field="owner">${selectOptions(OWNERS, f.owner, 'All owners')}</select>
        </div>
      </div>
      ${rows.length ? `<div class="table-wrap"><table class="data">
        <thead><tr><th>Priority</th><th>Owner</th><th>Category</th><th>Question</th><th class="num">Ev.</th><th>Status</th><th>Review</th><th></th></tr></thead>
        <tbody>${body}</tbody></table></div>` : emptyPanel('No seller questions', 'Questions are generated from findings and evidence gaps during analysis.')}
    </div>`;
}

function reviewEventsHtml() {
  if (!state.showEvents) return '';
  return `<div class="card">
    <div class="card-head"><h2>Review events</h2><span class="sub">Audit trail of reviewer changes</span>
      <div class="right"><button class="btn sm ghost" data-action="toggle-events">Hide</button></div></div>
    <div class="card-body"><div class="events-list">
      ${state.reviewEvents.length ? state.reviewEvents.map((ev) => `
        <div class="event-row">
          <span class="ts">${esc(fmtDate(ev.ts))}</span>
          <span class="actor">${esc(ev.actor || 'system')}</span>
          <span>${esc(ev.action || '')} ${esc(ev.entity_type || '')}</span>
          <span class="mono" style="color:var(--muted)">${esc(ev.entity_id || '')}</span>
        </div>`).join('') : '<div style="color:var(--muted)">No review events yet — inline edits are logged here.</div>'}
    </div></div>
  </div>`;
}

function findingsViewHtml() {
  const activeFindings = state.findings.filter(isActiveFinding);
  const historyCount = state.findings.length - activeFindings.length;
  const severityCounts = {};
  activeFindings.forEach((f) => { severityCounts[f.severity] = (severityCounts[f.severity] || 0) + 1; });
  const bars = SEVERITIES.map((sev) => {
    const n = severityCounts[sev] || 0;
    const max = Math.max(1, ...Object.values(severityCounts));
    return `<div class="bar-row"><span class="lbl">${sev}</span><span class="track"><span class="fill ${sev}" style="width:${(n / max) * 100}%"></span></span><span class="n">${n}</span></div>`;
  }).join('');
  return `
    <div class="page-head">
      <div><h1>Findings &amp; Questions</h1>
        <div class="sub">${activeFindings.length} active findings${historyCount ? ` (${historyCount} resolved/dismissed in history)` : ''} · ${state.questions.length} seller questions — inline edits are audit-logged.</div>
      </div>
      <div class="actions">
        <button class="btn" data-action="refresh-view">Refresh</button>
        <button class="btn" data-action="toggle-events">${state.showEvents ? 'Hide' : 'Review'} events</button>
        <button class="btn" data-action="run-analysis" data-force="1">Re-run analysis</button>
        <button class="btn" data-action="export-xlsx">内部尽调工作簿</button>
        <button class="btn" data-action="seller-reply">导出已批准的卖方问题单</button>
        <button class="btn primary" data-action="generate-report">Generate report</button>
      </div>
    </div>
    <div class="card" style="margin-bottom:14px"><div class="card-head"><h2>Risk profile</h2><span class="sub">active findings only${historyCount ? ` · ${historyCount} in history` : ''}</span></div>
      <div class="card-body"><div class="bar-chart">${bars}</div></div></div>
    ${findingsTableHtml()}
    ${questionsTableHtml()}
    ${reviewEventsHtml()}`;
}

async function renderFindings(seq) {
  const params = hashParams();
  if (params.get('severity')) state.filters.findings.severity = params.get('severity');
  if (params.get('type')) state.filters.findings.finding_type = params.get('type');
  if (params.get('priority')) state.filters.questions.priority = params.get('priority');
  setView(loadingBlock('Loading findings…'));
  try {
    const [findingsRes, questionsRes] = await Promise.all([
      api(`/api/projects/${state.projectId}/findings`),
      api(`/api/projects/${state.projectId}/questions`),
    ]);
    if (seq !== state.renderSeq) return;
    state.findings = findingsRes.findings || [];
    state.questions = questionsRes.questions || [];
    if (state.showEvents) {
      try {
        const eventsRes = await api(`/api/projects/${state.projectId}/review-events?limit=100`);
        state.reviewEvents = eventsRes.events || [];
      } catch (error) { state.reviewEvents = []; }
    }
    updateSidebarBadges();
    setView(findingsViewHtml());
  } catch (error) {
    if (seq === state.renderSeq) setView(errorPanel(error));
  }
}

/* ----------------------------------------------------------------- report */
function renderMarkdown(md) {
  const lines = String(md || '').split(/\r?\n/);
  const out = [];
  let i = 0;
  let paragraph = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    out.push(`<p>${inlineMd(paragraph.join(' '))}</p>`);
    paragraph = [];
  };

  const inlineMd = (text) => {
    let html = esc(text);
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    return html;
  };

  const splitRow = (line) => line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim());

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { flushParagraph(); i += 1; continue; }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph();
      const level = heading[1].length;
      out.push(`<h${level}>${inlineMd(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }
    if (/^```/.test(line)) {
      flushParagraph();
      const code = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) { code.push(lines[i]); i += 1; }
      i += 1;
      out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
      continue;
    }
    if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1])) {
      flushParagraph();
      const header = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) { rows.push(splitRow(lines[i])); i += 1; }
      out.push(`<table><thead><tr>${header.map((h) => `<th>${inlineMd(h)}</th>`).join('')}</tr></thead><tbody>${
        rows.map((row) => `<tr>${row.map((cell) => `<td>${inlineMd(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table>`);
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flushParagraph(); out.push('<hr/>'); i += 1; continue; }
    if (/^>\s?/.test(line)) {
      flushParagraph();
      const quote = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) { quote.push(lines[i].replace(/^>\s?/, '')); i += 1; }
      out.push(`<blockquote>${inlineMd(quote.join(' '))}</blockquote>`);
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      flushParagraph();
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*+]\s+/, '')); i += 1; }
      out.push(`<ul>${items.map((item) => `<li>${inlineMd(item)}</li>`).join('')}</ul>`);
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      flushParagraph();
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*\d+\.\s+/, '')); i += 1; }
      out.push(`<ol>${items.map((item) => `<li>${inlineMd(item)}</li>`).join('')}</ol>`);
      continue;
    }
    paragraph.push(line.trim());
    i += 1;
  }
  flushParagraph();
  return out.join('\n');
}

async function renderReport(seq) {
  setView(loadingBlock('Loading report…'));
  try {
    await ensureProjectLoaded();
    const outputsRes = await api(`/api/projects/${state.projectId}/outputs`);
    if (seq !== state.renderSeq) return;
    state.outputs = outputsRes.outputs || {};
    const md = state.outputs.report_md || {};
    state.reportMd = null;
    if (md.exists && md.url) {
      try {
        const raw = await fetch(md.url);
        if (raw.ok) state.reportMd = await raw.text();
      } catch (error) { state.reportMd = null; }
    }
    renderReportView();
  } catch (error) {
    if (seq === state.renderSeq) setView(errorPanel(error));
  }
}

function renderReportView() {
  const outputs = state.outputs || {};
  const md = outputs.report_md || {};
  const docx = outputs.report_docx || {};
  const xlsx = outputs.questions_xlsx || {};
  const stats = state.stats || {};
  setView(`
    <div class="page-head">
      <div><h1>Report</h1>
        <div class="sub">${esc(state.project ? state.project.name : '')} · AI-generated due-diligence report — requires expert human review.</div>
      </div>
      <div class="actions">
        <button class="btn" data-action="refresh-view">Refresh</button>
        <button class="btn navy" data-action="generate-report">${md.exists ? 'Regenerate report' : 'Generate report'}</button>
        ${docx.exists ? `<a class="btn" href="${esc(docx.url)}">Download DOCX</a>` : ''}
        ${md.exists ? `<a class="btn" href="${esc(md.url)}" download>Download MD</a>` : ''}
        ${xlsx.exists ? `<a class="btn" href="${esc(xlsx.url)}">内部尽调工作簿</a>` : ''}
      </div>
    </div>
    ${statStrip([
      { k: 'Documents', v: stats.documents || 0 },
      { k: 'Pages', v: stats.pages || 0 },
      { k: 'Evidence', v: stats.evidence || 0 },
      { k: 'Findings', v: stats.findings || 0 },
      { k: 'Questions', v: stats.questions || 0 },
      { k: 'Coverage', v: stats.coverage || 0 },
    ])}
    <div class="notice info"><span>📋</span><div><strong>Decision Brief — Section 1</strong> — one-page summary for BD heads / investment committee, with evidence highlights, top open risks, seller questions and missing evidence. No automated Go/No-Go recommendation is made; the brief summarises evidence and open items for human review.</div></div>
    ${md.exists && state.reportMd ? `<div class="report">${renderMarkdown(state.reportMd)}</div>` : `
      <div class="card">${emptyPanel('No report generated yet',
        'Run the analysis pipeline, then generate the Markdown + DOCX report and the Excel workbooks from the reviewed evidence chain.',
        '<button class="btn primary" data-action="generate-report">Generate report</button>')}</div>`}
    <div class="card" style="margin-top:16px">
      <div class="card-head"><h2>Outputs</h2><span class="sub">Files under data/projects/&lt;id&gt;/exports/</span></div>
      <div class="card-body">
        <div class="kv-grid">
          <div class="kv"><span class="k">Markdown report</span><span class="v">${md.exists ? `<a href="${esc(md.url)}" download>${esc(md.name || 'HiJenny_Report.md')}</a>` : 'not generated'}</span></div>
          <div class="kv"><span class="k">Word report</span><span class="v">${docx.exists ? `<a href="${esc(docx.url)}">${esc(docx.name || 'HiJenny_Report.docx')}</a>` : 'not generated'}</span></div>
          <div class="kv"><span class="k">内部尽调工作簿</span><span class="v">${xlsx.exists ? `<a href="${esc(xlsx.url)}">${esc(xlsx.name || 'Seller_Questions.xlsx')}</a>` : 'not generated'}</span></div>
        </div>
      </div>
    </div>`);
}

/* --------------------------------------------------------------- actions */
async function handleAction(action, el) {
  try {
    if (action === 'overview-filter') {
      const params = new URLSearchParams({ project: state.projectId });
      if (el.dataset.severity) params.set('severity', el.dataset.severity);
      if (el.dataset.type) params.set('type', el.dataset.type);
      location.hash = `#/findings?${params.toString()}`;
      return;
    }
    if (action === 'toggle-create-project') { state.showCreateForm = !state.showCreateForm; renderView(); return; }
    if (action === 'open-project') {
      state.projectId = el.dataset.id;
      localStorage.setItem('hijenny.project', state.projectId);
      state.project = null; state.documents = []; state.evidenceAll = []; state.findings = []; state.questions = [];
      state.previewDocId = null; state.selectedEvidenceId = null; state.outputs = null;
      await ensureProjectLoaded();
      nav('overview');
      return;
    }
    if (action === 'refresh-view') { await renderView(); return; }
    if (action === 'browse-files') { const input = document.querySelector('#file-input'); if (input) input.click(); return; }
    if (action === 'run-analysis') { await runAnalysis(el.dataset.force === '1'); return; }
    if (action === 'select-doc') { await selectDocument(el.dataset.id); return; }
    if (action === 'select-evidence') { await selectEvidenceById(el.dataset.id); return; }
    if (action === 'page-prev') { if (state.previewPage > 1) { state.previewPage -= 1; await updatePreview(); } return; }
    if (action === 'page-next') { state.previewPage += 1; await updatePreview(); return; }
    if (action === 'preview-mode') { state.previewMode = el.dataset.mode; await updatePreview(); return; }
    if (action === 'clear-evidence-filters') {
      state.filters.evidence = { q: '', field_name: '', category: '', document_id: '', verification_status: '' };
      renderEvidenceView(); renderEvidenceCards(); await updatePreview(); return;
    }
    if (action === 'focus-evidence') { state.pendingFocusEvidence = el.dataset.id; nav('evidence'); return; }
    if (action === 'toggle-events') {
      state.showEvents = !state.showEvents;
      if (state.showEvents) {
        const eventsRes = await api(`/api/projects/${state.projectId}/review-events?limit=100`);
        state.reviewEvents = eventsRes.events || [];
      }
      if (currentView() === 'findings') setView(findingsViewHtml());
      return;
    }
    if (action === 'patch-finding' || action === 'patch-question') return;
    if (action === 'delete-question') {
      const id = el.dataset.id;
      if (!window.confirm('Delete this seller question? The deletion is audit-logged.')) return;
      await api(`/api/questions/${id}?actor=reviewer`, { method: 'DELETE' });
      state.questions = state.questions.filter((question) => question.question_id !== id);
      setView(findingsViewHtml());
      toast('Seller question deleted', 'ok');
      return;
    }
    if (action === 'seller-reply') {
      const res=await fetch('/api/projects/'+encodeURIComponent(state.projectId)+'/exports/seller-reply.xlsx');
      if (!res.ok) throw new Error((await res.json()).detail || '问题单导出失败');
      const url=URL.createObjectURL(await res.blob());
      const a=document.createElement('a');a.href=url;a.download='HiJenny-Seller-Reply.xlsx';a.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);return;
    }
    if (action === 'export-xlsx') {
      toast('Building Excel workbook…');
      const res = await api(`/api/projects/${state.projectId}/exports/questions`, { method: 'POST' });
      if (res.download_url) {
        const a = document.createElement('a');
        a.href = res.download_url; a.download = res.name || 'Seller_Questions.xlsx';
        document.body.appendChild(a); a.click(); a.remove();
      }
      toast('Excel workbook ready', 'ok');
      return;
    }
    if (action === 'generate-report') {
      toast('Generating Markdown + DOCX report…');
      const res = await api(`/api/projects/${state.projectId}/exports/report`, { method: 'POST' });
      state.outputs = null;
      const stats = res.stats || {};
      toast(`Report ready: ${stats.findings || 0} findings, ${stats.questions || 0} questions`, 'ok');
      await renderView();
      return;
    }
  } catch (error) {
    toast(error.message || String(error), 'err');
  }
}

async function handleChange(change, el) {
  try {
    if (change === 'evidence-filter') {
      state.filters.evidence[el.dataset.field] = el.value;
      renderEvidenceCards();
      return;
    }
    if (change === 'finding-filter') {
      state.filters.findings[el.dataset.field] = el.value;
      setView(findingsViewHtml());
      return;
    }
    if (change === 'question-filter') {
      state.filters.questions[el.dataset.field] = el.value;
      setView(findingsViewHtml());
      return;
    }
    if (change === 'page-jump') {
      const total = (state.pageData && Number(state.pageData.total_pages)) || 0;
      let page = parseInt(el.value, 10) || 1;
      page = clamp(page, 1, total || 9999);
      state.previewPage = page;
      await updatePreview();
      return;
    }
    if (change === 'patch-finding') {
      const id = el.dataset.id;
      const field = el.dataset.field;
      const res = await api(`/api/findings/${id}`, jsonOptions('PATCH', { [field]: el.value, actor: 'reviewer' }));
      const idx = state.findings.findIndex((f) => f.finding_id === id);
      if (idx >= 0 && res.finding) state.findings[idx] = res.finding;
      setView(findingsViewHtml());
      toast(`Finding ${field.replace('_', ' ')} updated`, 'ok');
      return;
    }
    if (change === 'patch-question') {
      const id = el.dataset.id;
      const field = el.dataset.field;
      const res = await api(`/api/questions/${id}`, jsonOptions('PATCH', { [field]: el.value, actor: 'reviewer' }));
      const idx = state.questions.findIndex((q) => q.question_id === id);
      if (idx >= 0 && res.question) state.questions[idx] = res.question;
      setView(findingsViewHtml());
      toast(`Question ${field.replace('_', ' ')} updated`, 'ok');
      return;
    }
  } catch (error) {
    toast(error.message || String(error), 'err');
  }
}

function handleInput(input, el) {
  if (input === 'evidence-search') {
    state.filters.evidence.q = el.value;
    renderEvidenceCardsDebounced();
    return;
  }
  if (input === 'file-upload') {
    const files = el.files ? Array.from(el.files) : [];
    el.value = '';
    uploadFiles(files);
  }
}

const renderEvidenceCardsDebounced = debounce(() => renderEvidenceCards(), 160);

async function handleSubmit(form, formEl) {
  if (form !== 'create-project') return;
  try {
    const data = new FormData(formEl);
    const payload = {
      name: String(data.get('name') || '').trim(),
      asset_name: String(data.get('asset_name') || '').trim(),
      indication: String(data.get('indication') || '').trim(),
      phase: String(data.get('phase') || '').trim(),
    };
    if (!payload.name) { toast('Project name is required', 'warn'); return; }
    const res = await api('/api/projects', jsonOptions('POST', payload));
    state.projectId = res.project.project_id;
    localStorage.setItem('hijenny.project', state.projectId);
    state.project = res.project;
    state.showCreateForm = false;
    const list = await api('/api/projects');
    state.projects = list.projects || [];
    toast('Project created', 'ok');
    nav('upload');
  } catch (error) {
    toast(error.message || String(error), 'err');
  }
}

/* ------------------------------------------------------------ upload flow */
async function uploadFiles(files) {
  if (!state.projectId) { toast('Create or select a project first', 'warn'); return; }
  if (!files || !files.length) return;
  state.uploadQueue = files.map((file) => ({ name: file.name, size: file.size, status: 'uploading' }));
  if (currentView() === 'upload') await renderUpload(++state.renderSeq);
  const formData = new FormData();
  files.forEach((file) => formData.append('files', file, file.name));
  try {
    const res = await api(`/api/projects/${state.projectId}/documents?parse=true`, { method: 'POST', body: formData });
    state.uploadQueue = state.uploadQueue.map((item) => ({ ...item, status: 'done' }));
    const errors = (res.ingestion && res.ingestion.errors) || [];
    const warnings = res.warnings || [];
    if (errors.length) toast(`Uploaded ${res.count} file(s); ${errors.length} parse error(s) — see document table`, 'warn');
    else if (warnings.length) toast(`Uploaded ${res.count} file(s); ${warnings.length} warning(s)`, 'warn');
    else toast(`Uploaded ${res.count} document(s) and parsed`, 'ok');
    if (res.ingestion && res.ingestion.message) toast(res.ingestion.message, 'warn');
  } catch (error) {
    if (error.status === 503 && error.data && error.data.detail) {
      state.uploadQueue = state.uploadQueue.map((item) => ({ ...item, status: 'registered' }));
      const detail = error.data.detail;
      const count = detail && detail.count ? detail.count : files.length;
      toast(`Registered ${count} document(s); parsing module not available yet`, 'warn');
    } else {
      state.uploadQueue = state.uploadQueue.map((item) => ({ ...item, status: 'error' }));
      toast(`Upload failed: ${error.message}`, 'err');
    }
  }
  setTimeout(() => { state.uploadQueue = []; }, 4000);
  if (currentView() === 'upload') renderView();
}

async function runAnalysis(force) {
  if (!state.projectId) { toast('Select a project first', 'warn'); return; }
  try {
    const res = await api(`/api/projects/${state.projectId}/analyze`, jsonOptions('POST', { force: Boolean(force) }));
    state.runId = res.run_id;
    state.run = res.progress;
    state.runFull = null;
    if (res.already_running) toast('A pipeline run is already in progress', 'warn');
    else toast(`Analysis started (${res.run_id})`, 'ok');
    startPolling(res.run_id);
    if (currentView() === 'upload') renderView();
  } catch (error) {
    toast(`Could not start analysis: ${error.message}`, 'err');
  }
}

function stopPolling() {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.pollTimer = null;
}

function startPolling(runId) {
  stopPolling();
  state.runId = runId;
  const tick = async () => {
    if (state.runId !== runId) return;
    try {
      const data = await api(`/api/runs/${runId}`);
      state.run = data.progress;
      state.runFull = data.run;
      updateRunPanels();
      if (data.run.status === 'running') {
        state.pollTimer = setTimeout(tick, 1500);
      } else {
        state.pollTimer = null;
        const ok = data.run.status === 'succeeded';
        toast(ok ? 'Analysis complete — evidence, findings and questions are ready' : `Analysis failed: ${String(data.run.error || '').slice(0, 260)}`, ok ? 'ok' : 'err');
        state.project = null;
        state.outputs = null;
        if (['projects', 'upload', 'findings', 'report', 'evidence'].includes(currentView())) renderView();
      }
    } catch (error) {
      state.pollTimer = null;
    }
  };
  tick();
}

/* ------------------------------------------------------------------- init */
async function loadHealth() {
  try {
    const data = await api('/api/health');
    state.health = data;
    state.apiOnline = true;
    const dot = $('#api-dot');
    const label = $('#api-status');
    if (dot) dot.className = 'dot on';
    if (label) label.textContent = `API online · v${data.version || '0.1'}`;
  } catch (error) {
    state.apiOnline = false;
    const dot = $('#api-dot');
    const label = $('#api-status');
    if (dot) dot.className = 'dot off';
    if (label) label.textContent = 'API unreachable';
  }
  try {
    const llm = await api('/api/llm-status');
    state.llm = llm;
    const dot = $('#llm-dot');
    const label = $('#llm-status');
    if (dot) dot.className = `dot ${llm.available ? 'on' : 'amber'}`;
    if (label) label.textContent = `Extraction: ${llm.available ? llm.mode + ' + LLM' : 'offline rules'}`;
  } catch (error) { /* non-fatal */ }
}

function bindGlobalEvents() {
  document.addEventListener('click', async (event) => {
    const navBtn = event.target.closest('[data-nav]');
    if (navBtn) { event.preventDefault(); nav(navBtn.dataset.nav); return; }
    const actionEl = event.target.closest('[data-action]');
    if (actionEl) {
      if (actionEl.tagName === 'A' || actionEl.tagName === 'BUTTON') event.preventDefault();
      await handleAction(actionEl.dataset.action, actionEl);
    }
  });
  document.addEventListener('change', async (event) => {
    const el = event.target.closest('[data-change]');
    if (el) await handleChange(el.dataset.change, el);
  });
  document.addEventListener('input', (event) => {
    const el = event.target.closest('[data-input]');
    if (el) handleInput(el.dataset.input, el);
  });
  document.addEventListener('submit', async (event) => {
    const form = event.target.closest('[data-form]');
    if (form) { event.preventDefault(); await handleSubmit(form.dataset.form, form); }
  });
  document.addEventListener('dragover', (event) => {
    const zone = event.target.closest('.dropzone');
    if (zone) { event.preventDefault(); zone.classList.add('drag'); }
  });
  document.addEventListener('dragleave', (event) => {
    const zone = event.target.closest('.dropzone');
    if (zone) zone.classList.remove('drag');
  });
  document.addEventListener('drop', async (event) => {
    const zone = event.target.closest('.dropzone');
    if (!zone) return;
    event.preventDefault();
    zone.classList.remove('drag');
    const files = event.dataTransfer && event.dataTransfer.files ? Array.from(event.dataTransfer.files) : [];
    await uploadFiles(files);
  });
  const projectSelect = $('#project-select');
  if (projectSelect) {
    projectSelect.addEventListener('change', async () => {
      const value = projectSelect.value;
      state.projectId = value || null;
      if (value) localStorage.setItem('hijenny.project', value);
      state.project = null; state.documents = []; state.evidenceAll = []; state.findings = []; state.questions = [];
      state.previewDocId = null; state.selectedEvidenceId = null; state.outputs = null;
      if (value) await ensureProjectLoaded();
      renderView();
    });
  }
  const analyzeBtn = $('#btn-analyze-topbar');
  if (analyzeBtn) analyzeBtn.addEventListener('click', () => runAnalysis(false));
}

async function init() {
  bindGlobalEvents();
  await loadHealth();
  try {
    const data = await api('/api/projects');
    state.projects = data.projects || [];
    const stored = localStorage.getItem('hijenny.project');
    const requested = hashParams().get('project');
    const validRequested = requested && state.projects.find((p) => p.project_id === requested);
    const valid = state.projects.find((p) => p.project_id === stored);
    state.projectId = validRequested ? requested : (valid ? stored : (state.projects[0] ? state.projects[0].project_id : null));
    if (state.projectId) localStorage.setItem('hijenny.project', state.projectId);
    state.project = null;
  } catch (error) {
    toast(`Could not load projects: ${error.message}`, 'err');
  }
  if (state.projectId) {
    try {
      const detail = await api(`/api/projects/${state.projectId}`);
      state.project = detail.project;
      state.stats = detail.stats;
      state.outputs = detail.outputs || null;
      state.documents = detail.documents || [];
      const latest = detail.latest_run;
      if (latest && latest.run) {
        state.run = latest.progress;
        state.runFull = latest.run;
        state.runId = latest.run.run_id;
        if (latest.run.status === 'running') startPolling(latest.run.run_id);
      }
    } catch (error) { /* non-fatal */ }
  }
  updateTopbar();
  updateSidebarBadges();
  window.addEventListener('hashchange', () => renderView());
  await renderView();
}

document.addEventListener('DOMContentLoaded', init);

async function browserBackupDigest(records) {
  const bytes = new TextEncoder().encode(JSON.stringify(records));
  const digest = await crypto.subtle.digest('SHA-256',bytes);
  return Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');
}
