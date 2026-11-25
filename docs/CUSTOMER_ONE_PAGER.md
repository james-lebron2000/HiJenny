# HiJenny 客户一页纸 / Customer One-Pager

**给 License-in / BD 团队的临床尽调工作底稿：3–5 天拿到“可发卖方的问题清单”。**
A traceable clinical due-diligence work product for license-in teams — evidence,
findings and seller questions in days, not weeks.

---

## 你得到什么 / What you get

| 交付物 | 内容 |
|---|---|
| **Seller Question List**（核心） | 去重、合并、按优先级排序，含责任部门、所需文件、证据链接；可直接发卖方 |
| **Evidence Matrix** | 疗效/安全性/研究设计/统计字段 + 文件 · 页码 · 原文逐字引用 · 校验状态 |
| **Consistency & Gap Report** | A 直接矛盾 / B 版本差异 / C 定义差异 / 证据缺口，全部可解释 |
| **Asset 360 仪表盘** | 可交互汇总：资产发现、关键临床事实、8 维就绪度、A/B/C/U、Top 风险/问题，点击跳转原文 |
| **Decision Brief** | 给 BD 负责人/投委会的一页纸：证据支持点、Top 风险、Top 问题、缺失证据 |
| **交付格式** | Markdown / DOCX / PDF 报告 + 8-Sheet Excel（含 Asset Evaluation / Discovery）+ 审核记录 |

## 怎么做到 / How it works

1. **提供资料包**：一个资产（如肿瘤 Phase 2）的 CSR、IB、Protocol、SAP、安全性、CMC、专利等，PDF/PPTX/XLSX。
2. **AI 生成 + 专家复核**：解析 → 结构化证据 → 逐字校验 → 可比性分析 → 缺口检查 → 问题清单 → 报告。
3. **3–5 个工作日交付**：你们审核、修改优先级/状态、导出并发送卖方；系统记录完整审核链。

## 为什么可信 / Why it is trustworthy

- **每条结论可点回原文**：文件 + 页码 + 逐字引用；引用由程序独立复核（准确率 100%，合成语料）。
- **只比较可比数据**：同试验/人群/cutoff/终点定义/评价方法一致才判矛盾；不同版本/定义不会误报。
- **人工在环**：所有 finding/question 都是草稿，需专家签核；不自动给 Go/No-Go。
- **不编造**：没有原文支撑的结论不会进入交付物；扫描件/解析失败会明确标出，而不是猜测。

## 安全 / Security

- 默认**离线规则模式**，不出网；LLM 可选，可指定端点/私有化。
- 项目级隔离、文件白名单、审计事件；企业版路线含 SSO/RBAC、加密、VPC/私有化、审计导出（见 `docs/SECURITY_ONE_PAGER.md`）。
- 文档按**不可信数据**处理，注入文本会被隔离并标记为风险。

## 试点 / Pilot

| 项 | 内容 |
|---|---|
| 范围 | 1 个临床资料包（约 500–2,000 页） |
| 周期 | 3–5 个工作日 |
| 价格 | ¥9,800–29,800 / 项目（试点可换案例与评测数据） |
| 验收 | 关键字段召回 ≥95%；引用准确率 ≥99%；Type A 误报 ≤2 条/包；人工工时节约 ≥50% |
| 下一步 | NDA/DPA → 提供脱敏资料 → 3–5 天交付 → 复盘准确率/误报/工时 |

**注意**：当前仓库为合成示例数据的 MVP；上述指标是试点验收目标，不是已实现的真实客户性能。
产品边界与路线图见 [docs/PRODUCTION_ROADMAP.md](PRODUCTION_ROADMAP.md)。

---

*Demo: `./scripts/demo.sh` · Demo guide: [docs/DEMO_GUIDE.md](DEMO_GUIDE.md) · 示例交付物: [docs/sample_output/](sample_output/)*
