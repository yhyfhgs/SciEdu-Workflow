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

**架构**: Producer-Consumer 单管道，由 `PipelineOrchestrator`（`src/sciforge/pipeline/orchestrator.py`）编排两个并发 Task：

- **Task0 (Producer)**：`Recognizer` 协议的具体实现（`VlmRecognizer` / `PyMuPdfRecognizer` / `MarkdownRecognizer`）按 5 页一批识别为 markdown，每个 batch 立即推入 `asyncio.Queue`。落盘 `data/checkpoint/<file_hash>/recognize/file_NNN_<stem>/page_NNN-NNN.md`。
- **Task1 (Consumer)**：`Task1Extractor`（`pipeline/task1_extractor.py`）按 FIFO 取出 `MdBlockSource`，给每行注入全局行号，按 200 行滑动窗口送 LLM 完成"提取 + 校正 + 分类 + 打标签 + 难度评估"五合一调用，结果直接入库。

入口命令：`uv run sciforge parse run <path>`。

**Pipeline 主线只用两个 LLM 任务**:

| 任务名 | 用途 | 必备能力 |
|---|---|---|
| `vlm_convert` | Phase A: 多模态视觉识别 PDF/DOCX → markdown | 支持图像输入 |
| `extract_and_classify` | Task1: 一次调用完成 提取/校正/分类/打标签/难度 | JSON 结构化输出 + 较大 max_tokens（≥32k） |

**关键 prompt**:
- `src/sciforge/prompts/templates/pipeline/extract_and_classify.txt` (Task1 主 prompt)
- `src/sciforge/prompts/templates/pipeline/vlm_convert.txt` (Phase A VLM prompt)

**可靠性机制**:
- **行级断点恢复**：`FileExtractionState` 持久化到 `state.json`，记录 `vlm_batches_done` / `current_block_idx` / `line_offset` / `line_buffer_carry` / `tag_tree_json` / `order_index_next`。重启可无缝恢复。
- **统一重试**：`pipeline/retry.py` 提供 `with_retry` + `call_llm_json_with_retry`，所有 LLM/VLM 调用都包含重试 + 指数退避。
- **异常隔离**：单 batch / 单窗口失败被记入 `failed_batches` / `failed_blocks`，不阻断整体流程。
- **背压**：`asyncio.Queue(maxsize=4)` 自动平衡 Producer 与 Consumer 速度。
- **Tag 一致性**：`pipeline/tag_tree.py` 维护跨调用的层级 tag 树，每次 LLM 调用前序列化为 prompt context 注入，避免同义异名（`group:群` vs `groups:群论`）。

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
| 2 | **QGen Agent** | ✅ 完整 | `agents/qgen/agent.py` + 5 子组件 |
| 3 | **Solve Agent** | ✅ 完整 | `agents/solve_agent.py` (复用 qgen/solver.py) |
| 4 | **Tag Agent** | ✅ 完整 | `agents/tag_agent.py` (复用 qgen/annotator.py) |
| 5 | **DocAssembly Agent** | ✅ Lite | `agents/doc_agent.py` (markdown 输出) |
| 6 | Recommend Agent | 🔶 骨架 | `agents/stub_agents.py::RecommendAgent` |
| 7 | FormatAnalyzer Agent | 🔶 骨架 | `agents/stub_agents.py::FormatAnalyzerAgent` |
| 8 | ContentExtractor Agent | 🔶 骨架 | `agents/stub_agents.py::ContentExtractorAgent` |
| 9 | OcrCorrector Agent | 🔶 骨架 | `agents/stub_agents.py::OcrCorrectorAgent` |
| 10 | ContentTagger Agent | 🔶 骨架 | `agents/stub_agents.py::ContentTaggerAgent` |

**QGen Agent 5 阶段闭环** (`src/sciforge/agents/qgen/`):
1. **ContextRetriever** (`retriever.py`) — 从 `SQLiteStorageBackend` 按 discipline +
   subject + difficulty_range 检索知识点和参考题，含难度锚点 + 变式负样本
2. **QuestionGenerator** (`generator.py`) — 加载按学科参数化的 prompt
   (`generation/free_generation.txt` 或 `variant_generation.txt`)，温度递增调 LLM 生成 N 个候选
3. **SolveEngine** (`solver.py`) — 对候选独立求解，带 confidence 标注
4. **VerificationPipeline** (`verifier.py`) — 独立 review LLM 避免确认偏差，3 个检查
   (solve_confidence / review_passed / non_trivial)；失败时把建议写回 context 用于下一轮
5. **DifficultyAnnotator** (`annotator.py`) — 10 维评分 + 加权聚合

**CLI 入口**:
- `sciforge generate free` — QGenAgent 完整 5 阶段闭环
- `sciforge generate solve <question_id>` — SolveAgent
- `sciforge generate batch` — 批量循环 QGenAgent
- `sciforge generate lecture` — DocAssemblyAgent lite（生成 markdown 讲义）
- `sciforge generate variant/evolve/solve-deep` — Post-MVP 占位

**关键 prompt** (按学科参数化):
- `prompts/templates/generation/free_generation.txt` / `variant_generation.txt`
- `prompts/templates/solving/solve.txt` / `solve_deep_*.txt`
- `prompts/templates/verification/review.txt` / `difficulty_scoring.txt`

参数化由 `prompts/templates.py` 的 `get_generation_prompt_template(path, discipline)`
完成，使用 `_GENERATION_PROMPT_SUBSTITUTIONS` 词语替换表按学科做 str.replace。

## Agent 间通信

- Agent 之间通过 Pydantic 模型传递结构化数据（`core/models.py` 是唯一定义点）
- Pipeline 内部 Producer-Consumer 通过 `asyncio.Queue[MdBlockSource | _Sentinel]` 通信
- 不使用全局状态，所有上下文通过函数参数显式传递
- LLM 调用的并发由 `pipeline_queue_size`（背压）+ `max_concurrent_llm_calls` 控制

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
