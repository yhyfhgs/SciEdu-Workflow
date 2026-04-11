# CLAUDE.md

本文件为 Claude Code 提供项目上下文，使 AI 辅助开发更高效。

## 项目概述

SciForge 是 AI 驱动的**理工科知识处理与生成平台**，当前阶段为 CLI MVP。核心功能：

1. **数据处理 Pipeline** — 通用格式（PDF/DOCX/MD/TeX）→ 结构化知识点和题目 → SQLite + FTS5 知识库，按学科参数化
2. **多 Agent 生成引擎** — QGen / Solve / Tag / DocAssembly-lite 四个完整 Agent + 6 个 Post-MVP 骨架，基于 BaseAgent + 极简 StateGraph（不依赖 LangGraph）
3. **学科参数化** — 顶层 `Discipline` 枚举（math/physics/chemistry/biology/other）+ `DisciplineSpec` 配置每个学科的允许类型、tag 根、prompt 变量。MVP 阶段完整支持数学和物理

## 技术栈

- **语言**: Python 3.11+
- **包管理**: uv（不使用 pip）
- **数据模型**: Pydantic v2（严格模式）
- **CLI**: Typer + Rich
- **日志**: structlog
- **LLM**: Anthropic / OpenAI / Gemini，通过统一 LLMClient 接口调用
- **存储**: SQLite（FTS5 全文检索、内容去重），通过 StorageBackend Protocol 抽象
- **测试**: pytest + pytest-asyncio
- **Lint**: ruff（line-length=100, target py311）
- **类型检查**: mypy（strict mode）

## 常用命令

```bash
# 安装依赖
uv sync --all-extras

# 运行测试
uv run pytest

# 代码检查
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# 类型检查
uv run mypy

# 运行 CLI
uv run sciforge --help   # 或 `uv run sf --help`

# Pipeline（默认 VLM 后端 + 5 页/批 + 200 行/窗口，学科默认 math）
uv run sciforge parse run path/to/file --db data/sciforge.db

# 处理物理材料
uv run sciforge parse run physics.pdf --db data/sciforge.db -D physics

# 多章节作为一本书（共享 order_index 和 tag_tree）
uv run sciforge parse run-book chap1.md chap2.md --db data/sciforge.db

# 进度盘点
uv run sciforge parse audit "Raw Data" --db data/sciforge.db

# 数据库统计
uv run sciforge parse db-stats data/sciforge.db

# 生成新题目（QGenAgent 5 阶段闭环）
uv run sciforge generate free -D math \
  -s "real_analysis:实分析" \
  -t "math:数学,analysis:数学分析" \
  -d 6 --type proof --db data/sciforge.db

# 对已有题目生成详细解答
uv run sciforge generate solve <question_id> --db data/sciforge.db

# 生成 markdown 讲义
uv run sciforge generate lecture -D math \
  -t "math:数学,analysis:数学分析" \
  --title "连续函数专题" -o data/lectures/
```

## 项目结构

```
src/sciforge/
├── core/        # 核心领域模型与异常定义
│                #   models.py (Discipline/KnowledgePoint/Question/Lecture/
│                #     FileExtractionState/...), exceptions.py (SciForgeError ...)
├── disciplines/ # 学科库
│                #   registry.py: DisciplineSpec + DisciplineRegistry 单例
│                #   specs/math.py + specs/physics.py
├── llm/         # LLM 统一客户端：client.py, config.py, parser.py, backends/
├── pipeline/    # 数据处理流水线 (Producer-Consumer 两阶段)
│                #   orchestrator(2 Task + 单 queue + 缓存直通重放 + drain consumer),
│                #   recognizers / vlm_converter（system+user prompt 双路径学科化）,
│                #   task1_extractor(动态窗口扩展 + DisciplineSpec 类型校验),
│                #   tag_tree / resume_state / retry / audit / migrate
├── agents/      # 多 Agent 协作系统
│                #   base.py + state.py + graph.py (BaseAgent + 极简 StateGraph)
│                #   qgen/: 5 阶段闭环 agent + retriever/generator/solver/verifier/annotator
│                #   solve_agent / tag_agent / doc_agent: 独立 Agent 封装
│                #   stub_agents: 6 个 Post-MVP 骨架 (KB/Recommend/...)
├── prompts/     # 提示词模板：templates.py 做两阶段学科注入 +
│                #   templates/pipeline|generation|solving|verification/*.txt
├── storage/     # 存储：protocol.py(StorageBackend),
│                #   sqlite_backend.py(含 discipline 列 + filter),
│                #   schema.sql (v4)
├── cli/         # CLI: main.py + parse.py (run/run-book/audit/migrate-legacy/db-stats)
│                #   + generate.py (free/solve/batch/lecture + variant/evolve/solve-deep 占位)
└── config/      # 配置：settings.py (env_prefix=SCIFORGE_), logging.py
```

## Pipeline 架构（Producer-Consumer 单管道）

```
logical_document  (单文件 or 多个分卷)
      ↓
 [Task0 Producer] 文档识别 (vlm_converter / pymupdf / markdown)
   - 每 5 页一批 → page_NNN-NNN.md (1-based)
   - 顺序推入 queue (背压, maxsize=4)
   - vlm_batches_done 写 state.json
      ↓ asyncio.Queue (FIFO 严格顺序)
 [Task1 Consumer] Task1Extractor
   - 取出 mdblock → 累计全局行号 → 行缓冲
   - 200 行滑动窗口
   - 单次 LLM 调用 (extract_and_classify):
       提取 + 校正 + 分类 + 打标签 + 难度评估
       prompt 注入 tag 子树（避免同义异名）
   - 提取出的条目分配 order_index → 直接 storage.save_batch()
   - 行级 checkpoint 持久化
   - "incomplete" 时等下一个 mdblock 拼接
   - 单窗口失败 → 标记 failed_blocks，不阻断整体
```

**关键属性**:
- 完全 FIFO，全局行号跨 mdblock 连续
- `order_index` 单调递增供后续构建知识图谱
- `tag` 是有序的 `english:中文` 段列表（如 `["math:数学","algebra:代数","group:群论"]`）
- 断点恢复：state.json 记录 vlm_batches_done / current_block_idx / line_offset / line_buffer_carry / order_index_next / tag_tree_json
- state 写入节流（默认 3 秒，`checkpoint_flush_interval`），异常/完成时强制刷盘
- 多文件分卷：`*_part_N_M.ext` 自动按页码分组，作为单个 logical_document 顺序识别
- 目录并发：`process_directory` 为每个 logical_document 构造独立 `SQLiteStorageBackend` 实例（WAL + busy_timeout），避免共享 connection 的线程风险

**生成引擎（Agent 系统）**: `cli/generate.py` 的 `free/solve/batch/lecture` 子命令
已接入多 Agent 系统：
- `generate free` → QGenAgent 5 阶段闭环（retriever → generator → solver → verifier → annotator）
- `generate solve` → SolveAgent（内部复用 qgen/solver.py）
- `generate batch` → 批量循环 QGenAgent.run
- `generate lecture` → DocAssemblyAgent lite（按 tag 组装 markdown 讲义）

`generate variant/evolve/solve-deep` 保留为 Post-MVP 占位。架构见 `src/sciforge/agents/`
和 [`Docs/16_Agent系统MVP.md`](Docs/16_Agent系统MVP.md)。

## 学科参数化（Discipline）

- **顶层枚举**：`Discipline` (math/physics/chemistry/biology/other)，定义在 `core/models.py`
- **学科 spec**：`DisciplineSpec` 配置 `allowed_kp_types` / `allowed_question_types` /
  `tag_root` / `prompt_vars`，集中在 `src/sciforge/disciplines/specs/`
- **Pipeline 贯通**：`PipelineOrchestrator(discipline=...)` → `Task1Extractor(discipline=...)`
  → `VlmConverter(discipline=...)`；所有 prompt 通过 `get_extract_prompt_template(discipline)` /
  `get_vlm_prompt_template(discipline)` / `get_generation_prompt_template(path, discipline)`
  按学科参数化
- **两阶段注入**：第一阶段 `str.replace` 注入 `discipline_*` 变量（不触发 `.format()`，
  保留 JSON 示例 `{{}}` 转义），第二阶段调用方 `.format()` 注入 runtime 占位符
- **CLI 参数**：`parse run` / `parse run-book` / `generate *` 全部支持 `--discipline / -D`
- **新增学科**：见 [`Docs/15_学科扩展指南.md`](Docs/15_学科扩展指南.md)

## 代码规范

- 提交消息遵循 Conventional Commits：`<type>(<scope>): <subject>`
- 分支策略：`main`(稳定) → `dev`(集成) → `feat/<name>`(功能)
- 所有新代码必须通过 ruff 和 mypy 检查
- 异步函数使用 async/await，LLM 调用均为异步
- Pydantic 模型定义在 `core/models.py`，不要分散到其他模块
- 配置通过 pydantic-settings 管理，环境变量前缀 `SCIFORGE_`
- 日志使用 structlog，不要使用 print 或 logging 模块
- 优先使用枚举判别而非子类继承：KnowledgePoint(kp_type=KPType.LEMMA)，不要创建 LemmaItem 子类
- StorageBackend 通过 Protocol 抽象，新增后端只需实现统一接口
- Tag 格式约束：每段 `english:中文`，一个条目的 tags 是有序段列表代表一条根→叶路径
- prompt 必须存为 `.txt` 文件（不要内联到 Python 字符串）

## 重要约定

- `Raw Data/` 和 `data/` 不纳入版本控制，包含原始教材和运行时生成数据
- `.env` 包含 API Key，绝不提交
- LLM 后端通过 `llm/backends/` 下的适配器实现，新增后端只需实现统一接口
- Pipeline 统一入口为 `sciforge parse run`，默认 VLM 多模态识别 PDF/DOCX
- 存储默认 SQLite（`--db data/sciforge.db`）
- Pipeline checkpoint 在 `data/checkpoint/<file_hash>/`，含 `state.json` 与 `recognize/file_NNN_<stem>/page_NNN-NNN.md`
