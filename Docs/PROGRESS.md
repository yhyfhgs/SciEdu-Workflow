# SciForge 开发进度总览

> 更新日期: 2026-04-08 | 代码总量: ~13,350 LOC (Python+SQL) + ~1,455 行 prompt 模板

---

## 1. 总体进度

| 阶段 | 状态 | 说明 |
|------|------|------|
| Phase 0: 项目脚手架 | ✅ 完成 | pyproject.toml, uv, Pydantic 模型, structlog, CLI 骨架 |
| Phase 1: TeX 解析 | ✅ 完成 | 已合并入统一 Pipeline |
| Phase 2: MD 解析与知识库 | ✅ 完成 | 已合并入统一 Pipeline |
| Phase 3: LLM 客户端与生成引擎 | ✅ 完成 | 多 Provider 客户端 + 5 阶段闭环引擎 |
| Phase 4: 通用 Pipeline 与集成 | ✅ 完成 | 统一 `parse run` 入口，支持 TeX/MD/PDF/DOCX/CSV |
| Phase 5: SQLite + FastAPI | 🔶 部分完成 | SQLite 后端 + FTS5 + 迁移已实现；FastAPI / Embedding 未开始 |
| Phase 6+: Post-MVP | 🔲 未开始 | Agent 编排、文档生成、Web 前端等 |

---

## 2. 模块实现明细

### 2.1 数据处理 Pipeline (`src/sciforge/pipeline/` — 5,600 LOC)

| 文件 | 行数 | 职责 | 状态 | 是否调用 LLM |
|------|------|------|------|-------------|
| `orchestrator.py` | 547 | 总编排：3 个异步 Task + Queue 流式传递 | ✅ | 间接 |
| `block_splitter.py` | 608 | Stage 1: FormatSpec 推断 + 文档拆分 | ✅ | ✅ 推断 FormatSpec |
| `meta_extractor.py` | 577 | Stage 2A: 三策略链提取 RawItem + ContentMarkers | ✅ | ❌ 纯正则 |
| `agents/item_processor.py` | 467 | Stage 2B: OCR 修复 + LLM 校准 + 分类标注 | ✅ | ✅ 校准+分类 |
| `item_unifier.py` | 259 | Stage 2C: RawItem → UnifiedItem 类型映射 | ✅ | ❌ 纯逻辑 |
| `entity_builder.py` | 220 | Stage 3: UnifiedItem → KnowledgePoint/Question | ✅ | ❌ 纯逻辑 |
| `relation_builder.py` | 359 | Stage 4: 结构关系 + LLM 语义关系 | ✅ | ✅ 语义关系 |
| `dedup.py` | 166 | 精确 (SHA-256) + 近似 (MinHash Jaccard) 去重 | ✅ | ❌ |
| `quality_gate.py` | 88 | 可配置质量门控 (coverage/grade/completeness) | ✅ | ❌ |
| `metrics.py` | 127 | 全阶段指标收集与持久化 | ✅ | ❌ |
| `sampling.py` | 208 | 自适应文档采样 (转换点检测) | ✅ | ❌ |
| `format_spec_library.py` | 172 | FormatSpec 跨文档缓存 (精确+模糊匹配) | ✅ | ❌ |
| `stage.py` | 74 | 泛型 Stage[In,Out] 协议 (重试+错误隔离) | ✅ | ❌ |
| `mineru_processor.py` | 207 | MinerU PDF/DOCX 处理集成 | ✅ | ❌ |
| `pdf_processor.py` | 294 | PDF → MD (pymupdf / pymupdf4llm) | ✅ | ❌ |
| `docx_processor.py` | 103 | DOCX → MD (pandoc / python-docx) | ✅ | ❌ |
| `csv_processor.py` | 162 | CSV → 结构化知识点 | ✅ | ❌ |
| `md_parser.py` | 246 | MD 块级解析（定义/定理/引理等模式识别） | ✅ | ❌ |
| `ocr_repair.py` | 268 | 确定性 OCR 错误修复（正则替换） | ✅ | ❌ |
| `content_validator.py` | 278 | 内容验证（括号匹配、语义警告、悬挂引用） | ✅ | ❌ |
| `cross_page_detector.py` | 164 | PDF 跨页内容检测与缓冲合并 | ✅ | ❌ |

### 2.2 核心领域 (`src/sciforge/core/` — 3,613 LOC)

| 文件 | 行数 | 职责 | 状态 |
|------|------|------|------|
| `models.py` | 1,100+ | 全部 Pydantic 模型（StrEnum + 数据类） | ✅ |
| `engine.py` | 280 | 生成引擎编排（5 阶段闭环） | ✅ |
| `generator.py` | 144 | 题目候选生成（free / variant） | ✅ |
| `solver.py` | 243 | 解答求解（单轮 + 多轮 tool calling） | ✅ |
| `verifier.py` | 323 | 6 项验证检查 Pipeline | ✅ |
| `annotator.py` | 73 | 难度评分（1-10） | ✅ |
| `retriever.py` | 240 | 上下文检索（知识点 + 参考题目） | ✅ |
| `evolution_engine.py` | 696 | 迭代进化引擎（evolve 命令） | ✅ |
| `pipeline_orchestrator.py` | 268 | 生成流程编排（core 侧辅助） | ✅ |
| `sandbox.py` | 162 | Python 代码沙箱（10s 超时） | ✅ |
| `exceptions.py` | 110 | 自定义异常体系 | ✅ |

### 2.3 LLM 客户端 (`src/sciforge/llm/` — 1,253 LOC)

| 文件 | 行数 | 职责 | 状态 |
|------|------|------|------|
| `client.py` | 460+ | 统一 LLMClient 接口（async + sync） | ✅ |
| `config.py` | 152 | 10 个任务预设（温度/max_tokens） | ✅ |
| `parser.py` | 55 | LLM 响应 JSON 提取 | ✅ |
| `tool_definitions.py` | 120 | Tool calling schema 定义 | ✅ |
| `backends/anthropic.py` | 134 | Anthropic Claude 适配器 | ✅ |
| `backends/openai_backend.py` | 154 | OpenAI GPT 适配器 | ✅ |
| `backends/gemini.py` | 65 | Google Gemini 适配器 | ⚠️ 基础实现 |
| `backends/compatible.py` | 21 | OpenAI 兼容 API（本地模型） | ⚠️ 最小 stub |

### 2.4 Prompt 模板 (`src/sciforge/prompts/` — 140 LOC py + 1,455 行 txt)

| 分类 | 文件数 | 状态 | 说明 |
|------|--------|------|------|
| Pipeline 专用 | 4 txt | ✅ | format_spec_infer, block_split, item_correct_and_tag, relation_inference |
| 题目生成 | 2 txt | ✅ | free_generation, variant_generation |
| 解答求解 | 3 txt | ✅ | solve, solve_deep_system, solve_deep_user |
| 验证 | 4 txt | ✅ | review, difficulty_scoring, solution_relevance, generate_numerical_test |
| 变式策略 | 8 txt | ✅ | A1-A6 + B1-B2 |
| 进化迭代 | 4 txt | ✅ | improve_from_solver/verification, evolution_system, iteration_summary |
| 分类标注 | 1 txt | ✅ | tag_classification |

### 2.5 CLI (`src/sciforge/cli/` — 898 LOC)

| 文件 | 行数 | 职责 | 状态 |
|------|------|------|------|
| `main.py` | 98 | Typer 入口 + 子命令注册 | ✅ |
| `parse.py` | 187 | `parse run` + `migrate-db` + `db-stats` | ✅ |
| `generate.py` | 466 | `generate free/variant/batch/solve/evolve/solve-deep` | ✅ |
| `display.py` | 147 | Rich 终端格式化输出 | ✅ |

### 2.6 存储 (`src/sciforge/storage/` — 1,598 LOC)

| 文件 | 行数 | 职责 | 状态 |
|------|------|------|------|
| `protocol.py` | 29 | StorageBackend Protocol (runtime_checkable) | ✅ |
| `sqlite_backend.py` | 527 | SQLite + FTS5 存储后端 (CRUD/搜索/去重/关系/统计) | ✅ |
| `schema.sql` | 191 | 数据库 schema (表/索引/FTS5/触发器) | ✅ |
| `migrate.py` | 82 | JSON → SQLite 迁移工具 | ✅ |
| `file_backend.py` | 194 | JSON 文件存储 | ✅ |
| `enhanced_index.py` | 280 | 增强索引（全文搜索、过滤、排序） | ✅ |
| `index.py` | 75 | 基础 JSON 索引 | ✅ |
| `tag_trie.py` | 215 | 前缀树标签检索/自动补全 | ✅ |

### 2.7 配置 (`src/sciforge/config/` — 243 LOC)

| 文件 | 行数 | 职责 | 状态 |
|------|------|------|------|
| `settings.py` | 211 | AppConfig (pydantic-settings) | ✅ |
| `logging.py` | 32 | structlog 配置 | ✅ |

### 2.8 测试 (`tests/` — 74 LOC)

| 内容 | 状态 | 说明 |
|------|------|------|
| `conftest.py` | ✅ | 基础 fixture 已定义 |
| `test_pipeline/` | ⚠️ 空 | 仅 `__init__.py` |
| `test_core/` | ⚠️ 空 | 仅 `__init__.py` |
| `test_cli/` | ⚠️ 空 | 仅 `__init__.py` |
| `test_storage/` | ⚠️ 空 | 仅 `__init__.py` |

---

## 3. 待完善项

| 优先级 | 类别 | 说明 |
|--------|------|------|
| P0 | 测试 | 所有测试目录为空，需编写单元测试和集成测试 (~100 用例) |
| P0 | 端到端验证 | 用真实数据（Raw Data/ 下的 tex/md/pdf）跑通完整 Pipeline |
| P1 | LLM 后端 | `gemini.py` 和 `compatible.py` 仅基础实现 |
| P1 | Prompt 调优 | Pipeline 各阶段 prompt 需基于真实数据效果持续迭代 |
| P2 | mypy | 需确认全部代码通过 strict 类型检查 |
| P2 | Phase 5 剩余 | FastAPI 服务层、Embedding 向量索引 (sqlite-vec) |

---

## 4. 代码量统计

| 模块 | Python LOC | 其他 | 文件数 |
|------|-----------|------|--------|
| pipeline/ | 5,600 | — | 23 |
| core/ | 3,613 | — | 11 |
| llm/ | 1,253 | — | 8 |
| prompts/ | 140 | 1,455 行 txt | 7 py + 27 txt |
| cli/ | 898 | — | 4 |
| storage/ | 1,407 | 191 行 sql | 9 |
| config/ | 243 | — | 2 |
| tests/ | 74 | — | 6 |
| **合计** | **~13,228** | **~1,646** | **~97** |
