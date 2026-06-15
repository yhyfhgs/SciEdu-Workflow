# CLI 接口设计

> 版本: v6.1 | 日期: 2026-06-15
>
> **2026-06-15 优化批次新增（详见 [PROGRESS.md](PROGRESS.md) §00）**：
> - `parse run` 新增 `--force-vlm`（REC-01，关闭文字层自动探测）、`--parallel-extract` +
>   `--segment-concurrency`（单文档内并行提取）；窗口/批次/并发类参数默认值改为
>   `None`（不传则读 TOML → AppConfig，CLI 仅覆盖显式传入项，消除三处漂移，REC-30）
> - 新增 `parse relink`（REC-29，跨文档题答联动）、`parse audit-tags`（同义异名 tag 段体检）、
>   `parse validate`（入库后只读体检报告）
> - `parse audit` 的 `--pages-per-batch` 改为读 AppConfig/TOML（与真实 run 一致，REC-30）

## 1. 框架

Typer (Click 兼容，基于类型注解) + Rich 终端输出。入口 `sciforge` / `sf` 命令注册在 `pyproject.toml`。

---

## 2. 命令结构（当前 MVP）

```
sciforge
├── parse                               # 数据处理 5 阶段 Pipeline
│   ├── run <path>                     # Task0→Stage1→Stage2→Stage3 完整流水线
│   ├── run-book <files...>            # 多 md/tex 章节作为单本书（共享 order_index）
│   ├── audit <raw-data-dir>           # Raw Data 进度盘点
│   ├── audit-tags <db>                # 扫描入库 tag 段，列出疑似同义异名段对
│   ├── validate <db>                  # 入库后只读体检报告（类型分布/题答绑定/编号缺口…）
│   ├── relink <db>                    # 跨文档题答联动（习题册↔答案册分文件回填，REC-29）
│   ├── migrate-legacy <checkpoint>    # 旧 vlm/ 目录迁移至新结构
│   └── db-stats <db>                  # SQLite 数据库统计
│
├── generate                            # AI 题目 / 讲义 / 解答生成
│   ├── free                           # QGenAgent 5 阶段闭环（MVP 主线）
│   ├── solve <question-id>            # SolveAgent 为已有题目生成详细解答
│   ├── batch                          # 等价于 `free --count N`（便捷别名）
│   ├── lecture                        # DocAssemblyAgent lite 生成 markdown 讲义
│   ├── variant <source-id>            # Post-MVP 占位（NotImplementedError）
│   ├── evolve                         # Post-MVP 占位
│   └── solve-deep <question-id>       # Post-MVP 占位（依赖 sandbox + tool calling）
│
└── config                              # 配置诊断与校验
    ├── show
    └── validate
```

> MVP 主线完整覆盖：
> - Pipeline：`parse run` / `run-book` / `audit` / `audit-tags` / `validate` / `relink` / `migrate-legacy` / `db-stats`
> - Agent：`generate free` / `solve` / `batch` / `lecture`
> - Config：`config show` / `validate`
>
> `generate variant / evolve / solve-deep` 是显式占位（`typer.Exit(2)`），返回
> 明确错误并引导用户使用 `free` / `solve`。

---

## 3. parse run

```bash
uv run sciforge parse run <path> [OPTIONS]
```

> **默认值约定（REC-30）**：窗口/批次/并发/重试类参数 CLI 默认 `None` — 不传则读
> `--llm-config` TOML，再 fallback 到 `AppConfig` 默认。CLI 仅覆盖**显式传入**的项，
> 以此消除「settings 默认 vs 生产 TOML」三处漂移。下表「默认」列给出未配 TOML 时的 fallback。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `path` (positional) | Path | 必需 | 文件或目录（`exists=True` 校验） |
| `--output, -o` | Path | `data` | 输出目录（assets / logs） |
| `--db` | Path | `data/sciforge.db` | SQLite 数据库路径 |
| `--backend, -b` | str | `vlm` | 文档识别后端：`vlm` / `pymupdf`（**枚举校验，非法值立即退出**） |
| `--force-vlm` | bool | False | 关闭文字层自动探测，强制所有 PDF 全页走 VLM（REC-01）。默认会把含文字层的电子版 PDF 自动降级到 0-LLM 文本提取；脏文字层的扫描件用本开关强制 VLM |
| `--pages-per-batch` | int | None→5 | Task0 每批页数 |
| `--extract-window-lines` | int | None→500 | Stage1Extract 初始窗口行数 |
| `--extract-max-window-lines` | int | None→3000 | Stage1Extract 扩窗口上限 |
| `--extract-expand-step` | int | None→500 | Stage1Extract 每次扩窗口步长 |
| `--max-retries` | int | None→3 | LLM/VLM 调用重试次数（同时覆写 extract/stage1/vlm 三处重试上限） |
| `--max-files, -j` | int | None→8 | 目录模式下最大并发 logical_document 数（≤16GB RAM 用 4-6；32GB 用 8-12；64GB+ 用 16-24） |
| `--parallel-extract` | bool | False | 单文档内并行提取：按 section 边界切段、段间并发（段内 carry-aware 滑窗不切断条目）。适合**单个超大文件**提速；目录多文件已自带跨文件并行 |
| `--segment-concurrency` | int | None→4 | `--parallel-extract` 的段间并发度 |
| `--skip-llm` | bool | False | 跳过 LLM 调用（仅做识别） |
| `--dry-run` | bool | False | 仅处理不写入 DB |
| `--restart` | bool | False | 忽略 checkpoint 从头开始（DB/state.json schema 不兼容时必需，见下） |
| `--llm-provider` | str | None | LLM 提供者覆写（如 `openai_compatible`） |
| `--llm-model` | str | None | LLM 模型覆写 |
| `--llm-config` | Path | None | TOML 配置文件；未指定时自动读 `config/sciforge.toml`，显式指定不存在的路径**立即 fail** |
| `--discipline, -D` | str | `math` | 文档学科：`math` / `physics` / `chemistry` / `biology` / `other` |
| `--debug` | bool | False | Debug 模式：打印每轮窗口、LLM 回复和条目连续性 |

> **schema 版本不兼容**：当前 DB schema 为 **v10**。任何 v8 之前的旧 DB / 旧 `state.json`
> 加载时检测到版本不匹配会 `raise SchemaVersionMismatch`，需加 `--restart` 清库重建
> （`Raw Data/`、`data/` 不入版本控制且可重跑，新增字段沿用清库重建策略）。

### 3.1 输出示例（单文件）

```
Pipeline 结果: textbook.pdf
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ 指标           ┃ 值                        ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 文件 hash      │ 9c874b119bf22a68          │
│ 识别页数 (max) │ 124                       │
│ 推入 mdblock 数│ 25                        │
│ 入库条目数     │ 187                       │
│ 失败 batch     │ 0                         │
│ 失败 block     │ 0                         │
│ 总耗时         │ 312.45s                   │
└────────────────┴───────────────────────────┘
```

### 3.2 目录模式汇总

```
Pipeline 完成: 12 文件, 1843 条目入库, 0 失败 block, 0 错误, 1234.5s
```

---

## 4. parse run-book

把多个 md/tex 文件作为一本书按顺序处理，共享 `order_index` / tag_tree / 断点状态。

```bash
uv run sciforge parse run-book [FILES...] [--manifest PATH] [OPTIONS]
```

三种输入方式：
1. 位置参数列表：`run-book a.md b.md c.md`
2. 清单文件：`run-book --manifest book.yaml`
3. 两者合并（先 manifest 再位置参数）

manifest 支持 YAML/JSON，格式：
- 列表顶层：`["a.md", "b.md"]`
- 对象含 `files:` 键：`{files: ["a.md", "b.md"], name: "..."}`
- 对象含 `chapters:` 键：`{chapters: [{path: "a.md"}, ...]}`

所有章节必须是 `.md` / `.markdown` / `.tex`（目前不支持混合 PDF）。

---

## 5. parse audit

盘点 `Raw Data/` 目录的处理进度，输出每个文件的 hash / 入库条目数 / 最后一次 state
时间戳 / 是否完成。

```bash
uv run sciforge parse audit <raw-data-dir> [--output summary.md] [--db ...] [--pages-per-batch N]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `raw-data-dir` (positional) | `Raw Data` | Raw Data 根目录 |
| `--checkpoint-dir` | `data/checkpoint` | Checkpoint 根目录 |
| `--db` | `data/sciforge.db` | SQLite 数据库路径 |
| `--output, -o` | `data/pipeline_progress_report.md` | 报告落盘路径 |
| `--pages-per-batch` | None→`AppConfig().pages_per_batch` | 预估 PDF 批次数用的每批页数；**默认与真实 `run` 同源**（读 AppConfig/TOML），消除旧版硬编码 5 vs 实际 10 的盘点 2× 偏差（REC-30） |

---

## 6. parse audit-tags

扫描入库的全部 tag 段，用编辑距离找出疑似同义异名的段对（如 `group_theory:群论` vs
`group:群`），输出人工合并参考表。只读，不改库。

```bash
uv run sciforge parse audit-tags --db data/sciforge.db [--max-english-distance 2] [--max-chinese-distance 1]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--db` | `data/sciforge.db` | SQLite 数据库路径 |
| `--max-english-distance` | 2 | english 部分最大编辑距离 |
| `--max-chinese-distance` | 1 | 中文部分最大编辑距离 |

---

## 7. parse validate

入库后**只读体检**：跑完 `parse run` 后执行，得到提取总量 / 类型双维度分布（知识点类型 ·
题目认知类别 · 题目形式）/ 题答绑定率 / 编号规范化 / content_hash 去重 / 编号缺口 /
知识图谱边的结构化报告与逐项 PASS/WARN/FAIL 判定。**不修改数据**（对应诉求 #2/#3/#4/#5/#6/#8）。

```bash
uv run sciforge parse validate data/sciforge.db
```

---

## 8. parse relink

**跨文档题答联动（REC-29）**。把全库（按 `--discipline` 过滤）的题目一起跑 `answer_linker`，
解决「习题册 / 答案册分文件」时章末或独立答案文件无法回填到题干的问题：回填缺失 solution、
移除冗余的纯答案条目。默认 post-pass 按 `source_file` 过滤无法跨文件，本命令显式跨全库联动。

```bash
uv run sciforge parse relink data/sciforge.db -D math
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `db` (positional) | 必需 | SQLite 数据库路径（`exists=True` 校验） |
| `--discipline, -D` | `math` | 学科过滤 |

---

## 9. parse migrate-legacy

把旧版本 `checkpoint/<hash>/vlm/page_*.md` 目录迁移到新结构
`checkpoint/<hash>/recognize/file_NNN_<stem>/page_*.md`。当前命令默认真实执行，
需要预览时显式传 `--dry-run`。

```bash
uv run sciforge parse migrate-legacy "Raw Data" --checkpoint-dir data/checkpoint --dry-run
```

---

## 10. parse db-stats

```bash
uv run sciforge parse db-stats <db>
```

输出 knowledge_points / questions 总数。

---

## 11. generate free

```bash
uv run sciforge generate free \
  -t "math:数学,analysis:分析" \
  -d 6 \
  --type proof \
  -n 3 \
  --db data/sciforge.db
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `-s, --subject` | `""` | 科目（如 `real_analysis:实分析`） |
| `-t, --topic` | 必需 | 专题标签（逗号分隔），至少 1 个 |
| `-d, --difficulty` | 5 | 难度 1-10 |
| `--type` | `proof` | 题型：`proof` / `calculation` / … |
| `-n, --count` | 1 | 生成数量 |
| `-D, --discipline` | `math` | 学科 |
| `--db` | `data/sciforge.db` | DB 路径 |
| `--save / --no-save` | `--save` | 是否写入 DB |
| `--llm-provider / --llm-model / --llm-config` | None | LLM 覆写 |

---

## 12. generate solve

为已存在的题目生成详细解答（可选写回 `solution_content`）。

```bash
uv run sciforge generate solve <question-id> [--save] [--db ...]
```

- `--save`：把生成的解答写回 `question.solution_content`
- 题目不存在时（`FileNotFoundError` 或返回 None），立即显示 "题目不存在: ..." 并 `exit 1`

---

## 13. generate batch

`free --count N` 的便捷别名。docstring 明确标注"等价于 `generate free --count N`"。

---

## 14. generate lecture

按 tag 检索 KP/Question，调用 DocAssemblyAgent 生成 markdown 讲义。

```bash
uv run sciforge generate lecture \
  -t "math:数学,analysis:连续函数" \
  --title "连续函数专题" \
  --audience "大学一年级" \
  -o data/lectures/
```

输出 `<lecture-id>.md` + `<lecture-id>.json`（完整 `Lecture` 模型序列化）。

---

## 15. config 命令组

- `config show`：打印解析后的 providers / tasks / default_provider_ref，API key 自动脱敏
- `config validate`：遍历已知 task，校验每个 `LLMConfig.from_app_config(...)` 能否解析

---

## 16. TOML 配置文件 (v2 schema)

```toml
default_provider_ref = "openrouter_glm"

[providers.openrouter_glm]
type = "openai_compatible"
base_url = "https://openrouter.ai/api/v1"
api_key = "${OPENROUTER_API_KEY}"
default_model = "z-ai/glm-5.1"
supports_prompt_cache = true          # REC-08：兼容网关 opt-in prompt cache（静态 system 前缀包 cache_control）

[providers.anthropic_main]
type = "anthropic"
api_key = "${ANTHROPIC_API_KEY}"
default_model = "claude-sonnet-4-20250514"

# REC-13 分级路由：extract 走强档，review/tag/annotate_review 切便宜档（占 ~75% 调用）
[tasks.vlm_convert]
provider_ref = "openrouter_glm"
model = "z-ai/glm-5v-turbo"

[tasks.extract_and_classify]
provider_ref = "openrouter_glm"
max_tokens = 32768

[tasks.generate]
provider_ref = "anthropic_main"
temperature = 0.3

[pipeline]
pages_per_batch = 5
extract_window_lines = 500            # Stage1Extract 初始窗口
extract_max_window_lines = 3000       # 扩窗口上限
extract_empty_force_limit = 2         # 达上限后连续 N 次空提取 → force consume
# ── 2026-06-15 优化批次默认（REC-03/04/05）──
stage2_annotate_concurrency = 4       # Stage2Annotate N-worker 并发
stage2_review_concurrency = 2         # Stage2Review N-worker 并发
pipeline_stage_queue_size = 8         # stage 间队列深度（2→8，缓冲 LLM 往返）
max_concurrent_llm_calls = 8          # 全局 LLM 并发闸门（≥并发总和）
stage2_annotate_batch = true          # 批量打标：N 条合 1 次 LLM
stage2_annotate_batch_size = 8        # 每批条数
stage2_review_enabled = false         # 默认关闭（与 Stage3 确定性规则重叠）
vlm_auto_text_detect = true           # 文字层探测自动降级（--force-vlm 关闭）
reconciliation_autofix = false        # 保守：仅高置信 kp_type 自动纠，category 只报告

[logging]
log_level = "INFO"
log_format = "console"
```

> 完整优化开关清单（含 thinking PRESETS、单文档并行、post-pass 开关等）见
> [01_数据处理Pipeline.md](01_数据处理Pipeline.md) 与 `config/sciforge.example.toml`。

环境变量：
- `SCIFORGE_TASK_<TASK>_<FIELD>`：覆盖任务级 LLM 字段（如 `SCIFORGE_TASK_GENERATE_MODEL`）
- `SCIFORGE_<FIELD>`：覆盖 AppConfig 顶层字段（如 `SCIFORGE_EXTRACT_WINDOW_LINES`）
- 标准 vendor 变量：`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` / `OPENROUTER_API_KEY`

Legacy `MATHFORGE_*` 前缀会在启动时发 deprecation warning（不再生效）。

---

## 17. 实现文件

| 文件 | 职责 |
|---|---|
| `src/sciforge/cli/main.py` | Typer 入口 + `--log-level/--log-format/--log-dir` |
| `src/sciforge/cli/parse.py` | `parse run / run-book / audit / audit-tags / validate / relink / migrate-legacy / db-stats` + backend/llm-config 校验 |
| `src/sciforge/cli/generate.py` | `generate free / solve / batch / lecture` + 3 个 Post-MVP 占位 |
| `src/sciforge/cli/config_cmd.py` | `config show / validate` |
| `src/sciforge/cli/display.py` | Rich 输出 helper |

---

## 18. 帮助查看

```bash
uv run sciforge --help
uv run sciforge parse --help
uv run sciforge parse run --help
uv run sciforge generate --help
uv run sciforge generate free --help
```
