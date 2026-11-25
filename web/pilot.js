'use strict';

function renderPilot() {
  const select=(key,title,choices)=>`<label>${title}<select name="${key}" required><option value="">请选择</option>
    ${choices.map(c=>option(c,'',c)).join('')}</select></label>`;
  setView(`<section class="research-home"><h1>为一项资产准备首轮临床尽调</h1>
    <p>面向即将召开资产引进评审会的 BD、医学和统计团队。按一项资产、一轮评审确定试点范围。</p>
    <ul><li><strong>内部决策简报</strong>，列出影响交易判断的发现、证据缺口与待决事项。</li>
    <li><strong>卖方问题单</strong>，逐项说明需要补充的材料，供 BD 对外沟通。</li>
    <li><strong>证据底稿</strong>，保留数字口径、原件位置和专家复核意见。</li></ul>
    <details><summary>查看合成样例：同一资产的缓解率为何需要追问？</summary>
    <p>ASD-101 的样例研究报告第 10 页列出客观缓解率 42.0%（53/126），商业预测第 3 页使用 48.4%（61/126）。</p>
    <p>卖方问题：请提供两组应答人数的源表，并说明分析人群、截止日和评估方式。内部简报将这项差异列为待核对的估值假设。</p>
    <a href="/api/pilot/sample-report">下载合成尽调报告（Markdown）</a></details>
    <form id="pilot-form" class="card"><h2>申请试点报价</h2>
    <label>工作邮箱<input name="email" type="email" required maxlength="254" autocomplete="email"></label>
    ${select('stage','资产阶段',['临床前','I 期','II 期','III 期','上市后','待确认'])}
    ${select('pages','预计材料页数',['50 页以内','51–200 页','201–500 页','500 页以上','待确认'])}
    <label>预计评审日<input type="date" name="meeting" required></label>
    ${select('deployment','部署偏好',['HiJenny 托管试点','客户专属环境','待讨论'])}
    <p>本页仅收集报价所需信息。资料交接方式、服务费用和交付日期由双方在确认范围时约定。</p>
    <button class="btn primary" type="submit">提交报价需求</button></form>
    <div id="pilot-receipt" role="status"></div></section>`);
  const form=$('#pilot-form'), status=$('#pilot-receipt'), button=form.querySelector('button');
  const show=id=>{status.textContent='受理编号 '+id+'。下一步由 HiJenny 试点交付团队核对材料范围，并通过所填工作邮箱联系。';};
  const last=sessionStorage.getItem('hijenny.pilot.receipt');
  if (last) status.textContent='本标签页最近的受理编号 '+last;
  form.onsubmit=async event=>{
    event.preventDefault();
    if (button.disabled) return;
    const data=Object.fromEntries(new FormData(form));button.disabled=true;
    try {
      data.email=data.email.trim();
      const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(data)));
      const key='hijenny.pilot.'+Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,'0')).join('');
      data.request_key=sessionStorage.getItem(key) || crypto.randomUUID();
      sessionStorage.setItem(key,data.request_key);
      const saved=await api('/api/pilot/inquiries',jsonOptions('POST',data));
      show(saved.receipt);sessionStorage.setItem('hijenny.pilot.receipt',saved.receipt);
    } catch(err) {status.textContent='提交未确认，请重试。'+err.message;}
    finally {button.disabled=false;}
  };
}
