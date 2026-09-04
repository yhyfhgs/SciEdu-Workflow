# SciForge 修复与实施计划 — 索引

> 配套 [审计报告](../audit/REPORT-INDEX.md)。**报告说"是什么"（诊断），计划说"怎么办"（处方）**，两者分离、交叉引用。
> 所有代码片段均为 **proposed / not-yet-implemented**，仅为目标态说明。**重审后的依赖顺序与验收口径以 [plan-05](plan-05-modernization-program.md) 为权威总纲**；plan-00~04 保留历史 ID 的细粒度处方。

---

## 1. 计划结构

计划分**粗（主检查表）+ 细（分子系统）**两层，与报告的 index 约定一致。

| 文档 | 内容 |
|---|---|
| [plan-00-coarse-overview.md](plan-00-coarse-overview.md) | 历史 ID 主检查表 + 重审 DP/QG/XC 映射；区分展示 ID 与 canonical action |
| [plan-01-data-pipeline.md](plan-01-data-pipeline.md) | 数据管线：2 Critical 目标态设计 + 无损加固 + 治理闭环 + schema v15 增量 |
| [plan-02-qgen-agent-harness.md](plan-02-qgen-agent-harness.md) | harness/生成/验证：写回修复 + 生成质量 + 验证有效性 + 生产化 |
| [plan-03-qgen-frontend-design.md](plan-03-qgen-frontend-design.md) | 前端目标态设计：组件分解 + UI 流程 + 设计系统 + 线框图 |
| [plan-04-cross-cutting.md](plan-04-cross-cutting.md) | 跨领域：CI 门禁 + 文档对账 + 配额/鉴权/契约硬化 |
| [plan-05-modernization-program.md](plan-05-modernization-program.md) | **权威实施总纲**：身份/配置/资源 → 无损 patch/provenance → canonical storage → QuestionArtifact/evidence → durable effects → 成本 |

**前端可视化 artifact**（简报 §4.2.4 要求的 concrete visual planning artifacts）：
- [wireframes/component-decomposition.svg](wireframes/component-decomposition.svg) — 现状单 IIFE → 目标态原生模块 + 数学渲染管线
- [wireframes/target-workbench.svg](wireframes/target-workbench.svg) — 目标态三栏工作台高保真线框（新增功能 + 修复点标注）
- [wireframes/key-flows.svg](wireframes/key-flows.svg) — 三条关键 UI 流程 + P6 阶段划分

---

## 2. 阶段路线图

按**风险优先 + 依赖顺序**，重审后先执行 6 个跨系统 work package；旧 P0~P7 仍作为子模块标签：

| 顺序 | 目标 | 跨子系统落点 |
|---|---|---|
| **1 Gates** | fresh-install CI、coverage floors、golden/crash/security/SVG 门禁 | plan-05 §2 · plan-04 P0 |
| **2 Identity/Config/Resource** | 修 DP-01/02/08/12；先让每次运行可定义、可限流、可证明提交 | plan-05 §3 · plan-01 新 P0 |
| **3 Lossless/Canonical** | stable-ID review、SourceMap、occurrence/revision、post-pass DAG | plan-05 §4–5 · plan-01 P0/P1/P2 |
| **4 QGen Contract/Evidence** | `QuestionArtifact`、`EvidenceBinding`、manifest、trusted-local policy | plan-05 §6 · plan-02 P0/P4 |
| **5 Durable Effects/Cost** | lease/fencing、outbox、幂等计费、provider usage、按需生成/cache | plan-05 §7–8 · plan-02/04 P5 |
| **6 Governance/Product** | taxonomy/评价/review/图谱、组卷/讲义、typed artifact UI | plan-05 §9 · plan-01 P2 · plan-02 P3 · plan-03 |

**依赖关键路径**：Gates → identity/config/resource → lossless/canonical → artifact/evidence → effects/cost → product。UI 可以提前做视觉组件，但不能在 QG-01/02 后端契约前固化假 artifact。

---

## 3. 目标态设计规格覆盖（简报 §4.2）

每个被修复或新建的组件都有目标态设计（架构/接口/数据模型）：

| 组件 | 目标态设计位置 |
|---|---|
| answer_linker（分组键/判别/软删/dry-run） | plan-01 §P0.1（含 proposed 代码） |
| block 身份标识 + resume 集合差 | plan-01 §P0.2 / §P1.5 |
| schema v14→v15 增量 | plan-01 §目标态数据模型 |
| 知识图谱 prerequisite + 消费 | plan-01 §P2.4 |
| 真实组卷（约束满足） | plan-02 §P3.2 |
| 验证独立性 + 结构化判定 + Mathlib 镜像 | plan-02 §P4.2-4.4 |
| worker fencing + API 预算注入 | plan-02 §P5.1-5.2（含 proposed 代码） |
| 配额原子预留 + 消息游标分页 + 鉴权硬化 | plan-04 §P5（含 proposed 代码） |
| CI GitHub Actions | plan-04 §P0（含 proposed yaml） |
| 前端模块分解 + KaTeX 管线 + 设计系统 token | plan-03 §2/§5 + 3 张线框图 |
| run/cache/sink 三层身份 + ResourceGovernor | plan-05 §3 |
| stable-ID review + SourceMap conservation | plan-05 §4 |
| canonical item/occurrence/revision + schema shape | plan-05 §5 |
| QuestionArtifact + EvidenceBinding + RunManifest | plan-05 §6 |
| effect outbox + cost ledger | plan-05 §7–8 |

---

## 4. 与报告的可溯源关系

- **每条报告发现**（[REPORT-INDEX](../audit/REPORT-INDEX.md)）→ [plan-00 §2](plan-00-coarse-overview.md) 有归属阶段 + 详细计划链接。
- **每条缺失功能** → [plan-00](plan-00-coarse-overview.md) 的「§3 缺失功能清单」有阶段 + 设计位置。
- **反向核对**：plan-01~04 每小节标注其消化的发现 ID，可从计划反查报告。
- **覆盖性自检**：[plan-00 §4](plan-00-coarse-overview.md#4-覆盖性自检) 说明历史 ID 与重审 ID 的口径；[plan-05 §10](plan-05-modernization-program.md#10-pr-切分与依赖) 给出按依赖可落地的 PR 序列。

---

## 5. 执行原则

1. **P0 的 CI 最先落地**——它是后续所有修复的回归安全网；当前无 CI 是最大的隐性风险放大器。
2. **三个 Critical 与两个发布阻断契约优先**——DP-01、PA-01、PC-03，以及 QG-01/QG-02 在对外“已验证”发布路径上必须先关闭。
3. **修 bug 必附回归测试**——约 40% 发现有对应测试缺口；补测试与补 CI 同步，否则修复无保护。
4. **验证类改动必须在真实容器环境验证**（当前全 mock，SB-03），用"故意错解"夹具确认能被抓——这是"最后防线"名副其实的唯一证据。
5. **前端保持零构建 + XSS 不变量**——不推倒重来改框架；KaTeX 是唯一受控依赖。
6. **每阶段“完成”= 不变量测试 + fresh-install/真实入口 + 对应真实语料/容器/浏览器 + 可观测指标**，不以现有 pytest 绿为唯一标准。
