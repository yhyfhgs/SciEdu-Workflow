# SciForge 平台重审报告 — 索引

> **性质**：全仓证据化重审 · **基线**：`dev @ ba29f3b`（2026-07-11）· **范围**：176 个 `src/` 文件、159 个 `tests/` 文件、35 份 `Docs/` 资产、prompt/config/脚本及全部 audit/plan SVG
> **权威增量台账**：[RE-AUDIT-REGISTER.md](RE-AUDIT-REGISTER.md)。原报告中的历史 ID 保留用于溯源；优先级、计数和纠错以该台账与 [plan-05](../plan/plan-05-modernization-program.md) 为准。

---

## 1. 执行摘要

SciForge 是一个 AI 驱动的理工科知识处理与生成平台，由数据管线（多格式文档 → LLM 提取/标注 → SQLite schema v14 知识库）、QGen Agent Harness（出题/解题/多路径验证/进化，durable run）、FastAPI 后端（`/v1/*`，双通道鉴权 + 多租户 + 月配额）与内置零构建 Web Chatbox 四部分组成。

**整体判断**：质量基线健康（**pytest 1318 通过 / 1 跳过、ruff clean、mypy clean、`node --check` 通过**），durable store、容器参数加固、DOM 安全落点和大量确定性管线工具也有扎实实现；但这些局部优点不能推出“端到端 fail-closed / exactly-once / 无损 / 已验证”。本轮 branch coverage 仅 **66.9%**，CLI 为 **34.9%**，而真实入口还被隐藏 editable `.pth` 问题阻断。

重审后应把风险聚焦为五条系统主线：

1. **"静默丢失/损坏知识库内容"** 是最严重的系统性风险面。两个 Critical 都属此类：`answer_linker` 的题答联动会跨章误绑并**不可逆删除**题目（PA-01）；空白页导致的 `block_index` 不对称会在断点恢复后**静默跳过成段内容**（PC-03）。此外 VLM 截断丢批尾（IS-02）、混合 PDF 空提取（IS-03）、Stage1Review 清除卷尾题（PC-01）、resume watermark 丢条目（PC-02）、确定性 `trim_tail` 在多数学环境下过度截断并回写入库（SUP-01，默认开启）等多条 High 均无告警、无记账，与 README 宣称的"无损摄取"存在实质缺口。

2. **"宣称能力与实际实现的落差"**。文档分类是内部调参信号而非有存证的产品能力（IS-12）；知识图谱"只写不读"、无 prerequisite 边、无任何消费方（PA-08）；生成题因不写评价而被质量门禁**永久排除**在用户自己的组卷/推荐/检索之外（GN-02）；讲义/组卷都是"取前 N"头部截断（GN-03/GN-10）；物理学科化止于词面替换（GN-13）；数值/Lean 验证在默认配置下根本不跑、开启后与出题共用同一模型自证（SB-02/SB-04）。

3. **运行/缓存/提交身份混为一体**（DP-01，新增 Critical）。完成 checkpoint 可让全新目标 DB 直接早退，且 discipline、config、模型、prompt、schema、dry-run/skip-LLM 等都不参与身份；这比单个 resume bug 更根本。
4. **QGen 题目契约与验证证据不闭合**。choice/fill-blank 等结构字段在 solver、verifier、CLI、API、导出、UI 链上丢失（QG-01）；Lean 只证明“某段 Lean 可编译”，未证明它绑定当前题解（QG-02）；自定义配置、trusted-local 来源、恢复 manifest 和计费幂等也存在断层。
5. **权限、schema 和副作用边界依赖约定而非能力**。legacy scope 过宽（XC-01）、tenant view 是方法黑名单而非能力白名单（XC-02）、v14 只验版本号和两张表（XC-03）、计费/交付缺 effect idempotency（QG-06/XC-08）。

**这些都是可修复的工程债，不是架构性推倒重来。** 详细的分阶段修复与目标态设计见 [`../plan/PLAN-INDEX.md`](../plan/PLAN-INDEX.md)。

---

## 2. 计数口径与 Critical

第一版的“约 85 项”不可由报告正文复算：实际可搜索到 **161 个唯一发现 ID**，其中至少 14 个是别名/交叉印证，约为 **147 个 canonical action**。这不是新增了 60 多个 bug，而是纠正了“展示 ID、独立根因、修复动作”三种口径混用。完整去重原则与 DP/QG/XC 增量见 [RE-AUDIT-REGISTER §1](RE-AUDIT-REGISTER.md#1-这次重审纠正了什么)。

当前必须按 Critical 处理的三条是：

- **DP-01** — checkpoint/cache/sink 身份未分离，可对空目标库假完成。
- **PA-01** — `answer_linker` 跨章误绑并不可逆删题（已用最小样例复现）。
- **PC-03** — 空 batch 的位置型 `block_index` 导致 resume 静默跳过真实内容。

严重性不是简单相加：例如 QG-01 与 QG-02 在“产品宣称已验证/可发布”的上下文中可升级为发布阻断项，XC-02 在新增 storage 方法后会随能力面扩大而升级。

---

## 3. 工作流报告导航

| 报告 | 工作流范围 | 关键发现 |
|---|---|---|
| [RE-AUDIT-REGISTER.md](RE-AUDIT-REGISTER.md) | 全仓重审、纠错、coverage、安全/供应链与 SVG QA | DP-01~13 · QG-01~10 · XC-01~10 |
| [report-data-pipeline.md](report-data-pipeline.md) | 数据管线：识别/提取/标注/入库/图谱/存储 + LLM 效率 | DP-01🔴 · DP-02~13 · PA-01🔴 · PC-03🔴 + 历史细项 |
| [report-qgen-agent-harness.md](report-qgen-agent-harness.md) | QGen harness：9 组件 + 生成链 + 模板 + 验证沙箱 | QG-01/02 发布阻断 · QG-03~10 · GN/HR/SB 历史细项 |
| [report-qgen-frontend.md](report-qgen-frontend.md) | Web Chatbox：架构/XSS/SSE/七模式/可访问性 | QG-01/02 展示边界 · FE-01/03/09 + 行为测试缺口 |
| [report-frontend-backend-consistency.md](report-frontend-backend-consistency.md) | 前后端契约 + 后端 API 质量 + 多租户边界 | XC-01/02 · QG-06/07 · FB-01/04/06/08 |
| [report-cross-cutting.md](report-cross-cutting.md) | 文档漂移、测试/CI 缺口、跨工作流主题 | DR-01/02🟠 无 CI · 交叉印证项 |

## 4. 架构图（反映当前实现，非愿景）

| 图 | 内容 |
|---|---|
| [diagrams/system-overview.svg](diagrams/system-overview.svg) | 端到端：管线 ⇄ 知识库 ⇄ QGen ⇄ 前端 + 四库 |
| [diagrams/data-pipeline.svg](diagrams/data-pipeline.svg) | 管线内部：Task0 → 5 Stage 队列 → post-pass，7 个 LLM 调用点 |
| [diagrams/qgen-agent-harness.svg](diagrams/qgen-agent-harness.svg) | StateGraph 10 节点 + RunStore + durable + LLM 分层 + 沙箱 + 9 组件覆盖 |
| [diagrams/frontend-backend-sequence.svg](diagrams/frontend-backend-sequence.svg) | "生成一道题" + "组卷导出"端到端时序 + 契约缺陷 |

---

## 5. 发现字段说明

每条发现包含：**ID · 严重性 · 组件 · 类别 · 描述 · 证据（file:line）· 影响 · 疑似根因 · 复核状态**。

**复核状态**标注可信度来源：
- **亲读✓** — 主线逐行读码并交叉定位；PA-01/PC-03/GN-01 为历史重点，DP/QG/XC 证据另见重审台账
- **实测✓** — 主线运行只读诊断确认（IS-01 用 EXPLAIN QUERY PLAN、HR-04 用 grep、SB-01 用 grep + docstring 比对）
- **代理** — 采信子代理证据（均附精确行号 + 代码摘录；多条经跨代理独立交叉印证）

## 6. 方法与局限

- **动态基线**：运行了 pytest、branch coverage、ruff、mypy、Node 语法检查、Bandit、pip-audit、lock 检查，并对 checkpoint、hash collision、answer_linker、只读 validate、配置类型逃逸做了最小复现。
- **全文件清单**：按 `src/tests/Docs/prompts/config/scripts/audit/plan` 建立资产与功能覆盖矩阵；“覆盖每一行”表示每个文件进入清单和风险归属，不声称人工逐字符证明无缺陷。
- **真实外部依赖仍有限**：未对所有付费 provider、完整教材、OCI/Lean 镜像和高并发多进程做全量线上压测；这些必须进入 canary、golden、crash matrix 与真实容器测试。
- **测试通过不等于生产入口有效**：pytest 的 `pythonpath=["src"]` 掩盖了真实 CLI 的 editable `.pth` 故障；大量 LLM/容器路径仍是 mock。
- **审计产物已修改**：本轮只修改 `audit/` 与 `plan/` 产物（含 7 张 SVG），未改业务源码、原始数据或运行数据库。
