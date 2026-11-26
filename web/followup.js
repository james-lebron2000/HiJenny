'use strict';

const bdLabels = {comparison:'添加可比资产', request:'索取材料', reply:'登记卖方答复',
  review:'验证判断', experiment_result:'登记实验结果', publish:'更新 Decision Brief'};
const bdColumns = {asset:'资产', population:'分析人群', line:'治疗线次', method:'评价方法',
  followup:'随访', endpoint:'终点', value:'结果', limits:'可比性限制'};
const bdDecisions = {documents:'资料复核即可，无需新增实验', experiment:'需要实验验证', clarify:'继续补资料'};

const bdStages = {request:'待发起资料请求', reply:'待卖方答复', review:'待内部复核',
  experiment:'待验证结果', publish:'待更新简报', done:'简报已更新', closed:'已关闭', rejected:'已驳回或搁置', record:'待登记答复材料'};

function followupStage(question, history) {
  if (question.review_status === 'rejected' || question.status === 'dismissed') return 'rejected';
  if (question.status === 'closed') return 'closed';
  const steps = history.filter(event => event.action !== 'comparison');
  const last = steps.at(-1);
  if (!last) {
    if (question.status === 'sent') return 'reply';
    if (question.status === 'answered') return 'record';
    return 'request';
  }
  if (last.action === 'request') return 'reply';
  if (['reply', 'experiment_result'].includes(last.action)) return 'review';
  const decision = steps.findLast(event => event.action === 'review')?.after.decision;
  if (last.action === 'publish' && history.at(-1) !== last) return 'review';
  if (decision === 'experiment') return 'experiment';
  if (decision === 'clarify') return 'reply';
  return last.action === 'publish' && history.at(-1) === last ? 'done' : 'publish';
}

async function renderFollowup(seq) {
  const project = state.projectId;
  const base = '/api/projects/' + encodeURIComponent(project);
  const data = await api(base + '/followup');
  if (seq !== state.renderSeq) return;
  const questions = data.questions;
  const params = hashParams();
  const stage = Object.hasOwn(bdStages, params.get('stage')) ? params.get('stage') : '';
  const stages = new Map(questions.map(q => [q.question_id, followupStage(q, data.history[q.question_id] || [])]));
  const visible = questions.filter(q => !stage || stages.get(q.question_id) === stage);
  const id = visible.find(q => q.question_id === params.get('question'))?.question_id || visible[0]?.question_id;
  const q = questions.find(row => row.question_id === id);
  if (!questions.length) { setView(emptyPanel('暂无可跟踪的问题', '请先在本项目生成或选择卖方问题。')); return; }
  const queue = `<section class="card bd"><h2>项目跟进队列</h2><label>按当前待办筛选<select id="bd-stage">
    ${option('', stage, '全部问题（'+questions.length+'）')}
    ${Object.entries(bdStages).map(([key,label]) => option(key, stage, label+'（'+questions.filter(row=>stages.get(row.question_id)===key).length+'）')).join('')}</select></label>
    ${q ? `<label>选择问题<select id="bd-question">${visible.map(row=>option(row.question_id,id,row.question)).join('')}</select></label>
    <p>当前进度 · ${esc(bdStages[stages.get(id)])}</p>` : '<p>此阶段暂无问题。</p>'}</section>`;
  const navigate = (stage, question) => {
    const query = new URLSearchParams({project});
    if (stage) query.set('stage',stage);
    if (question) query.set('question',question);
    location.hash = '#/followup?'+query;
  };
  const bindQueue = () => {
    $('#bd-stage').onchange = e => navigate(e.target.value, id);
    if (q) $('#bd-question').onchange = e => navigate(stage, e.target.value);
  };
  if (!q) { setView(queue); bindQueue(); return; }
  const valuations = projectValuations(project).filter(v=>v.question?.question_id===id);
  const events = data.history[id] || [];
  const sources = data.evidence.filter(e => q.evidence_ids.includes(e.evidence_id));
  const documents = new Map(data.documents.map(d => [d.document_id, d]));
  const sourceLink = row => {
    const doc = documents.get(row.document_id);
    return doc ? `<a href="${esc(doc.file_url)}" target="_blank" rel="noopener">${esc(doc.filename)}</a> · ${esc(row.locator)}` : '来源待补';
  };
  const evidenceLink = e => `<a href="#/evidence?project=${esc(project)}&doc=${esc(e.document_id)}&page=${esc(e.page)}&evidence=${esc(e.evidence_id)}&preview=text">${esc(e.filename)} p.${esc(e.page)} · ${esc(evidenceValue(e))}</a>`;
  const rows = events.filter(e => e.action === 'comparison').map(e => e.after);
  const quote = sources.find(e => e.page === 11 && e.field_name.toLowerCase() === 'orr' && e.document_type.toUpperCase() === 'CSR') || sources[0];
  const cols = Object.keys(bdColumns);
  const reference = quote && {asset:state.project?.asset_name || quote.trial_key || '当前项目', population:quote.population,
    method:quote.assessment_method, endpoint:quote.field_name, value:evidenceValue(quote), limits:'原文口径；跨试验差异须复核'};
  const valuationChoices=(selected)=>valuations.map(v=>option(v.id,selected,
    v.name+' · '+fmtDate(v.created)+' · '+v.id.slice(0,8))).join('');
  function cells(row) { return cols.map(k => `<td>${esc(row[k] || '待核')}</td>`).join(''); }
  setView(`${queue}<section class="card bd"><div class="card-head"><h2>问题与来源</h2></div>
    <p>${esc(q.question)}</p><a class="btn sm" href="#/valuation?project=${encodeURIComponent(project)}&question=${encodeURIComponent(id)}">评估此问题对交易估值的影响</a>${quote ? `<blockquote>${esc(quote.quote)} · ${evidenceLink(quote)}</blockquote>` : ''}<details><summary>查看关联证据（${sources.length}）</summary>${sources.map(e => '<p>'+evidenceLink(e)+'</p>').join('')}</details></section>
    ${valuations.length?`<section class="card bd"><h2>此问题关联的本机估值版本（${valuations.length}）</h2>
    <ol>${valuations.map(v=>`<li><a href="#/valuation?${esc(new URLSearchParams({project,question:id,version:v.id}).toString())}">${esc(v.name)} · ${esc(fmtDate(v.created))}</a>
      <p>估值日 ${esc(v.asOf)} · 百万 ${esc(v.currency)} · 基准现值 ${esc(v.outputs[0].total.toLocaleString('zh-CN',{maximumFractionDigits:4}))}
      · 替代现值 ${esc(v.outputs[1].total.toLocaleString('zh-CN',{maximumFractionDigits:4}))} · 保存时问题状态 ${esc(v.question.status)}</p></li>`).join('')}</ol>
    ${valuations.length>1?`<details id="bd-valuation-compare"><summary>比较两次估值</summary>
    <label>参照版本<select id="bd-val-before">${valuationChoices(valuations.find(v=>v.id===valuations[0].parent)?.id || valuations[1].id)}</select></label>
    <label>比较版本<select id="bd-val-after">${valuationChoices(valuations[0].id)}</select></label>
    <div id="bd-val-difference" aria-live="polite"></div></details>`:''}</section>`:''}
    <section class="card bd"><h2>可比资产表</h2><div class="bd-scroll"><table><thead><tr>${Object.values(bdColumns).map(x=>'<th>'+x+'</th>').join('')}<th>来源</th></tr></thead><tbody>
    ${reference ? '<tr>'+cells(reference)+'<td>'+evidenceLink(quote)+'</td></tr>' : ''}
    ${rows.map(r=>'<tr>'+cells(r)+'<td>'+sourceLink(r)+'</td></tr>').join('')}</tbody></table></div></section>
    <section class="card bd"><h2>答复与复核记录</h2>
    ${events.length ? '<ol>'+events.map(e=>`<li><strong>${esc(bdLabels[e.action])}</strong> · ${esc(e.actor)} · ${esc(fmtDate(e.ts))}
      <p>${esc(bdDecisions[e.after.decision] || '')} ${esc(e.after.note || e.after.asset || '')}</p>
      ${e.after.document_id ? sourceLink(e.after) : ''}</li>`).join('')+'</ol>' : '<p>尚未登记。可先索取材料或添加可比资产。</p>'}
    <form id="bd-form"><label>下一步<select name="kind">${data.allowed[id].map(k=>option(k,'',bdLabels[k])).join('')}</select></label>
    <div id="bd-fields"></div><label>经办人<input name="actor" required maxlength="120"></label>
    <button class="btn primary" type="submit">保存记录</button> <a href="#/report?project=${esc(project)}">查看 Decision Brief →</a><p role="status"></p></form></section>`);
  bindQueue();
  const comparison=$('#bd-valuation-compare');
  if (comparison) {
    const before=$('#bd-val-before'), after=$('#bd-val-after'), output=$('#bd-val-difference');
    const amount=n=>n.toLocaleString('zh-CN',{maximumFractionDigits:4});
    function compare() {
      const a=valuations.find(v=>v.id===before.value), b=valuations.find(v=>v.id===after.value);
      if (a.id===b.id) {output.textContent='请选择两个不同的估值版本。';return;}
      const comparable=a.currency===b.currency && a.asOf===b.asOf;
      const heading=v=>esc(v.asOf)+' · 百万 '+esc(v.currency)+' · 折现率 '+esc(v.rate)+'%';
      const evidence=(x,y)=> (x.disclosures || []).filter(r=>!(y.disclosures || []).some(s=>s.id===r.id))
        .map(r=>`<li>${esc(r.asset || r.subject)} · ${esc(r.field)} · ${esc(r.value)} · <a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.source_date)} 披露</a></li>`).join('');
      const added=evidence(b,a), removed=evidence(a,b);
      output.innerHTML=`<p>参照版本：${heading(a)}<br>比较版本：${heading(b)}</p>
        ${comparable?'':'<p>两版的估值日或币种不同，以下按各自口径列示。</p>'}
        <div class="bd-scroll"><table><thead><tr><th>净现值</th><th>参照版本</th><th>比较版本</th>${comparable?'<th>比较减参照</th>':''}</tr></thead>
        <tbody>${scenarioNames.map((name,i)=>`<tr><th>${name}情景</th><td>${amount(a.outputs[i].total)}</td><td>${amount(b.outputs[i].total)}</td>
        ${comparable?`<td>${amount(b.outputs[i].total-a.outputs[i].total)}</td>`:''}</tr>`).join('')}</tbody></table></div>
        ${added?`<h3>比较版本新增的公开依据</h3><ul>${added}</ul>`:''}
        ${removed?`<h3>比较版本移出的公开依据</h3><ul>${removed}</ul>`:''}`;
    }
    before.onchange=after.onchange=compare;
    compare();
  }
  const form = $('#bd-form');
  const select = form.elements.kind;
  const preferred = {request:'request', reply:'reply', record:'reply', review:'review', experiment:'experiment_result', publish:'publish'}[stages.get(id)];
  select.value = data.allowed[id].includes(preferred) ? preferred : data.allowed[id].find(k=> !['comparison','request'].includes(k)) || 'request';
  const fields = () => {
    const kind = select.value;
    const attached = ['reply','comparison','experiment_result'].includes(kind);
    const input = (name,label) => `<label>${esc(label)}<input required name="${name}" maxlength="4000"></label>`;
    $('#bd-fields').innerHTML =
      (kind === 'comparison' ? '<div class="bd-grid">'+cols.map(k=>input(k,bdColumns[k])).join('')+'</div>' : '') +
      (kind === 'review' ? `<label>判断<select name="decision">${Object.entries(bdDecisions).map(([k,v])=>option(k,'',v)).join('')}</select></label>` : '') +
      (attached ? `<label>项目材料<select name="document_id"><option value="">选择已有文件，或在下方上传</option>${data.documents.map(d=>option(d.document_id,'',d.filename)).join('')}</select></label>
        <label>上传新材料<input type="file" name="attachment"></label>`+input('locator','页码、表号或工作表') : '') +
      (kind === 'comparison' ? '' : `<label>${({request:'所需资料及待解问题',reply:'答复内容和仍缺材料',review:'判断依据与后续动作',experiment_result:'结果、限制与原始数据位置',publish:'写入 Decision Brief 的结论'})[kind]}<textarea required name="note" maxlength="4000"></textarea></label>`);
  };
  fields(); select.onchange = fields;
  form.onsubmit = async e => {
    e.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true;
    try {
      const body = Object.fromEntries(new FormData(form));
      const file = body.attachment;
      delete body.attachment;
      if (file && file.size) {
        const upload = new FormData(); upload.append('files',file);
        const result = await api(base+'/documents?parse=false',{method:'POST',body:upload});
        if (!result.documents.length) throw Error(result.warnings.join(' '));
        body.document_id = result.documents[0].document_id;
      }
      if ('document_id' in body && !body.document_id) throw Error('请选择或上传来源文件');
      body.revision = events.length;
      await api(base+'/followup/'+encodeURIComponent(id),jsonOptions('POST',body));
      if (state.renderSeq === seq) await renderView();
    } catch(err) { form.querySelector('[role=status]').textContent = err.message; }
    finally { button.disabled = false; }
  };
}
