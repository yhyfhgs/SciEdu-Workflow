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

# Pipeline（默认 VLM 后端 + 5 页/批 + 500 行/窗口，学科默认 math）
# schema v7 DB：任何 v7 之前的旧 DB 不兼容 → 首次升级需加 --restart 重建 DB
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
├── pipeline/    # 数据处理流水线 (5 阶段并发 Producer-Consumer)
│                #   orchestrator (asyncio.gather 6 Task + 4 stage queue + 缓存直通重放
│                #     + OrderIndexAllocator 跨文件全局 ID 分配 + detect_section_boundaries),
│                #   recognizers / vlm_converter（system+user prompt 双路径学科化,
│                #     detect_section_boundaries 识别章节结构）,
│                #   reorder_buffer (Stage2 并发后按 item_id 恢复顺序),
│                #   stages/stage1_extract.py     (批量滑动窗口提取 500→3000 行 +
│                #     3 层防御：regex 预检 + 改进 force-consume + no_items_found 快速跳过),
│                #   stages/stage1_review.py      (batch 校验 + 条件化跳过 low-risk batch),
│                #   stages/stage2_annotate.py    (单条打标 + 可选 N-worker 并发 + 局部 taxonomy 注入),
│                #   stages/stage2_review.py      (annotation 审核 + 条件化跳过 + N-worker 并发),
│                #   stages/stage3_persist.py     (合并 KP/Question 入库，无 LLM),
│                #   llm_schemas (Batch*/Single* Pydantic 强校验 + exercise near-tail guard),
│                #   tag_tree (带 generation 缓存) / taxonomy_validator (支持 branch_filter),
│                #   resume_state / retry (call_llm_json_with_schema_retry_with_feedback 带指标日志) /
│                #     audit / migrate
├── agents/      # 多 Agent 协作系统（生成/推理，与 pipeline 分离）
│                #   base.py + state.py + graph.py (BaseAgent + 极简 StateGraph)
│                #   qgen/: 5 阶段闭环 agent + retriever/generator/solver/verifier/annotator
│                #   solve_agent / tag_agent / doc_agent: 独立 Agent 封装
│                #   stub_agents: 6 个 Post-MVP 骨架 (KB/Recommend/...)
├── prompts/     # 提示词模板：templates.py 做两阶段学科注入 +
│                #   templates/pipeline|generation|solving|verification/*.txt
├── core/sentinel.py  # 共享 SENTINEL 单例（5 stage queue 通知流结束）
├── storage/     # 存储：protocol.py(StorageBackend),
│                #   sqlite_backend.py(含 discipline 列 + filter + SchemaVersionMismatch),
│                #   schema.sql (v7 — 保留 source_file 用于追溯来源；无 source_chapter/exam_*)
├── cli/         # CLI: main.py + parse.py (run/run-book/audit/migrate-legacy/db-stats)
│                #   + generate.py (free/solve/batch/lecture + variant/evolve/solve-deep 占位)
│                #   + config_cmd.py (config show/validate)
└── config/      # 配置：settings.py (env_prefix=SCIFORGE_), logging.py
```

## Pipeline 架构（5 阶段并发 Producer-Consumer）

```
logical_document  (单文件 or 多个分卷)
      ↓
 [Task0 Producer] 文档识别 (vlm_converter / pymupdf / markdown)
   - 每 5 页一批 → page_NNN-NNN.md (1-based)
   - 对每批调 detect_section_boundaries 填 MdBlockSource.section_boundaries
   - 顺序推入 q_md (背压, maxsize=pipeline_queue_size)
      ↓ asyncio.Queue q_md
 [Stage1Extract] 批量滑动窗口提取（LLM 调用 1）
   - 500 行窗口起步；0 条则扩 500 → 上限 3000；达 max 后 force consume
   - **Phase 2.5 三层防御**:
     (1) LLM 前 regex 预检：无条目标记且非最后窗口 → 直接跳窗口（0 LLM 调用）
     (2) force-consume 改进：slide = 整个窗口；incomplete_at_tail 时保留尾部不完整条目
     (3) no_items_found + 无标记 → 快速跳整个窗口
   - 输出 BatchItemExtractPayloadLLM（items 数组 + stop_reason + last_consumed_line）
   - 分配连续 item_ids（OrderIndexAllocator 跨文件保证不重叠）
   - 镜像到 items/stage1/item_<id>.json + emit ExtractedBatch 到 q_raw
      ↓ asyncio.Queue q_raw
 [Stage1Review] batch 校验（LLM 调用 2，**条件化**）
   - **Phase 4 条件化跳过**：all-low-risk + stop_reason=all_complete + 编号连续 +
     ≤ 3 items + solution_status 一致 → 直通不调 LLM
   - 否则：用原 window + items JSON 批量复核（一次 LLM 修复 N 条）
   - retry 耗尽 → 降级用原始 items
   - emit ReviewedItem 到 q_rev（按 item_id 顺序拆分）
      ↓ asyncio.Queue q_rev
 [Stage2Annotate] 单条标注（LLM 调用 3，可 N-worker 并发）
   - 输入 item + tag_tree + 局部 taxonomy（按 structural_context 推断 branch）→ SingleAnnotationLLM
   - **Phase 3**: tag_tree 序列化带 generation 缓存，taxonomy 全量预缓存
   - **Phase 5**: stage2_annotate_concurrency > 1 时 N-worker 并发 + ReorderBuffer 恢复顺序
   - fallback 时写空标注（tags=uncategorized、difficulty=5）
   - emit AnnotatedItem 到 q_ann
      ↓ asyncio.Queue q_ann
 [Stage2Review] annotation 审核（LLM 调用 4，**条件化 + 可并发**）
   - **Phase 4 条件化跳过**：tag 根正确 + taxonomy 前缀正确 + 3 ≤ difficulty ≤ 8 +
     非高危 item_type + 段数合理 → 直通
   - 可 config 关闭（stage2_review_enabled=false）或部分并发（stage2_review_concurrency）
   - emit ReviewedAnnotatedItem 到 q_done
      ↓ asyncio.Queue q_done
 [Stage3Persist] 合并入库（无 LLM）
   - taxonomy_validator 校验/修正 taxonomy_path
   - tag_tree.insert_path（集中累积，递增 generation）
   - 合并 item + annotation → KnowledgePoint 或 Question
   - asyncio.to_thread(storage.save_batch) + mirror items/persisted/
   - 禁止 item_id 倒退；允许 Stage1Review 拒绝 item 后形成 gap
```

**Exercise 漏 solution bug 三道防线**:
1. Stage1Extract prompt 显式规则：exercise/example/problem 未扫到下一条正式条目前禁标 no_solution_in_source
2. Pydantic `BatchItemExtractPayloadLLM` validator：`solution_status=no_solution_in_source` + item_type ∈ {exercise,example,problem} + end_line 距窗口末行 < 10 → ValueError 触发 retry
3. Stage1Review：发现问题时对应位置设 null，让 Stage1Extract 下一轮重扫

**Retry with feedback**:
- `call_llm_json_with_schema_retry_with_feedback(llm, prompt_builder, model, stage_name=...)`
- 每次重试：prompt_builder(last_error_text) 生成新 prompt，把 schema 错误注入 LLM 上下文
- 成功时 emit `llm_call_metrics` 结构化日志：prompt_chars / output_chars / tokens_est / stage_name
- Stage1/2 的四次 LLM 调用都走这条接口

**关键属性**:
- 完全 FIFO + 各 stage 单协程消费（或 Stage2 N-worker + ReorderBuffer）→ Stage3 按 item_id 严格递增入库
- `order_index = item_id` 贯通到 DB，供构建知识图谱
- **OrderIndexAllocator**：目录并发处理多文件时，单一全局 counter + asyncio.Lock 保证跨文件 item_id 不重复
- `tag` 是有序的 `english:中文` 段列表（如 `["math:数学","algebra:代数","group_theory:群论"]`）
- 断点恢复：state.json 记录 `next_item_id / current_window_size / consecutive_empty / stageN_extracted_count / stageN_completed / last_persisted_item_id / tag_tree_json / vlm_batches_done`
- **v7 之前的 DB / 旧 state.json 不兼容**：启动时检测 → raise SchemaVersionMismatch → 用户加 `--restart` 清库重跑
- 多文件分卷：`*_part_N_M.ext` 自动按页码分组，单个 logical_document 顺序识别
- 目录并发：每 logical_document 独立 `SQLiteStorageBackend` 实例（WAL + busy_timeout）+ 共享 OrderIndexAllocator

**Pipeline 优化开关（AppConfig）**:
- `empty_window_fast_skip: bool = True` — Phase 2.5 窗口标记预检
- `review_conditional_enabled: bool = True` — Phase 4 条件化 review
- `stage2_annotate_concurrency: int = 1` / `stage2_review_concurrency: int = 1` — Phase 5 并发 worker 数（>1 启用 ReorderBuffer）
- `qgen_lightweight_prefilter: bool = True` / `qgen_merge_difficulty_into_verify: bool = True` — Phase 6 QGen 优化

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
- **Pipeline 贯通**：`PipelineOrchestrator(discipline=...)` → 各 Stage 构造时接收 discipline
  → `VlmConverter(discipline=...)`；所有 prompt 通过 `get_extract_stage1_initial_template(discipline)` /
  `get_extract_stage1_review_template(discipline)` / `get_annotate_stage2_initial_template(discipline)` /
  `get_annotate_stage2_review_template(discipline)` / `get_vlm_prompt_template(discipline)` /
  `get_generation_prompt_template(path, discipline)` 按学科参数化
- **两阶段注入**：第一阶段 `_inject_discipline_vars` 用 `str.replace` 注入 `discipline_*`
  变量，对 value 自动做 `{`→`{{` 花括号转义（spec 写裸 LaTeX 安全），第二阶段调用方
  `.format()` 注入 runtime 占位符并还原转义
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
