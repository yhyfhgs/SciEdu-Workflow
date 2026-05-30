# SciForge

AI 驱动的**理工科知识处理与生成平台**。从教材、讲义、试卷中自动提取结构化知识，构建可查询的跨学科知识库，并通过多 Agent 协作引擎生成新题目、详细解答和教学讲义。

**支持学科**：数学、物理（MVP）；化学、生物等其他理工科通过 `DisciplineRegistry` 按配置接入（见 [`Docs/15_学科扩展指南.md`](Docs/15_学科扩展指南.md)）。

## 核心能力

### 1. 数据处理 Pipeline

5 阶段并发 Producer-Consumer（6 Task × 4 stage queue × asyncio.gather），按学科参数化：

- **Task0**: 多模态视觉识别（VLM）每 5 页一批转 markdown，system prompt 按学科注入
- **Stage1Extract**: 500 行滑动窗口，每窗口一次 LLM 调用批量提取所有完整条目；0 条时扩窗口到 3000 行
- **Stage1Review**: 原 window + 批量 items → LLM 批量校验修正（专门修复 exercise 漏 solution bug）
- **Stage2Annotate**: 单条 item 打 tags/difficulty/subject/taxonomy_path
- **Stage2Review**: 单条 annotation 审核 tag 格式/复用/校准
- **Stage3Persist**: 合并 item+annotation → KP/Question 入库（无 LLM）
- **Retry with feedback**: schema 校验失败时把错误文本注入下一轮 prompt
- **严格保序**: 全局 item_id 自增 + FIFO queue；Stage2 并发时由 ReorderBuffer 恢复顺序
- **直通缓存重放**: Task0 已完成时直接从 `recognize/file_*/` 读取跳过原始文件
- 每条 item 镜像到 `items/<stage>/item_<id>.json` 供断点恢复
- 异常隔离 + try/finally SENTINEL 传播避免死锁

### 2. 多 Agent 生成引擎

基于 `BaseAgent` + 极简 `StateGraph` 构建的多 Agent 协作系统（不依赖 LangGraph）：

- **QGenAgent** — 5 阶段闭环（检索 → 生成 → 求解 → 验证 → 难度标注）
- **SolveAgent** — 对已有题目独立生成详细解答
- **TagAgent** — 10 维难度评分
- **DocAssemblyAgent (lite)** — 按 tag 组装 markdown 讲义
- 6 个骨架 Agent (KB / Recommend / FormatAnalyzer / ContentExtractor / OcrCorrector / ContentTagger) 预留扩展

Agent 全部按 `Discipline` 参数化，可单独运行也可通过 StateGraph 编排。详见 [`Docs/16_Agent系统MVP.md`](Docs/16_Agent系统MVP.md)。

### 3. 存储与 CLI

- **SQLite 存储** — FTS5 全文检索 + 内容哈希去重 + `discipline` 列按学科过滤；每个条目带 `order_index` 用于后续知识图谱构建
- **CLI 接口** — 统一入口 `sciforge`（含 `sf` 短别名），Typer + Rich，支持 `parse run / run-book / audit / migrate-legacy / db-stats` 和 `generate free / solve / batch / lecture`

## 快速开始

### 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 包管理器
- LibreOffice（可选，VLM 后端解析 DOCX 时需要）

### 安装

```bash
git clone https://github.com/ZyphingFHGS/SciForge.git
cd SciForge
uv sync --all-extras

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
# 生成新数学题（QGenAgent 5 阶段闭环）
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

# 生成 markdown 讲义（按 tag 聚合知识点+例题+练习）
uv run sciforge generate lecture \
  -D math \
  -t "math:数学,analysis:数学分析" \
  --title "连续函数专题" \
  -o data/lectures/
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
│                #   qgen/: 5 阶段闭环（retriever/generator/solver/verifier/annotator）
│                #   solve_agent / tag_agent / doc_agent: 独立 Agent
│                #   stub_agents: 6 个 Post-MVP 骨架
├── prompts/     # 提示词模板（必须为 .txt 文件），两阶段学科参数化注入
├── storage/     # SQLite + FTS5 + 内容去重 + discipline 列索引
├── cli/         # Typer CLI（parse run/run-book/audit/migrate-legacy/db-stats +
│                #   generate free/solve/batch/lecture/variant/evolve/solve-deep）
└── config/      # AppConfig + structlog
```

详细架构见 [`Docs/01_数据处理Pipeline.md`](Docs/01_数据处理Pipeline.md)、[`Docs/15_学科扩展指南.md`](Docs/15_学科扩展指南.md)、[`Docs/16_Agent系统MVP.md`](Docs/16_Agent系统MVP.md) 与 [`CLAUDE.md`](CLAUDE.md)。

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
