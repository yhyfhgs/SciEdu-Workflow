# AGENTS.md

本文件定义 SciForge 项目中 AI Agent 的协作规范。

## Agent 角色定义

### 1. Code Agent (Claude Code)

**职责**: 代码开发、重构、调试、测试编写

**工作流程**:
1. 阅读相关代码和文档（先读 `CLAUDE.md` 再读 `Docs/01_数据处理Pipeline.md`）
2. 在 `dev` 分支或功能分支上工作
3. 遵循 Conventional Commits 提交规范
4. 确保 ruff + mypy 检查通过后再提交

**权限边界**:
- 可以修改 `src/`, `tests/`, `config/`, `Docs/` 下的代码与文档
- 可以更新 `pyproject.toml` 的依赖和工具配置
- 不要修改 `Raw Data/` 下的原始数据
- 不要在代码中硬编码 API Key 或敏感信息

### 2. Pipeline Agent (LLM-in-the-loop)

**职责**: 数据处理流水线中的 LLM 调用环节

**架构**: 5 阶段并发 Producer-Consumer，由 `PipelineOrchestrator`（`src/sciforge/pipeline/orchestrator.py`）用 `asyncio.gather` 并发启动 6 个 Task + 4 个 stage queue：

- **Task0 (Producer)**：`Recognizer` 协议的具体实现（`VlmRecognizer` / `PyMuPdfRecognizer` / `MarkdownRecognizer`）按 5 页一批识别为 markdown，推入 `q_md`。落盘 `data/checkpoint/<file_hash>/recognize/file_NNN_<stem>/page_NNN-NNN.md`
- **Stage1Extract** (`stages/stage1_extract.py`)：从 `q_md` 取 mdblock → 500 行滑动窗口 → LLM 每窗口批量提取所有完整条目 → `OrderIndexAllocator` 预留连续 `item_id` → `ExtractedBatch` 推入 `q_raw`
- **Stage1Review** (`stages/stage1_review.py`)：原 window + 批量 items → LLM 批量校验修正（专门修复 exercise/example/problem 过早标 no_solution_in_source 的 bug）→ 拆成 `ReviewedItem` 推入 `q_rev`
- **Stage2Annotate** (`stages/stage2_annotate.py`)：单条 item + tag_tree + taxonomy → LLM 打 tags/difficulty/subject/taxonomy_path → `q_ann`
- **Stage2Review** (`stages/stage2_review.py`)：单条 annotation 审核（tag 格式/复用、taxonomy 存在、difficulty 校准）→ `q_done`
- **Stage3Persist** (`stages/stage3_persist.py`)：合并 item + annotation → `KnowledgePoint` / `Question` → `storage.save_batch` + `tag_tree.insert_path`（无 LLM，按 item_id 严格递增入库）。入库前经 `TagCanonicalizer` 做 tag 受控词表统一（#6）

**入库后确定性 post-pass**（`orchestrator` 块 10.5–10.8，无 LLM、可独立开关）：
- `numbering_audit` → `numbering_report.json`（#2 编号连续性兜底）
- `answer_linker`：章末/附录答案并回题干 `solution_content`（#3）
- `relation_inference`：`corollary_of`/`tests`/`successor` 边 → `item_relations` 表（#6 知识图谱）
- `reconciliation` → `reconciliation_report.json`：复核重复条目 + 类型误判（#7，不改库）

**Task0 鲁棒性**：VLM 整批被内容审核误判（如 MiniMax 1026 sensitive）时自动回退**逐页识别**（`_recognize_pages_individually`），只丢被误判页不丢整批。

**单文档内并行提取**（#1，opt-in，`intra_doc_parallel_extract`）：`document_segmenter` 按 section 分段（覆盖不变量断言）+ `parallel_extract.ParallelSegmentExtractor` 段级并发 + `assign_order_index` 按「段序+段内序」保序重排。

**Prompt caching / 思考预算（#9）**：各 stage 静态指令拆到可缓存 system 前缀（`templates.split_static_dynamic`）；推理模型（MiniMax-M3 等）按 stage 设 `thinking` budget 控制思考长度，经 anthropic 兼容网关时走 `extra_body` 透传。

**通用格式与确定性减负（schema v14）**：
- `pipeline/format_adapters.py` 对 CSV/TSV、JSON/JSONL、HTML、XLSX、EPUB、TXT/RST 做
  有界解析并产出带 source locator 的 Markdown block；这些格式在 Task0 默认 0 次 LLM。
- XLSX/EPUB 等 ZIP 容器先检查文件体积、entry 数、总解压体积、压缩比和路径穿越；
  结构化输入受列数、JSON 深度、block 行数和输入字节数限制。
- `pipeline/extraction_harness.py` / boundary patch / marker ledger 用确定性证据覆盖条目标记，
  只有高风险缺口才进入 LLM review；不得为了省调用而静默丢弃 marker。
- `pipeline/concept_linker.py` 构建受 taxonomy 约束的 concept/alias/item/concept-relation 四表；
  `pipeline/evaluation.py` 以 `pipeline_auto` 量规写 `item_evaluations` 和 `review_queue`，不调用 LLM。
- 知识库验收契约见 [`Docs/21_知识库构建标准与数据契约.md`](Docs/21_知识库构建标准与数据契约.md)。

入口命令：`uv run sciforge parse run <path>`；入库后只读体检：`uv run sciforge parse validate <db>`。

**Pipeline 主线 LLM 任务**（可分别配置 provider / model）：

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

**可靠性机制**:
- **断点恢复**：`FileExtractionState` 持久化到 `state.json`，记录 `vlm_batches_done` / `current_block_idx` / `line_offset` / `line_buffer_carry` / `next_item_id` / `current_window_size` / `stageN_count` / `last_persisted_item_id` / `tag_tree_json`。重启可无缝恢复；旧 v1 schema 不兼容 → 检测到时 raise `StateSchemaMismatch`，要求 `--restart` 清库
- **Retry with feedback**：`pipeline/retry.py` 的 `call_llm_json_with_schema_retry_with_feedback` 在每次重试时把上次 Pydantic 错误文本注入下次 prompt，LLM 有机会修正；所有 4 个 stage 的 LLM 调用都走这条路径
- **Exercise 漏 solution 三道防线**：prompt 显式规则 + Pydantic `window_last_line` context validator + Stage1Review 专项复核
- **异常隔离**：任一 stage 异常 → try/finally 向下游 put SENTINEL 避免死锁；Stage1 未解决的非空 span 写 `uncertain` 占位而不静默丢弃，Stage2 失败写保守空标注，不阻断整体
- **背压**：`q_md` (maxsize=pipeline_queue_size) + 4 个 stage queue (maxsize=pipeline_stage_queue_size)
- **严格保序**：item_id 全局递增 + FIFO queue；Stage2 可选 N-worker 并发时通过 `ReorderBuffer` 恢复顺序，Stage3 禁止 item_id 倒退并允许上游拒绝造成 gap
- **Tag 一致性**：`pipeline/tag_tree.py` 在 Stage3 原子累积，Stage2 prompt 注入 tag_subtree 避免同义异名（`group:群` vs `groups:群论`）

**约束**:
- 所有 LLM 调用通过 `llm/client.py` 的统一接口
- 输出必须符合 `core/models.py` 中定义的 Pydantic 模型
- Tag 段必须 `english:中文` 格式（`[a-z][a-z0-9_]*:中文`）
- 一个条目的 `tags` 是有序段列表（`["math:数学","algebra:代数",...]`），不要拼成 `/` 字符串
- prompt **必须**存为 `.txt` 文件

### 3. Generation Agents (多 Agent 生成系统)

**架构**: `BaseAgent[TIn, TOut]` 抽象基类 + 极简 `StateGraph`（不依赖 LangGraph）
构成的多 Agent 协作系统。所有 Agent 按 `Discipline` 参数化，可单独运行也可通过
状态图编排。详见 [`Docs/16_Agent系统MVP.md`](Docs/16_Agent系统MVP.md)。

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
`DurableQGenHarness` 将每个图节点映射到 `SQLiteRunStore` 的原子 step、不可变 checkpoint、
单调 event 和 LLM call/token/wall-time/step 预算；恢复从 `next_node` 继续，不重放已完成节点。
`verification_sandbox.py` 默认对 LLM/用户代码要求无网络、只读、drop capabilities 的
Docker/Podman 容器；trusted-local 只用于仓库内人工审核代码。Lean 未安装、容器不可用或超时
必须形成显式证据，不能伪装成验证通过。完整协议见
[`Docs/22_QGen_Harness_架构与验证协议.md`](Docs/22_QGen_Harness_架构与验证协议.md)。

对外只输出可审计的结构化解题步骤、方法摘要、验证证据与修正记录；不得把模型私有原始
chain-of-thought 当成产品字段、日志或 API 响应。

**CLI 入口**:
- `sciforge generate free` — QGenAgent 默认低延迟路径（含预筛、独立求解/审核与难度标注）
- `sciforge generate solve <question_id>` — SolveAgent
- `sciforge generate batch` — 批量循环 QGenAgent
- `sciforge generate lecture` — DocAssemblyAgent lite（生成 markdown 讲义）
- `sciforge generate variant` — A1–A6/B1–B2 变式生成
- `sciforge generate evolve` — 有界失败诊断、定向改进与候选池选择
- `sciforge generate solve-deep` — 多轮求解 + policy-controlled sandbox 工具验证

**关键 prompt** (按学科参数化):
- `prompts/templates/generation/free_generation.txt` / `variant_generation.txt`
- `prompts/templates/solving/solve.txt` / `solve_deep_*.txt`
- `prompts/templates/verification/review.txt` / `difficulty_scoring.txt`

参数化由 `prompts/templates.py` 的 `get_generation_prompt_template(path, discipline)`
完成，使用 `_GENERATION_PROMPT_SUBSTITUTIONS` 词语替换表按学科做 str.replace。

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
8. 更新本文件 + `CLAUDE.md` + `Docs/16_Agent系统MVP.md` 的 Agent 状态表

参考示例：`src/sciforge/agents/solve_agent.py` (最简单的完整 Agent)
和 `src/sciforge/agents/doc_agent.py` (含存储访问的 Agent)。
