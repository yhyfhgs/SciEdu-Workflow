# CLI 接口设计

> 版本: v4.0 | 日期: 2026-04-11

## 1. 框架

Typer (Click 兼容，基于类型注解) + Rich 终端输出。入口 `sciforge` 命令注册在 `pyproject.toml`。

---

## 2. 命令结构（当前 MVP）

```
sciforge
├── parse                       # 数据处理 Pipeline
│   ├── run <path>              # Producer-Consumer 流水线（核心）
│   └── db-stats <db>           # SQLite 数据库统计
│
├── validate <data_dir>         # 已处理数据的内容验证（占位）
└── generate                    # AI 题目生成（暂不在 MVP 主线，部分命令已破损）
    └── ...
```

> 当前 MVP 主线只覆盖 `parse run` 与 `parse db-stats`。`generate` 子命令由于早期模型重构 (commit 7f2abb9) 中字段变更而部分破损，将在生成路径单独修复时恢复。`kb` / `questions` / `subjects` / `config` 命令组规划中暂未实现。

---

## 3. parse run 详细参数

```bash
uv run sciforge parse run <path> [OPTIONS]
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `path` (positional) | Path | 必需 | 文件或目录 |
| `--output, -o` | Path | `data` | 输出目录（assets / logs） |
| `--db` | Path | `data/sciforge.db` | SQLite 数据库路径 |
| `--backend, -b` | str | `vlm` | 文档识别后端：`vlm` / `pymupdf` |
| `--pages-per-batch` | int | 5 | Phase A 每批页数 |
| `--extract-window-lines` | int | 200 | Task1 滑动窗口行数 |
| `--max-retries` | int | 3 | LLM/VLM 调用重试次数 |
| `--max-files, -j` | int | 4 | 目录模式下最大并发 logical_document 数 |
| `--skip-llm` | bool | False | 跳过 LLM 调用（仅做识别） |
| `--dry-run` | bool | False | 仅处理不写入 DB |
| `--restart` | bool | False | 忽略 checkpoint 从头开始 |
| `--llm-provider` | str | None | LLM 提供者覆写（如 `openai_compatible`） |
| `--llm-model` | str | None | LLM 模型覆写 |
| `--llm-config` | Path | `config/sciforge.toml` | TOML 配置文件路径 |

### 3.1 输出表格示例（单文件）

```
Pipeline 结果: textbook.pdf
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ 指标         ┃ 值                       ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 文件 hash    │ 9c874b119bf22a68         │
│ 识别页数 (max) │ 124                      │
│ 推入 mdblock 数 │ 25                       │
│ 入库条目数   │ 187                      │
│ 失败 batch   │ 0                        │
│ 失败 block   │ 0                        │
│ 总耗时       │ 312.45s                  │
└──────────────┴──────────────────────────┘
```

### 3.2 输出汇总（目录模式）

```
Pipeline 完成: 12 文件, 1843 条目入库, 0 失败 block, 0 错误, 1234.5s
```

---

## 4. parse db-stats 详细参数

```bash
uv run sciforge parse db-stats <db>
```

输出：

```
数据库统计: sciforge.db
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ 类型             ┃ 数量   ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ knowledge_points │ 487    │
│ questions        │ 312    │
└──────────────────┴────────┘
```

---

## 5. 使用示例

```bash
# 默认 VLM 后端，5 页/批，200 行/窗口
uv run sciforge parse run "Raw Data/textbook.pdf"

# 用 pymupdf 后端（无 LLM 识别，纯文本提取）
uv run sciforge parse run "Raw Data/textbook.pdf" -b pymupdf

# 调整 Phase A 批大小与 Task1 窗口
uv run sciforge parse run book.pdf --pages-per-batch 10 --extract-window-lines 300

# 失败重试次数加大
uv run sciforge parse run book.pdf --max-retries 5

# 目录模式，并发 8 个 logical_document
uv run sciforge parse run "Raw Data/" -j 8

# 跳过 LLM 调用，只识别保存为 page_NNN-NNN.md（用于离线检查）
uv run sciforge parse run book.pdf --skip-llm

# Dry run：处理但不写 DB
uv run sciforge parse run book.pdf --dry-run

# 忽略 checkpoint 从头跑
uv run sciforge parse run book.pdf --restart

# 用自定义 LLM provider
uv run sciforge parse run book.pdf \
  --llm-provider openai_compatible \
  --llm-model anthropic/claude-sonnet-4-20250514 \
  --llm-config config/sciforge.toml

# 数据库统计
uv run sciforge parse db-stats data/sciforge.db
```

---

## 6. TOML 配置文件

`config/sciforge.toml` 至少需要两个任务段：

```toml
[llm]
provider = "openai_compatible"
model = "z-ai/glm-5.1"
base_url = "https://openrouter.ai/api/v1"

[llm.tasks.vlm_convert]
# Phase A: 必须支持图像输入
provider = "openai_compatible"
model = "z-ai/glm-5v-turbo"
base_url = "https://openrouter.ai/api/v1"

[llm.tasks.extract_and_classify]
# Task1: 单次调用做提取/校正/分类/打标签/难度
provider = "openai_compatible"
model = "z-ai/glm-5.1"
base_url = "https://openrouter.ai/api/v1"
max_tokens = 32768
```

环境变量覆写：`SCIFORGE_TASK_VLM_CONVERT_MODEL=...`、`SCIFORGE_TASK_EXTRACT_AND_CLASSIFY_PROVIDER=...` 等。

API Key 通过环境变量或 `.env`：
- `ANTHROPIC_API_KEY=...`
- `OPENAI_API_KEY=...`（OpenRouter 也用这个）

---

## 7. 实现文件

| 文件 | 职责 |
|---|---|
| `src/sciforge/cli/main.py` | Typer 入口 + `validate` 命令 + 全局 `--log-level` 等 |
| `src/sciforge/cli/parse.py` | `parse run` / `parse db-stats` |
| `src/sciforge/cli/generate.py` | `generate` 命令组（暂破损） |
| `src/sciforge/cli/display.py` | Rich 输出格式化助手 |

---

## 8. 帮助查看

```bash
uv run sciforge --help
uv run sciforge parse --help
uv run sciforge parse run --help
uv run sciforge parse db-stats --help
```
