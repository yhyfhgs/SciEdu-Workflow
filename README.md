# SciForge

AI 驱动的大学数学智能教育平台。从教材、讲义、试卷中自动提取结构化知识，并为后续题目生成与个性化推荐打基础。

## 核心能力

- **数据处理 Pipeline** — Producer-Consumer 单管道，支持 PDF / DOCX / MD / TeX 等格式：
  - **Phase A**: 多模态视觉识别（VLM）每 5 页一批转 markdown
  - **Phase B**: 200 行滑动窗口提取，单次 LLM 调用完成"提取 + 校正 + 分类 + 打标签 + 难度评估"
  - 行级断点恢复，跨调用 tag 树注入避免同义异名
  - 异常隔离 + 自动重试，单 batch / 单窗口失败不阻断整体
- **SQLite 存储** — FTS5 全文检索 + 内容哈希去重，每个条目带 `order_index` 用于后续构建知识图谱
- **CLI 接口** — 单一入口 `sciforge parse run`，Rich 终端表格

## 快速开始

### 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 包管理器
- LibreOffice（可选，VLM 后端解析 DOCX 时需要）

### 安装

```bash
git clone https://github.com/ZyphingFHGS/SciForge.git
cd SciForge
uv sync --all-extras

# 配置 LLM
cp config/sciforge.example.toml config/sciforge.toml
# 编辑 config/sciforge.toml 修改 provider/model 与 base_url

# 配置 API Key
cp .env.example .env
# 编辑 .env 填入 ANTHROPIC_API_KEY 或 OPENAI_API_KEY
```

### 使用

```bash
# 解析单个 PDF（默认 VLM 后端 + 5 页/批 + 200 行/窗口）
uv run sciforge parse run path/to/textbook.pdf --db data/sciforge.db

# 解析整个目录（4 个 logical_document 并发）
uv run sciforge parse run "Raw Data/" -j 4

# 切换为纯文本 pymupdf 后端（不调用 LLM 识别）
uv run sciforge parse run book.pdf -b pymupdf

# 调整 Phase A / Task1 参数
uv run sciforge parse run book.pdf --pages-per-batch 10 --extract-window-lines 300

# 仅识别不入库（用于离线检查 page_NNN-NNN.md）
uv run sciforge parse run book.pdf --skip-llm

# 忽略 checkpoint 重新开始
uv run sciforge parse run book.pdf --restart

# 数据库统计
uv run sciforge parse db-stats data/sciforge.db

# 完整帮助
uv run sciforge parse run --help
```

## 项目结构

```
src/sciforge/
├── core/        # 核心 Pydantic 模型 (models.py) + 异常 (exceptions.py)
├── llm/         # LLM 客户端层（Anthropic / OpenAI / Gemini / OpenAI 兼容）
├── pipeline/    # Producer-Consumer 两阶段数据流水线
│                #   orchestrator / recognizers / vlm_converter
│                #   task1_extractor / tag_tree / resume_state / retry
├── prompts/     # 提示词模板（必须为 .txt 文件）
├── storage/     # SQLite + FTS5 + 内容去重（批量 executemany + per-task connection）
├── cli/         # Typer CLI（parse run / parse db-stats；generate 子命令占位待重建）
└── config/      # AppConfig + structlog
```

详细架构见 [`Docs/01_数据处理Pipeline.md`](Docs/01_数据处理Pipeline.md) 与 [`CLAUDE.md`](CLAUDE.md)。

## 技术栈

Python 3.11+ | Pydantic v2 | Typer + Rich | structlog | SQLite + FTS5 | uv | asyncio Producer-Consumer

## 开发

```bash
# 运行测试
uv run pytest

# 代码检查
uv run --extra dev ruff check src/
uv run --extra dev ruff format src/

# 类型检查
uv run --extra dev mypy src/sciforge/core src/sciforge/pipeline src/sciforge/storage src/sciforge/cli
```

提交规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

Private — All Rights Reserved.
