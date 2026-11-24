# HiJenny 安全一页纸 / Security One-Pager

> 面向客户安全问卷与采购评审。区分**当前 MVP** 与**试点/企业版路线**，不宣称未完成的认证。

## 1. 部署选项 / Deployment options

| 模式 | 状态 | 说明 |
|---|---|---|
| 本地/离线（默认） | ✅ MVP | 规则抽取，完全不出网；数据留在本机 |
| 客户 VPC / 私有化 | 🟡 试点可定制 | Docker 部署；LLM 网关可关闭 |
| 托管 SaaS | 🟡 路线图 | 多租户、SSO、区域化，随企业版交付 |

## 2. 数据流与隔离 / Data flow & isolation

- 上传文件 → 项目目录 → 解析（页码/表格）→ 证据库（SQLite；企业版 PostgreSQL）。
- 所有查询按 **project** 过滤；项目间不可见；organization 字段预留多租户。
- `/api/files` 仅允许访问项目 `documents/` 与 `exports/`，阻断路径穿越。
- 默认**不向任何第三方发送数据**；只有显式配置 `HIJENNY_LLM_*` 且非 `rules` 模式时才调用指定端点。

## 3. 模型调用政策 / LLM policy（可选）

- 端点由客户指定（OpenAI 兼容 / 私有化模型），不绑定供应商。
- 文档正文以**数据边界**传递，不作为指令；输出经 schema 校验，引用不在原文页面的证据直接丢弃。
- 建议合同条款：不训练、不留存、区域化处理、子处理商清单（按客户采购要求签署 DPA）。
- 成本上限与调用审计（企业版）：按项目/会话限制 token 与费用。

## 4. 提示注入与内容安全 / Prompt injection

- 文档视为**不可信输入**；规则层跳过指令式语句并记录 `injection_flags`。
- LLM 层：数据隔离 prompt + 输出结构约束 + 引用/页码校验；可疑文本生成 `TECHNICAL_CONCERN` finding。
- 文档内容不能触发网络请求、工具调用或文件系统访问（MVP 无此类工具）。
- 已知限制：模式库为启发式，不能保证抵御所有攻击；真实客户环境需红队测试。

## 5. 访问控制与审计 / Access & audit

| 能力 | MVP | 试点/企业版 |
|---|---|---|
| 登录 / SSO | ❌ 无（本地单用户演示） | 🟡 OIDC/SSO（Azure AD/Google/Okta） |
| RBAC / 项目成员 | ❌ | 🟡 Admin/BD/Clinical/Safety/Biostat/Reviewer |
| 操作审计（谁改了 finding/question） | ✅ review_events | ✅ + 导出/下载/模型调用审计、审计导出 |
| 文件加密 | ❌ | 🟡 TLS + 静态加密（KMS） |
| 备份/删除策略 | ❌ | 🟡 每日备份、按客户保留策略删除 |

> MVP 的定位是“可演示、可试点验证质量”；生产采购前必须补齐 SSO/RBAC/加密/审计导出。

## 6. 数据处理与合规 / Data protection & compliance

- 建议签署 NDA/DPA；明确数据用途、保留期限、删除方式与子处理商。
- 真实资料仅进入客户批准的环境；演示使用合成数据。
- 合规路线图（非现状）：DPIA、SOC 2 Type I / ISO 27001 准备、渗透测试、漏洞管理、
  AI 相关法规边界评估（决策支持工具，不构成医疗诊断/法律/投资建议）。
- 产品输出明确标注“AI 生成，需专家复核；不自动给 Go/No-Go”。

## 7. 已知限制 / Known limitations（请如实告知客户）

- 合成语料评测，非真实客户性能；真实基准测试见 [PRODUCTION_ROADMAP.md](PRODUCTION_ROADMAP.md)。
- 无 OCR（扫描件被标记但不识别）；中文抽取规则覆盖有限。
- SQLite + 单进程线程；试点级并发需迁移 PostgreSQL/队列/对象存储。
- 无生产级账号体系与数据加密；仅供受控演示/试点使用。

## 8. 客户问卷建议回答口径

- **数据是否用于训练？** 默认不；LLM 模式按客户合同约束，可关闭。
- **数据存储在哪？** 默认客户本机/指定 VPC；托管版按区域化部署（路线图）。
- **谁能访问？** MVP 单用户；试点按项目 ACL；企业版 SSO + RBAC。
- **如何删除数据？** 删除项目目录与数据库记录；企业版提供保留策略与删除证明。
- **是否通过认证？** 当前未认证；安全认证在路线图中，不虚假宣称。

*详细工程边界见 [docs/ARCHITECTURE.md](ARCHITECTURE.md)；真实落地计划见 [docs/PRODUCTION_ROADMAP.md](PRODUCTION_ROADMAP.md)。*
