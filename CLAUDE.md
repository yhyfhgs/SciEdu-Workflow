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
- **存储**: SQLite（FTS5 全文检索、内容去重），通过 StorageBackend Protocol 抽象
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

# Pipeline（默认 VLM 后端 + 5 页/批 + 200 行/窗口）
uv run mathforge parse run path/to/file --db data/mathforge.db

# Pipeline（pymupdf 纯文本后端）
uv run mathforge parse run path/to/file -b pymupdf

# 调整 Phase A / Task1 参数
uv run mathforge parse run path/to/file --pages-per-batch 5 --extract-window-lines 200

# 忽略 checkpoint 重新开始
uv run mathforge parse run path/to/file --restart

# 数据库统计
uv run mathforge parse db-stats data/mathforge.db
```

## 项目结构

```
src/mathforge/
├── core/        # 核心领域模型与异常定义
│                #   models.py (KnowledgePoint/Question/MdBlockSource/FileExtractionState/...)
│                #   exceptions.py (VlmConversionError / LLMError / ... 全仓库共用)
├── llm/         # LLM 统一客户端：client.py, config.py, parser.py, backends/
├── pipeline/    # 数据处理流水线 (Producer-Consumer 两阶段)
│                #   orchestrator(2 Task + 单 queue + per-logical_document DB 实例),
│                #   recognizers(VlmRecognizer / PyMuPdfRecognizer / MarkdownRecognizer),
│                #   vlm_converter(VLM 多模态识别, recognize_in_batches 入口),
│                #   task1_extractor(Consumer: 行号累加滑动窗口 + 单次 LLM 提取入库),
│                #   tag_tree(英中段层级 tag 树, prompt 注入用),
│                #   resume_state(checkpoint 加载/保存, asyncio lock + 节流刷盘),
│                #   retry(异步重试 + LLM JSON 重试)
├── prompts/     # 提示词模板：templates.py + templates/pipeline/ 下的 .txt 文件
├── storage/     # 存储：protocol.py(StorageBackend 协议),
│                #   sqlite_backend.py(SQLite+FTS5, executemany 批量写入),
│                #   schema.sql
├── cli/         # CLI 命令：main.py, parse.py, generate.py (占位待重建), display.py
└── config/      # 配置：settings.py(pydantic-settings), logging.py(structlog)
```

## Pipeline 架构（Producer-Consumer 单管道）

```
logical_document  (单文件 or 多个分卷)
      ↓
 [Task0 Producer] 文档识别 (vlm_converter / pymupdf / markdown)
   - 每 5 页一批 → page_NNN-NNN.md (1-based)
   - 顺序推入 queue (背压, maxsize=4)
   - vlm_batches_done 写 state.json
      ↓ asyncio.Queue (FIFO 严格顺序)
 [Task1 Consumer] Task1Extractor
   - 取出 mdblock → 累计全局行号 → 行缓冲
   - 200 行滑动窗口
   - 单次 LLM 调用 (extract_and_classify):
       提取 + 校正 + 分类 + 打标签 + 难度评估
       prompt 注入 tag 子树（避免同义异名）
   - 提取出的条目分配 order_index → 直接 storage.save_batch()
   - 行级 checkpoint 持久化
   - "incomplete" 时等下一个 mdblock 拼接
   - 单窗口失败 → 标记 failed_blocks，不阻断整体
```

**关键属性**:
- 完全 FIFO，全局行号跨 mdblock 连续
- `order_index` 单调递增供后续构建知识图谱
- `tag` 是有序的 `english:中文` 段列表（如 `["math:数学","algebra:代数","group:群论"]`）
- 断点恢复：state.json 记录 vlm_batches_done / current_block_idx / line_offset / line_buffer_carry / order_index_next / tag_tree_json
- state 写入节流（默认 3 秒，`checkpoint_flush_interval`），异常/完成时强制刷盘
- 多文件分卷：`*_part_N_M.ext` 自动按页码分组，作为单个 logical_document 顺序识别
- 目录并发：`process_directory` 为每个 logical_document 构造独立 `SQLiteStorageBackend` 实例（WAL + busy_timeout），避免共享 connection 的线程风险

**生成引擎（暂未对接）**: `cli/generate.py` 的所有子命令当前为 NotImplementedError
占位。破损的 `core/engine.py / generator.py / retriever.py / solver.py / verifier.py /
evolution_engine.py / pipeline_orchestrator.py / sandbox.py` 已在 2026-04 清理周期
中移除，等 parse 主线稳定后按新 models + SQLiteStorageBackend 重建。

## 代码规范

- 提交消息遵循 Conventional Commits：`<type>(<scope>): <subject>`
- 分支策略：`main`(稳定) → `dev`(集成) → `feat/<name>`(功能)
- 所有新代码必须通过 ruff 和 mypy 检查
- 异步函数使用 async/await，LLM 调用均为异步
- Pydantic 模型定义在 `core/models.py`，不要分散到其他模块
- 配置通过 pydantic-settings 管理，环境变量前缀 `MATHFORGE_`
- 日志使用 structlog，不要使用 print 或 logging 模块
- 优先使用枚举判别而非子类继承：KnowledgePoint(kp_type=KPType.LEMMA)，不要创建 LemmaItem 子类
- StorageBackend 通过 Protocol 抽象，新增后端只需实现统一接口
- Tag 格式约束：每段 `english:中文`，一个条目的 tags 是有序段列表代表一条根→叶路径
- prompt 必须存为 `.txt` 文件（不要内联到 Python 字符串）

## 重要约定

- `Raw Data/` 和 `data/` 不纳入版本控制，包含原始教材和运行时生成数据
- `.env` 包含 API Key，绝不提交
- LLM 后端通过 `llm/backends/` 下的适配器实现，新增后端只需实现统一接口
- Pipeline 统一入口为 `mathforge parse run`，默认 VLM 多模态识别 PDF/DOCX
- 存储默认 SQLite（`--db data/mathforge.db`）
- Pipeline checkpoint 在 `data/checkpoint/<file_hash>/`，含 `state.json` 与 `recognize/file_NNN_<stem>/page_NNN-NNN.md`
