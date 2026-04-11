# 数据处理 Pipeline

> 版本: v6.0 | 日期: 2026-04-11
> 架构: Producer-Consumer 单管道（一对 Task + 一个 queue）

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

> **"识别与提取并发，单 queue 串联，行号全局连续，提取一次入库"**
>
> 1. Producer (Task0) 把每 5 页文档识别为一个 markdown batch，立即推入 queue
> 2. Consumer (Task1) 按到达顺序取 mdblock，累积全局行号到行缓冲
> 3. Task1 每 200 行为一个滑动窗口，**单次 LLM 调用**完成提取 + 校正 + 分类 + 打标签 + 难度评估
> 4. 提取出的条目分配单调递增的 `order_index` 后**直接入库**
> 5. tag 是有序的 `english:中文` 段列表，跨调用通过 tag 子树注入避免同义异名
> 6. 行级 checkpoint 持久化，支持任意时刻断点恢复
> 7. 单 batch / 单窗口失败被隔离，不阻断整体流程

### 2.2 数据流

```
logical_document  (单文件 or *_part_N_M 多分卷)
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ Task0 Producer (vlm_converter / pymupdf / markdown)    │
│   foreach file in logical_document (按文件名顺序):       │
│     foreach 5 页 batch:                                 │
│       if batch in vlm_batches_done:                    │
│         read cached page_NNN-NNN.md (1-based)          │
│       else:                                            │
│         调用 VLM/pymupdf 识别（with_retry max=3）         │
│         保存 page_NNN-NNN.md + 更新 state.vlm_batches_done│
│       await queue.put(MdBlockSource(file_idx, pages, md))│
│   await queue.put(SENTINEL)                            │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼  asyncio.Queue (FIFO, maxsize=4)
                           │
┌──────────────────────────▼─────────────────────────────┐
│ Task1 Consumer (Task1Extractor)                        │
│   restore state: tag_tree, line_offset, line_buffer,   │
│                  order_index, current_block_idx        │
│   while not sentinel:                                  │
│     msg = await queue.get()                            │
│     if isinstance(msg, MdBlockSource):                 │
│       skip if msg.block_index < current_block_idx       │
│       annotate lines with global line numbers          │
│       line_buffer.extend(annotated)                    │
│     while len(buffer) >= 200 or is_last:               │
│       window = buffer[:200]                            │
│       llm_out = await extract_and_classify(            │
│         window=window, tag_subtree=tag_tree.serialize())│
│       if trunc == "incomplete": break (等下一个 block)   │
│       items = llm_out.items                            │
│       for item in items:                               │
│         item.order_index = order_index; order_index++  │
│         tag_tree.insert_path(item.tags)                │
│       storage.save_batch(kps, qs)                      │
│       buffer = buffer[consumed:]                       │
│       line_offset += consumed                          │
│     save_state(...)  # 行级 checkpoint                 │
└────────────────────────────────────────────────────────┘
```

### 2.3 关键属性

- **并发但有序**：Producer 与 Consumer 并发执行（VLM 识别和 LLM 提取重叠），但 queue 严格 FIFO，全局行号跨 mdblock 连续
- **背压**：`asyncio.Queue(maxsize=4)`，Producer 产出过快时自动阻塞，避免内存膨胀
- **`order_index` 单调递增**：作为后续构建知识图谱的天然顺序
- **单一 LLM 调用**：合并提取 + 校正 + 分类 + 打标签 + 难度评估五合一
- **行级断点**：state 记录 `current_block_idx` + `line_offset` + `line_buffer_carry` + `order_index_next` + `tag_tree_json`，重启时无缝恢复
- **节流保存**：state.json 默认 3 秒节流刷盘（`checkpoint_flush_interval`），异常/完成时强制刷盘，避免长文件高频磁盘写
- **异常隔离**：Producer 单 batch 失败 → 加入 failed_batches，跳过；Consumer 单窗口失败 → 加入 failed_blocks，跳过
- **并发 DB 写入**：目录模式每个 logical_document 在 `process_file` 内部构造独立的 `SQLiteStorageBackend` 实例（WAL + `busy_timeout=5000ms`），避免共享 sqlite3 connection 的线程风险
- **回滚一致性**：入库失败时 `order_index` 与 `tag_tree` 整体回滚到本次批次开始前的快照，确保重启后 tag_tree 与已入库条目一致

---

## 3. Task0 Producer：文档识别

### 3.1 Recognizer 协议

`pipeline/recognizers.py` 提供统一的异步生成器接口：

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
```

### 3.2 已实现的 Recognizer

| 类 | 适用格式 | 特点 |
|---|---|---|
| `VlmRecognizer` | PDF / DOCX | 多模态视觉识别（最高精度），保留 LaTeX 公式与图片裁剪 |
| `PyMuPdfRecognizer` | PDF | 纯文本提取，无 LLM，速度最快 |
| `MarkdownRecognizer` | MD / TeX | 整文件单 batch，跳过识别 |

工厂函数：`get_recognizer(format, backend, llm_client, config)`

### 3.3 落盘约定

```
data/checkpoint/<file_hash>/
└── recognize/
    ├── file_000_<stem>/
    │   ├── page_001-005.md       # 1-based 页号
    │   ├── page_006-010.md
    │   └── ...
    ├── file_001_<stem>/           # 第二个分卷（如有）
    │   └── ...
    └── ...
```

### 3.4 重试与失败隔离

- 每个 batch 调用 `with_retry(max_attempts=vlm_max_retries=3, base_delay=2.0)`
- 重试用尽 → log error + 加入 `state.failed_batches`，继续下一个 batch
- 失败 batch 不会推入 queue，Consumer 不感知缺页

---

## 4. Task1 Consumer：滑动窗口提取

### 4.1 行号注入

每个 mdblock 进入 buffer 前，每行被加上全局行号前缀：

```
   123 | 这是第123行的内容
   124 | 这是第124行的内容
```

跨 mdblock 行号连续，不会重置。

### 4.2 滑动窗口

```python
WINDOW = config.extract_window_lines  # 默认 200

while len(buffer) >= WINDOW or is_last:
    window = buffer[:WINDOW]
    llm_out = await extract_and_classify(window, tag_subtree)
    
    if llm_out.truncation_line == "incomplete":
        break  # 等下一个 mdblock 拼进 buffer
    
    if isinstance(llm_out.truncation_line, int):
        # LLM 指定截断行号，保留结束行（句号断句问题）
        cut_idx = find_buffer_idx(llm_out.truncation_line)
        consumed = cut_idx
    else:
        consumed = len(window)
    
    save_items(llm_out.items)
    buffer = buffer[consumed:]
    line_offset += consumed
```

### 4.3 单次 LLM 调用

`extract_and_classify` prompt 模板见 `prompts/templates/pipeline/extract_and_classify.txt`，输出 JSON 包含：

```json
{
  "truncation_line": null | int | "incomplete",
  "items": [
    {
      "category": "knowledge" | "question",
      "item_type": "theorem" | "example" | ...,
      "question_type": null | "proof" | "calculation" | ...,
      "number": "3.1",
      "structural_context": {
        "chapter_title": "...", "chapter_number": "3",
        "section_title": "...", "section_number": "3.2"
      },
      "corrected_content": "...",
      "corrected_proof": "..." | null,
      "corrected_solution": "..." | null,
      "subject": "real_analysis:实分析",
      "taxonomy_path": "math.analysis.continuity",
      "difficulty": 6,
      "tags": [
        "math:数学", "analysis:数学分析", "continuity:连续性",
        "uniform_continuity:一致连续"
      ],
      "methods": ["borel_covering:Borel覆盖"],
      "overall_quality": "A" | "B" | "C" | "D" | "F",
      "start_line": 12,
      "end_line": 28
    }
  ]
}
```

**关键约定**：
- proof / solution 嵌入父条目（不作独立 item）
- `start_line` / `end_line` / `truncation_line` 都是输入中显示的**全局行号**
- `tags` 是有序段列表，从领域到关键词，每段必须 `english:中文` 格式
- `category=question` 时填 `question_type`，knowledge 时为 null
- `quality_grade=F` 的 item 直接丢弃，不入库

### 4.4 入库

```python
for item in items:
    if item["overall_quality"] == "F":
        continue
    item.order_index = self.order_index
    self.order_index += 1
    self.tag_tree.insert_path(item["tags"])
    if item["category"] == "question":
        qs.append(build_question(item, current_block, oi))
    else:
        kps.append(build_kp(item, current_block, oi))

await asyncio.to_thread(storage.save_batch, kps, qs)
state.saved_item_count += len(kps) + len(qs)
```

---

## 5. Tag 分级系统

### 5.1 数据约定

- 每个 tag = 单一 `english:中文` 段（如 `math:数学`、`group_theory:群论`）
- 一个条目的 `tags: list[str]` 是**有序段列表**，从领域 (root) 到关键词 (leaf)
- english 部分：`[a-z][a-z0-9_]*` ；中文部分：非空，不含空白/冒号/斜杠
- 多个条目的路径合并成一棵 `TagTree` 维护在 Task1 内部状态中

### 5.2 Prompt 注入

每次 LLM 调用前调用 `tag_tree.serialize_for_prompt(max_depth, max_nodes)` 序列化为缩进 markdown：

```
- math:数学 (45)
  - algebra:代数 (20)
    - group_theory:群论 (12)
      - normal_subgroup:正规子群 (5)
      - quotient_group:商群 (3)
    - linear_algebra:线性代数 (8)
  - analysis:数学分析 (15)
    - sequences:数列 (4)
    - continuity:连续性 (6)
```

`max_nodes` 超限时按 `item_count` 降序保留最热子树，截断处用 `- ...` 占位。LLM 被指示**优先复用**子树中的现有路径，避免 `group:群` vs `groups:群论` 这样的同义异名。

### 5.3 持久化

`TagTree.to_json()` 写入 `state.tag_tree_json`，重启时 `TagTree.from_json()` 恢复。

---

## 6. 断点续传

### 6.1 State 文件

`data/checkpoint/<file_hash>/state.json` 由 `pipeline/resume_state.py` 管理，使用 `tmp + os.replace` 原子写入，并通过每个 file_hash 的 `asyncio.Lock` 保护并发写入。默认使用 3 秒节流刷盘（`min_interval_sec=3.0`，可通过 `config.checkpoint_flush_interval` 调整），Producer 结束、Consumer run 完成、异常捕获等关键出口处 force=True 强制刷盘，最坏丢失不超过一个节流窗口的状态。

### 6.2 State 字段

```python
class FileExtractionState(BaseModel):
    file_path: str
    file_hash: str
    phase: Literal["running", "completed"]
    
    # Producer 端
    vlm_batches_done: list[tuple[int, int, int]]  # (file_idx, page_start, page_end)
    failed_batches: list[tuple[int, int, int]]
    
    # Consumer 端
    current_block_idx: int          # 已完成入库的最后一个 block_index + 1
    line_offset: int                # 已消费 + commit 的全局行数
    line_buffer_carry: list[str]    # buffer 中已注入行号的剩余行
    order_index_next: int           # 下一个 item 应分配的 order_index
    tag_tree_json: str
    saved_item_count: int
    failed_blocks: list[int]
    errors: list[str]
```

### 6.3 持久化时机

- Producer：每完成一个 batch 写盘后立即更新 `vlm_batches_done` 并保存 state
- Consumer：每次 `_store_items` 成功 commit DB 后更新所有 Consumer 字段并保存 state
- 失败时也写一次 state（携带 failed_blocks/failed_batches）

### 6.4 恢复行为

- Producer 重启：`vlm_batches_done` 中已识别的 batch 直接读盘上的 `page_NNN-NNN.md` 并 yield，跳过 LLM 调用
- Consumer 重启：跳过 `current_block_idx` 之前的所有 block；从 `line_buffer_carry` + `line_offset` + `tag_tree_json` + `order_index_next` 无缝恢复内部状态

---

## 7. 异常处理与重试

### 7.1 重试工具

`pipeline/retry.py` 提供：
- `with_retry(fn, max_attempts, base_delay, on_retry)`：通用异步重试 + 指数退避
- `call_llm_json_with_retry(llm, prompt, validate=...)`：LLM JSON 调用 + 输出验证
- `RetryExhausted` 异常

### 7.2 异常隔离边界

| 边界 | 失败处理 |
|---|---|
| Producer 单 batch | retry 3 次失败 → log + 加入 `failed_batches` + 跳过该 batch |
| Producer 整个 Task0 | catch → log + put SENTINEL + 加入 `result.errors` |
| Consumer 单 LLM 调用 | retry 3 次失败 → 抛 `RetryExhausted` |
| Consumer 单窗口 | catch `RetryExhausted` → 加入 `failed_blocks` + 跳过该窗口 |
| Consumer 单次 save_batch | catch → log + 回滚 order_index + 写入 errors，下次重试 |
| Consumer 顶层 | catch → log + 写 state + 异常向 orchestrator 抛回 |
| 单 logical_document | `process_file` 顶层 catch → 写 errors.json + 不影响目录其他 doc |

---

## 8. 数据模型

### 8.1 KnowledgePoint / Question 字段（与 DB 一一对应）

```python
class KnowledgePoint(BaseModel):
    id: str
    kp_type: KPType                 # definition/theorem/lemma/...
    number: str
    content: str                    # 正文
    proof_content: str | None       # 证明嵌入
    proof_methods: list[str]
    difficulty: int                 # 1-10
    subject: str
    taxonomy_path: str
    tags: list[str]                 # ["math:数学", "algebra:代数", ...]
    source_file: str
    source_chapter: str
    source_section: str
    # 位置 / 顺序
    start_line: int
    end_line: int
    order_index: int                # 文件内全局递增（用于知识图谱构建）
    page_start: int | None
    page_end: int | None
    quality_grade: str              # A/B/C/D
    created_at: datetime

class Question(BaseModel):
    # 与 KP 类似，但有 question_type/solution_content/solution_methods/exam_type/exam_year/...
```

### 8.2 Pipeline 中间数据

- `MdBlockSource`：Producer 推入 queue 的单元，含 `file_path/page_start/page_end/md_path/md_text/block_index`
- `FileExtractionState`：断点恢复核心对象（详见 §6.2）
- `StoredItemBatch`：Task1 一次循环的入库产物，用于日志/进度

---

## 9. CLI

### 9.1 入口

```bash
uv run sciforge parse run <path> [options]
```

### 9.2 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `path` | — | 文件或目录 |
| `--db` | `data/sciforge.db` | SQLite 路径 |
| `--backend, -b` | `vlm` | `vlm` / `pymupdf` |
| `--pages-per-batch` | 5 | Phase A 每批页数 |
| `--extract-window-lines` | 200 | Task1 滑动窗口行数 |
| `--max-retries` | 3 | LLM/VLM 重试次数 |
| `--max-files, -j` | 4 | 目录模式并发 logical_document 数 |
| `--restart` | False | 忽略 checkpoint 从头开始 |
| `--dry-run` | False | 不写 DB |
| `--skip-llm` | False | 跳过 LLM 调用（仅识别） |
| `--llm-config` | `config/sciforge.toml` | TOML 配置 |
| `--llm-provider` / `--llm-model` | None | CLI 覆写 |

### 9.3 配置文件

`config/sciforge.toml` 至少需要两个任务段：

```toml
[llm.tasks.vlm_convert]
provider = "openai_compatible"
model = "z-ai/glm-5v-turbo"   # 必须支持图像输入
base_url = "https://openrouter.ai/api/v1"

[llm.tasks.extract_and_classify]
provider = "openai_compatible"
model = "z-ai/glm-5.1"
base_url = "https://openrouter.ai/api/v1"
max_tokens = 32768            # JSON 输出量较大
```

---

## 10. 验证

```bash
# 端到端
uv run sciforge parse run "Raw Data/sample.pdf" --db data/test.db -b vlm

# 检查 batch 文件
ls data/checkpoint/<file_hash>/recognize/file_000_*/

# DB 统计
uv run sciforge parse db-stats data/test.db

# 检查 order_index 单调
sqlite3 data/test.db "SELECT order_index, kp_type, number FROM knowledge_points ORDER BY order_index LIMIT 20"

# 检查 tag 格式
sqlite3 data/test.db "SELECT tags FROM knowledge_points LIMIT 5"
```

### 10.1 断点续传验证

中断（Ctrl+C）后再次运行同一命令：日志应显示 `task1_skip_block_checkpoint`，已识别 batch 直接从 cache 加载。

### 10.2 多文件分卷

把 `book_part_1_3.pdf` / `book_part_2_3.pdf` / `book_part_3_3.pdf` 放在同一目录，跑 `sciforge parse run <dir>`，三个文件应被合并为单个 logical_document，page_start 跨文件全局连续。

---

## 11. 关键文件路径

| 路径 | 作用 |
|---|---|
| `src/sciforge/pipeline/orchestrator.py` | Producer-Consumer 编排器 |
| `src/sciforge/pipeline/recognizers.py` | Recognizer 协议 + 三种实现 |
| `src/sciforge/pipeline/vlm_converter.py` | VLM 多模态识别 + recognize_in_batches |
| `src/sciforge/pipeline/task1_extractor.py` | Consumer：滑动窗口 + LLM 提取 + 入库 |
| `src/sciforge/pipeline/tag_tree.py` | 层级 tag 树 + prompt 序列化 |
| `src/sciforge/pipeline/resume_state.py` | state.json 加载 / 保存 / 锁 |
| `src/sciforge/pipeline/retry.py` | 异步重试 + LLM JSON 重试 |
| `src/sciforge/prompts/templates/pipeline/extract_and_classify.txt` | Task1 主 prompt |
| `src/sciforge/prompts/templates/pipeline/vlm_convert.txt` | Phase A VLM prompt |
| `src/sciforge/storage/sqlite_backend.py` | SQLite 存储 |
| `src/sciforge/storage/schema.sql` | DB schema |
| `src/sciforge/core/models.py` | 所有 Pydantic 模型 |
| `src/sciforge/cli/parse.py` | CLI 入口 |
| `config/sciforge.toml` | LLM TOML 配置 |
