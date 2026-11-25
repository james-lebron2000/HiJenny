# HiJenny 项目交接文档

> 版本：v0.1（客户/投资人演示版） · 更新：2026-11-25 · 适用对象：BD、临床、统计、注册、工程、产品同事
>
> 一句话：**HiJenny 把卖方 1,000+ 页临床资料包，变成可溯源、可评估、可发卖方问题清单的尽调工作底稿。**

---

## 0. 30 秒速览

| 项目 | 内容 |
|---|---|
| 产品定位 | AI-native 医药资产交易智能尽调（Evidence-first due diligence） |
| 首个场景 | 创新药 License-in 买方临床尽职调查（肿瘤 Phase 1–3） |
| 当前状态 | **Demo-ready MVP**：端到端可运行、177 个自动化测试、CI 通过、可现场演示 |
| 演示地址 | `http://127.0.0.1:8123/`（本机运行 `./scripts/demo.sh` 启动） |
| 演示项目 | ASD-101（合成数据）：13 文档 / 284 页 / 136 证据 / 11 findings / 6 问题 / 65/100 尽调就绪度 |
| 代码仓库 | https://github.com/james-lebron2000/HiJenny |
| 核心交付物 | Asset 360 仪表盘、证据矩阵、一致性/缺口报告、卖方问题清单、Decision Brief、报告 MD/DOCX/PDF、8-Sheet Excel |
| 核心边界 | 合成示例数据演示，**不是真实客户性能验证**；不自动给 Go/No-Go，不替代专家判断 |

---

## 1. 产品要解决的问题

一家药企准备引进某款临床阶段创新药，卖方提供 1,000–1,600 页资料（CSR、IB、Protocol、SAP、安全性、CMC、专利、商业预测等）。传统方式需要临床、安全、统计、注册、CMC、IP、法务 5–7 个角色分别审阅 2–4 周，再人工汇总问题；常见问题：

- **关键数字遗漏**：ORR、样本量、cutoff 淹没在附录和表格里；
- **版本/人群/cutoff 混淆**：v1 与 v2、不同分析人群的数据被当作同一事实比较；
- **无法溯源**：报告里的每个数字无法证明来自哪份文件的哪一页；
- **问题清单重复、不可执行**：缺少优先级、责任部门、所需材料和证据链接。

HiJenny 的做法：**先建结构化证据库，再做专业分析**，而不是让大模型直接读全文写报告。

> 不是 PDF 聊天机器人；不自动给投资建议 / Go-No-Go / 估值 / 交易条款。

---

## 2. 当前交付状态

### 2.1 代码与质量

- 仓库：https://github.com/james-lebron2000/HiJenny （main 分支）
- 测试：**177 个自动化测试全部通过**（解析、抽取、校验、一致性、缺口、问题、API、导出、e2e、评测、BD 复核）
- CI：GitHub Actions（安装 → 测试 → 生成示例包 → CLI 冒烟），最新 main 通过
- 文档：README、产品设计、架构、评测、演示指南、安全一页纸、客户一页纸、生产化路线图

### 2.2 演示项目（本机 `/demo_workspace`）

| 指标 | 数值 |
|---|---|
| 文档 | 13 份（CSR 203 页、IB、Protocol、SAP、Safety、CMC、临床前、监管、专利、商业 PPTX、数据 XLSX 等） |
| 页数 | 284 |
| 证据 | 136 条，**136/136 通过原文逐字引用校验** |
| Findings | 11 条 active（1 critical / 3 high / 3 medium / 4 low） |
| 卖方问题 | 6 条（1 critical / 3 high / 2 medium） |
| 覆盖度 | 46 项检查清单：33 sufficient / 2 partial / 1 not found / 10 N/A |
| 尽调就绪度 | 65/100（因 1 项 critical 跨文件矛盾封顶；规则化评分，不构成投资建议） |
| 运行时间 | 约 11 秒（默认离线规则模式，0 次 LLM 调用） |

### 2.3 演示材料（已入仓库）

| 材料 | 路径 | 用途 |
|---|---|---|
| 5 步真实交互 GIF | `docs/assets/demo_walkthrough.gif` | README 首屏、即时通讯/邮件转发 |
| 5 步 MP4 | `docs/assets/demo_walkthrough.mp4` | 路演 PPT / 录屏 |
| BD 复核 GIF | `docs/assets/bd_followup.gif` | 卖方答复跟踪与 Decision Brief 更新 |
| 路演 PPT | `docs/HiJenny_Pitch.pptx` / `.pdf` | 客户/投资人演示（14 页） |
| 演示指南 | `docs/DEMO_GUIDE.md` | 10/15 分钟演示脚本、FAQ |
| 示例交付物 | `docs/sample_output/` | 报告 MD/DOCX/PDF + 8-Sheet Excel 快照 |
| 客户一页纸 | `docs/CUSTOMER_ONE_PAGER.md` | 试点范围、验收标准、报价 |
| 安全一页纸 | `docs/SECURITY_ONE_PAGER.md` | 安全问卷、部署、LLM 政策、已知缺口 |
| 生产化路线图 | `docs/PRODUCTION_ROADMAP.md` | P0/P1/P2 与 90 天计划 |

---

## 3. 功能全景（已实现）

### 3.1 文档智能

- PDF / DOCX / PPTX / XLSX 解析；页码、表格、bbox、文本块；
- SHA-256 去重；文档分类（CSR/IB/Protocol/SAP/Safety/CMC/监管/专利/商业等）；版本链（v1 → v2 自动 supersede）；
- 扫描件标记、解析质量、解析失败隔离；提示注入文本扫描与标记。

### 3.2 临床证据抽取

- 研究设计：NCT、阶段、适应症、设计、臂、对照、主要/次要终点、样本量、cutoff、随机/盲法；
- 疗效：ORR、DCR、CR/PR/SD/PD、DoR、PFS、OS、HR、CI、p-value、随访；
- 安全性：Any-grade AE、≥3 级 AE、TRAE、SAE、治疗相关死亡、减量/中断/停药、AESI；
- 统计质量：评价标准、SAP 版本、缺失数据处理、多重性、亚组、方案偏离；
- 每条证据带页码 + 表格/原文逐字引用 + 六维可比性向量（试验/臂/人群/cutoff/终点定义/评价方法）。

### 3.3 校验与一致性

- 逐字引用校验、数值/日期在引用中出现、百分比/计数合理性、CR+PR ≈ ORR、CI 顺序、N 一致性；
- 一致性检查只在六维可比时比较数值，区分：
  - **Type A 直接矛盾**（critical/high）
  - **Type B 版本差异**（旧 cutoff，不判矛盾）
  - **Type C 定义差异**（人群/方法/终点定义不同）
  - **U 可比性不明**（缺关键维度，先补元数据）
- 证据缺口：基于 10 个 YAML 规则包、46 条检查项，输出 sufficient / partial / not_found / not_applicable；
- 语言纪律：“未在当前资料包中识别到” ≠ “卖方没有做”。

### 3.4 卖方问题与 BD 复核

- Finding 簇合并为问题：优先级、责任部门、所需文件、证据链接、审核状态；
- **BD 复核闭环**（新增）：
  - 在问题下登记「可比资产」（资产/人群/线次/方法/随访/终点/结果/限制/来源）；
  - 登记「索取材料」→「卖方答复/实验结果」→「验证判断（资料复核/需要实验/继续补资料）」；
  - BD 负责人「更新 Decision Brief」；
  - 全部写入 audit trail；报告自动更新并提示“有新记录，等待重新确认”；
  - API：`GET/POST /api/projects/{id}/followup[/{question_id}]`。

### 3.5 Asset 360（药物资产发现 + 评估）

- 资产档案：名称、靶点、机制、分子类型、适应症、阶段、试验、样本量、cutoff，每项可点回原文；
- 关键临床事实：ORR/DCR/PFS/OS/DoR/安全性指标 + 人群/cutoff/方法 + 文档页码 + 核验状态；
- 文件构成与关键文档覆盖（CSR/IB/Protocol/SAP/Safety/CMC/临床前/监管/专利/商业）；
- **8 维尽调就绪度**：资产档案完整度、疗效证据、安全性证据、证据可核验性、跨文件一致性、检查覆盖、数据成熟度、资料包完整度；显示分数、权重、依据、缺失项；
- 风险环图（点击筛选 findings）、一致性 A/B/C/U、覆盖度堆叠条、Top 风险/问题；
- 版本历史（latest/superseded）；
- 规则说明：评分是规则化“尽调就绪度”，不是投资评级。

### 3.6 交付物

- **报告** `HiJenny_Report.md/.docx/.pdf`：Decision Brief、执行摘要、资产档案、版本历史、证据矩阵、发现（含引用）、缺口、问题、限制与免责声明、**Appendix A. Asset Discovery & Evaluation**；
- **Excel** `Seller_Questions.xlsx`：**8 Sheet** = Executive Summary / Asset Profile / Asset Evaluation / Discovery / Evidence Matrix / Findings / Questions / Coverage；
- **UI**：Projects / Asset 360 / Upload & Analysis / Evidence / Findings & Questions / **BD 复核** / Report；
- **API**：项目、上传、分析、运行状态、证据、发现、问题、覆盖度、Asset 360 概览、BD 复核、导出、文件下载、健康检查。

### 3.7 安全与审计

- 默认离线规则模式，不出网；LLM 可选（OpenAI 兼容，端点和开关由环境变量控制）；
- 文档视为不可信数据：规则层跳过指令式语句并记录 `injection_flags`；LLM 输出 schema 校验、引用不在原文页面的证据丢弃；
- 项目级隔离；`review_events` 记录谁在何时修改了 finding/question/BD 复核记录；
- `/api/files` 白名单，阻断路径穿越；
- 尚未实现：SSO/RBAC、静态加密、多租户、生产审计导出（见 §8）。

---

## 4. 快速上手（5 分钟）

### 4.1 环境

- Python 3.11+
- 无需 API Key；默认离线
- 演示/录制额外需要：Node 20+、Chrome（Playwright 用）、ffmpeg（GIF/MP4）
- 支持 macOS/Linux；Windows 未专门测试

### 4.2 安装与启动

```bash
git clone https://github.com/james-lebron2000/HiJenny.git
cd HiJenny

python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # 运行时 + 测试 + 报告生成

./scripts/demo.sh                # 建隔离 demo_workspace + 跑流水线 + 启动 UI
# 打开 http://127.0.0.1:8123/
```

无 console script 时用 `python3 -m hijenny.cli ...`。

### 4.3 演示路径（建议 10 分钟）

- **Asset 360**：一屏看资产发现 + 8 维评估（65/100，critical 矛盾封顶）；
- **Projects**：13 文档 / 284 页 / 136 证据；
- **Upload & Analysis**：文档类型/版本、7 阶段进度；
- **Evidence**：点 ORR 证据 → CSR p10 原文引用高亮；
- **Findings & Questions**：critical ORR 矛盾（42.0% vs 48.4%）、SAE 矛盾、IB 旧 cutoff、FAS/BICR 定义差异、缺口、注入风险；选择 severity=Critical → 11 → 1；
- **BD 复核**：跟踪卖方答复、登记可比资产与验证判断、更新 Decision Brief；
- **Report**：Decision Brief、Top risks、问题清单，下载 MD/DOCX/XLSX。

演示材料：`docs/DEMO_GUIDE.md`；动图：`docs/assets/demo_walkthrough.gif`。

### 4.4 测试与评测

```bash
python3 -m pytest -q

# 合成语料 benchmark（非真实客户性能）
python3 -m eval.benchmark --package data/sample_package --fresh --out eval/reports/package
python3 -m eval.benchmark --lifecycle --out eval/reports/lifecycle
```

---

## 5. 架构与技术栈

### 5.1 数据流

```
Document -> Page -> Evidence -> Finding -> Question -> Report/Excel/UI
```

- **Document**：SHA-256、类型、版本、supersede 链、解析质量、注入标记；
- **Page**：文本、blocks、bbox、表格；
- **Evidence**：字段、数值/单位、人群/臂/试验/cutoff/终点定义/方法（六维可比性）、页码、逐字引用、核验状态；
- **Finding**：A/B/C/U、缺口、技术风险；带证据链接与解释；
- **Question**：优先级、责任部门、所需文件、证据链接、审核状态、BD 复核历史；
- **Coverage**：46 项检查清单状态；
- **ReviewEvent/AgentRun**：审计与运行记录。

### 5.2 确定性工作流

```
CREATED → PARSING → EXTRACTING → VALIDATING → ANALYZING
        → GENERATING_QUESTIONS → REPORT_READY
```

每阶段幂等、可重跑；证据按指纹去重；发现/问题按稳定 dedup key upsert；v2 上传自动 supersede 并重算。

### 5.3 模块地图

| 模块 | 职责 |
|---|---|
| `hijenny/parsers/` | PDF/DOCX/PPTX/XLSX 解析、表格、注入扫描 |
| `hijenny/ingestion.py` | 分类、去重、版本、page/chunk 落库 |
| `hijenny/extraction/` | 规则/表格抽取、维度解析、资产/试验归并、LLM 适配 |
| `hijenny/validation.py` | 逐字引用、数值/算术独立校验 |
| `hijenny/agents/` | 一致性、缺口、问题生成 |
| `hijenny/overview.py` | Asset 360 资产发现 + 评估聚合 |
| `hijenny/diligence.py` | BD 复核历史（可比资产/答复/判断/发布） |
| `hijenny/rules/` | 10 个 YAML 尽调规则包（46 条检查项） |
| `hijenny/reporting.py` + `exports.py` | 报告/Excel/Word 导出 |
| `hijenny/api/` + `web/` | FastAPI + 无构建 SPA |
| `hijenny/pipeline.py` | 状态机与后台运行 |
| `eval/` + `corpus/` | 评测指标、benchmark、合成语料 |

### 5.4 技术栈

Python 3.11、FastAPI、SQLAlchemy + SQLite（可换 PostgreSQL）、PyMuPDF、python-docx/pptx、openpyxl、reportlab（仅语料生成）、PyYAML、可选 OpenAI 兼容 LLM。

### 5.5 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `HIJENNY_DATA_DIR` | `data` | 数据根目录 |
| `HIJENNY_DB_PATH` | `data/hijenny.db` | SQLite 路径 |
| `HIJENNY_EXTRACT_MODE` | `rules` | rules / hybrid / llm |
| `HIJENNY_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | — | 可选 LLM |
| `HIJENNY_RULES_DIR` | `hijenny/rules` | 规则目录 |
| `HIJENNY_MAX_UPLOAD_MB` / `_MAX_PAGES` | 200 / 2500 | 上传/页数限制 |

---

## 6. 质量与评测（合成语料）

| 指标 | 结果 |
|---|---|
| 字段抽取准确率（curated facts） | **36/36 = 100%**（27/27 字段） |
| Findings 对 11 个裁定议题 | **P 100% / R 100% / F1 1.00** |
| 引用准确率 | **100%**（独立逐字复核） |
| 覆盖度状态 | 5/5 正确 |
| 生命周期 v1→v2 | **11/11** 议题状态正确 |
| 注入攻击控制 | **PASS**（伪造 ORR 90% 未采纳，且标记风险） |
| 自动化测试 | **177 passed** |

> **必须对客户/投资人说明**：以上为合成语料结果，用于证明“系统按设计工作”，**不是真实客户数据性能**。真实试点需按 `docs/PRODUCTION_ROADMAP.md` 做双人裁定 benchmark。

---

## 7. 已知限制（请如实同步）

- 合成示例数据、单资产；未用真实客户资料验证。
- 抽取规则针对英文 CSR/IB/SAP 表述调优；中文/复杂排版/扫描件召回会下降；无 OCR。
- 无 SSO/RBAC/静态加密/多租户；SQLite + 单进程线程；生产化前必须补齐。
- LLM 适配层未在真实供应商上长期验证；默认离线模式不需要 LLM。
- 报告是人工复核草稿，不构成医疗/法律/投资建议；不自动给 Go/No-Go。
- 当前演示工作区 `demo_workspace/` 为本地生成、不入库；重建后项目 ID 会变化。

---

## 8. 生产化路线（摘要）

| 阶段 | 时间 | 重点 |
|---|---|---|
| P0 | 0–6 周 | 3 个真实包 + 双人裁定 benchmark；OCR/表格/章节感知；审核工作流；SSO/RBAC/加密/VPC；Postgres/S3/队列；交付 SOP |
| P1 | 6–12 周 | 多租户、规则库/风险评分、VDR 集成、质量飞轮、SOC2/ISO 准备、成本优化 |
| P2 | 3–6 个月 | 私有化/BYOK、CMC/注册/IP Agent、跨资产 benchmark、组合视图 |

验收目标：真实包字段召回 ≥95%、引用 ≥99%、Type A 误报 ≤2/包、人工工时节约 ≥50%、问题可直接采用 ≥70%。

详见：`docs/PRODUCTION_ROADMAP.md`。

---

## 9. 仓库地图

```
hijenny/              核心代码（parsers / extraction / agents / overview / diligence / rules / api / reporting / exports）
web/                  无构建 SPA（Asset 360、Evidence、Findings、BD 复核、Report）
tools/                示例语料生成、截图/GIF 录制、路演 PPT 生成、BD 复核 replay
eval/ + corpus/       评测指标、benchmark、合成语料与对抗样例
tests/                177 个测试（含 e2e、导出、Asset 360、BD 复核）
docs/                 产品/架构/评测/演示/安全/生产化/交接文档
data/                 合成示例包 + 本地生成项目（data/ 下项目目录不入库）
scripts/demo.sh       一键演示
```

关键文档：

- `README.md`：项目主页 + 中英双语使用指南
- `docs/PRODUCT.md`：产品设计与商业试点
- `docs/ARCHITECTURE.md`：工程架构
- `docs/EVALUATION.md`：评测方法与限制
- `docs/DEMO_GUIDE.md`：演示脚本
- `docs/PRODUCTION_ROADMAP.md`：真实落地计划
- `docs/SECURITY_ONE_PAGER.md`：安全问卷口径

---

## 10. 运维手册

```bash
# 启动 / 停止
./scripts/demo.sh                     # http://127.0.0.1:8123/
lsof -ti:8123 | xargs kill

# 指定端口
PORT=8124 ./scripts/demo.sh

# 重建隔离演示工作区（含交付物 PDF）
python3 -m hijenny.cli demo-kit --out demo_workspace

# 测试
python3 -m pytest -q

# 真实交互 GIF 重录（需要 Chrome/Playwright/ffmpeg）
pip install -e ".[demo]"
python3 tools/record_demo_walkthrough.py --base http://127.0.0.1:8123
python3 tools/make_demo_gif.py --recording-dir demo_recording

# 路演 PPT 重新生成
python3 tools/make_pitch_deck.py --out docs/HiJenny_Pitch.pptx
```

### API 示例

```bash
curl http://127.0.0.1:8123/api/projects
curl http://127.0.0.1:8123/api/projects/<id>/overview
curl http://127.0.0.1:8123/api/projects/<id>/findings?severity=critical
curl http://127.0.0.1:8123/api/projects/<id>/questions
curl http://127.0.0.1:8123/api/projects/<id>/followup
curl -X POST http://127.0.0.1:8123/api/projects/<id>/exports/report
```

---

## 11. 给同事的交接清单

### D+0（今天）

- [ ] 跑通 `./scripts/demo.sh`，按 `docs/DEMO_GUIDE.md` 走一遍 5 步演示；
- [ ] 打开 `docs/HiJenny_Pitch.pdf` 与 `docs/sample_output/`，确认对外口径；
- [ ] 阅读 `docs/SECURITY_ONE_PAGER.md`，标注客户安全问卷需要补充项。

### D+1–3（本周）

- [ ] 用 1 个真实脱敏资料包跑通解析/抽取，记录失败页、表格、扫描件比例；
- [ ] 与医学/统计同事确认：关键字段清单、可比性维度、问题优先级口径；
- [ ] 按 `docs/PRODUCTION_ROADMAP.md` 拆分 P0 责任人（解析、评测、安全、工作流）。

### 待确认问题（需要产品/BD 负责人拍板）

- 首个试点客户/适应症是否限定肿瘤 Phase 2？
- 试点报价与交付周期（3–5 天，¥9.8k–29.8k）是否按客户调整？
- 真实资料进入的环境：本机/客户 VPC/私有化？LLM 是否允许？
- 报告对外发送前，谁负责最终签核（医学/BD/法务）？
- 是否需要把「BD 复核」纳入首个试点交付物？

---

## 12. 链接汇总

| 资源 | 地址 |
|---|---|
| GitHub 仓库 | https://github.com/james-lebron2000/HiJenny |
| 本地演示 | http://127.0.0.1:8123/ |
| 演示动图 | `docs/assets/demo_walkthrough.gif` |
| 路演 PPT | `docs/HiJenny_Pitch.pdf` |
| 演示指南 | `docs/DEMO_GUIDE.md` |
| 示例交付物 | `docs/sample_output/` |
| 评测 | `docs/EVALUATION.md` |
| 生产化 | `docs/PRODUCTION_ROADMAP.md` |
| 安全 | `docs/SECURITY_ONE_PAGER.md` |
| 客户一页纸 | `docs/CUSTOMER_ONE_PAGER.md` |

---

交接人：HiJenny 项目组。如文档与代码不一致，以仓库 main 分支和 docs/ 最新版本为准。
