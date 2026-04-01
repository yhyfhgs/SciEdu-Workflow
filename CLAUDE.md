# CLAUDE.md

本文件为 Claude Code 提供项目上下文，使 AI 辅助开发更高效。

## 项目概述

MathForge 是 AI 驱动的大学数学教育平台，当前阶段为 CLI MVP。核心功能：数据处理 Pipeline + 题目生成引擎。

## 技术栈

- **语言**: Python 3.11+
- **包管理**: uv（不使用 pip）
- **数据模型**: Pydantic v2（严格模式）
- **CLI**: Typer + Rich
- **日志**: structlog
- **LLM**: Anthropic / OpenAI / Gemini，通过统一 LLMClient 接口调用
- **存储**: JSON 文件存储（MVP），预留 SQLite 迁移路径
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
uv run mathforge --help
```

## 项目结构

```
src/mathforge/
├── core/        # 核心领域：models.py(Pydantic模型), engine.py(生成编排),
│                #   generator.py, solver.py, verifier.py, annotator.py, retriever.py
├── llm/         # LLM统一客户端：client.py, config.py, parser.py, backends/
├── pipeline/    # 统一数据处理流水线：orchestrator(编排), block_splitter(LLM拆分),
│                #   meta_extractor(条目提取), agents/item_processor(校准+分类),
│                #   item_unifier, entity_builder, relation_builder,
│                #   pdf/docx/csv_processor(格式转换), ocr_repair, content_validator
├── prompts/     # 提示词模板：templates.py, strategies.py
├── storage/     # 存储：file_backend.py(JSON), index.py
├── cli/         # CLI命令：main.py, parse.py, generate.py, display.py
└── config/      # 配置：settings.py(pydantic-settings), logging.py(structlog)
```

## 代码规范

- 提交消息遵循 Conventional Commits：`<type>(<scope>): <subject>`
- 分支策略：`main`(稳定) → `dev`(集成) → `feat/<name>`(功能)
- 所有新代码必须通过 ruff 和 mypy 检查
- 异步函数使用 async/await，LLM 调用均为异步
- Pydantic 模型定义在 `core/models.py`，不要分散到其他模块
- 配置通过 pydantic-settings 管理，环境变量前缀 `MATHFORGE_`
- 日志使用 structlog，不要使用 print 或 logging 模块

## 重要约定

- `Raw Data/` 和 `data/` 不纳入版本控制，包含原始教材和运行时生成数据
- `.env` 包含 API Key，绝不提交
- LLM 后端通过 `llm/backends/` 下的适配器实现，新增后端只需实现统一接口
- Pipeline 统一入口为 `mathforge parse run`，所有格式通过 LLM 推断 FormatSpec 后确定性提取
