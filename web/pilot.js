'use strict';

const pilotChoices = {
  stage:['临床前','I 期','II 期','III 期','上市后','待确认'],
  pages:['50 页以内','51–200 页','201–500 页','500 页以上','待确认'],
  deployment:['HiJenny 托管试点','客户专属环境','待讨论']
};
const pilotCopy = {
  zh:{
    title:'为一项资产准备首轮临床尽调',intro:'面向即将召开资产引进评审会的 BD、医学和统计团队。按一项资产、一轮评审确定试点范围。',
    outputs:['内部决策简报，列出影响交易判断的发现、证据缺口与待决事项。','卖方问题单，逐项说明需要补充的材料，供 BD 对外沟通。','证据底稿，保留数字口径、原件位置和专家复核意见。'],
    example:'查看合成样例：同一资产的缓解率为何需要追问？',
    figures:'ASD-101 的样例研究报告第 10 页列出客观缓解率 42.0%（53/126），商业预测第 3 页使用 48.4%（61/126）。',
    question:'请卖方提供两组应答人数的源表，并说明分析人群、截止日和评估方式。内部简报将这项差异列为待核对的估值假设。',
    download:'下载合成尽调报告（英文 Markdown）',quote:'申请试点报价',select:'请选择',email:'工作邮箱',stage:'资产阶段',pages:'预计材料页数',meeting:'预计评审日',deployment:'部署偏好',
    choices:pilotChoices,terms:'资料交接方式、服务费用和交付日期由双方在确认范围时约定。',submit:'提交报价需求',
    receipt:'受理编号',next:'HiJenny 试点交付团队将核对材料范围，并通过所填工作邮箱联系。',last:'本标签页最近的受理编号',error:'提交未确认，请重试。'
  },
  en:{
    title:'Clinical diligence for a single licensing opportunity',intro:'Prepare for an in-licensing review with your BD, medical and statistics teams. Each pilot covers one asset and one round of review.',
    outputs:['An internal decision brief sets out findings, evidence gaps and decisions for the review meeting.',
      'A seller question list specifies which source materials BD should request.',
      'An evidence workbook records how each figure was defined, where it came from and what reviewers concluded.'],
    example:'Synthetic example: why ask the seller about response rates?',
    figures:'For ASD-101, the sample clinical study report gives an objective response rate of 42.0% (53/126) on page 10. The commercial forecast uses 48.4% (61/126) on slide 3.',
    question:'Ask the seller for the source tables behind both counts and the analysis population, cutoff date and assessment method for each. The decision brief flags the valuation assumption for review.',
    download:'Download the synthetic diligence report (English Markdown)',quote:'Request a pilot quote',select:'Select an option',email:'Work email',stage:'Asset stage',pages:'Estimated document volume',meeting:'Expected review date',deployment:'Preferred deployment',
    choices:{stage:['Preclinical','Phase I','Phase II','Phase III','Postmarketing','To be confirmed'],pages:['Up to 50 pages','51–200 pages','201–500 pages','Over 500 pages','To be confirmed'],deployment:['HiJenny-hosted pilot','Customer environment','To be discussed']},
    terms:'Both parties agree on document transfer, fees and delivery dates when confirming the scope.',submit:'Submit quote request',
    receipt:'Request ID',next:'The HiJenny pilot team will review the scope and contact you at the work email provided.',last:'Latest request ID in this tab',error:'Submission could not be confirmed. Please retry.'
  },
  ja:{
    title:'導入候補品目の臨床デューデリジェンス',intro:'導入審査を控えた事業開発、メディカル、生物統計の各担当者向けに、1 品目・1 回の審査を対象とするパイロットを提供します。',
    outputs:['社内審査用の要約に、取引判断に関わる所見、不足している根拠、判断を要する事項をまとめます。',
      '導出側への質問票に、事業開発担当者が追加で依頼する資料を明記します。','根拠資料には、数値の定義、出典箇所、専門家のレビュー所見を記録します。'],
    example:'架空の事例で見る奏効率の確認',
    figures:'ASD-101 の架空の治験総括報告書では、10 ページの客観的奏効率は 42.0%（53/126）です。一方、売上予測資料のスライド 3 には 48.4%（61/126）と記載されています。',
    question:'導出側に、両方の奏効例数の集計表、解析対象集団、データカットオフ日、評価方法を確認します。社内審査用の要約には、資産評価に用いる前提の確認事項として記載します。',
    download:'架空の審査報告書をダウンロード（英語・Markdown）',quote:'パイロットの見積もり依頼',select:'選択してください',email:'勤務先メールアドレス',stage:'開発段階',pages:'資料の想定ページ数',meeting:'審査予定日',deployment:'希望する利用環境',
    choices:{stage:['非臨床','第 I 相','第 II 相','第 III 相','製造販売後','未定'],pages:['50 ページ以下','51～200 ページ','201～500 ページ','501 ページ以上','未定'],deployment:['HiJenny の環境','自社専用環境','要相談']},
    terms:'資料の受け渡し方法、料金、納期は、対象範囲の確認時に双方で決定します。',submit:'見積もりを依頼する',
    receipt:'受付番号',next:'HiJenny の担当チームが対象範囲を確認し、ご入力の勤務先メールアドレスにご連絡します。',last:'このタブの直近の受付番号',error:'送信を確認できませんでした。もう一度お試しください。'
  }
};

function renderPilot() {
  const requested=hashParams().get('lang') || sessionStorage.getItem('hijenny.pilot.lang');
  let lang=Object.hasOwn(pilotCopy,requested)?requested:'zh';
  function draw(values={},expanded=false) {
    const t=pilotCopy[lang];
    const select=key=>`<label>${t[key]}<select name="${key}" required><option value="">${t.select}</option>
      ${pilotChoices[key].map((v,i)=>option(v,values[key],t.choices[key][i])).join('')}</select></label>`;
    setView(`<section class="research-home" lang="${lang}"><label>语言 / Language / 言語
      <select id="pilot-language">${[['zh','中文'],['en','English'],['ja','日本語']].map(([v,l])=>option(v,lang,l)).join('')}</select></label>
      <h1>${t.title}</h1><p>${t.intro}</p><ul>${t.outputs.map(o=>'<li>'+o+'</li>').join('')}</ul>
      <details ${expanded?'open':''}><summary>${t.example}</summary><p>${t.figures}</p><p>${t.question}</p>
      <a href="/api/pilot/sample-report">${t.download}</a></details>
      <form id="pilot-form" class="card"><h2>${t.quote}</h2>
      <label>${t.email}<input name="email" type="email" required maxlength="254" autocomplete="email"></label>
      ${select('stage')}${select('pages')}<label>${t.meeting}<input type="date" name="meeting" required></label>${select('deployment')}
      <p>${t.terms}</p><button class="btn primary" type="submit">${t.submit}</button></form><div id="pilot-receipt" role="status"></div></section>`);
    $('#topbar-title').textContent=t.quote;$('#topbar-sub').textContent='HiJenny';
    const form=$('#pilot-form'), status=$('#pilot-receipt'), button=form.querySelector('button');
    form.elements.email.value=values.email || '';form.elements.meeting.value=values.meeting || '';
    const change=$('#pilot-language');
    change.onchange=()=>{
      lang=change.value;sessionStorage.setItem('hijenny.pilot.lang',lang);
      const params=hashParams();params.set('lang',lang);
      history.replaceState(null,'','#/pilot?'+params.toString());
      draw(Object.fromEntries(new FormData(form)),Boolean(form.parentElement.querySelector('details').open));
    };
    const show=id=>{status.textContent=t.receipt+' '+id+' · '+t.next;};
    const last=sessionStorage.getItem('hijenny.pilot.receipt');
    if (last) status.textContent=t.last+' '+last;
    form.onsubmit=async event=>{
      event.preventDefault();
      if (button.disabled) return;
      const data=Object.fromEntries(new FormData(form));button.disabled=change.disabled=true;
      try {
        data.email=data.email.trim();
        const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(data)));
        const key='hijenny.pilot.'+Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,'0')).join('');
        data.request_key=sessionStorage.getItem(key) || crypto.randomUUID();
        sessionStorage.setItem(key,data.request_key);
        const saved=await api('/api/pilot/inquiries',jsonOptions('POST',data));
        show(saved.receipt);sessionStorage.setItem('hijenny.pilot.receipt',saved.receipt);
      } catch(err) {status.textContent=t.error;}
      finally {button.disabled=change.disabled=false;}
    };
  }
  draw();
}
