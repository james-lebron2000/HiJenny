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
  return ['projects', 'upload', 'evidence', 'findings', 'report'].includes(view) ? view : 'projects';
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
    projects: ['Projects', 'Due-diligence packages'],
    upload: ['Upload & Analysis', 'Documents, parsing and pipeline runs'],
    evidence: ['Evidence Workspace', 'Every number traced to document, page and quote'],
    findings: ['Findings & Questions', 'Consistency, gaps, seller questions and review'],
    report: ['Report', 'Generated due-diligence report and exports'],
  };
  const [title, sub] = titles[currentView()] || titles.projects;
  const titleEl = $('#topbar-title');
  const subEl = $('#topbar-sub');
  if (titleEl) titleEl.textContent = title;
  if (subEl) subEl.textContent = sub;

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

  if (state.view === 'projects') { await renderProjects(seq); return; }
  if (!state.projectId) {
    setView(`<div class="card">${emptyPanel('No project selected', 'Create or select a project to continue.', '<button class="btn primary" data-nav="projects">Go to Projects</button>')}</div>`);
    return;
  }
  try {
    if (state.view === 'upload') await renderUpload(seq);
    else if (state.view === 'evidence') await renderEvidence(seq);
    else if (state.view === 'findings') await renderFindings(seq);
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
        <div class="title-cell">${esc(q.question)}</div>
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
        <button class="btn" data-action="export-xlsx">Export XLSX</button>
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
        ${xlsx.exists ? `<a class="btn" href="${esc(xlsx.url)}">Download XLSX</a>` : ''}
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
          <div class="kv"><span class="k">Questions workbook</span><span class="v">${xlsx.exists ? `<a href="${esc(xlsx.url)}">${esc(xlsx.name || 'Seller_Questions.xlsx')}</a>` : 'not generated'}</span></div>
        </div>
      </div>
    </div>`);
}

/* --------------------------------------------------------------- actions */
async function handleAction(action, el) {
  try {
    if (action === 'toggle-create-project') { state.showCreateForm = !state.showCreateForm; renderView(); return; }
    if (action === 'open-project') {
      state.projectId = el.dataset.id;
      localStorage.setItem('hijenny.project', state.projectId);
      state.project = null; state.documents = []; state.evidenceAll = []; state.findings = []; state.questions = [];
      state.previewDocId = null; state.selectedEvidenceId = null; state.outputs = null;
      await ensureProjectLoaded();
      nav('upload');
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
  window.addEventListener('hashchange', () => renderView());
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
  await renderView();
}

document.addEventListener('DOMContentLoaded', init);
