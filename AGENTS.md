# AGENTS.md

本文件定义 MathForge 项目中 AI Agent 的协作规范。

## Agent 角色定义

### 1. Code Agent (Claude Code)

**职责**: 代码开发、重构、调试、测试编写

**工作流程**:
1. 阅读相关代码和文档，理解上下文
2. 在 `dev` 分支或功能分支上工作
3. 遵循 Conventional Commits 提交规范
4. 确保 ruff + mypy 检查通过后再提交

**权限边界**:
- 可以修改 `src/`, `tests/`, `config/` 下的代码
- 可以更新 `pyproject.toml` 的依赖和工具配置
- 不要修改 `Raw Data/` 下的原始数据
- 不要在代码中硬编码 API Key 或敏感信息

### 2. Pipeline Agent (LLM-in-the-loop)

**职责**: 统一数据处理流水线中的 LLM 调用环节

**架构**: 通过 `PipelineOrchestrator` 编排的 asyncio.Queue 三并发 Task 流水线。
入口命令: `mathforge parse run`。支持 `--db` 参数切换 SQLite 存储。

**包含的 Agent 角色**:
- `format_spec_inferrer` (BlockSplitter) — LLM 推断 FormatSpec（内容块类型、正则模式、章节结构），支持缓存库复用和验证重试
- `item_processor` (agents/item_processor.py) — 单次 LLM 调用完成 OCR 校准 + 多维分类标注，内置 1 次重试
- `relation_builder` (relation_builder.py) — 批量 LLM 推断条目间语义关系 + 先决关系环路自动修复

**流水线阶段**:
1. Stage 0: 格式转换 — PDF/DOCX/CSV → 纯文本（Python，无 LLM）
2. Stage 1: FormatSpec 推断（缓存查找 → 自适应采样 → LLM → 验证重试）+ 确定性分块
3. Stage 2: MetaExtractor 多策略提取（spec regex → 启发式 → fallback）+ ContentMarkers 检测 + ItemProcessor 校准分类
4. Stage 3: ItemUnifier（枚举判别，扁平化 3 类）+ RelationBuilder → Storage

**可靠性机制**:
- 逐条错误隔离：单条目 LLM 失败不影响整批，fallback 为 quality="D" 结果
- LLM 重试：item_processor 内置 1 次自动重试
- 质量门控：QualityGate 可配置阈值拦截低质量条目
- 跨文档去重：DeduplicationEngine 精确 hash + Jaccard 近似检测
- FormatSpec 缓存：相同类型文档复用已推断的 FormatSpec

**约束**:
- 所有 Agent 通过 `llm/client.py` 的统一接口调用 LLM
- 输出必须符合 `core/models.py` 中定义的 Pydantic 模型（扁平化 3 类 UnifiedItem）
- FormatSpec 是 LLM 与 Python 之间的通用契约：LLM 负责识别，Python 负责执行
- 优先使用枚举判别（kp_type/question_type）而非子类继承

### 3. Generation Agent (核心引擎)

**职责**: 题目生成的 5 阶段闭环

**阶段**:
1. **ContextRetriever** — 从知识库检索相关上下文
2. **QuestionGenerator** — 基于上下文和策略生成题目
3. **SolveEngine** — 为生成的题目求解
4. **VerificationPipeline** — 验证题目和解答的正确性
5. **DifficultyAnnotator** — 标注难度等级

**约束**:
- 每个阶段独立实现，通过 `engine.py` 编排
- 生成策略定义在 `prompts/strategies.py`
- 验证失败时进入修正循环，最大轮次由 `MATHFORGE_GENERATION_MAX_ROUNDS` 控制

## Agent 间通信

- Agent 之间通过 Pydantic 模型传递结构化数据
- 不使用全局状态，所有上下文通过函数参数显式传递
- LLM 调用的并发数由 `MATHFORGE_MAX_CONCURRENT_LLM_CALLS` 控制

## 新增 Agent 检查清单

1. 在 `core/models.py` 定义输入/输出模型
2. 在 `prompts/` 下创建对应的提示词模板
3. 实现 Agent 逻辑，通过 `LLMClient` 调用 LLM
4. 在 `core/exceptions.py` 添加特定异常类型
5. 编写单元测试（mock LLM 响应）和集成测试
6. 更新本文件的 Agent 角色定义
