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
- **存储**: JSON 文件 + SQLite（FTS5 全文检索、关系表、去重），通过 StorageBackend Protocol 抽象
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

# Pipeline（SQLite 存储）
uv run mathforge parse run path/to/file --db data/mathforge.db

# 迁移 JSON → SQLite
uv run mathforge parse migrate-db data/ --target data/mathforge.db

# 数据库统计
uv run mathforge parse db-stats data/mathforge.db
```

## 项目结构

```
src/mathforge/
├── core/        # 核心领域：models.py(Pydantic模型, 扁平化3类UnifiedItem),
│                #   engine.py(生成编排), generator.py, solver.py, verifier.py,
│                #   annotator.py, retriever.py
├── llm/         # LLM统一客户端：client.py, config.py, parser.py, backends/
├── pipeline/    # 统一数据处理流水线：
│                #   orchestrator(编排, 3并发Task+Queue),
│                #   block_splitter(FormatSpec推断+缓存+验证+确定性分块),
│                #   sampling(自适应文档采样),
│                #   format_spec_library(FormatSpec跨文档缓存),
│                #   meta_extractor(多策略提取+内容类型识别),
│                #   agents/item_processor(OCR修复+LLM校准分类+重试),
│                #   item_unifier(枚举判别统一化+配对合并),
│                #   entity_builder(UnifiedItem→KP/Q, 向后兼容),
│                #   relation_builder(结构+LLM关系+环路修复),
│                #   quality_gate(可配置质量门控),
│                #   dedup(精确+近似去重),
│                #   metrics(全阶段指标收集),
│                #   stage(泛型Stage[In,Out]协议),
│                #   pdf/docx/csv_processor, ocr_repair, content_validator
├── prompts/     # 提示词模板：templates.py, strategies.py
├── storage/     # 存储：protocol.py(StorageBackend协议),
│                #   file_backend.py(JSON), sqlite_backend.py(SQLite+FTS5),
│                #   schema.sql, migrate.py, index.py
├── cli/         # CLI命令：main.py, parse.py, generate.py, display.py
└── config/      # 配置：settings.py(pydantic-settings), logging.py(structlog)
```

## Pipeline 架构

```
文件 → 格式检测 → 文本转换
       ↓
 [Task 1] BlockSplitter
   FormatSpec缓存查找 → LLM推断(自适应采样) → 验证+重试 → 确定性分块
       ↓ Queue A
 [Task 2] MetaExtractor(三策略链: spec→启发式→fallback) + ItemProcessor(LLM+重试)
   每条目附加 ContentMarkers(公式/表格/图片计数)
       ↓ Queue B
 [Task 3] 收集结果
       ↓
 ItemUnifier(枚举判别, 无子类) → RelationBuilder(环路自动修复) → Storage
```

**UnifiedItem 扁平化层级** (仅 3 个类):
- `KnowledgeItem` — 通过 `kp_type` 枚举区分: definition/theorem/lemma/corollary/...
- `QuestionItem` — 通过 `question_type` 枚举区分: proof/calculation/choice/fill_blank/...
- `ExampleItem` — 同时具有知识点和题目属性

## 代码规范

- 提交消息遵循 Conventional Commits：`<type>(<scope>): <subject>`
- 分支策略：`main`(稳定) → `dev`(集成) → `feat/<name>`(功能)
- 所有新代码必须通过 ruff 和 mypy 检查
- 异步函数使用 async/await，LLM 调用均为异步
- Pydantic 模型定义在 `core/models.py`，不要分散到其他模块
- 配置通过 pydantic-settings 管理，环境变量前缀 `MATHFORGE_`
- 日志使用 structlog，不要使用 print 或 logging 模块
- 优先使用枚举判别而非子类继承：KnowledgeItem(kp_type=KPType.LEMMA)，不要创建 LemmaItem 子类
- StorageBackend 通过 Protocol 抽象，新增后端只需实现统一接口

## 重要约定

- `Raw Data/` 和 `data/` 不纳入版本控制，包含原始教材和运行时生成数据
- `.env` 包含 API Key，绝不提交
- LLM 后端通过 `llm/backends/` 下的适配器实现，新增后端只需实现统一接口
- Pipeline 统一入口为 `mathforge parse run`，所有格式通过 LLM 推断 FormatSpec 后确定性提取
- 存储后端支持 `--db` 参数切换 SQLite，默认 JSON 文件
