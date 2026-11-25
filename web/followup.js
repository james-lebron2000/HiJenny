'use strict';

const bdLabels = {comparison:'添加可比资产', request:'索取材料', reply:'登记卖方答复',
  review:'验证判断', experiment_result:'登记实验结果', publish:'更新 Decision Brief'};
const bdColumns = {asset:'资产', population:'分析人群', line:'治疗线次', method:'评价方法',
  followup:'随访', endpoint:'终点', value:'结果', limits:'可比性限制'};
const bdDecisions = {documents:'资料复核即可，无需新增实验', experiment:'需要实验验证', clarify:'继续补资料'};

async function renderFollowup(seq) {
  const project = state.projectId;
  const base = '/api/projects/' + encodeURIComponent(project);
  const data = await api(base + '/followup');
  if (seq !== state.renderSeq) return;
  const questions = data.questions;
  const id = hashParams().get('question') || (questions.find(q => /objective response rate/i.test(q.question)) || questions[0] || {}).question_id;
  const q = questions.find(row => row.question_id === id);
  if (!q) { setView(emptyPanel('暂无可跟踪的问题', '请先在本项目生成或选择卖方问题。')); return; }
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
  function cells(row) { return cols.map(k => `<td>${esc(row[k] || '待核')}</td>`).join(''); }
  setView(`<section class="card bd"><div class="card-head"><h2>问题与来源</h2></div>
    <select id="bd-question" aria-label="选择尽调问题">${questions.map(x => option(x.question_id,id,x.question)).join('')}</select>
    <p>${esc(q.question)}</p>${quote ? `<blockquote>${esc(quote.quote)} · ${evidenceLink(quote)}</blockquote>` : ''}<details><summary>查看关联证据（${sources.length}）</summary>${sources.map(e => '<p>'+evidenceLink(e)+'</p>').join('')}</details></section>
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
  $('#bd-question').onchange = e => { location.hash = '#/followup?project='+encodeURIComponent(project)+'&question='+encodeURIComponent(e.target.value); };
  const form = $('#bd-form');
  const select = form.elements.kind;
  select.value = data.allowed[id].find(k=> !['comparison','request'].includes(k)) || 'request';
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
