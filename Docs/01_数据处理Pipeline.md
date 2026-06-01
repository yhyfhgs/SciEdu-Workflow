# 数据处理 Pipeline

> 版本: v8.1 | 日期: 2026-05-31
> 架构: **5 阶段并发 Producer-Consumer**（Task0 + 5 Stage + 4 queue + asyncio.gather）
>
> v7→v8 变更要点：
> - 三段式（Task0+Task1+Task2）→ **5 阶段**（Task0 + Stage1Extract + Stage1Review + Stage2Annotate + Stage2Review + Stage3Persist），共 6 个 `asyncio.Task` 并发
> - Stage1Extract 当前主线为**每窗口批量提取所有完整条目**，输出 `BatchItemExtractPayloadLLM`
> - 内联 review → Review 拆为独立 Stage，各 stage 真正并发
> - 新增 **retry with feedback**：schema 失败时把错误注入下一轮 prompt
> - **Exercise 漏 solution bug 三道防线**：prompt + Pydantic validator + Stage1Review 专项
> - 字段精简：删除 `source_chapter` / `exam_type` / `exam_year`（Schema v5→v7；source_file 保留用于追溯来源）
> - **旧 state.json / v5 DB 不兼容**，需 `--restart` 清库

> Pipeline 设计为**学科无关** —— 核心流程不假设特定学科，多学科支持依靠 prompt 与层级 tag 树。MVP 阶段聚焦大学数学。

---

## 1. 数据清单

### 1.1 MVP 数据（大学数学）

| 来源 | 格式 | 体量 | 质量 |
|------|------|------|------|
| `Raw Data/新领军真题整理/` | .tex | ~22k 行 | 高（手写 LaTeX） |
| `Raw Data/新领军讲义md/` | .md | ~6k 行 | 中（OCR） |
| `Raw Data/数学分析课本md/` | .md | ~32k 行 | 中（OCR） |
| `Raw Data/抽代教材PPT习题/` | .pdf / .pptx | ~20MB | 待处理 |

### 1.2 通用数据

任意 PDF / DOCX / MD / TeX / PPTX / EPUB / CSV 教材、讲义、试卷。多卷文件（`*_part_1_3.pdf` 命名约定）会被自动按页码合并为单个 logical_document。

---

## 2. 总体架构

### 2.1 设计原则

> **"识别 + 5 阶段并发，批量提取、下游保序、retry 带错误反馈"**
>
> 1. Task0 把每 5 页文档识别为 markdown batch，推入 `q_md`
> 2. Stage1Extract 按 500 行滑动窗口批量提取完整条目，并通过 `OrderIndexAllocator` 预留连续 `item_id`
> 3. Stage1Review 用原 window + 批量 items 校验修正（专门修复 exercise 漏 solution bug）
> 4. Stage2Annotate 给单条 item 打 tags/difficulty/taxonomy
> 5. Stage2Review 审核 tag 格式/复用/校准
> 6. Stage3Persist 合并 item + annotation → KP/Question 入库（无 LLM，严格按 item_id 递增）
> 7. tag 是有序的 `english:中文` 段列表，在 Stage2/3 通过 tag 子树避免同义异名
> 8. 单 item / 单窗口失败被隔离，不阻断整体

### 2.2 数据流

```
logical_document (单文件 or *_part_N_M 多分卷)
       │
       ▼  Task0 Producer (vlm / pymupdf / markdown)
            每 5 页一批 → page_NNN-NNN.md (1-based)
            失败 batch → state.failed_batches；不推入 queue
       │
       ▼  q_md (maxsize=pipeline_queue_size，FIFO)
       │
       ▼  Stage1Extract (LLM 调用 1)
            行缓冲 + 500 行窗口；0 条则扩 500 → 上限 3000；达 max 后连续 N 次空 → force consume
            每次输出 BatchItemExtractPayloadLLM：items[] + stop_reason + last_consumed_line
            为 items 连续分配 item_ids，滑窗到 last_consumed_line+1
            mirror → items/stage1/item_<id>.json
       │
       ▼  q_raw (ExtractedBatch：items + 原 window_lines + item_ids)
       │
       ▼  Stage1Review (LLM 调用 2)
            原 window + items JSON 批量复核
            修正 exercise/example/problem 过早标 no_solution_in_source
            拒绝 → 丢弃；retry 耗尽 → 降级用原始 item
            mirror → items/stage1_review/item_<id>.json
       │
       ▼  q_rev (ReviewedItem：修正后 item + item_id)
       │
       ▼  Stage2Annotate (LLM 调用 3)
            item + tag_tree subtree + taxonomy → SingleAnnotationLLM
            fallback：写空标注（tags=[uncategorized:...]，difficulty=5）
            mirror → items/stage2/item_<id>.json
       │
       ▼  q_ann (AnnotatedItem：item + annotation + item_id)
       │
       ▼  Stage2Review (LLM 调用 4，可 config 关闭)
            tag 格式/复用、taxonomy 存在、question_type 一致、difficulty 校准
            retry 耗尽 → 降级用原始 annotation
            mirror → items/stage2_review/item_<id>.json
       │
       ▼  q_done (ReviewedAnnotatedItem：最终 item + annotation + item_id)
       │
       ▼  Stage3Persist (无 LLM)
            taxonomy_validator.validate(taxonomy_path)
            tag_tree.insert_path(tags)  # 集中累积
            合并 → KnowledgePoint or Question
            禁止 item_id 倒退；允许 review 拒绝造成 gap
            asyncio.to_thread(storage.save_batch)
            mirror → items/persisted/item_<id>.json
            state.last_persisted_item_id++
```

### 2.3 关键属性

- **并发**：6 个 `asyncio.Task` 由 `asyncio.gather` 并发启动；4 个 stage queue 解耦；LLM 调用可在不同 item 之间并行
- **严格保序**：item_id 全局递增；目录并发用共享 `OrderIndexAllocator` 避免跨文件重复；Stage2 并发时用 `ReorderBuffer` 恢复顺序
- **背压**：`q_md` (maxsize=pipeline_queue_size) + 4 stage queue (maxsize=pipeline_stage_queue_size)
- **批量输出**：Stage1 每窗口一次 LLM 调用产出所有完整条目，Stage1Review 同窗口批量审核，减少 LLM 往返
- **Retry with feedback**：schema 校验失败时把错误文本注入下一轮 prompt
- **Exercise 漏 solution 三道防线**：prompt + Pydantic near-tail validator + Stage1Review
- **节流保存**：state.json 默认 3 秒节流刷盘（`checkpoint_flush_interval`），异常/完成时强制刷盘
- **异常隔离**：任一 stage 异常 → try/finally 向下游 put SENTINEL 避免死锁；单 item 失败降级不阻断整体
- **并发 DB 写入**：目录模式每个 logical_document 独立 `SQLiteStorageBackend` 实例（WAL + `busy_timeout=5000ms`）

---

## 3. Task0 Producer：文档识别

### 3.1 Recognizer 协议

`pipeline/recognizers.py` 提供统一异步生成器接口：

```python
class Recognizer(Protocol):
    def recognize_in_batches(
        self,
        file_path: Path,
        *,
        batch_pages: int,
        output_dir: Path,
        already_done: list[tuple[int, int]],
        max_retries: int = 3,
    ) -> AsyncIterator[BatchResult]: ...

@dataclass
class BatchResult:
    page_start: int    # 1-based
    page_end: int      # 1-based, inclusive
    md_text: str
    md_path: Path
    is_failed: bool = False
```

### 3.2 已实现的 Recognizer

| 类 | 适用格式 | 特点 |
|---|---|---|
| `VlmRecognizer` | PDF / DOCX | 多模态视觉识别（最高精度），保留 LaTeX 公式与图片裁剪 |
| `PyMuPdfRecognizer` | PDF | 纯文本提取，无 LLM，速度最快 |
| `MarkdownRecognizer` | MD / TeX | 整文件单 batch |

### 3.3 落盘约定

```
data/checkpoint/<file_hash>/
└── recognize/
    ├── file_000_<stem>/
    │   ├── page_001-005.md       # 1-based 页号
    │   ├── page_006-010.md
    │   └── ...
    └── file_001_<stem>/           # 第二个分卷（如有）
        └── ...
```

---

## 4. Stage1Extract：批量滑动窗口提取

### 4.1 窗口算法

```python
base_window = 500        # config.extract_window_lines
max_window = 3000        # config.extract_max_window_lines
expand_step = 500        # config.extract_window_expand_step
empty_force_limit = 2    # config.extract_empty_force_limit
current_window_size = base_window

while buffer:
    if len(buffer) < current_window_size and not is_last:
        break  # 等下一个 md block 拼进 buffer
    window = buffer[:current_window_size]

    payload = await stage1_extract_llm(window, retry_feedback=...)  # BatchItemExtractPayloadLLM

    if not payload.items:
        consecutive_empty += 1
        if current_window_size >= max_window and consecutive_empty >= empty_force_limit:
            slide = window_len
            buffer = buffer[slide:]; line_offset += slide
            current_window_size = base_window; consecutive_empty = 0
            continue
        if current_window_size < max_window:
            current_window_size = min(current_window_size + expand_step, max_window)
            continue
        break

    # 提到一批
    consecutive_empty = 0
    items = payload.items
    item_ids = await allocate_item_ids(len(items))
    cut_idx = find_buffer_idx(payload.last_consumed_line)
    consumed = cut_idx + 1

    await q_raw.put(ExtractedBatch(
        item_ids=item_ids, items=items, window_lines=window.copy(),
        window_line_range=(line_offset+1, line_offset+window_len),
        window_last_line=window_last_line, block_index=..., ...
    ))
    buffer = buffer[consumed:]; line_offset += consumed
    current_window_size = base_window
```

### 4.2 Prompt 契约（`pipeline/extract_stage1_initial.txt`）

LLM 在窗口内搜寻**所有完整条目**（有边界确认、题答绑定完整），输出：

```json
// 提到一批：
{
  "items": [
    {
      "category": "knowledge" | "question",
      "item_type": "theorem" | "exercise" | "...",
      "number": "3.1",
      "structural_context": {"chapter_title": "...", "section_number": "3.2", ...},
      "corrected_content": "\\begin{theorem}[3.1]\n...\\end{theorem}",
      "corrected_proof": "\\begin{proof}...\\end{proof}" | null,
      "corrected_solution": null | "\\begin{solution}...\\end{solution}",
      "solution_status": "complete" | "no_solution_in_source",
      "start_line": 12,
      "end_line": 28
    }
  ],
  "stop_reason": "all_complete" | "incomplete_at_tail" | "no_items_found",
  "last_consumed_line": 28,
  "issues": []
}

// 未能提取（窗口头 / 尾未闭合）：
{ "items": [], "stop_reason": "incomplete_at_tail", "last_consumed_line": 0, "issues": ["reason"] }
```

**solution_status 二态**（相对 v7 删除了 `truncated_skip` 中间态）：
- `complete`：窗口内能看到完整 proof/solution，两者至少一个非 null
- `no_solution_in_source`：已扫到下一条正式条目或章节标题，中间确认无 proof/solution，两者都必须为 null

无法确认的尾部条目**不要进入 `items`**，把 `stop_reason` 设为 `incomplete_at_tail`，让 extractor 扩大窗口重试。

### 4.3 Exercise 漏 solution bug 修复三道防线

1. **Prompt 显式规则**：`extract_stage1_initial.txt` 单独一节强调 "exercise/example/problem 若 end_line 距窗口末尾 < 10 行 → 从 `items` 移出并返回 `incomplete_at_tail`，禁止标 `no_solution_in_source`"
2. **Pydantic validator**：`BatchItemExtractPayloadLLM._check_exercise_near_tail` 在 `model_validate(data, context={"window_last_line": N})` 时触发：若 item_type ∈ {exercise, example, problem} ∧ solution_status=no_solution_in_source ∧ end_line > window_last_line - 10 → raise ValueError → retry（带 feedback）
3. **Stage1Review 专项复核**：`extract_stage1_review.txt` 在 "solution 绑定" 一节专门列出 exercise 类修正表，发现错误时对应位置设为 `null`，Stage1Review 丢弃该条目

### 4.4 Mirror 目录

```
checkpoint/<hash>/items/
  stage1/item_000001.json          # ExtractedBatch 中单条 item 的 snapshot
  stage1_review/item_000001.json   # ReviewedItem
  stage2/item_000001.json          # AnnotatedItem
  stage2_review/item_000001.json   # ReviewedAnnotatedItem
  persisted/item_000001.json       # Stage3 完成标记
```

---

## 5. Stage1Review：批量 item 校验

从 `q_raw` 取 `ExtractedBatch`，用 LLM 校验（`extract_stage1_review.txt`），输入：
- 原始 window 文本（全局行号前缀）
- 待审 items 的 JSON 数组

校验项：行号对齐、category/item_type/number 一致、content 忠实原文、**solution 绑定修正表**。

输出：`BatchReviewPayloadLLM` 结构：
- `reviewed_items[i]=修正后的 item` → 按位置拆成 `ReviewedItem` 推入 `q_rev`
- `reviewed_items[i]=null` → 丢弃该 item（Stage1Extract 的假阳性或尾部风险）
- RetryExhausted → 降级使用原始 items（不丢数据）

`config.stage1_review_enabled=false` 时本阶段为直通；低风险 batch 可由 `review_conditional_enabled` 条件化跳过。

---

## 6. Stage2Annotate：单条标注

从 `q_rev` 取 `ReviewedItem`，输入单条 item + `tag_tree.serialize_for_prompt(max_depth, max_nodes)` + `taxonomy_validator.serialize_for_prompt(discipline)` 给 LLM（`annotate_stage2_initial.txt`），产出：

```json
{
  "subject": "real_analysis:实分析",
  "taxonomy_path": "math.analysis.continuity",
  "tags": ["math:数学", "analysis:分析", "continuity:连续性"],
  "difficulty": 6,
  "question_type": null | "proof" | ...,
  "methods": ["heine_cantor:海涅-康托尔定理"]
}
```

Pydantic `SingleAnnotationLLM` 强校验 tag 格式（`^[a-z][a-z0-9_]*:[^\s:]+$`）、difficulty 范围、学科根和主分支白名单。tags 数量不再硬编码为 3-5，由 prompt 引导常见 3-6 段。

Retry 耗尽 → `_fallback_annotation` 写兜底标注（tags=[`${tag_root}`, `uncategorized:未分类`, `auto_fallback:自动兜底`]）。

emit `AnnotatedItem` 到 `q_ann`。

---

## 7. Stage2Review：单条 annotation 审核

从 `q_ann` 取 `AnnotatedItem`，LLM（`annotate_stage2_review.txt`）复核：
- tag 格式（强校验）
- tag 路径复用（避免同义异名，如 `group_theory:群论` vs `groups:群`）
- taxonomy_path 存在性
- question_type 与 category 一致性
- difficulty 结合 content 校准

retry 耗尽 → 降级用 Stage2Annotate 原始 annotation。

`config.stage2_review_enabled=false` 时本阶段为直通；低风险 annotation 可由 `review_conditional_enabled` 条件化跳过。

emit `ReviewedAnnotatedItem` 到 `q_done`。

---

## 8. Stage3Persist：合并入库（无 LLM）

从 `q_done` 取 `ReviewedAnnotatedItem`：

1. `taxonomy_validator.validate(annotation.taxonomy_path)` 精确/前缀/模糊三级匹配修正
2. `tag_tree.insert_path(annotation.tags)` 原子累积（所有 item 集中在此累积，保证一致性）
3. 按 `item.category` 构造 `KnowledgePoint` 或 `Question`，`kp_type` / `question_type` 走 `DisciplineSpec.allowed_*_types` 校验（不在列表时 fallback 到第一个允许类型）
4. 检查 item_id 不倒退；上游 review 拒绝 item 时允许出现 gap，并记录日志
5. `await asyncio.to_thread(storage.save_batch, [kp] if kp else [], [q] if q else [])`
6. mirror `items/persisted/item_<id>.json`
7. state 更新：`last_persisted_item_id` / `stage3_persisted_count`

---

## 9. Retry with feedback

### 9.1 接口

`pipeline/retry.py` 新增：

```python
async def call_llm_json_with_schema_retry_with_feedback(
    llm: LLMClient,
    prompt_builder: Callable[[str], str],   # lambda feedback_text: final_prompt
    *,
    model: type[T],                          # Pydantic BaseModel
    max_attempts: int = 3,
    base_delay: float = 2.0,
    validation_context: dict[str, Any] | None = None,
) -> T:
    last_err = ""
    for attempt in range(1, max_attempts + 1):
        prompt = prompt_builder(last_err)
        try:
            raw = await llm.generate_json(prompt)
            return model.model_validate(raw, context=validation_context)
        except ValidationError as ve:
            last_err = _pretty_validation(ve)  # "- 字段 X.Y [type]: msg"
        except ValueError as ve:
            last_err = str(ve)[:500]
        if attempt < max_attempts:
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
    raise RetryExhausted(max_attempts, last_exc)
```

### 9.2 Prompt 集成

每个 stage prompt 都含 `{retry_feedback}` 占位符：
```
## 上一次输出被拒绝，错误信息（请修正）
{retry_feedback}
```

Stage 在 `prompt_builder(feedback)` 里把 feedback 注入到占位符上。首次调用 feedback 为空串，retry 时注入格式化后的 ValidationError 文本。

---

## 10. 断点续传

### 10.1 State 文件

`data/checkpoint/<file_hash>/state.json` 由 `pipeline/resume_state.py` 管理，`tmp + os.replace` 原子写入 + 每 file_hash `asyncio.Lock` 保护 + 3 秒节流（force=True 时强制刷盘）。

### 10.2 State 字段（`core/models.FileExtractionState`）

```python
class FileExtractionState(BaseModel):
    file_path: str
    file_hash: str
    phase: Literal["running", "completed"]
    schema_version: int = 2   # 不兼容旧版时 raise StateSchemaMismatch

    # Task0
    vlm_batches_done: list[tuple[int, int, int]]
    failed_batches: list[tuple[int, int, int]]
    task0_completed: bool

    # Stage1Extract 运行状态
    current_block_idx: int
    line_offset: int
    line_buffer_carry: list[str]
    next_item_id: int
    current_window_size: int
    consecutive_empty: int

    # 各 stage 计数 / 完成标记
    stage1_extracted_count: int
    stage1_reviewed_count: int
    stage2_annotated_count: int
    stage2_reviewed_count: int
    stage3_persisted_count: int

    stage1_completed: bool
    stage2_completed: bool
    stage3_completed: bool

    last_persisted_item_id: int = -1    # Stage3 保序断言
    failed_blocks: list[int]
    out_of_order_items: list[int]
    tag_tree_json: str
    errors: list[str]
```

**旧 v1 schema（三段式留下）不兼容**：load_state 检测到 `order_index_next` / `saved_item_count` / `task1_saved_items` 等旧字段时 raise `StateSchemaMismatch`，提示 `--restart`。

### 10.3 恢复行为

- Task0：`task0_completed=True` 或 cache 扫描完整时走 `_task0_replay_from_cache`
- Stage1：从 `line_buffer_carry` + `line_offset` + `next_item_id` + `current_window_size` + `consecutive_empty` 恢复
- Stage mirror：从 `items/stage1*` / `stage2*` / `persisted` 重放未消费 payload
- Stage3：启动时 `storage.max_order_index(discipline)` 或共享 `OrderIndexAllocator` 同步 `next_item_id` 起点，避免 id 冲突

---

## 11. 配置参数

| 字段 | 默认值 | 说明 |
|---|---|---|
| `extract_window_lines` | 500 | Stage1Extract 初始窗口 |
| `extract_max_window_lines` | 3000 | 扩窗口上限 |
| `extract_window_expand_step` | 500 | 每次扩窗口步长 |
| `extract_empty_force_limit` | 2 | 达 max 后连续 N 次空 → force consume |
| `stage1_extract_max_retries` | 3 | Stage1Extract LLM retry |
| `stage1_review_enabled` | True | Stage1Review 是否启用 |
| `stage1_review_max_retries` | 2 | Stage1Review LLM retry |
| `stage2_annotate_max_retries` | 3 | Stage2Annotate LLM retry |
| `stage2_review_enabled` | True | Stage2Review 是否启用 |
| `stage2_review_max_retries` | 2 | Stage2Review LLM retry |
| `retry_feedback_enabled` | True | retry 时把 error 注入 prompt |
| `empty_window_fast_skip` | True | 无条目标记窗口零成本跳过 |
| `review_conditional_enabled` | True | Stage1/Stage2 低风险 review 条件化跳过 |
| `stage2_annotate_concurrency` | 1 | Stage2Annotate worker 数（>1 启用 ReorderBuffer） |
| `stage2_review_concurrency` | 1 | Stage2Review worker 数（>1 启用 ReorderBuffer） |
| `pipeline_queue_size` | 4 | Task0 → Stage1Extract q_md 大小 |
| `pipeline_stage_queue_size` | 2 | 各 stage 间 queue 大小 |
| `extract_retry_base_delay` | 2.0 | LLM retry 指数退避基础秒数 |
| `tag_tree_max_depth` | 4 | tag_tree 注入 prompt 深度上限 |
| `tag_tree_max_nodes` | 200 | tag_tree 注入 prompt 节点上限 |
| `checkpoint_flush_interval` | 3.0 | state.json 节流保存秒数 |
| `pages_per_batch` | 5 | Task0 每批页数 |
| `vlm_max_retries` | 3 | Task0 batch 重试次数 |
| `max_concurrent_files` | 4 | 目录模式并发 logical_document 数 |
| `max_concurrent_llm_calls` | 5 | 全局 LLM 并发上限 |

---

## 12. LLM 任务配置

5 阶段共 4 个 LLM 任务，每个都可独立配置 provider/model：

| 任务名 | 阶段 | Fallback 链 |
|---|---|---|
| `vlm_convert` | Task0 | — |
| `extract_and_classify` | Stage1Extract | — |
| `review` | Stage1Review | → extract_and_classify |
| `tag` | Stage2Annotate | → extract_and_classify |
| `annotate_review` | Stage2Review | → tag → extract_and_classify |

见 `config/sciforge.example.toml` 的 `[tasks.*]` 段。

---

## 13. CLI

### 13.1 入口

```bash
uv run sciforge parse run <path> [options]
```

### 13.2 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `path` | — | 文件或目录 |
| `--db` | `data/sciforge.db` | SQLite 路径 |
| `--backend, -b` | `vlm` | `vlm` / `pymupdf` |
| `--pages-per-batch` | 5 | Task0 每批页数 |
| `--extract-window-lines` | 500 | Stage1Extract 初始窗口 |
| `--extract-max-window-lines` | 3000 | 扩窗口上限 |
| `--extract-expand-step` | 500 | 每次扩窗口步长 |
| `--max-retries` | 3 | LLM/VLM 重试次数 |
| `--max-files, -j` | 4 | 目录模式并发 |
| `--restart` | False | 忽略 checkpoint 从头开始（DB schema < v7 时必需） |
| `--dry-run` | False | 不写 DB |
| `--skip-llm` | False | 跳过 LLM 调用（仅识别，整条 pipeline drain） |
| `--llm-config` | `config/sciforge.toml` | TOML 配置 |
| `--llm-provider` / `--llm-model` | None | CLI 覆写 |
| `--discipline, -D` | `math` | 学科 (math/physics/...) |

---

## 14. 数据模型

### 14.1 KnowledgePoint / Question（DB schema v8，详见 Docs/05 顶部 v8 变更）

```python
class KnowledgePoint(BaseModel):
    id: str
    discipline: Discipline
    kp_type: str                       # DisciplineSpec.allowed_kp_types 约束
    number: str
    content: str
    proof_content: str | None
    difficulty: int                    # 1-10
    subject: str
    taxonomy_path: str
    tags: list[str]                    # ["math:数学", "algebra:代数", ...]
    relations: list[ItemRelation]      # 内存字段，不持久化
    source_file: str                   # 源 logical_document 文件路径（用于追溯）
    start_line: int
    end_line: int
    order_index: int                   # = item_id（跨文件全局递增）
    # 相对 v5 删除：source_chapter

class Question(BaseModel):
    # 类似 KP，但有 question_type / solution_content
    # 相对 v5 删除：source_chapter / exam_type / exam_year
    # source_file 保留（v6 曾删除，v7 重新恢复）
```

### 14.2 Stage 间 payload（`core/models.py`）

- `MdBlockSource`：Task0 → q_md
- `ExtractedBatch`：Stage1Extract → q_raw（含原 window_lines + 批量 item_ids/items）
- `ExtractedSingleItem`：旧版单条 payload，保留兼容 checkpoint/代码说明
- `ReviewedItem`：q_raw → Stage1Review → q_rev
- `AnnotatedItem`：q_rev → Stage2Annotate → q_ann
- `ReviewedAnnotatedItem`：q_ann → Stage2Review → q_done → Stage3Persist

### 14.3 LLM schema（`pipeline/llm_schemas.py`）

- `SingleItemExtractLLM`：单条提取条目
- `BatchItemExtractPayloadLLM`：`{items, stop_reason, last_consumed_line, issues}` + exercise near-tail validator
- `BatchReviewPayloadLLM`：`{reviewed_items, issues}`，长度必须与输入 items 对齐
- `SingleItemExtractPayloadLLM`：旧版单条输出 schema，保留兼容说明
- `SingleAnnotationLLM`：单条标注

---

## 15. 验证

```bash
# 1. 清旧 DB + 端到端
rm -rf data/checkpoint data/sciforge.db
uv run sciforge parse run "Raw Data/sample.pdf" --db data/sciforge.db -b vlm -D math

# 2. 检查 batch 文件
ls data/checkpoint/<file_hash>/recognize/file_000_*/

# 3. DB 统计
uv run sciforge parse db-stats data/sciforge.db

# 4. order_index 单调递增 + schema v8 确认
sqlite3 data/sciforge.db "PRAGMA user_version"  # 应为 8
sqlite3 data/sciforge.db ".schema knowledge_points"  # 含 number_canonical/seq_*/content_hash

# 5. 入库后只读体检（提取量/类型双维度/题答绑定/编号/去重/知识图谱边）
uv run sciforge parse validate data/sciforge.db
sqlite3 data/sciforge.db "SELECT order_index, kp_type, number FROM knowledge_points ORDER BY order_index LIMIT 20"

# 5. tag 格式
sqlite3 data/sciforge.db "SELECT tags FROM knowledge_points LIMIT 5"

# 6. 断点恢复：Ctrl+C 后再跑同命令
#    应看到 task0_replay 或 stage1_skip_block_checkpoint 日志，cache 从 items/<stage>/ 重放

# 7. Exercise 漏 solution 回归
#    跑含"习题+后接 solution"的样本，抽查 questions 表里
#    question_type=exercise 类条目的 solution_content 命中率
```

---

## 16. 关键文件路径

| 路径 | 作用 |
|---|---|
| `src/sciforge/pipeline/orchestrator.py` | 6 Task gather 编排器 |
| `src/sciforge/pipeline/recognizers.py` | Recognizer 协议 + 三种实现 |
| `src/sciforge/pipeline/vlm_converter.py` | VLM 多模态识别 |
| `src/sciforge/pipeline/stages/stage1_extract.py` | 批量滑动窗口提取 |
| `src/sciforge/pipeline/stages/stage1_review.py` | 批量 item 校验（修复 exercise bug） |
| `src/sciforge/pipeline/stages/stage2_annotate.py` | 单条标注 |
| `src/sciforge/pipeline/stages/stage2_review.py` | 单条 annotation 审核 |
| `src/sciforge/pipeline/stages/stage3_persist.py` | 合并入库（无 LLM） |
| `src/sciforge/pipeline/llm_schemas.py` | Single* schema + exercise near-tail validator |
| `src/sciforge/pipeline/retry.py` | call_llm_json_with_schema_retry_with_feedback |
| `src/sciforge/pipeline/tag_tree.py` | 层级 tag 树 + prompt 序列化 |
| `src/sciforge/pipeline/taxonomy_validator.py` | taxonomy_path 校验 + 模糊匹配 |
| `src/sciforge/pipeline/resume_state.py` | state.json + SchemaVersionMismatch |
| `src/sciforge/core/sentinel.py` | 共享 SENTINEL 单例 |
| `src/sciforge/core/models.py` | Pydantic 模型 + 5 stage payload |
| `src/sciforge/prompts/templates/pipeline/extract_stage1_initial.txt` | Stage1Extract prompt |
| `src/sciforge/prompts/templates/pipeline/extract_stage1_review.txt` | Stage1Review prompt |
| `src/sciforge/prompts/templates/pipeline/annotate_stage2_initial.txt` | Stage2Annotate prompt |
| `src/sciforge/prompts/templates/pipeline/annotate_stage2_review.txt` | Stage2Review prompt |
| `src/sciforge/prompts/templates/pipeline/vlm_convert.txt` | Task0 VLM prompt |
| `src/sciforge/storage/sqlite_backend.py` | SQLite 存储 + SchemaVersionMismatch |
| `src/sciforge/storage/schema.sql` | DB schema v7 |
| `src/sciforge/cli/parse.py` | CLI 入口 |
| `config/sciforge.toml` | LLM + pipeline TOML 配置 |
