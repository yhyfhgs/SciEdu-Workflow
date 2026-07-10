# SciForge

AI 驱动的**理工科知识处理与生成平台**。从教材、讲义、试卷中自动提取结构化知识，构建可查询的跨学科知识库，并通过多 Agent 协作引擎生成新题目、详细解答和教学讲义。

**已注册领域契约**：数学、物理、化学、生物、其他；每个领域由 `DisciplineRegistry` 约束
题型、题面形式、tag 根和 prompt 变量。形式化验证当前只适用于数学证明题（见
[`Docs/15_学科扩展指南.md`](Docs/15_学科扩展指南.md)）。

## 核心能力

### 1. 数据处理 Pipeline

通用识别 + 5 阶段并发 Producer-Consumer（6 Task × 4 stage queue × asyncio.gather），按领域参数化：

- **Task0**: 多模态视觉识别（VLM）每 5 页一批转 markdown，system prompt 按学科注入；
  内容审核误判（如 MiniMax 1026 sensitive）时**自动回退逐页识别**，只丢被误判页不丢整批
- **0-LLM adapters**: CSV/TSV/XLSX/JSON/JSONL/HTML/EPUB/TXT/RST 安全、有界转换；
  电子 PDF 自动走文字层；ZIP bomb、路径穿越、列数/深度/字节数均有限制
- **Stage1Extract**: 500 行滑动窗口，每窗口一次 LLM 调用批量提取所有完整条目；0 条时扩窗口到 3000 行
- **Stage1Review**: 原 window + 批量 items → LLM 批量校验修正（专门修复 exercise 漏 solution bug）
- **Stage2Annotate**: 单条 item 打 tags/difficulty/subject/taxonomy_path
- **Stage2Review**: 单条 annotation 审核 tag 格式/复用/校准
- **Stage3Persist**: 合并 item+annotation → KP/Question 入库（无 LLM）+ tag 受控词表统一（#6）
- **无损兜底**: 未解决的强 marker 或非空尾部保留为 `uncertain/needs_patch`，不会因重试耗尽
  或 force-consume 静默丢失；默认发布查询排除这些占位
- **入库后确定性 post-pass**（无 LLM，可独立开关）：编号连续性 audit（#2）→ 跨章节题答回填（#3）
  → 结构化关系边推断 `corollary_of/tests/successor`（#6 知识图谱）→ reconciliation 复核重复/类型误判（#7）
- **Prompt caching（#9）**：各 stage 静态指令拆到可缓存 system 前缀；纯 Anthropic 自动缓存（省 ~90% 静态输入 token）
- **思考预算分级（#9）**：推理模型（如 MiniMax-M3）按 stage 设 `thinking` budget——提取 1024、求解 16384，避免高频抄写任务过度推理
- **Retry with feedback**: schema 校验失败时把错误文本注入下一轮 prompt
- **严格保序**: 全局 item_id 自增 + FIFO queue；Stage2 并发时由 ReorderBuffer 恢复顺序
- **单文档并行（#1，opt-in）**：按 section 分段 + 段级并发提取 + 段序保序重排（`document_segmenter` / `parallel_extract`）
- **直通缓存重放**: Task0 已完成时直接从 `recognize/file_*/` 读取跳过原始文件
- 每条 item 镜像到 `items/<stage>/item_<id>.json` 供断点恢复
- 异常隔离 + try/finally SENTINEL 传播避免死锁
- **schema v14 治理**: concept 四表 + 版本化评价 + review queue + ingestion status；QGen、知识
  搜索、推荐和组卷在 SQL 的 limit 前复用质量门禁

### 2. 多 Agent 生成引擎

基于 `BaseAgent` + 极简 `StateGraph` 构建的题目专用 Agent Harness（不依赖 LangGraph）：

- **QGenAgent** — `retrieve → generate → prefilter → solve → verify → annotate`，失败可
  `diagnose → evolve`，候选池可做确定性多目标选择
- **SolveAgent** — 对已有题目独立生成详细解答
- **TagAgent** — 10 维难度评分
- **DocAssemblyAgent (lite)** — 按 tag 组装 markdown 讲义
- **DurableQGenHarness** — run/step/budget/checkpoint/event、node-boundary resume/cancel；数值代码和
  Lean 证据遵循 fail-closed sandbox policy
- **Chatbox** — `/app/` 七模式工作台，会话历史、知识引用、SSE、验证面板、暂停/恢复与源题延续
- Recommend 算法引擎已实现；KB 与若干独立 Agent wrapper 仍为骨架，相关职责已由 pipeline 内置

Agent 全部按 `Discipline` 参数化，可单独运行也可通过 StateGraph 编排。详见 [`Docs/16_Agent系统MVP.md`](Docs/16_Agent系统MVP.md)。

### 3. 存储、API 与 CLI

- **SQLite 内容库（schema v14）** — FTS5、来源 locator、tenant-scoped question hash、
  `ingestion_status`、concept graph、版本化 evaluation/review queue；关系桥表使用紧凑复合主键
- **分库运行状态** — `conversations.db` 保存产品历史，`qgen_runtime.db` 保存不可变 checkpoint 与
  append-only event，`saas.db` 保存租户/账号/用量
- **CLI 接口** — 统一入口 `sciforge`（含 `sf` 短别名），Typer + Rich，支持
  `parse run / run-book / audit / validate / migrate-legacy / db-stats` 和
  `generate free / solve / batch / lecture / variant / evolve / solve-deep / durable / resume / cancel`
  - `parse validate <db>` — 入库后只读体检：提取量/类型双维度分布/题答绑定率/编号规范化/去重/编号缺口/知识图谱边
- **统一服务** — `sciforge serve` 提供 `/v1/*`、OpenAPI 与 `/app/`；默认鉴权/租户隔离，
  `--open` 仅用于可信本地调试

## 快速开始

### 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 包管理器
- LibreOffice（可选，VLM 后端解析 DOCX 时需要）

### 安装

```bash
git clone https://github.com/ZyphingFHGS/SciForge.git
cd SciForge
# `pdf-ml` 与 `pdf-mineru` 互斥；使用项目提供的聚合 extra
uv sync --extra all

# 配置 LLM
cp config/sciforge.example.toml config/sciforge.toml
# 编辑 config/sciforge.toml 修改 provider/model 与 base_url

# 配置 API Key
cp .env.example .env
# 编辑 .env 填入 ANTHROPIC_API_KEY 或 OPENAI_API_KEY
```

### 使用

#### 数据处理

```bash
# 解析数学材料（默认学科 math）
uv run sciforge parse run path/to/textbook.pdf --db data/sciforge.db

# 解析物理材料
uv run sciforge parse run physics.pdf --db data/sciforge.db -D physics

# 多章节作为一本书处理（共享 order_index 和 tag_tree）
uv run sciforge parse run-book chap1.md chap2.md chap3.md --db data/sciforge.db

# 或用 manifest 指定章节顺序
uv run sciforge parse run-book --manifest book.yaml --db data/sciforge.db

# 解析整个目录（并发）
uv run sciforge parse run "Raw Data/" -j 4 -D math

# 盘点 Raw Data 下所有 logical_document 的处理进度
uv run sciforge parse audit "Raw Data" --db data/sciforge.db -o data/report.md

# 数据库统计
uv run sciforge parse db-stats data/sciforge.db

# 配置诊断
uv run sciforge config show
uv run sciforge config validate
```

#### 内容生成

```bash
# 生成新数学题（QGen Harness）
uv run sciforge generate free \
  -D math -s "real_analysis:实分析" \
  -t "math:数学,analysis:数学分析" \
  -d 6 --type proof -n 1 \
  --db data/sciforge.db

# 生成新物理题
uv run sciforge generate free \
  -D physics -s "classical_mechanics:经典力学" \
  -t "physics:物理,mechanics:力学" \
  -d 5 --type calculation

# 对已有题目生成详细解答
uv run sciforge generate solve <question_id> --db data/sciforge.db

# 变式、进化与可恢复长任务
uv run sciforge generate variant <question_id> --strategy A1 --db data/sciforge.db
uv run sciforge generate evolve -t "math:数学,analysis:数学分析" -d 7 --max-improve 2
uv run sciforge generate durable -t "math:数学,analysis:数学分析" -d 8 --evolve
uv run sciforge generate resume <run_id>

# 生成 markdown 讲义（按 tag 聚合知识点+例题+练习）
uv run sciforge generate lecture \
  -D math \
  -t "math:数学,analysis:数学分析" \
  --title "连续函数专题" \
  -o data/lectures/

# Chatbox + API
uv run sciforge serve --open
# 浏览 http://localhost:8000/app/；OpenAPI: /docs
```

## 项目结构

```
src/sciforge/
├── core/        # 核心 Pydantic 模型 (models.py 含 Discipline/Lecture) + 异常
├── llm/         # LLM 客户端层（Anthropic / OpenAI / Gemini / OpenAI 兼容）
├── disciplines/ # 学科库：DisciplineSpec + DisciplineRegistry + specs/{math,physics}
├── pipeline/    # 5 阶段并发数据流水线
│                #   orchestrator（asyncio.gather 6 Task + 4 stage queue + 缓存直通）
│                #   recognizers / vlm_converter
│                #   stages/stage1_extract|stage1_review|stage2_annotate|stage2_review|stage3_persist
│                #   llm_schemas (Batch*/Single* + exercise near-tail guard) / reorder_buffer / tag_tree / taxonomy_validator
│                #   resume_state / retry (with_feedback) / audit（进度盘点）/ migrate
├── agents/      # 多 Agent 系统
│                #   base/state/graph: BaseAgent + AgentState + StateGraph
│                #   qgen/: 状态图 Harness（prefilter/evolver/population/deep solver/verifier/sandbox）
│                #   runtime/: run/step/budget/checkpoint/event
│                #   solve_agent / tag_agent / doc_agent: 独立 Agent
│                #   stub_agents: 6 个 Post-MVP 骨架
├── prompts/     # 提示词模板（必须为 .txt 文件），两阶段学科参数化注入
├── api/         # FastAPI + durable conversation/run/SSE + tenant storage + 零构建 Chatbox
├── storage/     # schema v14 内容库 + FTS5 + concept/evaluation/review + SaaS store
├── cli/         # Typer CLI（parse、generate、durable/resume/cancel、serve、config）
└── config/      # AppConfig + structlog
```

详细架构见 [`Docs/01_数据处理Pipeline.md`](Docs/01_数据处理Pipeline.md)、
[`Docs/21_知识库构建标准与数据契约.md`](Docs/21_知识库构建标准与数据契约.md)、
[`Docs/22_QGen_Harness_架构与验证协议.md`](Docs/22_QGen_Harness_架构与验证协议.md)、
[`Docs/12_Web前端设计.md`](Docs/12_Web前端设计.md) 与 [`CLAUDE.md`](CLAUDE.md)。

## 技术栈

Python 3.11+ | Pydantic v2 | Typer + Rich | structlog | SQLite + FTS5 | uv | asyncio Producer-Consumer

## 开发

```bash
# 运行测试
uv run pytest

# 代码检查
uv run --extra dev ruff check src/
uv run --extra dev ruff format src/

# 类型检查
uv run --extra dev mypy src/sciforge/core src/sciforge/pipeline src/sciforge/storage src/sciforge/cli
```

提交规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

Private — All Rights Reserved.
