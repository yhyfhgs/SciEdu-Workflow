# CLAUDE.md

本文件为 Claude Code 提供项目上下文，使 AI 辅助开发更高效。

## 项目概述

SciForge 是 AI 驱动的**理工科知识处理与生成平台**，当前阶段为 CLI MVP + Web SaaS MVP。核心功能：

1. **数据处理 Pipeline** — 通用格式（PDF/DOCX/MD/TeX）→ 结构化知识点和题目 → SQLite + FTS5 知识库，按学科参数化
2. **多 Agent 生成引擎** — QGen / Solve / Tag / DocAssembly-lite 四个完整 Agent + Post-MVP 骨架，基于 BaseAgent + 极简 StateGraph（不依赖 LangGraph）
3. **学科参数化** — 顶层 `Discipline` 枚举（math/physics/chemistry/biology/other）+ `DisciplineSpec` 配置每个学科的允许类型、tag 根、prompt 变量。MVP 阶段完整支持数学和物理
4. **Web SaaS 层** — `sciforge serve` 启动统一后端：FastAPI 网关（`/v1/*`）+ 内置控制台（`/app/`）+ 多租户鉴权计费（SaasStore）+ 个性化推荐（RecommendEngine），详见下文"Web SaaS 层"

## 技术栈

- **语言**: Python 3.11+
- **包管理**: uv（不使用 pip）
- **数据模型**: Pydantic v2（严格模式）
- **CLI**: Typer + Rich
- **日志**: structlog
- **LLM**: Anthropic / OpenAI / Gemini，通过统一 LLMClient 接口调用
- **存储**: SQLite（FTS5 全文检索、内容去重），通过 StorageBackend Protocol 抽象
- **Web 服务**: FastAPI + uvicorn（`server` extra，`sciforge serve` 启动）
- **测试**: pytest + pytest-asyncio
- **Lint**: ruff（line-length=100, target py311）
- **类型检查**: mypy（strict mode）

## 常用命令

```bash
# 安装依赖（注意：pdf-ml 与 pdf-mineru 互斥，--all-extras 会因 uv conflicts 失败，
# 必须用 all 聚合 extra；`uv run sciforge` 入口失效时补 `uv pip install -e .`）
uv sync --extra all

# 运行测试（tests/ 按模块分包：test_pipeline/ test_agents/ test_api/ ...）
uv run pytest
uv run pytest tests/test_pipeline -k stage1   # 单目录 / 按名过滤

# 代码检查
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# 类型检查
uv run mypy

# 运行 CLI
uv run sciforge --help   # 或 `uv run sf --help`

# Pipeline（默认 VLM 后端 + 5 页/批 + 500 行/窗口，学科默认 math）
# schema 当前 v10：v8/v9 旧库自动增量 ALTER 升级（见 _MIGRATIONS）；v8 之前需 --restart
# REC-01：含文字层的 PDF 默认自动走 fitz（0 LLM、秒级）；脏文字层扫描件加 --force-vlm 强制 VLM
uv run sciforge parse run path/to/file --db data/sciforge.db
uv run sciforge parse run scanned.pdf --db data/sciforge.db --force-vlm

# 处理物理材料
uv run sciforge parse run physics.pdf --db data/sciforge.db -D physics

# 多章节作为一本书（共享 order_index 和 tag_tree）
uv run sciforge parse run-book chap1.md chap2.md --db data/sciforge.db

# 进度盘点
uv run sciforge parse audit "Raw Data" --db data/sciforge.db

# 跨文档题答联动（习题册 + 答案册分文件时；REC-29）
uv run sciforge parse relink data/sciforge.db -D math

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

# 启动统一后端（API + 内置 SaaS 控制台；`/` → /app/ 控制台，/docs → OpenAPI）
# 默认鉴权+计费开启（SaaS 元数据存 data/saas.db）；--open 无鉴权，仅本地调试
uv run sciforge serve --port 8000
uv run sciforge serve --open
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
│                #   recognizers (REC-01 pdf_has_extractable_text_layer 文字层探测→fitz 降级) /
│                #     vlm_converter（system+user 学科化 + REC-02 批内 Semaphore 并发+保序+doc_lock）,
│                #   source_classifier (REC-14 detect_genre 书本/试卷/讲义 + 文件名 baseline),
│                #   reorder_buffer (Stage2 并发后按 item_id 恢复顺序),
│                #   stages/stage1_extract.py     (批量滑动窗口提取 500→3000 行 + 3 层防御 +
│                #     REC-12 文末 force_extract 兜底 + REC-14 genre 探测调 fast-skip + REC-20 章节先验注入;
│                #     REC-17 窗口管理点 _sync_state 节流; opt-in 时 _run_parallel 单文档并行),
│                #   stages/stage1_review.py      (batch 校验 + REC-06 风险信号制条件跳过),
│                #   stages/stage2_annotate.py    (REC-04 批量打标 BatchAnnotationLLM + REC-09 tag_tree 只读快照 + N-worker 并发),
│                #   stages/stage2_review.py      (annotation 审核 + REC-05 默认关闭),
│                #   stages/stage3_persist.py     (REC-10 批量入库+count_existing_content 去重 + REC-15 自动纠正 +
│                #     REC-18 figures 抽取 + REC-19 options 直通; tag 受控词表统一，无 LLM),
│                #   llm_schemas (Batch*/Single*/BatchAnnotationLLM 强校验 + near-tail guard + options/blanks),
│                #   tag_tree (TagTreeSnapshot 只读快照 REC-09 + generation 缓存) / tag_canonicalizer /
│                #     taxonomy_validator (branch_filter + REC-15 discipline 防跨学科漂移),
│                #   入库后 post-pass(无 LLM): numbering_audit(#2) / answer_linker(#3) /
│                #     relation_inference(#6 知识图谱边) / reconciliation(#7 复核),
│                #   document_segmenter + parallel_extract (#1 单文档并行,
│                #     已接入 Stage1Extract._run_parallel; segment_extract_fn=carry-aware 滑窗),
│                #   resume_state / retry (call_llm_json_with_schema_retry_with_feedback 带指标日志) /
│                #     audit / migrate
├── agents/      # 多 Agent 协作系统（生成/推理，与 pipeline 分离）
│                #   base.py + state.py + graph.py (BaseAgent + 极简 StateGraph)
│                #   qgen/: 5 阶段闭环 agent + retriever/generator/solver/verifier/annotator
│                #   solve_agent / tag_agent / doc_agent: 独立 Agent 封装
│                #   recommend/engine.py: RecommendEngine（P4 纯算法推荐，无 LLM——
│                #     BehaviorRecord 流水 → UserProfile → 最近发展区难度 + 薄弱标签打分）
│                #   stub_agents: 6 个 Post-MVP 骨架 (KB/Recommend/...；推荐引擎已在 recommend/ 实装，
│                #     RecommendAgent 占位仍保留)
├── api/         # Web SaaS 层（P2）：app.py(create_app 工厂 + /v1/* 端点),
│                #   schemas.py(请求/响应模型), security.py(HMAC-SHA256 无状态 token，标准库实现),
│                #   static/index.html(内置控制台，挂载 /app)
├── export/      # 组卷/讲义导出：render_markdown(零依赖) + DOCX(python-docx 延迟 import)
├── prompts/     # 提示词模板：templates.py 做两阶段学科注入 +
│                #   templates/pipeline|generation|solving|verification|classification|evolution|strategies/*.txt
├── core/sentinel.py  # 共享 SENTINEL 单例（5 stage queue 通知流结束）
├── storage/     # 存储：protocol.py(StorageBackend + MultiTenantStorageBackend),
│                #   sqlite_backend.py(discipline 列 + filter + SchemaVersionMismatch + _MIGRATIONS 增量迁移 +
│                #     relation_rows/save_relations/reconciliation_rows/validation_report +
│                #     count_existing_content(批量去重) + save_figures/figures_for_item + load_questions_by_discipline +
│                #     update_kp_type(REC-15 纠正) + delete_question 级联清边(REC-28)),
│                #   multitenant.py(MultiTenantSQLiteBackend — tenant_id 行级隔离 + item_embeddings
│                #     线性 cosine 语义检索；云端换 PG/pgvector 实现同协议),
│                #   saas.py(SaasStore — 独立 data/saas.db：tenants/api_keys/users/usage_records/tasks；
│                #     API Key 仅存 sha256；与题库 DB 解耦，便于私有化部署),
│                #   schema.sql (v10 — number_canonical/seq_*/question_form/content_hash UNIQUE/item_relations 边表 +
│                #     questions.options/correct_option/blanks(REC-19) + figures 表(REC-18) + order_index 单列索引(REC-16))
├── cli/         # CLI: main.py + parse.py (run[--force-vlm]/run-book/audit/relink/validate/migrate-legacy/db-stats)
│                #   + generate.py (free/solve/batch/lecture + variant/evolve/solve-deep 占位)
│                #   + serve.py (统一后端: uvicorn + create_app 组装) + config_cmd.py (config show/validate)
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
   - **REC-06 风险信号制条件跳过**（取代旧的"高危类型一律强审"）：仅在 stop_reason≠all_complete /
     习题贴近窗口末尾且声称无解 / 编号跳号（用 core/numbering 确定性解析）时才调 LLM；
     纯定义/定理 + 干净提取 + 编号连续 → 直通
   - 否则：用原 window + items JSON 批量复核（一次 LLM 修复 N 条）
   - retry 耗尽 → 降级用原始 items
   - emit ReviewedItem 到 q_rev（按 item_id 顺序拆分）
      ↓ asyncio.Queue q_rev
 [Stage2Annotate] 标注（LLM 调用 3，**REC-04 批量打标** + 可 N-worker 并发）
   - **REC-04**: 同来源 batch（同 block_index）的 N 条合并为**一次** LLM 调用（BatchAnnotationLLM，
     1:1 长度校验 + 单条失败降级逐条）；stage2_annotate_batch=True 默认开。800→~180 次
   - 输入 item + tag_tree 只读快照(REC-09) + 局部 taxonomy（按 structural_context 推断 branch）
   - **REC-09**: Stage2 读 TagTreeSnapshot（消除 Stage3 每条 insert 触发的序列化缓存失效 + 读写隔离）
   - **REC-03**: stage2_annotate_concurrency 默认 4，N-worker 并发 + ReorderBuffer 恢复顺序
   - fallback 时写兜底标注（tags=uncategorized、taxonomy=学科纯根 REC-15、difficulty=5）
   - emit AnnotatedItem 到 q_ann
      ↓ asyncio.Queue q_ann
 [Stage2Review] annotation 审核（LLM 调用 4，**REC-05 默认关闭**）
   - **REC-05**: stage2_review_enabled 默认 **false**——其校验职责（学科根/tag/taxonomy）
     与 Stage3 确定性规则重叠，关闭后这一轮 LLM 调用归零（700→0）；需 LLM 复核显式置 true
   - 开启时：条件化跳过 + 可并发（stage2_review_concurrency 默认 2）
   - emit ReviewedAnnotatedItem 到 q_done
      ↓ asyncio.Queue q_done
 [Stage3Persist] 合并入库（无 LLM）
   - taxonomy_validator 校验/修正 taxonomy_path
   - tag_tree.insert_path（集中累积，递增 generation）
   - 合并 item + annotation → KnowledgePoint 或 Question
   - asyncio.to_thread(storage.save_batch) + mirror items/persisted/
   - 禁止 item_id 倒退；允许 Stage1Review 拒绝 item 后形成 gap
```

**Exercise 漏 solution bug 四道防线**:
1. Stage1Extract prompt 显式规则：exercise/example/problem 未扫到下一条正式条目前禁标 no_solution_in_source
2. Pydantic `BatchItemExtractPayloadLLM` validator：`solution_status=no_solution_in_source` + item_type ∈ {exercise,example,problem} + end_line 距窗口末行 < 10 → ValueError 触发 retry
3. Stage1Review：发现问题时对应位置设 null，让 Stage1Extract 下一轮重扫
4. **REC-12 文末兜底**：文档/章节末尾窗口若 regex 有标记但 LLM 返空 → `_extract_batch(force_extract=True)`
   关 near-tail guard 再尽力提取一次（solution_deferred），避免试卷最后几题/章末习题在 EOF 永久漏失

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
- **schema v10**：v8/v9 旧库自动增量 ALTER 升级（`_MIGRATIONS`）；v8 之前 → raise SchemaVersionMismatch → 加 `--restart`
- 多文件分卷：`*_part_N_M.ext` 自动按页码分组，单个 logical_document 顺序识别
- 目录并发：每 logical_document 独立 `SQLiteStorageBackend` 实例（WAL + busy_timeout）+ 共享 OrderIndexAllocator

**Pipeline 优化开关（AppConfig，2026Q2 优化批次后默认值）**:
- `empty_window_fast_skip: bool = True` — Phase 2.5 窗口标记预检（REC-14：试卷/讲义自动关以提召回）
- `review_conditional_enabled: bool = True` — 条件化 review（REC-06 风险信号制）
- **REC-03**: `stage2_annotate_concurrency: int = 4` / `stage2_review_concurrency: int = 2` /
  `pipeline_stage_queue_size: int = 8` / `max_concurrent_llm_calls: int = 8`（默认开 Stage2 并发，依赖 REC-09 快照）
- **REC-04**: `stage2_annotate_batch: bool = True` / `stage2_annotate_batch_size: int = 8`（批量打标）
- **REC-05**: `stage2_review_enabled: bool = False`（默认关 Stage2Review）
- **REC-01**: `vlm_auto_text_detect: bool = True` / `vlm_text_layer_min_chars_per_page: int = 100`（文字层探测走 fitz）
- **REC-09**: `tag_tree_snapshot_refresh_gap: int = 16`（Stage2 读 tag_tree 只读快照）
- **REC-15**: `reconciliation_autofix: bool = False`（高置信 kp_type 误判回改 DB，保守默认关）
- **REC-13**: LLMConfig.PRESETS 内置 thinking 默认（extract/vlm=disabled，review/tag/annotate=小 budget）
- **REC-08**: ProviderConfig/LLMConfig `supports_prompt_cache: bool = False`（兼容网关 prompt cache opt-in）
- `qgen_lightweight_prefilter: bool = True` / `qgen_merge_difficulty_into_verify: bool = True` — Phase 6 QGen 优化
- `pipeline_prompt_cache_enabled: bool = True` — #9 静态指令拆到可缓存 system 前缀
- `answer_linking_enabled / relation_inference_enabled / reconciliation_enabled: bool = True` — 入库后 post-pass 开关（#3/#6/#7）
- `intra_doc_parallel_extract: bool = False` / `intra_doc_segment_concurrency: int = 4`
  / `intra_doc_min_segment_lines: int = 40` — #1 单文档内并行（**已接入** Stage1Extract，opt-in）。
  按 section 边界切段、段间并发、段内 carry-aware 滑窗（不在窗口边界切断条目）。
  CLI：`parse run --parallel-extract [--segment-concurrency N]`。适合**单个超大文件**提速；
  目录多文件已自带跨文件并行（`max_concurrent_files`）。注：opt-in 模式会先 drain 全部 block
  再并行提取（失去 Task0↔Stage1 重叠），且中断 resume 为整段重提（靠 content_hash 去重兜底）。

**字段规范化与类型双维度（#4/#5）**：`KnowledgePoint/Question` 含 `number_canonical`（由 `number` 自动派生，消除 "1" vs "第1题"）+ `seq_chapter/seq_section/seq_index`；`Question.question_form`（选择/填空/判断/解答，与 `question_type` 认知类别正交，Stage1 从题面识别）。`DisciplineSpec` 含 `allowed_question_forms` / `tag_aliases`（受控词表别名）/ `branch_keywords`。

**推理模型思考控制（#9）**：`thinking={"type":"disabled"}` 或 `{"type":"enabled","budget_tokens":N}` 经 `extra_body` 透传（兼容老 SDK + anthropic 兼容网关如 MiniMax/Kimi）；`ANTHROPIC_COMPATIBLE` 能力矩阵已放行 `thinking`。提取等高频任务务必设小 budget（MiniMax-M3 不限制会单窗口 >5 分钟）。

**生成引擎（Agent 系统）**: `cli/generate.py` 的 `free/solve/batch/lecture` 子命令
已接入多 Agent 系统：
- `generate free` → QGenAgent 5 阶段闭环（retriever → generator → solver → verifier → annotator）
- `generate solve` → SolveAgent（内部复用 qgen/solver.py）
- `generate batch` → 批量循环 QGenAgent.run
- `generate lecture` → DocAssemblyAgent lite（按 tag 组装 markdown 讲义）

`generate variant/evolve/solve-deep` 保留为 Post-MVP 占位。架构见 `src/sciforge/agents/`
和 [`Docs/16_Agent系统MVP.md`](Docs/16_Agent系统MVP.md)。

## Web SaaS 层（P2/P4，`sciforge serve`）

- **组装方式**：`cli/serve.py` 构造 QGen/Solve 工厂 + 长生命周期检索 storage（WAL 并发读）
  + `SaasStore`，注入 `api/app.py:create_app(qgen_factory, solve_factory, saas_store=...,
  storage_factory=..., jwt_secret=..., cors_origins=..., serve_console=...)`。agent 经工厂注入，
  便于测试替换；`saas_store=None`（CLI `--open`）= 无鉴权开放模式
- **端点**（`/v1/*`）：health / auth/register / auth/login / generate / solve / usage /
  behavior / recommend / generate/batch（异步任务）/ tasks/{id} / papers（组卷）/
  papers/export（markdown/DOCX，走 `export/`）；`/` 重定向内置控制台 `/app/`，`/docs` OpenAPI
- **双通道鉴权**：B2B 用 `X-API-Key`（SaasStore 校验 sha256），C 端用 `Bearer <token>`
  （`api/security.py` HMAC-SHA256 无状态 token，标准库实现无 PyJWT，payload 含 exp/user_id/tenant_id）。
  重端点走 `require_tenant`（校验配额 429），轻端点（behavior/recommend）走 `require_identity`（不计配额）
- **数据分离**：题库 `data/sciforge.db`（内容）与 SaaS 元数据 `data/saas.db`（租户/Key/用户/用量/任务）
  分库，私有化部署时题库可独立交付
- **多租户题库**：`MultiTenantSQLiteBackend` 检索强制注入 `tenant_id` 过滤（行级隔离），
  附加 `item_embeddings` 表做线性 cosine 语义检索（幂等建表，不入核心 schema 迁移）
- **推荐**：`agents/recommend/engine.py` 纯算法（无 LLM），行为流水聚合 UserProfile →
  最近发展区（掌握难度 + 步长）+ 薄弱标签命中打分；冷启动退化为默认难度
- 后续 P3（多端 + 网关 + 统一身份）见 [`Docs/17_前端产品化实施计划.md`](Docs/17_前端产品化实施计划.md)

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
- 存储默认 SQLite（题库 `--db data/sciforge.db`；SaaS 元数据独立 `data/saas.db`）
- Pipeline checkpoint 在 `data/checkpoint/<file_hash>/`，含 `state.json` 与 `recognize/file_NNN_<stem>/page_NNN-NNN.md`
- **云同步冲突副本**：仓库位于 iCloud 目录，会产生 `xxx 2.py` / `xxx 2/` 冲突副本
  （如 `prompts/templates/pipeline 2/`）。pytest 已通过 `addopts --ignore-glob` 忽略、
  .gitignore 已排除——遇到时不要引用或"修复"它们，可安全删除
