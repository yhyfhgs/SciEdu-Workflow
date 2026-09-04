# AGENTS.md

本文件是 SciForge 的**单一权威开发上下文**——项目概况、常用命令、架构、协作规范与当前工作重点全部集中于此，供 Claude Code、Codex 等 AI 协作代理与人类贡献者使用。

> **单一源约定**：`CLAUDE.md` 仅是指向本文件的引用入口（`@AGENTS.md` 导入）。更新任何上下文只改本文件，不要在 `CLAUDE.md` 中新增内容。

## 项目概述

SciForge 是 AI 驱动的**理工科知识处理与生成平台**，当前阶段为 CLI MVP + Web SaaS MVP。核心功能：

1. **数据处理 Pipeline** — PDF/DOCX/MD/TeX 及 CSV/TSV/XLSX/JSON/JSONL/HTML/EPUB/TXT/RST/图片 → 结构化条目、概念图与质量治理 → SQLite + FTS5，按学科参数化
2. **多 Agent 生成引擎** — QGen 专用可恢复 Harness + Solve / Tag / DocAssembly-lite；支持变式、定向进化、深度求解、数值/代码/Lean 验证与持久 run/checkpoint/event
3. **学科参数化** — 顶层 `Discipline` 枚举（math/physics/chemistry/biology/other）+ `DisciplineSpec` 配置每个学科的允许类型、tag 根、prompt 变量。MVP 阶段完整支持数学和物理
4. **Web SaaS 层** — `sciforge serve` 启动统一后端：FastAPI 网关（`/v1/*`）+ 内置零构建 Chatbox 工作台（`/app/`，会话历史/SSE/验证面板/暂停恢复）+ 多租户鉴权计费（SaasStore）+ 个性化推荐（RecommendEngine），详见下文"Web SaaS 层"

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

# LLM 配置（v2 统一 TOML：[providers.*] 连接池 + [tasks.*] 按任务选 provider/model/thinking +
# [pipeline]/[logging]）。CLI 自动加载 config/sciforge.toml，--llm-config 可换路径；
# api_key 推荐 "${ENV_VAR}" 占位符从 .env 解析；SCIFORGE_* 环境变量可覆盖 TOML 值
cp config/sciforge.example.toml config/sciforge.toml

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
# schema 当前 v14：v8-v13 旧库按 _MIGRATIONS 逐版本升级；更旧/未来/不完整 schema 拒绝写入
# REC-01：含文字层的 PDF 默认自动走 fitz（0 LLM、秒级）；脏文字层扫描件加 --force-vlm 强制 VLM
uv run sciforge parse run path/to/file --db data/sciforge.db
uv run sciforge parse run scanned.pdf --db data/sciforge.db --force-vlm

# CSV/TSV/XLSX/JSON/JSONL/HTML/EPUB/TXT/RST 默认由确定性 adapter 转 Markdown block（Task0 0 LLM）
uv run sciforge parse run path/to/dataset.xlsx --db data/sciforge.db

# 处理物理材料
uv run sciforge parse run physics.pdf --db data/sciforge.db -D physics

# 多章节作为一本书（共享 order_index 和 tag_tree）
uv run sciforge parse run-book chap1.md chap2.md --db data/sciforge.db

# 进度盘点
uv run sciforge parse audit "Raw Data" --db data/sciforge.db

# 跨文档题答联动（习题册 + 答案册分文件时；REC-29）
uv run sciforge parse relink data/sciforge.db -D math

# 数据库统计 / 入库后只读体检（提取量/类型分布/题答绑定率/编号/去重/图谱边，不改库）
uv run sciforge parse db-stats data/sciforge.db
uv run sciforge parse validate data/sciforge.db

# LLM 成本报告：聚合 JSON 日志中的 llm_call_metrics 事件（需 [logging] log_format="json"）
uv run sciforge parse cost-report <pipeline-json-log>

# 生成新题目（QGenAgent 专用状态图 Harness）
uv run sciforge generate free -D math \
  -s "real_analysis:实分析" \
  -t "math:数学,analysis:数学分析" \
  -d 6 --type proof --db data/sciforge.db

# 对已有题目生成详细解答
uv run sciforge generate solve <question_id> --db data/sciforge.db

# 变式、定向进化、带 sandbox 工具的深度求解
uv run sciforge generate variant <question_id> --strategy A1 --db data/sciforge.db
uv run sciforge generate evolve -t "math:数学,analysis:数学分析" -d 7 --max-improve 2
uv run sciforge generate solve-deep <question_id> --db data/sciforge.db

# 可恢复长任务（DurableQGenHarness；状态存 data/qgen_runtime.db，node-boundary 恢复）
uv run sciforge generate durable -t "math:数学,analysis:数学分析" -d 8 --evolve
uv run sciforge generate resume <run_id>
uv run sciforge generate cancel <run_id>

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
│                #   recognizers (REC-01 文字层探测→fitz；AdapterRecognizer；有界单图 VLM) /
│                #   format_adapters (CSV/TSV/JSON/JSONL/HTML/XLSX/EPUB/TXT 确定性适配；
│                #     ZIP bomb/path traversal 与输入深度/体积/列数防护；source locator) /
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
│                #   extraction_harness / boundary_patch + patch_instruction / concept_linker / evaluation
│                #     (marker ledger 完整性、局部修补、稳定 concept 图、确定性质量门禁与 review queue),
│                #   text_tools/ (markers/ocr_repair/math_balance/numbering_check/structure 等确定性文本工具),
│                #   resume_state / retry (call_llm_json_with_schema_retry_with_feedback 带指标日志) /
│                #     audit / migrate / cost_report(llm_call_metrics 聚合) / char_span / format_profile
├── agents/      # 多 Agent 协作系统（生成/推理，与 pipeline 分离）
│                #   base.py + state.py + graph.py (BaseAgent + 支持 node-boundary resume 的 StateGraph)
│                #   runtime/: 独立 SQLiteRunStore（run/step/budget/checkpoint/event/cancel/resume）
│                #   qgen/: 专用 Harness（retriever/generator/prefilter/solver/deep_solver/verifier/
│                #     sandbox/evolver/population/durable_harness/annotator）
│                #   solve_agent / tag_agent / doc_agent: 独立 Agent 封装
│                #   recommend/engine.py: RecommendEngine（P4 纯算法推荐，无 LLM——
│                #     BehaviorRecord 流水 → UserProfile → 最近发展区难度 + 薄弱标签打分）
│                #   stub_agents: 6 个 Post-MVP 骨架 (KB/Recommend/...；推荐引擎已在 recommend/ 实装，
│                #     RecommendAgent 占位仍保留)
├── api/         # Web SaaS 层（P2）：app.py(create_app 工厂 + /v1/* 端点),
│                #   schemas.py(请求/响应模型), security.py(HMAC-SHA256 token + scope/quota),
│                #   tenant_storage.py(认证租户题库视图，public+本租户，先过滤后排序/limit),
│                #   conversations.py(Chatbox 会话/消息/工件存储，独立 data/conversations.db，
│                #     tenant-scoped；执行状态唯一权威仍是 SQLiteRunStore) +
│                #   conversation_routes.py(/v1/conversations* + /v1/runs/{id} SSE 事件流/cancel/resume),
│                #   static/index.html(零构建 Chatbox 七模式工作台，挂载 /app)
├── export/      # 组卷/讲义导出：render_markdown(零依赖) + DOCX(python-docx 延迟 import)
├── prompts/     # 提示词模板：templates.py 做两阶段学科注入 +
│                #   templates/pipeline|generation|solving|verification|classification|evolution|strategies/*.txt
├── core/sentinel.py  # 共享 SENTINEL 单例（5 stage queue 通知流结束）
├── storage/     # 存储：protocol.py(StorageBackend + MultiTenantStorageBackend),
│                #   query.py(QualityGateFilter 等类型化检索策略，SQLite 后端与租户视图共用),
│                #   sqlite_backend.py(discipline 列 + filter + SchemaVersionMismatch + _MIGRATIONS 增量迁移 +
│                #     relation_rows/save_relations/reconciliation_rows/validation_report +
│                #     concept 四表 + item_evaluations/review_queue + 质量门禁读取 +
│                #     count_existing_content(批量去重) + save_figures/figures_for_item + load_questions_by_discipline +
│                #     update_kp_type(REC-15 纠正) + delete_question 级联清边(REC-28)),
│                #   multitenant.py(MultiTenantSQLiteBackend — tenant_id 行级隔离 + item_embeddings
│                #     线性 cosine 语义检索；云端换 PG/pgvector 实现同协议),
│                #   saas.py(SaasStore — 独立 data/saas.db：tenants/api_keys/users/usage_records/tasks；
│                #     API Key 仅存 sha256；与题库 DB 解耦，便于私有化部署),
│                #   schema.sql (v14 — v10 条目字段 + v12 concept 四表 +
│                #     v13 evaluation/review + v14 ingestion_status/tenant-scoped question dedup/FTS update)
├── cli/         # CLI: main.py + parse.py (run[--force-vlm]/run-book/audit/audit-tags/relink/
│                #     validate/cost-report/bench/migrate-legacy/db-stats)
│                #   + generate.py (free/solve/batch/lecture/variant/evolve/solve-deep/durable/resume/cancel)
│                #   + serve.py (统一后端: uvicorn + create_app 组装) + config_cmd.py (config show/validate)
└── config/      # 配置：settings.py (AppConfig：dotenv → config/sciforge.toml → SCIFORGE_* env 逐层加载), logging.py
```

## Pipeline 架构（5 阶段并发 Producer-Consumer）

```
logical_document  (单文件 or 多个分卷)
      ↓
 [Task0 Producer] 文档识别 (format adapter / vlm_converter / pymupdf / markdown)
   - 每 5 页一批 → page_NNN-NNN.md (1-based)
   - CSV/TSV/JSON/JSONL/HTML/XLSX/EPUB/TXT 走确定性 AdapterRecognizer，0 LLM
   - ZIP 容器与结构化输入先做体积、entry、压缩比、路径、列数、深度限制
   - 对每批调 detect_section_boundaries 填 MdBlockSource.section_boundaries
   - VLM 整批被内容审核误判时自动回退逐页识别，只丢被误判页不丢整批
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
      ↓
 [Deterministic post-pass] 编号/答案/关系/概念/评价（无 LLM，orchestrator 块 10.5–10.8，可独立开关）
   - numbering_audit + answer_linker + relation_inference + reconciliation
   - concept_linker 写 concepts/aliases/item_concepts/concept_relations
   - evaluation 写 item_evaluations；风险条目进入 review_queue，不静默删除
```

**Pipeline 主线 LLM 任务**（`config/sciforge.toml` 的 `[tasks.*]` 可按任务分别配置 provider/model/thinking）：

| 任务名 | 用途 | 必备能力 |
|---|---|---|
| `vlm_convert` | Task0：多模态视觉识别 PDF/DOCX → markdown | 支持图像输入 |
| `extract_and_classify` | Stage1Extract：批量窗口提取 + 内容纠错 | JSON 结构化输出，16-32k max_tokens |
| `review` | Stage1Review：批量 item 校验（fallback 到 extract_and_classify） | JSON，可用廉价模型 |
| `tag` | Stage2Annotate：单条打 tags/difficulty/taxonomy（fallback 到 extract_and_classify） | JSON |
| `annotate_review` | Stage2Review：单条 annotation 审核（fallback 到 tag） | JSON，可用廉价模型 |

**关键 prompt**（全部含 `{retry_feedback}` 占位符）：
- `prompts/templates/pipeline/extract_stage1_initial.txt` (Stage1Extract)
- `prompts/templates/pipeline/extract_stage1_review.txt` (Stage1Review)
- `prompts/templates/pipeline/annotate_stage2_initial.txt` (Stage2Annotate)
- `prompts/templates/pipeline/annotate_stage2_review.txt` (Stage2Review)
- `prompts/templates/pipeline/vlm_convert.txt` (Task0)

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
- 断点恢复：state.json 记录 `next_item_id / current_window_size / consecutive_empty / stageN_extracted_count / stageN_completed / last_persisted_item_id / tag_tree_json / vlm_batches_done`；旧 v1 state schema 不兼容 → raise `StateSchemaMismatch`，要求 `--restart`
- **异常隔离**：任一 stage 异常 → try/finally 向下游 put SENTINEL 避免死锁；Stage1 未解决的非空 span 写 `uncertain` 占位而不静默丢弃，Stage2 失败写保守空标注，不阻断整体
- **schema v14**：逐版本迁移加入 concept 图、确定性评价/review queue、无损摄取状态、
  tenant-scoped question 去重与 FTS UPDATE trigger；无法安全迁移时
  raise `SchemaVersionMismatch`，必须明确 `--restart`，不得手工跳版本
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
- `adapter_rows_per_block / adapter_max_input_bytes / adapter_max_archive_bytes /
  adapter_max_archive_entries / adapter_max_compression_ratio / adapter_max_columns /
  adapter_max_json_depth` — 结构化输入的分块与资源上限
- `item_evaluation_enabled: bool = True` / `evaluation_extraction_threshold: float = 0.7` /
  `evaluation_solution_threshold: float = 0.6` — 默认确定性评价与 review queue
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

## 生成引擎（多 Agent 系统）

基于 `BaseAgent[TIn, TOut]` 抽象基类 + 极简 `StateGraph`（不依赖 LangGraph）构成的多 Agent
协作系统。所有 Agent 按 `Discipline` 参数化，可单独运行也可通过状态图编排。
详见 [`Docs/16_Agent系统MVP.md`](Docs/16_Agent系统MVP.md)。

**十大 Agent 实现状态**:

| # | Agent | 状态 | 文件 |
|---|---|---|---|
| 1 | KB Agent | 🔶 骨架 | `agents/stub_agents.py::KBAgent` |
| 2 | **QGen Agent** | ✅ Harness | `agents/qgen/agent.py` + `agents/qgen/` + `agents/runtime/` |
| 3 | **Solve Agent** | ✅ 完整 | `agents/solve_agent.py` (复用 qgen/solver.py) |
| 4 | **Tag Agent** | ✅ 完整 | `agents/tag_agent.py` (复用 qgen/annotator.py) |
| 5 | **DocAssembly Agent** | ✅ Lite | `agents/doc_agent.py` (markdown 输出) |
| 6 | Recommend Agent | 🔶 Agent 骨架 / ✅ 算法引擎 | `stub_agents.py::RecommendAgent` / `agents/recommend/engine.py` |
| 7 | FormatAnalyzer Agent | ✅ Pipeline 内置 / 🔶 Agent 骨架 | `pipeline/recognizers.py` + `format_adapters.py` |
| 8 | ContentExtractor Agent | ✅ Pipeline 内置 / 🔶 Agent 骨架 | `pipeline/stages/stage1_extract.py` |
| 9 | OcrCorrector Agent | ✅ Pipeline 内置 / 🔶 Agent 骨架 | `pipeline/text_tools/ocr_repair.py` + Stage1 review |
| 10 | ContentTagger Agent | ✅ Pipeline 内置 / 🔶 Agent 骨架 | Stage2 annotate/review + Stage3 canonicalizer |

**CLI 入口**（`cli/generate.py` 已接入题目专用 Harness）:
- `generate free` → QGenAgent 状态图（retrieve → generate → prefilter → solve/deep-solve →
  independent verify → annotate；失败可 diagnose/evolve，候选池可做多目标选择）
- `generate solve` → SolveAgent（内部复用 qgen/solver.py）
- `generate batch` → 批量循环 QGenAgent.run
- `generate lecture` → DocAssemblyAgent lite（按 tag 组装 markdown 讲义）
- `generate variant` → 基于源题的 A1–A6/B1–B2 变式策略
- `generate evolve` → 有界失败诊断、定向改进、lineage 与 population objective 选择
- `generate solve-deep` → 多轮 tool calling；`execute_python` 必须经过 verification sandbox
- `generate durable / resume / cancel` → DurableQGenHarness 可恢复长任务（`--run-id` 稳定 ID、
  `--runtime-db` 默认 `data/qgen_runtime.db`；resume 时可临时调整预算）

`agents/runtime/SQLiteRunStore` 与 `DurableQGenHarness` 提供独立 runtime DB（默认
`data/qgen_runtime.db`）、run/step 状态机、step/LLM call/token/wall-time 预算、不可变 checkpoint、
单调事件、取消及 node-boundary 恢复。CLI 经 `generate durable / resume / cancel` 直接使用持久
Harness；`generate free` 等其余子命令走一次性 `QGenAgent.run()`。API 对话式生成
（`/v1/conversations*` + `/v1/runs/{id}`）同样由 runtime store 支撑 SSE 事件流与暂停/恢复。
`DurableQGenHarness` 将每个图节点映射到原子 step；恢复从 `next_node` 继续，不重放已完成节点。

**QGen Agent 专用 Harness** (`src/sciforge/agents/qgen/`):
1. **ContextRetriever** (`retriever.py`) — 从 `SQLiteStorageBackend` 按 discipline +
   subject + difficulty_range 检索知识点和参考题，含难度锚点 + 变式负样本
2. **QuestionGenerator** (`generator.py`) — 加载按学科参数化的 prompt
   (`generation/free_generation.txt` 或 `variant_generation.txt`)，温度递增调 LLM 生成 N 个候选
3. **SolveEngine** (`solver.py`) — 对候选独立求解，带 confidence 标注
4. **VerificationPipeline** (`verifier.py`) — 独立 reviewer 同时读取完整题面与完整解答；
   结构/相关性检查外，可按 policy 运行数值代码和 Lean，证据严格区分
   `passed / failed / unavailable / error`；required 检查不可用时禁止发布
5. **DifficultyAnnotator** (`annotator.py`) — 10 维评分 + 加权聚合

状态图还包含 deterministic prefilter、失败诊断、定向 evolve 回边和可选 population selection。

验证证据使用 `passed / failed / unavailable / error` 四态。LLM/用户生成代码默认
`container_required`：Docker/Podman 无网络、只读、drop capabilities、no-new-privileges、
非 root，并限制 CPU/内存/pids/tmpfs/output；trusted-local 只允许仓库内人工审核代码。
启用 required 数值或 Lean 验证时，unavailable/error 也会阻止通过。Lean 未安装、容器不可用或
超时必须形成显式证据，不能伪装成验证通过。

产品输出只包含结构化解题步骤、方法摘要、验证证据和失败修正记录；不得记录或返回模型的
私有原始 chain-of-thought（不得当成产品字段、日志或 API 响应）。完整协议见
[`Docs/22_QGen_Harness_架构与验证协议.md`](Docs/22_QGen_Harness_架构与验证协议.md)。

**关键 prompt**（按学科参数化，`get_generation_prompt_template(path, discipline)` 用
`_GENERATION_PROMPT_SUBSTITUTIONS` 词语替换表按学科做 str.replace）:
- `prompts/templates/generation/free_generation.txt` / `variant_generation.txt`
- `prompts/templates/solving/solve.txt` / `solve_deep_*.txt`
- `prompts/templates/verification/review.txt` / `difficulty_scoring.txt`

**QGen / sandbox 关键配置**：
- `generation_max_rounds / generation_candidates_per_round`；
- `evolution_enabled / evolution_max_improvement_rounds / qgen_population_selection_enabled`；
- `qgen_deep_solve_enabled / solver_max_turns`；
- `enable_numerical_verification / require_numerical_verification`；
- `enable_lean_verification / require_lean_verification`；
- `sandbox_trust_policy / sandbox_container_runtime / sandbox_*` 资源与镜像设置；
- `qgen_runtime_db_path / qgen_run_max_steps / qgen_run_max_llm_calls /
  qgen_run_max_tokens / qgen_run_max_wall_time_seconds`。

## Web SaaS 层（P2/P4，`sciforge serve`）

- **组装方式**：`cli/serve.py` 构造 QGen/Solve 工厂 + 长生命周期检索 storage（WAL 并发读）
  + `SaasStore`，注入 `api/app.py:create_app(qgen_factory, solve_factory, saas_store=...,
  storage_factory=..., jwt_secret=..., cors_origins=..., serve_console=...)`。agent 经工厂注入，
  便于测试替换；`saas_store=None`（CLI `--open`）= 无鉴权开放模式
- **端点**（`/v1/*`）：health / auth/register / auth/login / generate / solve / usage /
  behavior / recommend / generate/batch（异步任务）/ tasks/{id} / papers（组卷）/
  papers/export（markdown/DOCX，走 `export/`）/ conversations*（Chatbox 会话/消息）/
  runs/{id}（+ `/events` SSE、`/cancel`、`/resume`）/ templates / knowledge/search；
  `/` 重定向内置控制台 `/app/`，`/docs` OpenAPI
- **双通道鉴权**：B2B 用 `X-API-Key`（SaasStore 校验 sha256），C 端用 `Bearer <token>`
  （`api/security.py` HMAC-SHA256 无状态 token，标准库实现无 PyJWT，payload 含 exp/user_id/tenant_id）。
  principal 先解析 scope，再执行业务；重端点按 UTC 月份原子预留配额（429），quota=0 表示无限；
  usage 在耗尽后仍可读取。批量请求一次预留整批额度，避免并发逐条超卖
- **数据分离**：四库各司其职——题库 `data/sciforge.db`（内容）、Chatbox 会话 `data/conversations.db`
  （产品侧消息/工件，tenant-scoped；执行状态权威仍是 SQLiteRunStore）、QGen runtime
  `data/qgen_runtime.db`（checkpoint/event）、SaaS 元数据 `data/saas.db`（租户/Key/用户/用量/任务）；
  私有化部署时题库可独立交付
- **多租户题库**：认证请求在 API 边界绑定 `TenantScopedStorageView`，题目只允许
  `tenant_id=current` 或 public (`tenant_id=''`)；SQL 在 `ORDER BY/LIMIT` 前过滤，foreign id
  与不存在 id 等价。papers/export、recommend、QGen、batch、Solve 均使用 scoped view；
  knowledge points 仍是共享公共 corpus。离线 pipeline 的 unscoped backend 不直接暴露给认证请求
- **推荐**：`agents/recommend/engine.py` 纯算法（无 LLM），行为流水聚合 UserProfile →
  最近发展区（掌握难度 + 步长）+ 薄弱标签命中打分；冷启动退化为默认难度
- 后续 P3（多端 + 网关 + 统一身份）见 [`Docs/17_前端产品化实施计划.md`](Docs/17_前端产品化实施计划.md)
- 知识库的条目、concept、质量门禁与存储契约见
  [`Docs/21_知识库构建标准与数据契约.md`](Docs/21_知识库构建标准与数据契约.md)

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

## Agent 间通信

- Agent 之间通过 Pydantic 模型传递结构化数据（`core/models.py` 是唯一定义点）
- Pipeline 5 阶段通过 4 个 `asyncio.Queue` 传递 Pydantic payload（`ExtractedBatch` / `ReviewedItem` / `AnnotatedItem` / `ReviewedAnnotatedItem`），通过共享 `SENTINEL` 单例（`core/sentinel.py`）通知流结束
- 不使用全局状态，所有上下文通过函数参数显式传递
- LLM 调用的并发由 `pipeline_queue_size`（Task0→Stage1 背压）+ `pipeline_stage_queue_size`（各 stage 间）+ `max_concurrent_llm_calls` 控制

## 新增 Agent 检查清单

1. 在 Agent 文件顶部定义 Input/Output Pydantic 模型（不必进 `core/models.py`）
2. 继承 `BaseAgent[TIn, TOut]`，设置类属性 `name`（必填）和 `prompt_file`（可选）
3. 在 `prompts/templates/<group>/` 下创建对应的 `.txt` 提示词模板
4. 实现 `async def run(self, input: TIn) -> TOut`，使用 `self._call_llm_json()` 带重试调用 LLM
5. 在 `src/sciforge/agents/__init__.py` 导出新 Agent + 其 Input/Output
6. 若需新异常：在 `core/exceptions.py` 继承 `SciForgeError`
7. 编写单元测试（mock LLM 响应），参考 `tests/test_agents/test_qgen_agent.py`
8. 更新本文件（AGENTS.md）+ [`Docs/16_Agent系统MVP.md`](Docs/16_Agent系统MVP.md) 的 Agent 状态表

参考示例：`src/sciforge/agents/solve_agent.py` (最简单的完整 Agent)
和 `src/sciforge/agents/doc_agent.py` (含存储访问的 Agent)。

## 代码规范

- 提交消息遵循 Conventional Commits：`<type>(<scope>): <subject>`
- 分支策略：`main`(稳定) → `dev`(集成) → `feat/<name>`(功能)；在 `dev` 或功能分支上工作
- 所有新代码必须通过 ruff 和 mypy 检查后再提交
- 异步函数使用 async/await，LLM 调用均为异步且必须通过 `llm/client.py` 的统一接口
- Pydantic 模型定义在 `core/models.py`，不要分散到其他模块（Agent 私有 Input/Output 除外）
- 配置通过 pydantic-settings 管理，启动时逐层加载：dotenv → `config/sciforge.toml`
  （`[providers.*]`/`[tasks.*]`/`[pipeline]`/`[logging]`，`--llm-config` 可换路径，
  api_key 支持 `${ENV_VAR}` 占位）→ `SCIFORGE_*` 环境变量覆盖
- 日志使用 structlog，不要使用 print 或 logging 模块
- 优先使用枚举判别而非子类继承：KnowledgePoint(kp_type=KPType.LEMMA)，不要创建 LemmaItem 子类
- StorageBackend 通过 Protocol 抽象，新增后端只需实现统一接口
- Tag 格式约束：每段 `english:中文`（`[a-z][a-z0-9_]*:中文`），一个条目的 tags 是有序段列表
  代表一条根→叶路径，不要拼成 `/` 字符串
- prompt 必须存为 `.txt` 文件（不要内联到 Python 字符串）

## 重要约定

- **Agent 权限边界**：可修改 `src/`、`tests/`、`config/`、`Docs/` 与 `pyproject.toml` 的依赖/工具配置；
  不要修改 `Raw Data/` 下的原始数据；不得在代码中硬编码 API Key 或敏感信息
- `Raw Data/` 和 `data/` 不纳入版本控制，包含原始教材和运行时生成数据
- `.env` 包含 API Key，绝不提交
- LLM 后端通过 `llm/backends/` 下的适配器实现，新增后端只需实现统一接口
- Pipeline 统一入口为 `sciforge parse run`，默认 VLM 多模态识别 PDF/DOCX
- 存储默认 SQLite，四库分离：题库 `--db data/sciforge.db`、Chatbox 会话 `data/conversations.db`、
  QGen runtime `data/qgen_runtime.db`、SaaS 元数据 `data/saas.db`
- Pipeline checkpoint 在 `data/checkpoint/<file_hash>/`，含 `state.json` 与 `recognize/file_NNN_<stem>/page_NNN-NNN.md`
- **云同步冲突副本**：仓库位于 iCloud 目录，会产生 `xxx 2.py` / `xxx 2/` 冲突副本
  （如 `prompts/templates/pipeline 2/`）。pytest 已通过 `addopts --ignore-glob` 忽略、
  .gitignore 已排除——遇到时不要引用或"修复"它们，可安全删除

## 当前工作重点（2026-07 全仓重审与现代化计划）

- `audit/`（诊断报告）与 `plan/`（实施处方）是 2026-07 的全仓证据化重审产物（基线 dev@ba29f3b，
  **未纳入版本控制**）：入口 `audit/REPORT-INDEX.md` 与 `plan/PLAN-INDEX.md`；实施顺序与验收口径以
  `plan/plan-05-modernization-program.md` 为权威总纲，增量台账见 `audit/RE-AUDIT-REGISTER.md`
- 3 个 Critical 待关闭：**DP-01**（run/cache/sink 身份未分离，完成 checkpoint 可对全新目标库假完成）、
  **PA-01**（answer_linker 跨章误绑并不可逆删题）、**PC-03**（空 batch 的位置型 block_index 使 resume
  静默跳过成段内容）；**QG-01/QG-02**（choice/fill-blank 结构字段链路丢失 / Lean 证据未绑定当前题解）
  在"已验证"发布路径上为发布阻断
- 执行原则：CI 门禁最先落地（当前无 CI 是最大隐性风险放大器）；修 bug 必附回归测试；验证类改动须在
  真实容器环境用"故意错解"夹具确认；阶段完成以不变量测试 + fresh-install 真实入口为准，
  不以现有 pytest 绿为唯一标准
