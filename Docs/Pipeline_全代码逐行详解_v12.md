# SciForge Pipeline 代码深度解读（v12）

> **生成时间**：2026-06-17  
> **项目路径**：`/Users/fhgs/Documents/MathForge`  
> **覆盖范围**：`src/sciforge/pipeline/` 全部 25 个源文件 + `core/models.py` + `core/sentinel.py`  
> **阅读目标**：理解**设计思想**和**设计动机**，掌握**具体代码功能和逻辑**，快速上手修改

---

## 目录

1. [全局架构：5阶段并发Producer-Consumer](#1-全局架构5阶段并发producer-consumer)
2. [核心数据模型：队列里传什么](#2-核心数据模型队列里传什么)
3. [SENTINEL：流结束信号](#3-sentinel流结束信号)
4. [编排器：PipelineOrchestrator](#4-编排器pipelineorchestrator)
5. [Task0 Producer：文档识别](#5-task0-producer文档识别)
6. [VLM转换器：PDF→Markdown](#6-vlm转换器pdfmarkdown)
7. [Stage1Extract：滑动窗口批量提取](#7-stage1extract滑动窗口批量提取)
8. [Stage1Review：批量校验](#8-stage1review批量校验)
9. [Stage2Annotate：单条标注](#9-stage2annotate单条标注)
10. [Stage2Review：标注审核](#10-stage2review标注审核)
11. [Stage3Persist：合并入库](#11-stage3persist合并入库)
12. [LLM Schema：Pydantic强校验体系](#12-llm-schemapydantic强校验体系)
13. [Retry机制：带反馈的重试](#13-retry机制带反馈的重试)
14. [断点恢复：State管理](#14-断点恢复state管理)
15. [TagTree：标签树管理](#15-tagtree标签树管理)
16. [TagCanonicalizer：标签归一化](#16-tagcanonicalizer标签归一化)
17. [TaxonomyValidator：分类路径校验](#17-taxonomyvalidator分类路径校验)
18. [ReorderBuffer：并发保序](#18-reorderbuffer并发保序)
19. [DocumentSegmenter与ParallelExtract：段级并发](#19-documentsegmenter与parallelextract段级并发)
20. [后处理模块：确定性Post-Pass](#20-后处理模块确定性post-pass)
21. [设计总评：架构决策与权衡](#21-设计总评架构决策与权衡)

---

## 1. 全局架构：5阶段并发Producer-Consumer

### 1.1 设计动机

SciForge的核心任务是把任意格式的STEM教材/试卷/讲义（PDF、TeX、Markdown、DOCX）**自动提取为结构化的知识图谱数据库**。这个任务天然适合流水线化：

1. **异构输入**：PDF扫描件 vs 电子版文本 vs Markdown，需要不同的识别策略
2. **逐层抽象**：原始文字 → 条目提取 → 语义标注 → 入库查询，每层依赖前一层
3. **LLM昂贵且慢**：每次LLM调用都需要网络延迟+token成本，需要最大化每次调用的信息密度
4. **容错要求极高**：LLM输出不稳定，网络波动，内容审核误判——任何一个环节失败都不能丢失已处理的数据

### 1.2 架构全景

```
CLI: sciforge parse run <file_or_dir>
  ↓
PipelineOrchestrator.process_file()
  ↓ 创建 5 个 asyncio.Queue，启动 6 个 asyncio.Task

Task0 Producer (Recognizer) ──q_md──→ Stage1Extract ──q_raw──→ Stage1Review
                                                                     ──q_rev──→ Stage2Annotate
                                                                               ──q_ann──→ Stage2Review
                                                                                         ──q_done──→ Stage3Persist
                                                                                                           ↓
                                                                                                    SQLite + FTS5
                                                                                                           ↓
                                                                             Post-Pass (编号/题答/关系/复核)
```

### 1.3 设计决策

| 决策 | 理由 | 代价 |
|------|------|------|
| **asyncio而非多进程** | LLM调用是IO密集型，asyncio足以并发；多进程会带来序列化开销和共享状态复杂度 | 单核CPU受限（但实际瓶颈在LLM API延迟） |
| **5个Queue解耦** | 各Stage独立伸缩、独立失败、独立重试。Stage1慢不会阻塞Stage2（有queue缓冲） | 内存占用（每个queue有一份数据副本） |
| **SENTINEL流结束信号** | 用专用单例而非None，类型检查更安全；与合法payload绝对不冲突 | 每个Stage的finally都要put(SENTINEL) |
| **Stage2可N-worker并发** | 单条LLM标注是最慢的环节（每项一次API调用），N-worker+ReorderBuffer可以实现线性加速 | ReorderBuffer引入复杂度，gap处理需要skip |
| **Post-Pass全是确定性规则** | 编号审计、题答回填、关系推断不需要LLM，纯规则更快更可靠 | 规则可能覆盖不全（已通过编号白名单+正则覆盖中英文场景） |

### 1.4 核心不变量

1. **`item_id == order_index`**，全局单调递增，每个条目在其源文档中唯一
2. **流结束保证**：任一Stage异常 → `finally` 块向下游queue put `SENTINEL`，杜绝死锁
3. **断点恢复**：`FileExtractionState` + mirror `items/<stage>/item_*.json` 双保险
4. **Stage3保序断言**：`item_id` 只允许gap（上游拒绝），不允许backward（FIFO被破坏）
5. **Tag一致性**：Stage3集中累积tag_tree，Stage2只读快照，Stage3做canonicalize+validate

---

## 2. 核心数据模型：队列里传什么

**文件**：`src/sciforge/core/models.py`（865行）—— 整个项目所有Pydantic模型的唯一定义点

### 2.1 设计哲学

SciForge采用**Pydantic v2**作为数据模型的唯一来源。这不是简单的"用个ORM"，而是有深刻的架构考量：

1. **全链路类型安全**：从LLM输出的JSON到Stage间queue传递的payload，再到入库的DB记录，每一步都经过Pydantic的`model_validate`校验，**不合格的数据绝不会静默流入下一环节**。
2. **`validate_assignment=True`**：任何时候修改字段都会触发校验（如`number_canonical`被自动派生后不会产生非法值）。
3. **`@model_validator(mode="after")`**：跨字段一致性校验的自然位置。比如`solution_status="complete"`与`corrected_proof is None`的矛盾会在构造函数阶段直接抛出`ValidationError`，不会进入queue。
4. **内存字段约定**：`relations: list[ItemRelation]`标记为不入库，只在内存中传递，关系表独立存储。

### 2.2 顶层枚举

```python
# models.py:17-28
class Discipline(StrEnum):
    MATH = "math"
    PHYSICS = "physics"
    CHEMISTRY = "chemistry"
    BIOLOGY = "biology"
    OTHER = "other"
```

**设计意图**：`Discipline`是**编译时**路由键——新增学科必须改代码，不能在运行时配置。这是故意的：每个学科有独立的`DisciplineSpec`（tag别名表、taxonomy树、allowed_kp_types等），运行时发现未知学科意味着配置缺失，应尽早失败而非静默降级。

```python
# models.py:82-98
class RelationType(StrEnum):
    PREREQUISITE = "prerequisite"
    SUCCESSOR = "successor"
    COROLLARY_OF = "corollary_of"
    # ... 共15种关系
```

`RelationType`用于`relation_inference.py`构建知识图谱边。新增关系类型只需加一行。

### 2.3 KnowledgePoint 与 Question

```python
# models.py:117-155
class KnowledgePoint(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    discipline: Discipline = Discipline.MATH
    kp_type: str = "theorem"
    number: str = ""              # 原文逐字题号
    number_canonical: str = ""    # 规范化题号，自动派生
    seq_chapter: int | None = None
    seq_section: int | None = None
    seq_index: int | None = None
    content: str = ""
    proof_content: str | None = None
    difficulty: int = Field(ge=1, le=10, default=5)
    subject: str = ""
    taxonomy_path: str = ""
    tags: list[str] = Field(default_factory=list)  # ["math:数学","algebra:代数",...]
    relations: list[ItemRelation] = Field(default_factory=list)  # 内存字段
    source_file: str = ""
    start_line: int = 0
    end_line: int = 0
    order_index: int = 0
```

**`number` vs `number_canonical`**：`number`保留原文逐字题号（如"例 3.2"、"第1题"、"习题三A.5"），`number_canonical`自动派生为机器可比较的规范化形式（如"3.2"）。派生逻辑：
```python
# models.py:142-155
@model_validator(mode="after")
def _derive_numbering(self) -> KnowledgePoint:
    if self.number and not self.number_canonical:
        object.__setattr__(self, "number_canonical", canonicalize_number(self.number))
    if self.number and self.seq_index is None:
        ch, sec, idx = parse_number_components(self.number)
        object.__setattr__(self, "seq_chapter", ch)
        ...
```

**为什么用`object.__setattr__`绕过校验？** 因为`validate_assignment=True`，普通赋值`self.number_canonical = ...`会再次触发所有validator，包括`_derive_numbering`自身——无限递归。`object.__setattr__`直接绕过Pydantic的赋值钩子。

**Question与KnowledgePoint的区别**：
- `kp_type`（如theorem/definition/lemma） vs `question_type`（如proof/calculation/construction）
- `proof_content`（证明） vs `solution_content`（解答）
- Question额外有`options`/`correct_option`/`blanks`（选择题/填空题的结构化字段）

`question_type`（认知类别）和`question_form`（题目形式）**正交**：一道选择题在形式上是`choice`，认知类别上可能是`calculation`。

### 2.4 Pipeline中间Payload

```python
# models.py:629-640
class MdBlockSource(BaseModel):
    """Task0输出的单个批次（默认5页PDF = 1个block）"""
    file_path: str
    page_start: int       # 1-based
    page_end: int
    md_path: str
    md_text: str
    block_index: int      # 在logical_document中的全局序号
    section_boundaries: list[tuple[int, int, str]] = Field(default_factory=list)
    # (line_no, level, title)，用于structural_context注入
```

```python
# models.py:730-752
class ExtractedBatch(BaseModel):
    """Stage1Extract→Stage1Review的批量payload"""
    item_ids: list[int]
    items: list[SingleItemExtractLLM]
    window_lines: list[str]        # 原窗口全文（带全局行号前缀）
    window_line_range: tuple[int, int]
    window_last_line: int          # 供near-tail校验
    block_index: int
    source_file: str
    stop_reason: str = "all_complete"
```

**`stop_reason`是关键信号**：只有`"all_complete"`且无其他风险信号时，Stage1Review才会跳过review。`"incomplete_at_tail"`表示窗口末尾有未闭合条目，上游会扩窗重提。

```python
# models.py:643-689
class FileExtractionState(BaseModel):
    """断点续传的核心状态对象 —— 持久化到state.json"""
    file_path: str = ""
    file_hash: str = ""
    phase: Literal["running", "completed"] = "running"
    schema_version: int = 2          # 2 = 当前5-stage pipeline

    # Task0端
    vlm_batches_done: list[tuple[int, int, int]] = Field(default_factory=list)
    failed_batches: list[tuple[int, int, int]] = Field(default_factory=list)
    task0_completed: bool = False

    # Stage1Extract端
    current_block_idx: int = 0
    line_offset: int = 0            # 全局行号偏移
    line_buffer_carry: list[str] = Field(default_factory=list)
    next_item_id: int = 0
    current_window_size: int = 500
    consecutive_empty: int = 0      # 连续空窗口计数

    # 各Stage计数
    stage1_extracted_count: int = 0
    stage1_reviewed_count: int = 0
    stage2_annotated_count: int = 0
    stage2_reviewed_count: int = 0
    stage3_persisted_count: int = 0
    stage3_deduped_count: int = 0

    stage1_completed: bool = False
    stage2_completed: bool = False
    stage3_completed: bool = False

    last_persisted_item_id: int = -1
    failed_blocks: list[int] = Field(default_factory=list)
    out_of_order_items: list[int] = Field(default_factory=list)
    tag_tree_json: str = ""
    errors: list[str] = Field(default_factory=list)
```

**`schema_version=2`的设计意义**：硬编码兼容性检查。`resume_state.py`加载state时若发现旧版（`schema_version < 2`或存在legacy字段），直接抛出`StateSchemaMismatch`，**拒绝恢复**，要求用户`--restart`清checkpoint重跑。这样做避免了向后兼容的复杂性——与其花时间写迁移代码，不如保证state schema的正确性。

---

## 3. SENTINEL：流结束信号

**文件**：`src/sciforge/core/sentinel.py`（17行）

```python
class _Sentinel:
    __slots__ = ()
    def __repr__(self) -> str:
        return "<SENTINEL>"

SENTINEL = _Sentinel()
```

**设计思想**：极简到极致的设计。`__slots__ = ()`表示实例无任何属性——这是纯标记类型，只用于`isinstance(msg, _Sentinel)`身份检查。

**为什么不用`None`？** `None`可能是合法的payload（虽然当前没有），但设计上要防御未来某个Stage产出`None`作为"跳过"信号。`_Sentinel`永远不会与任何合法payload混淆。

**使用契约**：每个Stage的`finally`块都必须执行`await out_queue.put(SENTINEL)`。这是**硬约束**——违反会导致下游永远阻塞在`await queue.get()`。`PipelineOrchestrator.process_file()`的异常处理（`except BaseException`）也会兜底塞SENTINEL到所有queue。

---

## 4. 编排器：PipelineOrchestrator

**文件**：`src/sciforge/pipeline/orchestrator.py`（1246行）—— 整个流水线的中枢神经

### 4.1 辅助数据结构

```python
# orchestrator.py:84-97
@dataclass
class PipelineResult:
    """单个logical_document的处理结果"""
    file_path: str = ""
    file_hash: str = ""
    pages_recognized: int = 0
    blocks_pushed: int = 0
    items_saved: int = 0
    completed: bool = False
    failed_batches: list[tuple[int, int, int]] = field(default_factory=list)
    failed_blocks: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
```

`PipelineResult`是`process_file()`的返回值，汇总结果供CLI或`process_directory()`使用。

```python
# orchestrator.py:158-182
class OrderIndexAllocator:
    """跨文件全局单调order_index分配器"""
    def __init__(self, start: int) -> None:
        self._next = start
        self._lock = asyncio.Lock()

    async def reserve(self, count: int) -> int:
        async with self._lock:
            base = self._next
            self._next += max(count, 1)
            return base
```

**目录并发时的关键设计**：多个`process_file()`共享同一个`OrderIndexAllocator`，避免不同文件从相同的`max_order_index`起步导致ID冲突。`count=0`时仍推进1，用于获取初始起点。

```python
# orchestrator.py:111-153
class NoopStorageBackend:
    """dry-run存储后端：执行完整pipeline但不写数据库"""
```

dry-run模式的设计目的：测试prompt效果或估算成本，避免污染生产数据库。所有方法签名与真实backend一致，但操作都是no-op。

### 4.2 `__init__` —— 初始化

```python
# orchestrator.py:194-220
def __init__(self, config, llm_client, storage, db_path,
             llm_clients, pdf_backend, discipline):
    self.config = config
    self.llm = llm_client
    self.storage = storage
    self.db_path = db_path
    self._llm_clients = llm_clients or {}
    self._pdf_backend = pdf_backend
    self.discipline = discipline

    # 启用pipeline日志（每个LLM调用的prompt/output/token usage）
    for c in self._llm_clients.values():
        c.enable_pipeline_logging(log_dir, log_interval)
```

**多LLM client设计**：`_llm_clients`是一个`dict[str, LLMClient]`，key是任务名（`"extract_and_classify"`、`"review"`、`"tag"`等）。每个任务可配置独立的provider/model+cost预算。这允许"强模型提取+廉价模型校验"的混合策略。

### 4.3 `process_file()` —— 核心流程

这是整个pipeline的入口方法，按**严格的顺序**执行以下11个步骤：

**步骤1-2：文件哈希 + State加载**
```python
# orchestrator.py:245-276
file_hash = compute_file_hash(files)
state = load_state(checkpoint_dir, file_hash) or FileExtractionState(...)

if is_state_complete(state):
    result.items_saved = state.stage3_persisted_count
    result.completed = True
    return result  # 已完成→秒退
```

`compute_file_hash()`对多文件按传入顺序拼接hash，所以**分卷顺序变化=不同hash=不同checkpoint**，这是安全设计：防止分卷顺序错乱导致resume错误。

**步骤3：按任务分配LLM**
```python
# orchestrator.py:279-282
extract_llm = self._get_llm("extract_and_classify")
review_llm = self._get_llm("review") or extract_llm       # 回退链
annotate_llm = self._get_llm("tag") or extract_llm
annotate_review_llm = self._get_llm("annotate_review") or annotate_llm
```

fallback链设计：如果用户只配置了`extract_and_classify`，review/tag/annotate_review自动降级使用同一个client。

**步骤4：TagTree加载（三来源优先级）**
```python
# orchestrator.py:285-302
shared_tag_tree = TagTree()
if state.tag_tree_json:
    shared_tag_tree = TagTree.from_json(state.tag_tree_json)  # 1. state优先
elif isinstance(storage_for_pipeline, SQLiteStorageBackend):
    tree_json = storage_for_pipeline.load_discipline_tag_tree(discipline)
    if tree_json:
        shared_tag_tree = TagTree.from_json(tree_json)        # 2. DB次之
    # 3. 空树兜底
```

**步骤5-6：ID起点 + Queue构造**
```python
# orchestrator.py:305-324
if order_index_allocator is not None:
    pass  # 目录并发模式：Stage1动态reserve
elif isinstance(storage_for_pipeline, SQLiteStorageBackend):
    db_max = storage_for_pipeline.max_order_index(discipline)
    state.next_item_id = max(state.next_item_id, db_max + 1)

# 构造5个Queue
q_md: asyncio.Queue = asyncio.Queue(maxsize=pipeline_queue_size)     # 较大(16)
q_raw = q_rev = q_ann = q_done = asyncio.Queue(maxsize=stage_queue_size)  # 较小(2)
```

**`q_md`的maxsize较大**：Task0识别速度快于Stage1提取，需要更多缓冲。
**Stage queue的maxsize较小**：形成背压——下游慢时上游阻塞在`put()`，避免内存爆炸。

**步骤7：构造Stage实例并启动Task（从下游到上游）**
```python
# orchestrator.py:335-423
# 关键：先启动消费者，再启动生产者
s3 = Stage3Persist(storage, ..., tag_tree=shared_tag_tree)
tasks.append(asyncio.create_task(s3.run(q_done, state)))     # 1. 最下游先启动

await self._replay_stage_outputs(file_hash, "stage2_review", ...)  # 重放已完成items
tasks.append(asyncio.create_task(s2_rev.run(q_ann, q_done, state)))  # 2.
# ...依次向上游...
tasks.append(asyncio.create_task(s1_ex.run(q_md, q_raw, state)))  # 5. 最上游最后启动
```

**为什么从下游到上游启动？** 如果先启动上游Stage1Extract，它产出items后下游消费者还没ready，items堆积在queue中可能触发`maxsize`阻塞。**先启动消费者确保queue有人消费**。

**TagTreeSnapshot（REC-09优化）**：
```python
# orchestrator.py:357-362
tag_tree_view = TagTreeSnapshot(
    shared_tag_tree,
    refresh_gap=16  # 每16次insert_path才刷新快照
)
```
Stage3持续向live树`insert_path`，每次都会bump `_generation`。若Stage2直接读live树，`serialize_for_prompt`的缓存每次都失效，每条item都要重新序列化整棵树。`TagTreeSnapshot`把live周期性冻结为独立只读副本，缓存稳定命中，读写隔离。

**步骤8：Task0 Producer（识别或Replay）**
```python
# orchestrator.py:426-444
if self._is_cache_complete(files, state):
    tasks.append(asyncio.create_task(
        self._task0_replay_from_cache(files, state, q_md, result)))
else:
    tasks.append(asyncio.create_task(
        self._task0_producer(files, state, q_md, result)))
```

`_is_cache_complete()`检查checkpoint是否已有完整识别结果：
- Markdown/TeX：只需1个batch
- PDF：batch必须连续覆盖全部页码

**步骤9：await全部Task**
```python
# orchestrator.py:447
await asyncio.gather(*tasks)
```

**步骤10-10.8：入库后确定性Post-Pass（无LLM，可独立开关）**
```python
# 10. tag_tree回写DB（跨文件累积）
storage_for_pipeline.save_discipline_tag_tree(discipline, shared_tag_tree)

# 10.5 编号连续性核对（#2/#8召回兜底，不阻断入库）
rows = await asyncio.to_thread(storage.numbering_rows, ...)
report = audit_numbering(rows)
write_numbering_report(report, checkpoint_dir, file_hash)

# 10.6 跨章节题答回填（#3）：章末/附录答案→回填到题干
questions = await asyncio.to_thread(storage.load_questions_by_source, ...)
link = link_answers(questions)
# 回填solution_content，删除冗余答案条目

# 10.7 结构化关系推断（#6知识图谱边）
rel_rows = await asyncio.to_thread(storage.relation_rows, ...)
edges = infer_structural_relations(rel_rows)
storage.save_relations(edges, discipline)

# 10.8 文档级reconciliation复核（#7）
rec_rows = await asyncio.to_thread(storage.reconciliation_rows, ...)
report = reconcile(rec_rows)
```

**所有post-pass都用`asyncio.to_thread()`放到线程池**，避免同步磁盘/CPU操作阻塞event loop。

**步骤11：异常兜底**
```python
# orchestrator.py:586-602
except BaseException as exc:
    result.errors.append(str(exc))
    for q in (q_md, q_raw, q_rev, q_ann, q_done):
        try:
            q.put_nowait(SENTINEL)  # 避免剩余协程卡死
        except Exception:
            pass
    for t in tasks:
        if not t.done():
            t.cancel()
    await save_state_locked(checkpoint_dir, state, force=True)
    raise
```

**异常隔离设计**：任一Stage异常→所有queue收到SENTINEL→所有Stage优雅退出。不做"重试或跳过"——文件级异常意味着环境问题（磁盘满、网络断等），重试也无意义。

### 4.4 Task0 Producer详细实现

```python
# orchestrator.py:630-732
async def _task0_producer(self, files, state, queue, result):
    producer_had_error = False
    try:
        block_index = 0
        for file_idx, f in enumerate(files):
            fmt = self._detect_format(f)
            recognizer = get_recognizer(fmt, self._pdf_backend, vlm_llm,
                                        self.config, self.discipline, f)
            async for batch in recognizer.recognize_in_batches(
                f, batch_pages=pages_per_batch, ...):
                if batch.is_failed:
                    state.failed_batches.append((file_idx, batch.page_start, batch.page_end))
                    producer_had_error = True
                    continue  # 跳过失败的batch，不阻断后续

                # 构造MdBlockSource推入queue
                msb = MdBlockSource(
                    file_path=str(f),
                    page_start=batch.page_start,
                    page_end=batch.page_end,
                    md_path=str(batch.md_path),
                    md_text=batch.md_text,
                    block_index=block_index,
                    section_boundaries=detect_section_boundaries(batch.md_text.splitlines()),
                )
                block_index += 1
                await queue.put(msb)

    finally:
        if not producer_had_error:
            state.task0_completed = True  # 注意：有失败batch时task0不标completed
        await save_state_locked(checkpoint_dir, state, force=True)
        await queue.put(SENTINEL)
```

**关键设计——`producer_had_error`标志**：只要有任何一个batch识别失败（VLM重试耗尽、内容审核误判等），`task0_completed`就不会被设为`True`。这保证了下次resume时，`_is_cache_complete()`会因为`failed_batches`非空而返回`False`，触发重新识别。

### 4.5 目录并发处理

```python
# orchestrator.py:869-986
async def process_directory(self, dir_path, max_concurrent_files=8):
    files = sorted(self._collect_files(dir_path), key=self._natural_sort_key)
    grouped = self._group_part_files(files)  # 分卷文件合并

    # 跳过已完成的文件（hash不变+checkpoint完整）
    work_items = []
    for item in grouped:
        cur_hash = compute_file_hash(item)
        if dir_progress.completed_files.get(pkey) == cur_hash and cur_hash:
            if self._has_completed_checkpoint(cur_hash):
                continue  # 已完成，跳过
        work_items.append((i, item, pkey))

    # 全局OrderIndexAllocator
    allocator_start = await asyncio.to_thread(_scan_allocator_start)
    allocator = OrderIndexAllocator(start=allocator_start)

    # 信号量控制并发度
    sem = asyncio.Semaphore(max_concurrent_files)
    tasks = [_process_one(idx, item, pkey) for ...]
    await asyncio.gather(*tasks)
```

**`_scan_allocator_start()`预扫**（行924-954）：**O(总条目数)**的磁盘遍历——读DB的`max(order_index)`+所有文件checkpoint的mirror目录——放线程池避免阻塞event loop。这是"目录并发处理"的代价：启动时需要一次全量预扫。

### 4.6 分卷文件分组

```python
# orchestrator.py:991-1012
_PART_RE = re.compile(r"^(.+?)_part_(\d+)_(\d+)$", re.IGNORECASE)

@classmethod
def _group_part_files(cls, files):
    groups = {}  # key → [(part_index, Path)]
    for idx, f in enumerate(files):
        m = cls._PART_RE.match(f.stem)
        if m:
            key = f"{f.parent}/{m.group(1)}{f.suffix}"
            groups.setdefault(key, []).append((int(m.group(2)), f))
    # 排序并展平
```

命名约定：`book_part_1_3.pdf`表示"book"的第1/3卷。3个分卷被合并为单个logical_document，共享同一file_hash和checkpoint。

---

## 5. Task0 Producer：文档识别

**文件**：`src/sciforge/pipeline/recognizers.py`（536行）

### 5.1 协议设计

```python
# recognizers.py:219-230
class Recognizer(Protocol):
    def recognize_in_batches(
        self, file_path: Path, *, batch_pages: int, output_dir: Path,
        already_done: list[tuple[int, int]], max_retries: int = 3,
    ) -> AsyncIterator[BatchResult]: ...
```

`Recognizer`是Python Protocol（结构子类型），不要求继承。这允许三种完全不同的实现（VLM/纯文本/整文件）共享同一接口，`get_recognizer()`工厂函数在运行时选择。

### 5.2 章节边界检测（纯CPU，无LLM）

```python
# recognizers.py:34-92
_MD_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")
_TEX_SECTION_RE = re.compile(
    r"^\s*\\(chapter|section|subsection|subsubsection|part)\*?\s*\{(.+?)\}", re.IGNORECASE)
_CN_CHAPTER_RE = re.compile(
    r"^\s*\*\*\s*(第[零〇一二三四五六七八九十百千\d]+[章节部分篇]|§[\d.]+)\s*.+?\*\*\s*$")

def detect_section_boundaries(lines: list[str]) -> list[tuple[int, int, str]]:
    results = []
    for i, raw in enumerate(lines):
        line = raw.rstrip("\n\r")
        # Markdown heading: # → level=1, ## → level=2, ...
        m = _MD_HEADING_RE.match(line)
        if m:
            results.append((i, len(m.group(1)), m.group(2).strip()))
            continue
        # LaTeX sectioning: \chapter→1, \section→2, \subsection→3, ...
        m = _TEX_SECTION_RE.match(line)
        if m:
            level_map = {"part": 0, "chapter": 1, "section": 2,
                         "subsection": 3, "subsubsection": 4}
            results.append((i, level_map.get(m.group(1).lower(), 2), m.group(2).strip()))
            continue
        # 中文粗体章节标题
        if _CN_CHAPTER_RE.match(line):
            if "章" in line or "篇" in line or "部分" in line:
                level = 1
            elif "节" in line:
                level = 2
            else:
                level = 3
            results.append((i, level, line.strip().strip("*").strip()))
    return results
```

**设计意图**：在Task0产出时就做章节检测，结果随`MdBlockSource.section_boundaries`传递到Stage1Extract，用于`structural_context`注入（让LLM知道当前提取内容属于哪一章哪一节）。这是**零成本的先验信息**，不依赖LLM解析。

### 5.3 PDF文字层探测（REC-01）

```python
# recognizers.py:156-194
def pdf_has_extractable_text_layer(file_path, *, min_chars_per_page=100, sample_pages=5):
    if not _has_fitz():
        return False
    with fitz.open(str(file_path)) as doc:
        total = int(doc.page_count)
        n = min(sample_pages, total)
        total_chars = 0
        garbled = 0
        for pn in range(n):
            text = doc[pn].get_text("text")
            total_chars += len(text)
            garbled += text.count("�")  # U+FFFD替换符=乱码
    avg_chars = total_chars / max(1, n)
    garbled_ratio = garbled / total_chars
    return avg_chars >= min_chars_per_page and garbled_ratio < 0.05
```

**目的**：区分"电子版教材PDF"（有可提取文字层）和"扫描件PDF"（无文字层或纯乱码）。前者可以走0-LLM的`PyMuPdfRecognizer`直接文本提取，后者必须走VLM视觉识别。**这个探测在`get_recognizer()`中自动化执行（需`vlm_auto_text_detect`开关打开），对用户透明。**

**探测策略**：
- 抽样前5页（可用--force-vlm跳过探测）
- 平均字符/页 ≥ 100（有内容）
- U+FFFD替换符占比 < 5%（文本质量可接受）
- **保守设计**：fitz不可用或读取失败时返回False（不误判扫描件，继续走VLM）

### 5.4 工厂函数决策树

```python
# recognizers.py:467-536
def get_recognizer(fmt, backend, llm_client, config, discipline, file_path):
    # .md/.tex → MarkdownRecognizer（直接读文件，单batch）
    if fmt in (DocumentFormat.MARKDOWN, DocumentFormat.TEX):
        return MarkdownRecognizer()

    if fmt == DocumentFormat.PDF:
        if backend == "vlm" and llm_client.supports_vision:
            # REC-01：探测到文字层→自动降级到PyMuPdf（0-LLM）
            if config.vlm_auto_text_detect and pdf_has_extractable_text_layer(file_path):
                return PyMuPdfRecognizer()
            return VlmRecognizer(llm_client, config, discipline)

        if _has_fitz():
            return PyMuPdfRecognizer()      # fitz可用→文本提取
        return PdfToTextRecognizer()        # 回退到pdftotext

    # DOCX/PPTX → 必须VLM（转PDF后多模态识别）
    if fmt in (DocumentFormat.DOCX, DocumentFormat.PPTX):
        return VlmRecognizer(llm_client, config, discipline)
```

### 5.5 PyMuPdfRecognizer（无LLM纯文本）

```python
# recognizers.py:288-341
class PyMuPdfRecognizer:
    async def recognize_in_batches(self, file_path, *, batch_pages, output_dir,
                                    already_done, max_retries=3):
        doc = fitz.open(str(file_path))
        try:
            total = doc.page_count
            page_idx = 0
            while page_idx < total:
                end_excl = min(page_idx + batch_pages, total)
                cache_path = output_dir / f"page_{page_idx+1:03d}-{end_excl:03d}.md"

                # checkpoint快速路径
                if (page_idx+1, end_excl) in done_set or cache_path.exists():
                    md_text = cache_path.read_text() if cache_path.exists() else ""
                    if md_text.strip():
                        yield BatchResult(page_idx+1, end_excl, md_text, cache_path)
                    page_idx = end_excl
                    continue

                # 拼接文本
                parts = [doc[pn].get_text("text") for pn in range(page_idx, end_excl)]
                md_text = "\n\n".join(parts).strip()
                cache_path.write_text(md_text)
                yield BatchResult(page_idx+1, end_excl, md_text, cache_path)
                page_idx = end_excl
        finally:
            doc.close()
```

**设计亮点**：`already_done`和`cache_path.exists()`双重检查——支持断点恢复。已识别的batch直接读缓存yield，不重新提取。

---

## 6. VLM转换器：PDF→Markdown

**文件**：`src/sciforge/pipeline/vlm_converter.py`（713行）

### 6.1 核心接口

```python
# vlm_converter.py:363-468
async def recognize_in_batches(
    self, file_path, *, batch_pages, output_dir, already_done, max_retries=3,
) -> AsyncGenerator[tuple[int, int, str, Path, bool], None]:
    """把文档按batch_pages一批识别为markdown并yield"""
    doc = self._open_document(file_path)
    doc_lock = asyncio.Lock()  # fitz Document非线程安全

    # 枚举所有batch
    specs = [(ps, pe, page_nums_0based) for ...]

    if self.max_concurrent <= 1:
        # 串行：保留prev_context跨批续接
        prev_md_tail = ""
        for ps, pe, nums in specs:
            for tup in await self._process_one_batch(..., prev_context=prev_md_tail, ...):
                if not tup[4] and tup[2].strip():
                    prev_md_tail = self._tail_lines(tup[2])
                yield tup
    else:
        # REC-02：批内有界并发+滑动窗口保序yield
        sem = asyncio.Semaphore(self.max_concurrent)
        tasks = [asyncio.create_task(run_spec(ps, pe, nums)) for ps, pe, nums in specs]
        for t in tasks:
            for tup in await t:
                yield tup
```

**`doc_lock`的设计原因**：fitz的Document对象**非线程安全**。图像渲染（`get_pixmap`）和裁图（`_crop_figures_from_md`）必须在锁内串行化。但VLM网络调用（`_convert_batch`）在锁外——这是IO密集型操作，并发有意义。

### 6.2 并发模式 vs 串行模式的权衡

| 维度 | 串行（max_concurrent=1） | 并发（max_concurrent>1） |
|------|--------------------------|---------------------------|
| prev_context | 跨批续接，前批末尾10行传给下批 | 放弃prev_context（各批独立） |
| 墙钟时间 | 线性（∑每批时间） | 近似 max(每批时间) × 批数/并发度 |
| 内存 | 低（一次一个batch在途） | 在途图像 ≤ max_concurrent × pages_per_batch |
| 适用场景 | 高质量要求、小文档 | 大文档提速 |

### 6.3 内容审核误判的逐页回退

```python
# vlm_converter.py:533-560
except RetryExhausted as exc:
    err_str = str(getattr(exc, "last_exc", "") or "")
    is_content_flag = any(s in err_str.lower()
                          for s in ("sensitive", "new_sensitive", "1026"))
    if is_content_flag and len(page_nums_0based) > 1:
        # 整批被内容审核误判→逐页识别
        logger.warning("vlm_batch_sensitive_fallback_perpage", ...)
        async for tup in self._recognize_pages_individually(
            doc=doc, page_nums_0based=page_nums_0based, ...):
            yield tup
        return

    # 非内容审核→标记is_failed=True
    return [(page_start, page_end, "", cache_path, True)]
```

**这是对MiniMax等provider内容审核误判的针对性防御**：整批5页中只要有1页触发审核（如含特定数学公式被误判为敏感内容），整批会被拒绝。回退策略是**逐页识别**——只让真正被误判的那一页`is_failed=True`，其余4页正常恢复。代价是4次额外LLM调用，但避免了丢5页数据的严重后果。

### 6.4 插图裁剪

```python
# vlm_converter.py:248-344
def _crop_figures_from_md(self, md, doc, page_nums, file_stem, assets_dir):
    """
    VLM输出 {{FIGURE:top%,left%,bottom%,right%}} 占位符
    → 从原PDF用fitz重新渲染裁剪区域 → 保存为PNG → 替换为 ![](rel_path)
    """
    matches = list(_FIGURE_RE.finditer(md))
    # 按文本位置比例推断FIGURE所属页面
    page_idx = min(int(match.start() / md_len * n_pages), n_pages - 1)
    target_page = page_nums[page_idx]

    # fitz裁剪渲染
    clip = fitz.Rect(x0, y0, x1, y1) & page_rect
    pix = page.get_pixmap(dpi=self.dpi, clip=clip)
    filename = f"{file_stem}_p{target_page+1:03d}_fig{fig_idx}.png"
    pix.save(str(assets_dir / filename))

    # 替换占位符为Markdown图片链接
    replacement = f"![{alt}](assets/{assets_dir.name}/{filename})"
```

**为什么要重新渲染裁剪？** VLM输出的图片占位符只有百分比坐标，实际图像数据需要从原PDF重新渲染。这个过程在`_convert_batch`返回后、写磁盘前完成——最终落盘的`.md`文件已包含正确的图片链接。

---

## 7. Stage1Extract：滑动窗口批量提取

**文件**：`src/sciforge/pipeline/stages/stage1_extract.py`（1064行）—— 最复杂、最精密的模块

### 7.1 设计哲学：三不原则

1. **不漏**（最高优先级）：宁可多调LLM验证，也不静默跳过可能有条目的窗口。三层防线（强标记→弱信号→LLM确认）保证。
2. **不重**：全局`order_index`单调递增+窗口滑动`last_consumed_line`保证同一条目不会被两个窗口重复提取。
3. **不截**：`incomplete_at_tail`机制保证跨窗口条目（解答在下一窗口的习题）完整保留。

### 7.2 条目预检正则（零成本跳过真空白区）

```python
# stage1_extract.py:61-97
_ITEM_MARKER_RE = re.compile(r"""
    # LaTeX环境: \begin{theorem}...
    \\begin\{(?:theorem|definition|lemma|corollary|proposition|...)\}
    # 中文关键字+数字: 定义 1.2.2 / 定理1.1 / 例1．2．7 / **定义 1.1**
    |(?:\*\*\s*)?(?:定[理义]|引理|推论|命题|性质|公式|例题?|习题|...)
     [\s\u00a0\u3000]{0,3}[\d\uff10-\uff19X]
    # 英文全称+编号: Theorem 3.2 / Law 1.2
    |(?:Theorem|Definition|Lemma|Corollary|...)\s+[\d.]
    # 英文缩写+编号: Thm 3 / Def 1
    |(?:Thm|Def|Prop|Cor|Lem|Pf|Rmk|Eg)\.?\s*\d
""", re.IGNORECASE)

# 弱信号：强标记都没命中但仍有"可能是条目"的迹象
_WEAK_SIGNAL_RE = re.compile(r"""
    \d+\s*[.．]\s*\d+           # 编号样式 3.2
    |\\begin\{                   # 任意LaTeX环境
    |\\underline|＿{2,}|{3,}    # 填空下划线
    |证\s*明|解\s*答               # 中文解答/证明
    |[(（]?[A-Da-d][)）.、]      # 选择题选项
    |^\s*\d{1,3}\s*[.、．)）]   # 行首裸编号
    |第\s*[0-9０-９一二三]+[章节]  # 中文章节
""", re.IGNORECASE | re.MULTILINE)
```

**设计原则**：`_ITEM_MARKER_RE`宁可false-positive（多调一次LLM验证，无害），也不false-negative（漏条目）。`_WEAK_SIGNAL_RE`在strict模式下作为第二道防线——覆盖OCR把关键字彻底糊掉、或裸编号/选项等不含标准关键字的情形。

### 7.3 主循环 `_run_streaming()`

```python
# stage1_extract.py:186-278
async def _run_streaming(self, in_queue, out_queue, state):
    self._restore_state(state)  # 从state恢复line_buffer/line_offset/next_item_id
    sentinel_seen = False

    try:
        while not sentinel_seen:
            msg = await in_queue.get()
            if isinstance(msg, _Sentinel):
                sentinel_seen = True
                is_last = True
            else:
                # REC-14：首个block探测体裁（试卷/讲义关fast-skip提召回）
                if not self._genre_detected:
                    self._genre = detect_genre(msg.md_text)
                    if self._genre in ("exam_paper", "lecture"):
                        self.fast_skip_enabled = False  # 关快速跳过

                # REC-20：章节标题作structural_context先验
                if msg.section_boundaries:
                    self._section_hint = " / ".join(
                        t for _, _, t in msg.section_boundaries if t.strip())[:8]

                # 跳过checkpoint中已处理的block
                if msg.block_index < state.current_block_idx:
                    continue
                if msg.block_index in state.failed_blocks:
                    continue

                # 追加新行到line_buffer（带全局行号前缀）
                start_no = self.line_offset + len(self.line_buffer) + 1
                new_lines = self._annotate(msg.md_text, start_no)
                self.line_buffer.extend(new_lines)
                state.current_block_idx = msg.block_index + 1
                is_last = False

            # 处理窗口
            await self._process_windows(state, msg, is_last, out_queue)

        # 流结束：残留buffer报警（不应发生）
        if self.line_buffer:
            logger.warning("stage1_residual_buffer", residual_lines=len(self.line_buffer))

        state.stage1_completed = True
        await self._sync_state(state, force=True)
    finally:
        await out_queue.put(SENTINEL)
```

**`_restore_state()`恢复的内容**：
```python
# stage1_extract.py (restore逻辑)
self.line_offset = state.line_offset
self.line_buffer = list(state.line_buffer_carry)
self.next_item_id = state.next_item_id
self.current_window_size = state.current_window_size
self.consecutive_empty = state.consecutive_empty
```

### 7.4 窗口处理 `_process_windows()` —— 最复杂的逻辑

```python
# stage1_extract.py:281-501
async def _process_windows(self, state, current_block, is_last, out_queue):
    while True:
        if not self.line_buffer:
            break
        if len(self.line_buffer) < self.current_window_size and not is_last:
            break  # 等更多block到来

        window = self.line_buffer[:window_len]
        clean_window = self._clean_window_text(window)  # 剥离行号前缀

        # ── 第一层：条目预检 ──
        has_markers = _ITEM_MARKER_RE.search(clean_window) is not None
        if has_markers:
            window_empty = False
        elif self.fast_skip_strict:
            window_empty = _WEAK_SIGNAL_RE.search(clean_window) is None  # strict模式
        else:
            window_empty = True  # 非strict：仅强标记预检

        if self.fast_skip_enabled and window_empty and not is_last:
            # 零条目迹象→跳过整窗，不调LLM
            slide = window_len
            self.line_buffer = self.line_buffer[slide:]
            self.line_offset += slide
            self.current_window_size = self.base_window
            self.consecutive_empty = 0
            continue

        # ── 调LLM提取 ──
        try:
            payload = await self._extract_batch(window, window_start_int, window_last_int)
        except RetryExhausted as exc:
            # 429→中断文件（保留state等重启续跑）
            # 非429→标记block为failed，滑base_window
            ...

        # ── REC-12：文末强制再提取 ──
        if (not payload.items and not window_empty and is_last
            and window_len >= len(self.line_buffer)):
            forced = await self._extract_batch(window, ..., force_extract=True)
            if forced and forced.items:
                payload = forced  # 抢救成功

        if not payload.items:
            self.consecutive_empty += 1
            # ── 第三层：no_items_found+无信号→快跳整窗 ──
            if (self.fast_skip_enabled and payload.stop_reason == "no_items_found"
                and window_empty):
                slide = window_len  # LLM确认无条目→整窗跳
                self.line_buffer = self.line_buffer[slide:]
                self.line_offset += slide
                ...

            # ── 第二层：force-consume ──
            elif (at_max and consecutive_empty >= empty_force_limit) or terminal_tail:
                if payload.stop_reason == "incomplete_at_tail" and payload.last_consumed_line > 0:
                    slide = cut + 1  # 精确跳到已消费行
                elif not window_empty and not terminal_tail:
                    slide = base_window  # 有标记但LLM返空→保守滑base_window重扫
                else:
                    slide = window_len  # 真空白/文末尾部→跳整窗

            # ── 扩窗 ──
            else:
                next_size = min(current_window_size + expand_step, max_window)
                self.current_window_size = next_size
```

**三层防线总结**：

| 层级 | 触发条件 | 动作 | 风险 |
|------|---------|------|------|
| 第一层 | regex预检无条目信号+strict模式无弱信号+fast_skip_enabled | 跳整窗（不调LLM） | 极低（strict模式已覆盖弱信号） |
| 第二层 | LLM返空+达max_window+连续空够多次 | force-consume（精确跳或保守滑） | 低（有incomplete_at_tail精确跳；有标记保守滑） |
| 第三层 | LLM返no_items_found+窗口真空白 | 快跳整窗 | 极低（LLM已确认无条目） |
| REC-12 | 文末+有标记+LLM返空 | force_extract=True再调一次 | 抢救文末最后几题 |

### 7.5 LLM提取调用 `_extract_batch()`

```python
# stage1_extract.py (提取调用)
async def _extract_batch(self, window, window_start_int, window_last_int, force_extract=False):
    # 构造prompt
    window_text = "".join(window)
    structural_context = self._section_hint  # 从section_boundaries来

    def build_prompt(feedback):
        return self.user_template.format(
            window_text=window_text,
            structural_context=structural_context,
            order_index_start=self.next_item_id,
            force_extract=str(force_extract).lower(),
            retry_feedback=retry_block,
        )

    # 调LLM+强校验
    payload = await call_llm_json_with_schema_retry_with_feedback(
        self.llm, build_prompt,
        model=BatchItemExtractPayloadLLM,
        system=self.system_prompt,
        validation_context={"window_last_line": window_last_int},
        ...
    )

    # 分配item_id
    if payload.items:
        base_id = self.next_item_id
        if self.order_index_allocator:
            base_id = await self.order_index_allocator.reserve(len(payload.items))
        else:
            self.next_item_id += len(payload.items)
        item_ids = list(range(base_id, base_id + len(payload.items)))

        # 镜像每条item到磁盘
        for item_id, item in zip(item_ids, payload.items):
            self._mirror_item(item_id, item, state)

        # emit ExtractedBatch到q_raw
        batch = ExtractedBatch(
            item_ids=item_ids,
            items=payload.items,
            window_lines=window,
            window_last_line=window_last_int,
            stop_reason=payload.stop_reason,
            ...
        )
        await out_queue.put(batch)

        # 滑动窗口
        slide = self._find_buffer_idx(payload.last_consumed_line) + 1
        self.line_buffer = self.line_buffer[slide:]
        self.line_offset += slide
        self.current_window_size = self.base_window
        self.consecutive_empty = 0
```

**`order_index_allocator` vs 自己管理`next_item_id`**：目录并发时用共享allocator的`reserve()`（保证跨文件ID连续），单文件时自己递增`next_item_id`。

---

## 8. Stage1Review：批量校验

**文件**：`src/sciforge/pipeline/stages/stage1_review.py`（286行）

### 8.1 设计动机

Stage1Extract的LLM可能犯以下错误：
1. **习题漏解答**：exercise/example在窗口末尾时，solution在下一窗口
2. **编号跳号**：相邻条目的index递增>1，可能漏提取
3. **行号错误**：start_line/end_line不准确
4. **类型误判**：把exercise标成knowledge

Stage1Review用**一次LLM调用校验一整批**（N条item合1次调用），把N次单条校验压缩为1次。

### 8.2 条件化跳过 `_should_skip_review()`

```python
# stage1_review.py:221-257
def _should_skip_review(self, batch: ExtractedBatch) -> bool:
    items = batch.items
    if not items:
        return True  # 空batch无需review

    # 风险信号1：提取不完整/截断
    if getattr(batch, "stop_reason", "all_complete") != "all_complete":
        return False

    # 风险信号2：习题/例题贴近窗口末尾且声称无解
    window_last = getattr(batch, "window_last_line", 0) or 0
    if window_last > 0:
        for it in items:
            if (it.item_type.lower() in _EXERCISE_LIKE_TYPES
                and it.solution_status == "no_solution_in_source"
                and window_last - it.end_line < _NEAR_TAIL_MARGIN):  # 10行以内
                return False

    # 风险信号3：编号跳号（确定性解析）
    if self._has_numbering_gap(items):
        return False

    return True  # 无任何风险信号→跳过review
```

**设计哲学**：默认不假设LLM会犯错。只有当**客观可检测的风险信号**出现时才调用review。旧实现把theorem/lemma等几乎所有类型都列为"高危"强行review，导致条件化跳过在真实教材上近乎失效。

### 8.3 降级策略

```python
# stage1_review.py:127-219
async def _review_batch(self, batch, state):
    if not self.enabled:
        return list(batch.items)  # 未启用→直通

    if self.conditional_skip and self._should_skip_review(batch):
        return list(batch.items)  # 低风险→直通

    try:
        payload = await call_llm_json_with_schema_retry_with_feedback(...)
    except RetryExhausted:
        return list(batch.items)  # 审核失败→降级使用原始items

    if len(payload.reviewed_items) != len(batch.items):
        return list(batch.items)  # 长度不匹配→降级直通

    return list(payload.reviewed_items)  # None位置=拒绝该item
```

**关键设计**：任何失败都**降级直通**（使用Stage1Extract的原始items），而不是丢弃整个batch。只有LLM明确将某位置设为None时，该item才被拒绝（不进q_rev）。

---

## 9. Stage2Annotate：单条标注

**文件**：`src/sciforge/pipeline/stages/stage2_annotate.py`（478行）

### 9.1 标注内容

Stage2Annotate给每条item打以下标注：
- `tags`：有序tag段列表（如`["math:数学","algebra:代数","group_theory:群论"]`）
- `difficulty`：1-10整数
- `subject`：二级学科（如`"real_analysis:实分析"`）
- `taxonomy_path`：分类路径（如`"math.analysis.continuity"`）
- `question_type`：题目认知类别（仅question类item）
- `methods`：解题方法列表

### 9.2 批量打标（REC-04）

```python
# stage2_annotate.py:341-423
async def _annotate_group(self, items, state):
    if self.batch_enabled and len(items) > 1:
        anns = await self._annotate_batch_llm(items)  # 一次LLM调用
        if anns is not None:
            return [self._to_annotated(it, ann) for it, ann in zip(items, anns)]

    # 批量失败→降级逐条
    results = []
    for it in items:
        annotated = await self._annotate_one(it, state)
        if annotated is not None:
            results.append(annotated)
    return results
```

**批量vs逐条的权衡**：同block_index的N条item可以一次LLM调用打标，共享tag_subtree和taxonomy_tree上下文，比N次单条调用更省token。但批量失败不会污染整批——降级逐条重试。

### 9.3 并发模式与ReorderBuffer

```python
# stage2_annotate.py:163-260
async def _run_concurrent(self, in_queue, out_queue, state):
    buf = ReorderBuffer(out_queue, start_id=start_id)

    # N个worker并发消费
    workers = [asyncio.create_task(worker(i)) for i in range(self.concurrency)]

    # 分发主循环
    last_dispatched = start_id - 1
    while True:
        msg = await in_queue.get()
        if isinstance(msg, _Sentinel):
            break
        # gap处理：上游拒绝的item_id→主动skip，防止ReorderBuffer卡死
        if msg.item_id > last_dispatched + 1:
            for missing in range(last_dispatched + 1, msg.item_id):
                await buf.skip(missing)
        last_dispatched = msg.item_id

        # 同block组batch
        if group and (msg.block_index != group_block or len(group) >= self.batch_size):
            await task_queue.put(group)
            group = []
        group.append(msg)

    # 等待worker完成
    for _ in range(self.concurrency):
        await task_queue.put(SENTINEL)
    await asyncio.gather(*workers)
    await buf.close()
```

**gap处理是并发模式的关键**：上游Stage1Review拒绝的item会造成item_id不连续。如果不主动`buf.skip(missing_id)`，ReorderBuffer的`_next_id`会永远卡在缺失id上。

### 9.4 兜底标注

```python
# stage2_annotate.py:442-467
def _fallback_annotation(self, reviewed):
    spec = self.discipline_spec
    return SingleAnnotationLLM(
        subject=spec.prompt_vars.get("discipline_example_subject", "general:通用"),
        taxonomy_path=self.discipline.value,  # 纯根（不套示例值）
        tags=[spec.tag_root, "uncategorized:未分类", "auto_fallback:自动兜底"],
        difficulty=5,
        question_type=spec.allowed_question_types[0] if is_question else None,
        methods=[],
    )
```

**兜底设计的关键改进**（REC-15）：`taxonomy_path`用学科纯根（如`"math"`），而不是某个具体子主题（如`"math.analysis.continuity"`）——否则会把所有标注失败的条目错误归类到同一个具体主题，污染分类。

---

## 10. Stage2Review：标注审核

**文件**：`src/sciforge/pipeline/stages/stage2_review.py`（380行）

### 10.1 审核内容

与Stage2Annotate结构相似，但prompt不同——LLM看到的是**原始item+已有标注**，任务是复核：
- tag格式是否正确（`english:中文`）
- tag是否复用已有tag_tree中的段
- taxonomy_path是否在合法路径集合中
- question_type与category是否一致
- difficulty是否合理（非极端值）

### 10.2 条件化跳过 `_should_skip_review()`

```python
# stage2_review.py:322-369
def _should_skip_review(self, annotated):
    ann = annotated.annotation
    item = annotated.item

    # 全部条件同时满足才跳过：
    # 1. tags[0]==discipline_root
    # 2. tags[1]的english部分∈allowed_branches
    # 3. taxonomy_path以discipline前缀开头
    # 4. 3≤difficulty≤8（非极端值）
    # 5. item_type不在_HIGH_RISK_TYPES或category=="knowledge"且无question_type
    # 6. tag段数在[2,6]范围内
```

**审核失败→降级使用原始annotation**（与Stage1Review一致的容错哲学）。

---

## 11. Stage3Persist：合并入库

**文件**：`src/sciforge/pipeline/stages/stage3_persist.py`（389行）

### 11.1 设计哲学

Stage3Persist是**纯CPU确定性代码，无LLM调用**。它的职责是：
1. 合并`item`（内容）+`annotation`（标注）→`KnowledgePoint`或`Question`
2. 确定性校验和修正（tag规范化、taxonomy校验、学科根补齐）
3. 批量入库（SQLite）+ tag_tree累积

**为什么Stage3做tag规范性检查而非Stage2？** Stage2的LLM已经尽力了，但LLM不可靠。Stage3用确定性代码做最后一层防线——tag受控词表统一、taxonomy路径校验、学科根补齐——这些逻辑简单、可测、不会出错。

### 11.2 批量入库（REC-10）

```python
# stage3_persist.py:90-138
async def run(self, in_queue, state):
    buffer = []
    cursor = state.last_persisted_item_id
    while True:
        msg = await in_queue.get()
        if isinstance(msg, _Sentinel):
            break

        # 保序断言
        expected = cursor + 1
        if msg.item_id < expected:
            logger.error("stage3_out_of_order_backward")  # FIFO被破坏！
        elif msg.item_id > expected:
            logger.info("stage3_skipped_items", gap=msg.item_id - expected)  # 合法gap

        buffer.append(msg)
        if len(buffer) >= self.persist_batch_size:  # 默认16
            await self._flush_batch(buffer, state)
            buffer = []

    if buffer:
        await self._flush_batch(buffer, state)

    state.stage3_completed = True
    state.tag_tree_json = self.tag_tree.to_json()
    await save_state_locked(checkpoint_root, state, force=True)
```

**攒小批再save_batch**：默认16条一批，一次SQLite事务提交。减少WAL fsync次数，入库吞吐量显著提升。

### 11.3 `_build_persistable()` —— 确定性规范化

```python
# stage3_persist.py:155-243
def _build_persistable(self, msg):
    annotation = msg.annotation

    # 1. 补学科根（tags[0]和taxonomy_path前缀）
    annotation = self._ensure_discipline_root(annotation, item_id=msg.item_id)

    # 2. Tag受控词表统一（#6）
    annotation.tags = self.tag_canon.canonicalize_path(annotation.tags)
    annotation.methods = self.tag_canon.canonicalize_path(annotation.methods)

    # 3. Taxonomy校验+模糊匹配（限定本学科，防跨学科漂移）
    corrected_taxonomy, _ = self.taxonomy_validator.validate(
        annotation.taxonomy_path, discipline=self.discipline.value)
    if corrected_taxonomy != annotation.taxonomy_path:
        annotation.taxonomy_path = corrected_taxonomy

    # 4. 累积tag tree（集中维护）
    self.tag_tree.insert_path(annotation.tags)

    # 5. 构造KP或Question
    if item.category == "knowledge":
        kp_type = item.item_type
        if kp_type not in self.discipline_spec.allowed_kp_types:
            kp_type = self.discipline_spec.allowed_kp_types[0]  # 回退到第一个合法值
        return KnowledgePoint(...), None
    else:
        return None, Question(...)
```

**`_ensure_discipline_root()`的跨学科保护**：
```python
# stage3_persist.py:300-378
def _ensure_discipline_root(self, annotation, item_id):
    # 如果tags[0]是其他已注册学科的根（如physics:物理），保留原值并warning
    if cross_discipline:
        logger.warning("stage3_tag_root_cross_discipline", ...)
    else:
        # 去重后前置本学科根
        new_tags = [self.discipline_spec.tag_root, *deduped]
```

---

## 12. LLM Schema：Pydantic强校验体系

**文件**：`src/sciforge/pipeline/llm_schemas.py`（439行）

### 12.1 设计哲学

所有LLM输出在进入pipeline queue之前**必须**通过Pydantic的`model_validate()`校验。这是防止LLM随意发挥的最后一道门禁。校验失败→`ValidationError`被`retry.py`捕获→带错误反馈重试→仍失败→降级或丢弃。

### 12.2 Stage1 Schema：`BatchItemExtractPayloadLLM`

```python
# llm_schemas.py:110-205
class BatchItemExtractPayloadLLM(BaseModel):
    items: list[SingleItemExtractLLM] = Field(default_factory=list)
    stop_reason: Literal["all_complete", "incomplete_at_tail", "no_items_found"]
    last_consumed_line: int = Field(ge=0, default=0)
    issues: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_items_ordering(self):
        """items必须按start_line严格升序、不重叠"""
        for i in range(1, len(self.items)):
            if self.items[i].start_line <= self.items[i-1].end_line:
                raise ValueError(...)

    @model_validator(mode="after")
    def _check_last_consumed_line(self):
        """last_consumed_line必须等于items[-1].end_line（非空时）"""
        if self.items and self.last_consumed_line != self.items[-1].end_line:
            raise ValueError(...)

    @model_validator(mode="after")
    def _check_exercise_near_tail(self, info):
        """exercise距窗口末行<10且标no_solution→拒绝（留给下一窗口）"""
        window_last_line = info.context.get("window_last_line")
        for item in self.items:
            if (item.item_type in _EXERCISE_LIKE_TYPES
                and item.solution_status == "no_solution_in_source"
                and window_last_line - item.end_line < _NEAR_TAIL_MARGIN):
                raise ValueError(...)
```

**`validation_context`注入**：`info.context`是Pydantic v2的特性，调用方通过`model_validate(data, context={"window_last_line": N})`注入运行时信息。这让schema能校验"window_last_line"这种不属于LLM输出本身、但对校验至关重要的上下文。

### 12.3 Stage1单条Schema：`SingleItemExtractLLM`

```python
# llm_schemas.py:41-103
class SingleItemExtractLLM(BaseModel):
    category: Literal["knowledge", "question"]
    item_type: str = Field(min_length=1)
    number: str = ""
    corrected_content: str = Field(min_length=1)
    corrected_proof: str | None = None
    corrected_solution: str | None = None
    solution_status: Literal["complete", "no_solution_in_source", "solution_deferred"]
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def _check_solution_status_consistency(self):
        """complete↔proof/solution非空；no_solution/deferred↔proof/solution为null"""
        if self.solution_status == "complete" and not (
            self.corrected_proof or self.corrected_solution):
            raise ValueError(...)
        if self.solution_status in ("no_solution_in_source", "solution_deferred") and (
            self.corrected_proof or self.corrected_solution):
            raise ValueError(...)
```

**`solution_deferred`的设计**：与`no_solution_in_source`不同，它表示"原文明示答案在他处"（如章末习题解答），**豁免**exercise near-tail拒绝——这是有意为之，不是截断。

### 12.4 Stage2 Schema：`SingleAnnotationLLM`

```python
# llm_schemas.py:334-414
class SingleAnnotationLLM(BaseModel):
    subject: str = Field(min_length=1)
    taxonomy_path: str = Field(min_length=1)
    tags: list[str] = Field(min_length=1)
    difficulty: int = Field(ge=1, le=10)
    question_type: str | None = None
    methods: list[str] = Field(default_factory=list)

    @field_validator("tags")
    def _check_tags_format(cls, v):
        for seg in v:
            _validate_tag_segment(seg)  # "english:中文"格式+零宽字符检测
        return v

    @model_validator(mode="after")
    def _check_discipline_root(self, info):
        """通过context注入校验tags[0]/taxonomy_path根/主分支白名单"""
        tag_root = info.context.get("discipline_tag_root")
        if tag_root and self.tags[0] != tag_root:
            raise ValueError(...)

        allowed_branches = info.context.get("discipline_allowed_branches")
        if allowed_branches and len(self.tags) >= 2:
            branch_english = self.tags[1].split(":", 1)[0]
            if branch_english not in allowed_branches:
                raise ValueError(...)
```

**tag格式的零宽字符检测**：
```python
# llm_schemas.py:311-319
_FORBIDDEN_TAG_CHARS = (
    "\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\ufeff", "\u2060",
)

def _validate_tag_segment(seg: str):
    for ch in _FORBIDDEN_TAG_CHARS:
        if ch in seg:
            raise ValueError(f"tag含零宽/格式控制字符 U+{ord(ch):04X}")
    if not _TAG_SEGMENT_RE.match(seg):
        raise ValueError(f"tag必须匹配'english:中文'格式")
```

这是对LLM（尤其是某些中文优化模型）偶尔输出不可见零宽字符的防御。

---

## 13. Retry机制：带反馈的重试

**文件**：`src/sciforge/pipeline/retry.py`（418行）

### 13.1 设计思想

LLM调用有三种失败模式，需要**完全不同的处理策略**：

| 失败类型 | 原因 | 策略 | 计入max_attempts？ |
|----------|------|------|-------------------|
| **429 Rate Limit** | API配额耗尽 | 长睡等待（5min起，指数退避，累加上限6h） | **否** |
| **ValidationError** | LLM输出不符合Pydantic schema | 带错误反馈重试 | **是** |
| **LLMParseError** | LLM输出不是合法JSON | 带格式要求反馈重试 | **是** |

### 13.2 核心函数：`call_llm_json_with_schema_retry_with_feedback`

```python
# retry.py:274-418
async def call_llm_json_with_schema_retry_with_feedback(
    llm, prompt_builder, *, model, system, max_attempts=3, base_delay=2.0,
    validation_context=None, stage_name="",
    rate_limit_base_sleep=300.0, rate_limit_max_sleep=1800.0,
    rate_limit_total_budget=21600.0,  # 6小时
):
    last_err_text = ""
    attempt = 0
    rate_limit_hits = 0
    rate_limit_total_slept = 0.0

    while attempt < max_attempts:
        attempt += 1
        prompt = prompt_builder(last_err_text)  # 重建prompt（含上次错误反馈）

        try:
            raw = await llm.generate_json(prompt, system=system)
            result = model.model_validate(raw, context=validation_context)

            # 成功：记录token usage指标
            logger.info("llm_call_metrics", stage=stage_name, ...)
            return result

        except LLMRateLimitError as rle:
            # 429：不计attempt，长睡
            rate_limit_hits += 1
            attempt -= 1
            slept = await _sleep_for_rate_limit(rle, rate_limit_hits, ...)
            rate_limit_total_slept += slept
            if rate_limit_total_slept > rate_limit_total_budget:
                raise RetryExhausted(attempt, rle) from rle
            continue

        except ValidationError as ve:
            last_err_text = _pretty_validation(ve)  # "字段tags[0]必须等于学科根..."
        except LLMParseError as pe:
            last_err_text = "你上一次的输出不是合法JSON..."
        except ValueError as ve:
            last_err_text = str(ve)[:500]

        # 非429错误：指数退避等待
        await asyncio.sleep(_compute_retry_delay(base_delay, attempt, last_exc))

    raise RetryExhausted(max_attempts, last_exc)
```

**反馈机制的关键**：`prompt_builder(last_err_text)`在每次重试时用上次的Pydantic错误文本重建prompt。例如第一次输出`tags[0]`不是`"math:数学"`，第二次的prompt会包含"上一次输出被拒绝：tags[0]必须等于学科根 'math:数学'"。这让LLM有机会自我纠正。

### 13.3 速率限制的专用等待策略

```python
# retry.py:63-93
async def _sleep_for_rate_limit(exc, hit_count, *, base_sleep, max_sleep, stage_name):
    sleep_s = base_sleep * (2 ** max(hit_count - 1, 0))
    retry_after = exc.retry_after_seconds
    if retry_after is not None and retry_after > sleep_s:
        sleep_s = retry_after
    sleep_s = min(sleep_s, max_sleep)  # 单次上限30分钟

    logger.warning("llm_rate_limit_sleep", stage=stage_name, sleep_sec=sleep_s, ...)
    await asyncio.sleep(sleep_s)
    return sleep_s
```

**total_budget限制**：累计睡眠超过6小时才放弃。这是对"MiniMax 5小时4500次配额"场景的适配——如果只是配额临时耗尽，让它睡到恢复；如果真的永远不够，6小时后放弃并标记失败。

---

## 14. 断点恢复：State管理

**文件**：`src/sciforge/pipeline/resume_state.py`（302行）

### 14.1 设计思想

断点恢复的核心是"**状态可序列化+产物可重放**"。SciForge用双层恢复：

1. **State层**：`FileExtractionState`持久化到`state.json`，记录所有计数器和进度
2. **Mirror层**：每个Stage产出item时落盘到`items/<stage>/item_<id>.json`，恢复时按item_id顺序重放

### 14.2 原子写入

```python
# resume_state.py:161-169
def save_state(checkpoint_root, state):
    state.last_checkpoint_at = datetime.now()
    f = state_file(checkpoint_root, state.file_hash)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, f)  # 原子rename
```

**写tmp再os.replace**：保证任何时刻磁盘上都有一份完整的`state.json`或`.tmp`——不会出现写到一半的内容（原子替换是POSIX保证）。

### 14.3 节流保存

```python
# resume_state.py:191-217
async def save_state_locked(checkpoint_root, state, *, force=False,
                             min_interval_sec=3.0):
    if checkpoint_root is None:
        return
    async with _LOCKS.get(state.file_hash):
        if not force:
            now = time.monotonic()
            last = _LAST_SAVED_AT.get(state.file_hash, 0.0)
            if (now - last) < min_interval_sec:
                return  # 距上次刷盘<3秒→跳过
        save_state(checkpoint_root, state)
        _LAST_SAVED_AT[state.file_hash] = time.monotonic()
```

**节流的意义**：300页PDF可产生100+次state写入，每次都是磁盘I/O。3秒节流把100+次压缩为约`elapsed/3`次，不影响正确性（crash最多丢3秒进度）。`force=True`用于Producer完成/Stage完成/异常退出等关键点。

### 14.4 Schema版本检查

```python
# resume_state.py:76-138
_CURRENT_STATE_SCHEMA_VERSION = 2

class StateSchemaMismatch(Exception):
    """state.json schema与当前代码不兼容（需--restart）"""

def load_state(checkpoint_root, file_hash):
    data = json.loads(f.read_text())
    legacy_fields = ("order_index_next", "current_task2_block_idx", ...)
    has_legacy = any(k in data for k in legacy_fields)
    data_version = data.get("schema_version", 1)
    if has_legacy or data_version < _CURRENT_STATE_SCHEMA_VERSION:
        raise StateSchemaMismatch(
            f"检测到旧版state.json。请删除checkpoint后加--restart：\n"
            f"  rm -rf {checkpoint_root / file_hash}\n"
            f"  uv run sciforge parse run ... --restart"
        )
```

**不向后兼容的哲学**：与其花大量时间写迁移代码（旧state→新state），不如直接拒绝并提供清晰的解决方法。这在快速迭代的MVP阶段是正确的选择。

---

## 15. TagTree：标签树管理

**文件**：`src/sciforge/pipeline/tag_tree.py`（264行）

### 15.1 设计思想

TagTree是一个**纯内存数据结构**，专门为**prompt注入**而设计（不做检索查询），目的有两个：

1. **避免同义异名**：LLM在Stage2标注时，如果能看到已有tag树，就不会把"群"标为`groups:群`而另一个条目标为`group_theory:群论`。
2. **保持一致性**：跨条目、跨文件累积的tag树让整个知识图谱的标签体系自洽。

### 15.2 核心数据结构

```python
# tag_tree.py:29-36
@dataclass
class TagNode:
    segment: str = ""            # "algebra:代数"
    children: dict[str, TagNode] = field(default_factory=dict)
    item_count: int = 0          # 该路径下挂载的条目数
```

### 15.3 Prompt序列化——热度裁剪

```python
# tag_tree.py:96-132
def serialize_for_prompt(self, max_depth=4, max_nodes=200):
    # 1. 收集所有节点（带深度）
    nodes_with_depth: list[tuple[TagNode, int, TagNode]] = []
    self._collect_nodes(self.root, 0, max_depth, nodes_with_depth)

    # 2. 按item_count降序保留最热max_nodes个节点
    nodes_with_depth.sort(key=lambda x: -x[0].item_count)
    allowed = {id(node) for node, _, _ in nodes_with_depth[:max_nodes]}

    # 3. 递归输出（保留原始树结构，但只展示allowed中的节点）
    lines = []
    self._render(self.root, 0, max_depth, allowed, lines)

    # 被截断位置用"... (N more)"占位
```

**热度裁剪的设计动机**：tag树增长到几千个节点后，完整序列化会撑爆prompt token预算。按`item_count`降序保留最常用的200个节点，确保prompt注入的都是高频tag。

### 15.4 TagTreeSnapshot（REC-09读写隔离）

```python
# tag_tree.py:237-264
class TagTreeSnapshot:
    def __init__(self, live: TagTree, refresh_gap: int = 16):
        self._live = live
        self._snapshot = TagTree.from_json(live.to_json())
        self._snapshot_gen = live._generation

    def _maybe_refresh(self):
        if self._live._generation - self._snapshot_gen >= self._refresh_gap:
            self._snapshot = TagTree.from_json(self._live.to_json())
            self._snapshot_gen = self._live._generation

    def serialize_for_prompt(self, max_depth=4, max_nodes=200):
        self._maybe_refresh()
        return self._snapshot.serialize_for_prompt(max_depth, max_nodes)
```

**设计核心**：Stage3每条item入库都调用`insert_path`，每次都bump `_generation`。若Stage2直接读live树：(1)缓存每次都失效→每条item重新序列化整棵树；(2)序列化结果每条都变→LLM prompt的静态前缀永不命中缓存；(3)并发/未来多线程下读写交错不安全。

快照解耦：Stage2读独立的只读副本，缓存稳定、前缀稳定。代价是Stage2最多滞后`refresh_gap`条——可接受，因为Stage3还有确定性canonicalize做最后修正。

---

## 16. TagCanonicalizer：标签归一化

**文件**：`src/sciforge/pipeline/tag_canonicalizer.py`（84行）

### 16.1 设计思想

LLM标注产出的tag段可能存在同义异名问题：
- `"groups:群"` vs `"group_theory:群论"`（同一中文对应不同english）
- `"group_theory:群"` vs `"group_theory:群论"`（同一english对应不同中文）

TagCanonicalizer用**纯确定性规则**（不调LLM）消除这种不一致。

### 16.2 三层归并

```python
# tag_canonicalizer.py:26-84
class TagCanonicalizer:
    def __init__(self, alias_map=None):
        self._alias = {k.lower(): v for k, v in (alias_map or {}).items()}  # 别名表
        self._zh_to_en = {}  # 中文→规范english（first-seen-wins）
        self._en_to_zh = {}  # english→规范中文

    def canonicalize_segment(self, seg):
        if ":" not in seg:
            return seg
        en, zh = seg.split(":", 1)
        # 第一层：格式规范化（小写化、-→_、去空白）
        en = en.strip().lower().replace("-", "_").replace(" ", "_")
        # 第二层：别名表（groups→group_theory）
        en = self._alias.get(en, en)
        zh = zh.strip()

        # 第三层：双向一致性
        if zh in self._zh_to_en:
            en = self._zh_to_en[zh]   # 同一中文→复用既有english
        if en in self._en_to_zh:
            zh = self._en_to_zh[en]   # 同一english→复用既有中文

        # 注册
        self._zh_to_en.setdefault(zh, en)
        self._en_to_zh.setdefault(en, zh)
        return f"{en}:{zh}"
```

**`seed_from_tree()`**：把resume时从state加载的已有tag_tree注册为既有规范，保证续跑与历史一致。

---

## 17. TaxonomyValidator：分类路径校验

**文件**：`src/sciforge/pipeline/taxonomy_validator.py`（241行）

### 17.1 设计思想

TaxonomyValidator从`taxonomy.json`加载学科分类树，对LLM输出的`taxonomy_path`做三级匹配：

1. **精确匹配**：路径在合法集合中→直接通过
2. **前缀匹配**：LLM给了更深的路径→截断到最长合法前缀
3. **模糊匹配**：按段做Levenshtein编辑距离（单段≤2，总距离≤段数×2）

```python
# taxonomy_validator.py:96-134
def validate(self, raw_path, discipline=None):
    path = self._normalize(raw_path)  # 统一分隔符为'.'，小写化

    # 1. 精确匹配
    if path in self._valid_paths:
        return path, False

    # 2. 前缀匹配
    best_prefix = self._longest_valid_prefix(path)
    if best_prefix and _in_discipline(best_prefix):
        return best_prefix, True

    # 3. 模糊匹配（每段edit_distance ≤ 2）
    fuzzy = self._fuzzy_match(path)
    if fuzzy and _in_discipline(fuzzy):
        return fuzzy, True

    # 无匹配→保留原值
    return path, False
```

**REC-15跨学科保护**：模糊匹配的结果如果不在本学科根下，**拒绝修正**。防止一条math路径被模糊匹配到physics路径。

---

## 18. ReorderBuffer：并发保序

**文件**：`src/sciforge/pipeline/reorder_buffer.py`（88行）

### 18.1 设计思想

Stage2可以N-worker并发处理items（各item间无状态依赖），但Stage3Persist要求按`item_id`严格递增入库。ReorderBuffer解决这个矛盾：

```
Worker A完成item_id=5 → buf.put(5, result5) → pending{5: result5}
Worker B完成item_id=3 → buf.put(3, result3) → pending{3: result3}
                                         → _next_id=3有数据→释放3
                                         → _next_id=4无数据→阻塞
Worker C完成item_id=4 → buf.put(4, result4) → pending{4: result4}
                                         → _next_id=4有数据→释放4
                                         → _next_id=5有数据→释放5（连续释放）
```

### 18.2 核心实现

```python
# reorder_buffer.py:26-88
class ReorderBuffer:
    def __init__(self, q_out, start_id=0):
        self._q_out = q_out
        self._next_id = start_id
        self._pending: dict[int, Any] = {}
        self._skipped: set[int] = set()
        self._lock = asyncio.Lock()

    async def put(self, item_id, item):
        async with self._lock:
            self._pending[item_id] = item
            await self._flush()  # 连续释放所有可连续的项

    async def skip(self, item_id):
        async with self._lock:
            self._skipped.add(item_id)
            await self._flush()

    async def _flush(self):
        while True:
            if self._next_id in self._pending:
                await self._q_out.put(self._pending.pop(self._next_id))
                self._next_id += 1
            elif self._next_id in self._skipped:
                self._skipped.discard(self._next_id)
                self._next_id += 1
            else:
                break
```

**`skip()`的必要性**：上游Stage1Review拒绝的item造成item_id gap。如果不主动skip，`_next_id`永远卡在缺失id上，后续所有item积压到`close()`才释放。

---

## 19. DocumentSegmenter与ParallelExtract：段级并发

**文件**：`src/sciforge/pipeline/document_segmenter.py`（172行）+ `parallel_extract.py`（145行）

### 19.1 设计动机

对于5000+行的大文档，逐块流式串行提取太慢。DocumentSegmenter把文档按**section边界**切成互不重叠的独立段，每段交给ParallelExtract的独立worker并发提取。最后`assign_order_index()`按（段序，段内序）重排回文档顺序。

### 19.2 `segment_document()` —— 覆盖不变量

```python
# document_segmenter.py:39-138
def segment_document(lines, boundary_indices, *, min_lines=40, max_lines=400):
    # 1. 归一化切分点
    cuts = sorted({i for i in boundary_indices if 0 < i < n})
    starts = [0, *cuts]

    # 2. 原始段
    raw = [(s, starts[i+1] if i+1 < len(starts) else n) for i, s in enumerate(starts)]

    # 3. 合并过小段（前向累积直到≥min_lines）
    merged = []
    cur_s, cur_e = None, 0
    for s, e in raw:
        if cur_s is None:
            cur_s, cur_e = s, e
        else:
            cur_e = e  # 向后扩展
        if (cur_e - cur_s) >= min_lines:
            merged.append((cur_s, cur_e))
            cur_s = None
    # 剩余尾段<min_lines→并入前一段

    # 4. 拆分过大段（按max_lines硬切）
    split = []
    for s, e in merged:
        if (e - s) > max_lines:
            for cur in range(s, e, max_lines):
                split.append((cur, min(cur + max_lines, e)))
        else:
            split.append((s, e))

    # 5. 构建DocSegment

    # 6. 覆盖不变量断言
    assert segments[0].start_idx == 0
    assert segments[-1].end_idx == n
    for i, seg in enumerate(segments):
        if i + 1 < len(segments):
            assert seg.end_idx == segments[i + 1].start_idx
    assert total == n
    return segments
```

**`assert`强制覆盖不变量**：在开发阶段迅速暴露"漏行"或"重叠"的bug。生产环境中这些assert不应该失败（逻辑本身是确定性的）。

### 19.3 `ParallelSegmentExtractor`

```python
# parallel_extract.py:69-145
async def extract(self, lines, boundary_indices, *, start_order_index=0, min_segment_lines=40):
    grouped = await self.extract_grouped(lines, boundary_indices, ...)
    per_seg = [items for _, items in grouped]
    result = assign_order_index(per_seg, start=start_order_index)
    return result  # [(order_index, item), ...]
```

**生产路径**：提供`segment_extract_fn`（包Stage1Extract的carry-aware滑窗提取器），整段交给它自行处理。这避免了在chunk边界切断条目的问题（entry可能跨chunk，但几乎不会跨section）。

---

## 20. 后处理模块：确定性Post-Pass

所有post-pass都是**纯规则、无LLM调用、通过`asyncio.to_thread()`放线程池执行**。

### 20.1 NumberingAudit（编号连续性审计 #2/#8）

**文件**：`src/sciforge/pipeline/numbering_audit.py`

检测相邻条目的`seq_index`是否漏号（如1→2→4，可能漏了3）。写`numbering_report.json`但不修改数据库——由人工或后续自动化决定如何处理。

### 20.2 AnswerLinker（题答回填 #3）

**文件**：`src/sciforge/pipeline/answer_linker.py`

场景：章末/附录的"习题解答"部分，解答与题干分离。AnswerLinker用确定性编号匹配把答案并回题干`solution_content`，删除已被吸收的答案条目。

### 20.3 RelationInference（关系推断 #6）

**文件**：`src/sciforge/pipeline/relation_inference.py`

构建知识图谱边：
- `corollary_of`：推论→定理（编号前缀相同）
- `tests`：习题→定理（习题内容引用定理编号）
- `successor`：章节A的条目→章节B的同编号条目（跨章对应）

### 20.4 Reconciliation（文档级复核 #7）

**文件**：`src/sciforge/pipeline/reconciliation.py`

入库后的文档级复核：
- 检测重复条目（content_hash相同但item_id不同）
- 检测类型误判（KnowledgePoint标成了Question或反之）
- 支持`autofix`开关：开启时自动回改高置信的`kp_type_mismatch`

---

## 21. 设计总评：架构决策与权衡

### 21.1 整体评价

SciForge Pipeline是**工程实用主义**的成熟体现：

**亮点**：
1. **容错设计贯穿全链路**：SENTINEL死锁防御、降级直通（Review失败用原始items）、兜底标注（Annotate失败用fallback）、逐页回退（VLM内容审核误判）——没有单点把整个批次的处理搞崩。
2. **Pydantic强校验做最后防线**：LLM是不可靠的，但Pydantic是可靠的。从`_check_items_ordering`到`_check_exercise_near_tail`到零宽字符检测，schema层把LLM可能犯的各种错都拦住了。
3. **"零成本"的确定性先验注入**：`detect_section_boundaries`在Task0产出时就用正则解析章节结构，`parse_number_components`在Stage3确定性解析编号。这些信息不需要LLM"猜"，直接注入prompt作为权威上下文。
4. **背压+节流+并发度的精细控制**：Queue maxsize形成自然背压，`save_state_locked`的3秒节流避免高频磁盘写入，`max_concurrent_files`/`stage2_annotate_concurrency`的独立并发度控制。
5. **可开关的opt-in机制**：`intra_doc_parallel_extract`、`reconciliation_autofix`、`vlm_auto_text_detect`等都是默认关闭的opt-in特性，新功能不会破坏现有用户的行为。

**可改进点**：
1. **Stage1Extract的滑窗逻辑过于复杂**：三层防线+force_consume+fast_skip+REC-12文末强制提取——复杂度已经让修改变得危险。考虑是否可以用"整文档一次性LLM提取+后校验"的方式简化（代价是超长文档可能超出LLM上下文窗口）。
2. **断点恢复依赖mirror文件**：每个Stage的每个item都会写磁盘——理论上Stage3的content_hash去重可以替代mirror，但mirror仍然是快速判断"哪些item已完成"的最直接方式。
3. **ReorderBuffer的gap处理依赖上游主动skip**：如果上游逻辑修改后忘了skip，ReorderBuffer会永远阻塞。

### 21.2 改代码前的必读规则

1. **改queue payload类型前**：检查所有Stage的`isinstance(msg, ...)`分支和`Queue[object]`泛型标注。
2. **改Pydantic schema前**：确保`model_validate(context=...)`的调用方也传入了新字段需要的context。
3. **改Stage的LLM prompt前**：确保`templates/*.txt`的`{retry_feedback}`占位符还在——否则带反馈重试失效。
4. **加新Stage前**：确保`finally`块有`await out_queue.put(SENTINEL)`，确保orchestrator的启动顺序正确（下游先于上游）。
5. **改retry逻辑前**：注意429和普通错误的两条路径——429不计attempt，普通错误计attempt。不要把两类混在一起处理。
