# SciForge 开发进度总览

> 更新：2026-07-11 CST
> 当前阶段：通用无损 Pipeline + QGen durable Harness + Web Chatbox MVP。
> 这里记录当前代码事实；历史优化过程可查 Git，不再把过期测试数和本机数据快照当作产品状态。

## 1. 总体状态

| 模块 | 状态 | 当前事实 |
|---|---|---|
| 项目基础 | ✅ | Python 3.11+、uv、Pydantic v2、Typer/Rich、structlog、Ruff、mypy strict |
| 内容存储 | ✅ schema v14 | FTS5、locator、tenant question dedup、ingestion status、concept/evaluation/review |
| Pipeline | ✅ | Task0 + 5 Stage、无损兜底、marker/patch/concept/evaluation post-pass |
| 多格式 | ✅ | PDF/DOCX/PPTX/MD/TeX/Image + CSV/TSV/XLSX/JSON/JSONL/HTML/EPUB/TXT/RST |
| LLM 优化 | ✅ | 0-LLM adapters、文字层、批量、风险 review、cache、thinking、局部 patch |
| QGen | ✅ Harness | prefilter、deep solve、evolve、population、数值/Lean、四态证据 |
| Durable runtime | ✅ | run/step/budget/checkpoint/event、resume/cancel、启动恢复 |
| CLI | ✅ | parse/generate/variant/evolve/deep/durable/resume/cancel/config/serve |
| API/SaaS | ✅ MVP | auth/scope/quota/tenant、conversation/run/SSE、推荐、组卷/导出 |
| Chatbox | ✅ MVP | 七模式、知识引用、进度/验证、移动 rail、取消/恢复、历史 |
| 分布式执行 | 🔲 | 多 worker lease、队列 broker、hardened executor 尚未实现 |
| 富前端 | 🔲 | KaTeX、artifact center、完整会话管理与图谱工作台尚未实现 |

## 2. Pipeline 完成项

### 2.1 输入与安全

- 统一 Recognizer 协议；电子 PDF 自动文字层，扫描文档才走 VLM；
- CSV/TSV、JSON/JSONL、XLSX、HTML、EPUB、TXT/RST 由确定性 adapter 产生 source-located
  Markdown block，Task0 默认 0 LLM；
- 单图有界 VLM；VLM 整批内容审核误判时逐页回退；
- ZIP 容器限制体积、entry、解压总量、压缩比与路径穿越；结构化输入限制字节、列和深度。

### 2.2 无损提取

- genre/format profile、强弱 marker、章节边界、adaptive window、carry/halo；
- Stage1 每窗口批量提取；Stage1Review 按风险调用；Stage2 小批标注；Stage2Review 默认关闭；
- missed marker 只做局部 micro extraction，不重跑整窗；
- 文末、重试耗尽或 patch 仍未解决时保存 `uncertain_fragment`/占位，非空原始 span 不静默丢失；
- provisional id remap、ReorderBuffer、Stage3 单调写入与 checkpoint/mirror 恢复。

### 2.3 治理与发布

- schema v14 把摄取可靠性固化为 `accepted/uncertain/needs_patch/rejected`；
- answer linker、numbering audit、relation inference、reconciliation；
- taxonomy 驱动 concept/alias/item/concept-relation 四表；
- 默认 0-LLM evaluation 与 review queue；
- SQL 质量门禁在 limit 前排除非 accepted、非 active concept、open/in-progress review 和低分 item；
- QGen、知识搜索、推荐和组卷共享门禁。

## 3. QGen 完成项

- 领域契约：math/physics/chemistry/biology/other 均有 type/form/tag 规则；
- 图节点：retrieve/generate/next/prefilter/solve/verify/annotate/diagnose/evolve/select；
- 模式：new/evolve/variant/adjust_difficulty/deepen/solve/evaluate；
- A1–A6/B1–B2 策略、有限失败修正、parent/generation/directive lineage；
- 候选池按 correctness、difficulty fit、novelty、pedagogy、verification strength 稳定选择；
- full-question/full-solution independent reviewer；
- fast/balanced/strict/formal 策略与 passed/failed/unavailable/error 证据；
- 默认受限 Docker/Podman sandbox，数值 Python 与 Lean；trusted-local 只允许审核代码；
- public structured solution，不输出 raw chain-of-thought；
- DurableQGenHarness 节点 checkpoint、硬预算、事件、取消与恢复。

## 4. Web/API 完成项

- `/v1/conversations`、messages、runs、SSE、cancel/resume、templates、knowledge search；
- 会话历史在提交前做有界不可变快照并写入 durable input；
- QGen 成功后把 question + solution materialize 到当前 tenant 题库；
- 启动恢复 pending/running run，并 reconciliation 缺失 assistant/artifact/question/usage；
- `Last-Event-ID` 补发与 heartbeat；
- HMAC 签名 bearer access token、API key、scope、月配额和 tenant/public 可见性；
- RecommendEngine、确定性组卷、Markdown/DOCX 导出；
- `/app/` 原生 HTML/CSS/JS Chatbox：七模式、领域题型联动、知识 locator/pin、验证面板、
  桌面三栏与窄屏 drawers。

## 5. 数据库职责

| 数据库 | 内容 |
|---|---|
| `data/sciforge.db` | 教育内容、FTS、概念、评价、review |
| `data/conversations.db` | 会话、消息、run links、artifacts |
| `data/qgen_runtime.db` | runs、steps、checkpoints、events |
| `data/saas.db` | tenants、users、keys、usage、batch tasks、behavior |

## 6. 明确限制

1. SQLite 调度是单实例 MVP；没有跨进程 worker lease/exactly-once；
2. restricted OCI container 不是 hostile multi-tenant 的最终边界；生产应增加 gVisor/Firecracker；
3. numerical verifier 当前执行生成的 Python，尚无每题型 property-based hidden suite；
4. Lean compile 证明形式化 source，不自动证明自然语言翻译忠实；
5. KB Agent/RecommendAgent wrapper 等仍有骨架，但相关 pipeline/recommend engine 职责已实现；
6. 前端无 KaTeX、完整 CommonMark、artifact 中心、上传审核、会话删除/分支/分享；
7. 向量检索未成为默认路径；当前为结构过滤 + 标签/轻量文本排序；
8. access token 没有 refresh/吊销生命周期和第三方登录。

## 7. 当前验收入口

```bash
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
node --check src/sciforge/api/static/app.js

uv run sciforge parse validate data/sciforge.db
uv run sciforge config validate
uv run sciforge serve --open
```

Pipeline 的“完成”还必须读取实际 checkpoint、报告和数据库状态；Web 的“完成”还必须在真实
浏览器验证 desktop/mobile、SSE 重连、取消/恢复、知识 locator/pin 和生成题继续变式。

## 8. 后续优先级

| 优先级 | 工作 | 验收 |
|---|---|---|
| P0 | 用真实多领域语料做 lossless/quality gate 基准 | marker 100% 有去向，非合格进入生成池为 0 |
| P0 | hardened sandbox 与部署 egress policy | hostile-code 安全评审与逃逸测试 |
| P1 | 题型专用 deterministic/property verifier | counterexample seed 可重放 |
| P1 | artifact API/中心、KaTeX 与可复制结构化产物 | 浏览器 E2E |
| P1 | 分布式 worker lease/队列 | crash/duplicate delivery chaos test |
| P2 | 向量/图混合检索 | source-backed relevance benchmark |
| P2 | refresh/撤销/第三方身份 | token lifecycle 与权限测试 |
