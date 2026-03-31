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

**职责**: 数据处理流水线中的 LLM 调用环节

**包含的 Agent 角色**:
- `format_analyzer` — 分析输入文档的格式和结构
- `content_extractor` — 从文档中提取题目、知识点等结构化内容
- `ocr_corrector` — 修正 OCR 产生的数学公式错误
- `content_tagger` — 为内容打标签（学科、难度、知识点）

**约束**:
- 所有 Agent 通过 `llm/client.py` 的统一接口调用 LLM
- 输出必须符合 `core/models.py` 中定义的 Pydantic 模型
- 失败时应抛出 `core/exceptions.py` 中定义的异常，由调用方处理重试

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
