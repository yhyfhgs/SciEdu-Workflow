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

### 3. Generation Agent (生成引擎)

**职责**: 题目生成的 5 阶段闭环（暂不在 MVP 主线，整体未对接）

**阶段设计**（未来重建目标）:
1. ContextRetriever — 从知识库检索相关上下文
2. QuestionGenerator — 基于上下文和策略生成题目
3. SolveEngine — 为生成的题目求解
4. VerificationPipeline — 验证题目和解答的正确性
5. DifficultyAnnotator — 标注难度等级

**当前状态**: 由于 commit 7f2abb9 重构了 `KnowledgePoint` / `Question` 字段
（移除 `content_latex`/`difficulty_ai`/`title` 等），加上 `FileStorageBackend` /
`EnhancedIndexManager` 被 SQLite 替代，整个生成路径的代码在 2026-04 清理周期
中已被删除：`core/engine.py / generator.py / retriever.py / solver.py /
verifier.py / evolution_engine.py / pipeline_orchestrator.py / sandbox.py`。
`cli/generate.py` 的所有子命令保留了 Typer 签名但函数体统一为
`raise NotImplementedError`，等 parse 主线稳定后按新 models + `SQLiteStorageBackend`
重建。所需的 .txt prompt 模板仍保留在 `src/sciforge/prompts/templates/` 下。

## Agent 间通信

- Agent 之间通过 Pydantic 模型传递结构化数据（`core/models.py` 是唯一定义点）
- Pipeline 内部 Producer-Consumer 通过 `asyncio.Queue[MdBlockSource | _Sentinel]` 通信
- 不使用全局状态，所有上下文通过函数参数显式传递
- LLM 调用的并发由 `pipeline_queue_size`（背压）+ `max_concurrent_llm_calls` 控制

## 新增 Agent 检查清单

1. 在 `core/models.py` 定义输入/输出 Pydantic 模型
2. 在 `prompts/templates/<group>/` 下创建对应的 `.txt` 提示词模板
3. 在 `prompts/templates.py` 注册加载点
4. 实现 Agent 逻辑，通过 `LLMClient` 调用 LLM，并用 `call_llm_json_with_retry` 包装
5. 在 `core/exceptions.py` 添加特定异常类型（如需）
6. 编写单元测试（mock LLM 响应）
7. 更新本文件 + `CLAUDE.md` + `Docs/01_数据处理Pipeline.md` 的相关说明
