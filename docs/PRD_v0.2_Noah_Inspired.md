# HiJenny v0.2 / v1.0 PRD
## 从「尽调工具」到「证据优先的医药交易智能工作台」

> 文档版本：v0.2 草案 · 日期：2026-11-26 · 作者：HiJenny 产品组
> 参考对象：[Noah AI](https://www.noah.bio/)（生命科学 AI Agent 平台）
> 关联文档：[产品设计](PRODUCT.md) · [生产化路线](PRODUCTION_ROADMAP.md) · [评测](EVALUATION.md) · [交接文档](HANDOFF.md)

---

## 0. 背景与结论

### 0.1 为什么要看 Noah

从公开信息看，[Noah 官网](https://www.noah.bio/) 的定位是 **“AI Agent for Life-Science Professionals”**；公开博客覆盖 [药物管线、临床试验与市场信号追踪](https://www.noah.bio/blog/drug-pipeline-clinical-trial-market-signals)、[临床试验结果分析](https://www.noah.bio/blog/analyze-clinical-trial-results-with-ai)；公开报道提到其 **AI Analyst 面向生物医药投资与商业决策**，并提供[订阅定价](https://www.noah.bio/pricing)。其产品形态不是“文档问答”，而是 **外部数据持续更新 + 多场景 Agent + 专业工作台 + 订阅制**。

> 说明：本节依据 Noah 官网标题、公开博客与公开报道的定位归纳；未获取其完整产品后台与全部功能清单，具体能力以其官方为准。

### 0.2 一句话结论

**HiJenny 不应该复制 Noah 的“横向数据平台”，而应该做“纵向交易尽调操作系统”：以内部 VDR 的逐字证据链与六维可比性为护城河，叠加 Noah 式的外部资产情报、Agent 交互、持续监测和团队协作。**

### 0.3 三个必须补齐的差距

| 差距 | Noah 的做法（公开信息归纳） | HiJenny 现状 | 补齐方式 |
|---|---|---|---|
| **外部上下文** | 管线/试验/市场/文献/新闻持续更新 | 只分析上传的 VDR，外部世界不可见 | 资产图谱 + 外部数据连接器 |
| **Agent 体验** | 自然语言研究、AI Analyst、任务化 | 固定流水线 + 表单式 UI | “Ask HiJenny” + 工具调用 + 引用卡 |
| **持续使用** | 订阅制、日常研究工作台 | 一次性项目、交付后沉默 | Watchlist + 变更提醒 + 决策备忘录 |

---

## 1. 借鉴 Noah 的 8 条产品判断

### 1.1 要借鉴

1. **从“工具”到“工作台”**：用户每天回来，不是因为要跑一次 DD，而是因为要看资产变化、写备忘录、和团队协作。
2. **外部数据是入口**：没有 VDR 也可以先建资产页（NCT、公司、阶段、竞品、交易），再逐步上传资料；降低首次使用门槛。
3. **Agent 不是聊天框，是任务**：AI Analyst 应能完成“比较两个资产”“生成 IC 备忘录”“追踪某靶点”这类任务，并返回可引用产物。
4. **订阅 + 席位**：免费/试用 → 专业版 → 团队/企业；按席位、连接器、数据量、私有化分层。
5. **横向覆盖、纵向深挖**：用统一资产图谱覆盖管线/试验/交易，再在尽调场景做深。
6. **数据新鲜度即价值**：新试验、读数、批准、交易、文献、新闻的变化要主动推送。
7. **专业用户要“可核查”**：每个结论给来源；Noah 的引用体验是基线，HiJenny 的逐字证据链是升级。
8. **团队协作**：评论、@、共享链接、权限、审阅状态，是付费转化的关键。

### 1.2 不要照搬

1. **不要做通用“什么都能问”的聊天机器人**：HiJenny 的差异是“可审计、可签字”的证据链，泛化会稀释信任。
2. **不要一开始自建全量临床数据库**：成本极高；先接 ClinicalTrials.gov / PubMed / 监管公告 / 公司管线，交易数据可后补。
3. **不要做自动 Go/No-Go、自动估值**：监管、医学、法务责任无法由模型承担；输出“决策支持 + 待确认问题”。
4. **不要为了订阅而堆功能**：先把“资产发现 → 证据尽调 → 卖方问题 → 决策备忘录”闭环做穿。

---

## 2. 现状与差距（HiJenny 审计）

### 2.1 已有优势（必须守住）

- **Evidence-first**：Document → Page → Evidence → Finding → Question 全链路可追溯；每条证据有页码 + 逐字引用 + 核验状态。
- **六维可比性门控**：同试验/同臂/同人群/同 cutoff/同终点定义/同评价方法才做数值比对；区分 A/B/C/U。
- **确定性流水线**：7 阶段、幂等、可重跑、版本 supersede；规则模式离线、0 LLM 依赖。
- **Asset 360**：资产发现 + 8 维尽调就绪度 + 风险/一致性/覆盖度一屏汇总。
- **BD 复核闭环**：可比资产、索取材料、卖方答复、验证判断、更新 Decision Brief，全程 audit trail。
- **交付物**：MD/DOCX/PDF + 8-Sheet Excel + 审核记录。
- **质量基线**：177 个测试、CI、合成语料 36/36、引用 100%、Type A P/R/F1 100%。

### 2.2 结构性短板

| 短板 | 影响 | 优先级 |
|---|---|---|
| 只有内部 VDR，没有外部资产上下文 | 无法做 sourcing、竞品、交易对比；Asset 360 只是“资料包 360” | P0 |
| 一次性项目，无 watchlist/变更提醒 | 尽调结束即流失；无法形成订阅留存 | P0 |
| 无自然语言 Agent | 新用户学习成本高；无法完成“比较/总结/起草”任务 | P0 |
| 单人使用，无评论/权限/共享 | 无法进入团队采购；审核链断裂 | P0 |
| 无企业级认证/加密/多租户 | 无法通过药企/投资机构安全评审 | P0（试点前） |
| 无 sourcing/scouting | 只服务“已选定资产”，不服务“找资产” | P1 |
| 无跨资产 comps/benchmark | 估值与 IC 讨论缺参照 | P1 |
| 无 VDR 连接器 | 仍需人工下载上传 | P1 |

---

## 3. 产品战略

### 3.1 定位

> **HiJenny = 医药交易团队的证据优先智能工作台。**
> 外部资产情报帮你“找到并看懂资产”；内部 VDR 证据链帮你“证明并签得下去”；Agent 与协作帮你“更快形成决策”。

### 3.2 三层产品结构

```
┌─────────────────────────────────────────────┐
│  决策层：Decision Memo / IC Brief / 问题清单  │
├─────────────────────────────────────────────┤
│  情报层：Asset Graph + 外部数据 + Watchlist   │
├─────────────────────────────────────────────┤
│  证据层：VDR 文档 → 逐字证据 → 可比性 → 发现  │
└─────────────────────────────────────────────┘
```

### 3.3 差异化壁垒

1. **证据链深度**：外部平台能给“数据 + 引用”，HiJenny 能给“页码 + 原文 + 核验 + 可比性判定 + 人工复核记录”。
2. **交易场景闭环**：从资产发现到卖方问题、BD 复核、Decision Brief，别人给数据，HiJenny 给“可签字的底稿”。
3. **可审计性**：每个结论可回放、可签字、可导出；这是药企/投资机构真正付费的点。
4. **离线/私有化**：默认离线规则模式，可进客户 VPC；横向 SaaS 平台通常不具备。

---

## 4. 目标与成功指标

### 4.1 产品目标（12 个月）

- 从“单项目 DD 工具”升级为“资产级、团队级、持续使用的交易工作台”；
- 让用户在 **30 分钟内**从 0 得到一个“可讨论的资产初判 + 问题清单”；
- 让尽调结论在交易后持续被监控和更新，而不是交付即结束。

### 4.2 北极星指标

**每周完成“有证据支撑的决策动作”数**（Decision-ready actions / week）：
= 生成并被人评审/评论/导出的 Decision Memo + 已关闭的卖方问题 + 更新的投资判断。

### 4.3 关键指标

| 层级 | 指标 | v0.2 目标 | v1.0 目标 |
|---|---|---|---|
| 激活 | 新用户 30 分钟内生成首份 Memo | ≥60% | ≥75% |
| 留存 | 次周回访率（Watchlist 用户） | ≥35% | ≥50% |
| 协作 | 每项目 ≥2 名活跃成员 | ≥30% | ≥50% |
| 质量 | 引用可核验率 | ≥99% | ≥99.5% |
| 质量 | 真实包字段召回（人工双人裁定） | ≥90% | ≥95% |
| 质量 | Type A 误报 | ≤3/包 | ≤2/包 |
| 效率 | 首份 Memo 时间 | ≤60 min | ≤30 min |
| 商业 | 付费试点 | 3 个 | 10 个 |
| 商业 | 试点转年费 | — | ≥40% |

---

## 5. 用户与 JTBD

| 角色 | 典型任务 | 痛点 | 成功标准 |
|---|---|---|---|
| **BD 负责人（买家）** | 找资产、初筛、IC 材料、卖方沟通 | 信息散、时间不够、问题不专业 | 30 分钟得到可开会材料；问题可直接发卖方 |
| **临床/医学 DD** | 验证 ORR/PFS/安全性、设计可比性 | 版本/人群/cutoff 混淆 | 每个数字可回原文；矛盾可解释 |
| **生物统计** | 终点定义、SAP、统计方法、敏感性 | 不同分析集混用 | 六维可比性 + 统计风险清单 |
| **投资分析师/合伙人** | 赛道扫描、竞品、交易对比、投后监控 | 外部数据与内部材料割裂 | 一页资产页 + 变更提醒 |
| **注册/CMC/IP（次级）** | 监管路径、专利、CMC 风险 | 资料散、无结构化 | 外部信息 + 待补清单 |
| **外部顾问/律师** | 审阅、评论、签字 | 无权限/无审计 | 只读/评论 + 审计导出 |

### 核心 Jobs-to-be-Done

1. “当我看到一个资产，我想在 30 分钟内知道它值不值得深入，以及最该问卖方什么。”
2. “当我拿到 VDR，我想快速定位关键数字和矛盾，并确保每个结论有原文。”
3. “当我要上 IC，我想一键生成有证据、有风险、有问题的备忘录。”
4. “当交易推进/投后，我想持续知道这个资产发生了什么变化。”
5. “当团队协作时，我想知道谁改了什么、谁签字、依据是什么。”

---

## 6. 版本范围

### v0.2 — Asset Intelligence（4–6 周，可演示）

**目标：把外部上下文 + Agent + 持续监测 + 协作的“骨架”做出来，形成可卖的故事。**

| 模块 | 范围 |
|---|---|
| 外部资产页 | ClinicalTrials.gov 试验、公司管线、PubMed 文献、监管公告；资产统一 ID；外部来源标注 |
| Asset 360 v2 | 内部证据 + 外部 context tabs；外部与文档冲突标记 |
| Ask HiJenny | 自然语言问答/任务；工具调用；答案带引用；保存到 Memo |
| Decision Memo | 一键生成 IC-ready 备忘录；可编辑；导出 DOCX/PDF |
| Watchlist | 关注资产/靶点/公司；每日/每周变更摘要 |
| 团队协作 | 评论、@、角色（Owner/Reviewer/Viewer）、共享链接、审计 |
| 导入 | 从 NCT/公司管线 URL 创建项目；无文档也能先出“外部初判” |

### v1.0 — Deal Workspace（8–12 周，试点可用）

| 模块 | 范围 |
|---|---|
| Scout | 按靶点/ modality/阶段/适应症筛选资产；评分卡；shortlist；可比交易 |
| Monitor | 新试验/读数/批准/交易/文献触发；自动 diff；提醒 |
| Agent 库 | Clinical / Stats / Regulatory / Commercial Analyst 模板；可复用任务 |
| 企业级 | SSO/RBAC、Postgres/S3、审计导出、数据驻留、VDR 连接器（Intralinks/Datasite/Box） |
| API/MCP | 供客户系统/内部工具调用 |
| 跨资产 benchmark | comps、疗效/安全对标、估值参考区间（非自动估值） |

### 明确不做（Non-goals）

- 自动 Go/No-Go、自动估值、自动交易决策；
- 替代医学/统计/法务专家签字；
- 自建全量临床数据库（先接外部源）；
- 通用聊天/写作助手（所有回答必须落到资产/证据）；
- 完整 CMC/IP/注册深度审阅（v2 再扩）。

---

## 7. 功能需求（FR）

### 7.1 外部资产图谱（P0）

- **FR-01** 资产主数据：规范名、别名、靶点、modality、适应症、阶段、公司、NCT、分子/商品名；人工可合并/拆分。
- **FR-02** 外部来源：ClinicalTrials.gov、PubMed、FDA/EMA/NMPA 公告、公司官网管线；每条外部记录带 URL、抓取时间、来源类型。
- **FR-03** 实体解析：名称/NCT/公司/靶点匹配；置信度；低置信度需人工确认，不自动并入。
- **FR-04** 展示：资产页显示“内部证据 / 外部试验 / 竞品 / 交易 / 文献 / 新闻”分栏；外部信息明确标 `EXTERNAL`。
- **FR-05** 冲突标记：外部试验结果与 VDR 报告数字不一致时，生成“外部 vs 内部”待核项，不直接判矛盾。

**验收**：从 NCT 号创建资产页 ≤10 秒；外部记录 100% 有 URL 与抓取时间；低置信度不自动合并。

### 7.2 Ask HiJenny Agent（P0）

- **FR-06** 输入自然语言问题，Agent 选择工具：检索内部证据、查外部试验、比较资产、读页面、生成 Memo。
- **FR-07** 回答必须带引用卡：内部证据 → 文档/页码/quote；外部 → URL/来源/时间。
- **FR-08** 不知道/证据不足时明确说“不足以回答”，并给出需要补充的材料；不得编造。
- **FR-09** 支持把回答保存为 Memo 段落/问题/评论。
- **FR-10** 任务型 Agent：比较两个资产、生成某资产风险清单、起草卖方问题、总结某试验结果。
- **FR-11** 所有工具调用、模型输出、引用进入审计日志。

**验收**：10 个预置评测问题中，引用可核验率 ≥99%，无引用断言 = 0，拒答正确率 ≥90%。

### 7.3 Decision Memo（P0）

- **FR-12** 模板：资产概览、机制/差异化、疗效、安全性、可比性、监管/CMC/IP 待补、商业、Top 风险、卖方问题、待确认事项。
- **FR-13** 每个结论可选“插入证据卡”；未插入证据的结论标黄“待补证据”。
- **FR-14** 多人编辑/评论；版本历史；一键导出 DOCX/PDF；可分享只读链接。
- **FR-15** 与报告/8-Sheet Excel 数据一致；Memo 更新触发报告版本。

**验收**：从项目一键生成 Memo ≤10 秒；导出格式不丢引用；评审人可留痕。

### 7.4 Watchlist & 变更（P0）

- **FR-16** 关注资产/靶点/公司；分组；负责人。
- **FR-17** 变更事件：新试验、状态变化、读数、批准、交易、文献、新闻；去重、分级。
- **FR-18** 每日/每周摘要（站内/邮件）；可配置频率；可静音。
- **FR-19** 变更可一键“重新分析/更新 Memo”，保留 diff。

**验收**：新试验 24h 内出现；摘要可一键退订；变更可追溯到来源。

### 7.5 协作与权限（P0）

- **FR-20** 角色：Owner / Reviewer / Viewer / Guest；项目级权限。
- **FR-21** 评论、@、解决状态；@ 通知。
- **FR-22** 共享链接（只读/可评论/可编辑）；过期时间。
- **FR-23** 审计：谁在何时改了什么；可导出 CSV/PDF。
- **FR-24** 签字/批准状态：Draft → In review → Approved；Approved 后修改需新版本。

**验收**：外部顾问只能看指定项目；所有修改有审计；共享链接可撤销。

### 7.6 Scout（P1）

- **FR-25** 筛选器：靶点、modality、阶段、适应症、公司、地区、试验状态、日期。
- **FR-26** 评分卡：科学差异化、临床阶段、竞争格局、交易活跃度、证据质量；权重可配。
- **FR-27** Shortlist + 对比表 + 导出；从 shortlist 一键建项目。
- **FR-28** 可比交易：首付/里程碑/总额/阶段/适应症；来源标注（不保证完整）。

### 7.7 企业级（P1）

- **FR-29** SSO（SAML/OIDC）、SCIM、RBAC、审计导出。
- **FR-30** Postgres/S3、队列、水平扩展；数据驻留选项。
- **FR-31** VDR 连接器（Intralinks/Datasite/Box/SharePoint）。
- **FR-32** API/MCP：资产、证据、问题、Memo 的读写；速率限制与密钥管理。
- **FR-33** 私有化/离线部署包；模型可 BYOK。

---

## 8. 关键用户流程

### 流程 A：从外部资产到首份 Memo（v0.2 核心）

1. 用户搜索 “PRMT5 inhibitor” 或粘贴 NCT；
2. 进入资产页：外部试验/公司/竞品/文献先展示；
3. 点击“开始尽调”→ 建项目 → 上传 VDR；
4. 流水线解析/抽取/一致性/缺口/问题；
5. Asset 360 展示“内部 + 外部”对照，冲突项标黄；
6. Ask HiJenny：“这个资产最该问卖方的 5 个问题是什么？”
7. 保存为 Memo → 邀请 Reviewer 评论 → 导出 PDF。

### 流程 B：投后/交易推进监控

1. 关注资产；设置每日摘要；
2. 新试验/读数触发提醒；
3. 打开变更 diff → 点击“重新分析”；
4. 更新 Memo/问题状态；通知团队；
5. 审计记录保留。

### 流程 C：团队评审与签字

1. Owner 生成 Memo Draft；
2. Reviewer 逐条评论/@；
3. 解决评论 → 标记 Approved；
4. 生成只读共享链接给 IC/外部律师；
5. 导出带审计页的 PDF。

### 流程 D：Scout → 深度尽调（v1.0）

1. 设置筛选（靶点/阶段/适应症）；
2. 查看评分卡/shortlist；
3. 对比 3 个资产；
4. 选择 1 个建项目，进入流程 A。

---

## 9. 信息架构与页面

```
Today（首页）
├── Watchlist 变更摘要
├── 待办：待评论/待补证据/待更新 Memo
└── 最近项目

Assets（资产图谱）
├── 搜索/筛选
├── 资产页：Overview | Trials | Competitors | Deals | Literature | News
└── 内部证据入口（如有 VDR 项目）

Projects（尽调项目）
├── Upload
├── Asset 360
├── Evidence
├── Findings & Questions
├── BD 复核
├── Memo
└── Report

Scout（v1.0）
Monitor（v1.0）
Memos（全局）
Ask HiJenny（全局侧边栏）
Admin（数据源/团队/权限/审计）
```

**设计原则**：外部信息永远带 `EXTERNAL` 标签；内部证据永远可点回页码；两者不混排而不标注。

---

## 10. 数据模型与外部数据源

### 10.1 新增核心表

| 表 | 关键字段 |
|---|---|
| `asset_master` | asset_id、canonical_name、aliases、target、modality、company、stage、ids（NCT/ChEMBL/DrugBank） |
| `external_source` | source_id、type、name、url、license、refresh_policy |
| `external_record` | record_id、asset_id、source_id、record_type、payload、url、fetched_at、confidence |
| `asset_link` | from_asset、to_asset、relation（competitor/parent/partner）、confidence |
| `watchlist` | user/team、entity_type、entity_id、frequency |
| `alert_event` | event_id、watchlist_id、type、severity、payload、read_at |
| `memo` | memo_id、project_id、version、status、content、approved_by |
| `comment` | entity、author、body、resolved_at |
| `workspace_member` | workspace_id、user_id、role |
| `connector_sync` | connector、last_run、status、errors |

### 10.2 外部数据源优先级

| 优先级 | 数据源 | 用途 | 许可注意 |
|---|---|---|---|
| P0 | ClinicalTrials.gov API | 试验、状态、终点、结果 | 公共数据，遵守条款 |
| P0 | PubMed E-utilities | 文献、摘要、作者 | 公共 API，限速 |
| P0 | FDA/EMA/NMPA 公告页 | 批准/标签/安全更新 | 公开信息，需注明来源 |
| P0 | 公司官网/管线页 | 管线、阶段、合作 | 抓取合规，注意 robots |
| P1 | 专利（PatentsView/Google Patents） | 专利族、到期、FTO 初筛 | 许可与引用 |
| P1 | 交易数据库（公开报道/公司公告） | 首付/里程碑/总额 | 不保证完整，标“公开报道” |
| P2 | 流行病学/市场数据 | 患者数、市场规模 | 商业授权 |

**原则**：外部数据只做“上下文与线索”，不作为最终结论；每条记录保留来源、时间、许可。

---

## 11. Agent 设计

### 11.1 工具集

| 工具 | 作用 | 约束 |
|---|---|---|
| `search_evidence(project, query)` | 检索内部证据 | 只返回已核验证据；带页码/quote |
| `get_asset(asset_id)` | 资产主数据 + 外部记录 | 标 `EXTERNAL` |
| `search_trials(query)` | 外部试验检索 | 返回 NCT/URL/时间 |
| `compare_assets(ids)` | 多维对比 | 只比较同维度；缺失标 N/A |
| `read_page(doc, page)` | 读原文 | 禁止跨页拼接 |
| `draft_memo(section)` | 起草 Memo | 必须引用；未引用标黄 |
| `add_question(...)` | 生成问题 | 需去重、优先级、责任部门 |

### 11.2 回答契约

```
每个结论 = Claim + Evidence[] + Confidence + Caveat
- Evidence 内部：doc_id, page, quote, evidence_id
- Evidence 外部：url, source, fetched_at
- 不足：明确“不足以判断”，列出需要补充的资料
- 外部与内部冲突：并列展示，不自动选边
```

### 11.3 Agent 评测

- 引用精确率/召回率；
- 无引用断言率（目标 0）；
- 拒答正确率；
- 任务完成率（比较/起草/汇总）；
- 人工采纳率；
- 延迟/成本（P95）。

---

## 12. 架构与技术

```
[Connectors] ClinicalTrials / PubMed / FDA / Company sites
        ↓ (scheduled, rate-limited, licensed)
[Raw store] → [Normalizer] → [Asset Graph] → [Search/Index]
                                   ↑
[Internal VDR] → [Parser] → [Evidence Store] → [Consistency/Gap]
                                   ↓
[Retrieval] → [Agent Orchestrator + Tool Registry] → [Memo/Report]
                                   ↓
[Web Workspace] [API/MCP] [Audit]
```

- **保留确定性核心**：解析、抽取、校验、一致性、缺口仍走规则/流水线；LLM 只做检索编排、总结、起草、比较。
- **引用服务**：统一 citation service，所有输出必须经过 citation 校验；无引用断言拦截。
- **异步任务**：连接器同步、重新分析、Memo 生成走队列；前端轮询/SSE。
- **多租户**：workspace_id 贯穿；Postgres RLS；S3 前缀隔离。
- **可观测**：连接器成功率、数据新鲜度、Agent 工具调用、引用校验失败率。
- **部署**：SaaS / 客户 VPC / 私有化；离线模式保留。

---

## 13. 协作、权限与安全

- 角色：Owner / Reviewer / Viewer / Guest；项目级 + 资产级。
- 共享：只读/评论/编辑链接；过期；水印（企业版）。
- 审计：所有写操作、Agent 输出、导出、共享；可导出。
- 安全：SSO、RBAC、加密（传输/静态）、密钥管理、数据驻留、渗透测试。
- 合规：客户数据不训练模型；LLM 可关闭；外部数据许可清单；AI 生成标识。
- 数据保留/删除：项目删除策略、导出后删除、备份保留期。

---

## 14. 评测与质量门

| 门 | 标准 | 阻塞 |
|---|---|---|
| 引用 | 100% 内部引用可回原文页；外部 100% 有 URL | 发布 |
| 一致性 | A/B/C/U 规则回归；Type A 误报 ≤2/包 | 试点 |
| 抽取 | 真实包字段召回 ≥95%（双人裁定） | 试点 |
| Agent | 无引用断言 = 0；拒答正确 ≥90% | 发布 |
| 安全 | 注入攻击不采纳；越权测试通过 | 试点 |
| 性能 | 100 页包首份 Memo ≤30 min；API P95 <2s | 发布 |

---

## 15. 发布计划与资源

### v0.2（4–6 周）

| 周 | 交付 |
|---|---|
| W1 | 数据模型/资产主数据/ClinicalTrials+PubMed 连接器；Agent 工具契约 |
| W2 | Asset 360 v2（外部 tabs/冲突标记）；项目导入 NCT |
| W3 | Ask HiJenny v0（问答+引用+保存）；Memo v0 |
| W4 | Watchlist/变更摘要；评论/角色/共享 |
| W5 | 端到端评测、安全加固、Demo 数据与话术 |
| W6 | 3 个设计伙伴试用；修复 |

**团队**：PM 1、后端 2、前端 1、数据/连接器 1、临床/统计顾问 0.5、设计 0.5。

### v1.0（8–12 周）

Scout、Monitor、Agent 库、企业级、VDR 连接器、API/MCP、跨资产 benchmark。

---

## 16. 商业模式与定价（建议）

| 层级 | 目标 | 价格（建议，需验证） | 包含 |
|---|---|---|---|
| Free/Demo | 试用/活动 | ¥0 | 1 项目、只读 Demo、限制导出 |
| Pro | 单个 BD/分析师 | ¥2,000–4,000/席/月 | 项目、Asset 360、Ask、Memo、导出 |
| Team | BD/投资团队 | ¥15k–40k/月（5 席起） | 协作、权限、审计、Watchlist、共享 |
| Enterprise | 药企/机构 | ¥50 万–200 万/年 | SSO、VPC/私有化、VDR 连接器、API、SLA、培训 |
| Pilot | 首个客户 | ¥9.8k–29.8k/3–5 天 | 1 个资产、报告+问题清单+复盘 |

> 定价必须通过 5–10 个客户访谈验证；不要按“功能数量”定价，按“节省的专家时间 + 交易决策价值”定价。

---

## 17. 风险与开放问题

### 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 外部数据许可/抓取合规 | 法律/停服 | 优先公共 API；签授权；保留来源；法务评审 |
| 实体解析错误 | 错误资产页 | 置信度 + 人工确认；可回滚 |
| 外部与内部冲突误导 | 决策错误 | 并列展示 + 标注；不自动选边 |
| Agent 幻觉 | 信任崩塌 | 引用强制 + 无引用拦截 + 拒答 |
| 与 Noah 正面竞争 | 资源不足 | 纵向深挖 + 私有化 + 证据链 |
| 范围蔓延 | 延期 | v0.2 只做骨架；Non-goals 写死 |
| 企业安全评审 | 无法签约 | P0 补 SSO/RBAC/加密/审计 |

### 开放问题

1. 首批客户是药企 BD 还是投资机构？两者对数据/协作/估值需求不同。
2. 外部数据先做全球还是中国？ClinicalTrials.gov 全球可用，NMPA/CDE 需单独适配。
3. 是否接受“外部数据不完整”的免责声明？交易数据尤其如此。
4. Agent 是否默认开启 LLM？药企可能要求完全离线。
5. 是否把 Memo/协作作为 v0.2 的必选，还是先做外部数据？
6. 定价按席位还是按项目/资产数？

---

## 18. 验收清单（v0.2）

- [ ] 从 NCT/URL 10 秒建资产页，外部试验/文献/公司信息可看；
- [ ] Asset 360 同时展示内部证据与外部 context，冲突项标黄；
- [ ] Ask HiJenny 回答 100% 带引用，无引用断言为 0；
- [ ] 一键生成 Memo（DOCX/PDF），每个结论可插证据；
- [ ] Watchlist 每日摘要可收/可退订，变更可追溯；
- [ ] 邀请 Reviewer 评论/@，共享只读链接，审计可查；
- [ ] 3 个设计伙伴完成真实包试用并给出 NPS/问题清单；
- [ ] 安全评审清单（SSO/RBAC/加密/审计）完成 P0 项；
- [ ] 评测门全部通过（引用/一致性/抽取/Agent/注入/性能）。

---

## 附录 A：竞品/替代方案对比

| 维度 | Noah（公开定位） | HiJenny 目标 | Citeline/Evaluate/GlobalData | VDR（Intralinks 等） |
|---|---|---|---|---|
| 外部管线/试验数据 | 强 | 中→强（连接器） | 强 | 无 |
| 内部 VDR 逐字证据 | 弱/未知 | **强** | 弱 | 强（存储，不分析） |
| 六维可比性/一致性 | 弱/未知 | **强** | 中 | 无 |
| 卖方问题/BD 复核 | 弱/未知 | **强** | 弱 | 无 |
| Agent 自然语言 | 强 | 中→强 | 弱 | 无 |
| 审计/可签字 | 未知 | **强** | 中 | 强（权限） |
| 私有化/离线 | 未知 | **强** | 弱 | 强 |
| 定价 | 订阅 | 席位+企业 | 高额订阅 | 按项目/席位 |

> 竞品能力为公开信息/行业常识归纳，具体以官方为准；不要把对比表直接对外，需法务审核。

## 附录 B：Noah 参考链接

- [Noah 官网｜AI Agent for Life-Science Professionals](https://www.noah.bio/)
- [Noah Pricing & Plans](https://www.noah.bio/pricing)
- [Noah Blog｜Drug pipeline, clinical trials, market signals](https://www.noah.bio/blog/drug-pipeline-clinical-trial-market-signals)
- [Noah Blog｜Analyze clinical trial results with AI](https://www.noah.bio/blog/analyze-clinical-trial-results-with-ai)
- [Noah AI Tutorial](https://www.noah.bio/blog/tutorial)
- [Noah AI Analyst 发布（BIO Asia-Taiwan 展商新闻）](https://expo.bioasiataiwan.com/en//visitorExNewsDetail.asp?id=20723)
- [36氪｜若生科技（Noah AI）Pre-A+ 轮](https://36kr.com/p/3596513788182532)
- [Pandaily｜Noah AI Pre-A+ round](https://pandaily.com/noah-ai-completes-oversubscribed-pre-a-round-to-accelerate-deployment-of-life-sciences-ai-agents)

---

*本文为产品建议与 PRD 草案，不构成投资/法律/医疗建议；外部数据与竞品信息需在立项前由法务与数据团队复核。*
