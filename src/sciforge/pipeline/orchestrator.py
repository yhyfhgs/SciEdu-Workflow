"""PipelineOrchestrator — 5 阶段并发流水线编排器

架构:
  Task0 (Producer): 文档识别 → q_md
  Stage1Extract  : q_md  → q_raw   单条窗口提取
  Stage1Review   : q_raw → q_rev   单条校验
  Stage2Annotate : q_rev → q_ann   单条打标注
  Stage2Review   : q_ann → q_done  单条标注审核
  Stage3Persist  : q_done → DB     合并入库 + mirror + state

6 个 asyncio.Task 由 asyncio.gather 并发启动，4 个 stage queue 连接各 stage。
每个 Stage1Extract 批次由共享 OrderIndexAllocator 预留连续 item_id；
Stage2 可启用 N-worker 并发，并通过 ReorderBuffer 恢复下游顺序。

断点恢复：通过 state.stageN_completed + mirror items/<stage>/*.json，
检测到中断时从最近完成的阶段起点重放；`--restart` 用于强制清 checkpoint 重跑。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from sciforge.core.models import (
    AnnotatedItem,
    DirectoryProgress,
    Discipline,
    DocumentFormat,
    ExtractedBatch,
    Figure,
    FileExtractionState,
    KnowledgePoint,
    MdBlockSource,
    Question,
    ReviewedAnnotatedItem,
    ReviewedItem,
)
from sciforge.core.sentinel import SENTINEL
from sciforge.pipeline.answer_linker import link_answers
from sciforge.pipeline.numbering_audit import audit_numbering, write_numbering_report
from sciforge.pipeline.recognizers import (
    detect_section_boundaries,
    get_recognizer,
    pdf_page_count,
)
from sciforge.pipeline.reconciliation import reconcile
from sciforge.pipeline.relation_inference import infer_structural_relations
from sciforge.pipeline.resume_state import (
    compute_file_hash,
    enum_cached_batches_for_file,
    is_state_complete,
    load_state,
    recognize_dir,
    save_state_locked,
    state_dir,
)
from sciforge.pipeline.stages import (
    Stage1Extract,
    Stage1Review,
    Stage2Annotate,
    Stage2Review,
    Stage3Persist,
)
from sciforge.pipeline.tag_tree import TagTree, TagTreeSnapshot
from sciforge.storage.sqlite_backend import SQLiteStorageBackend

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig
    from sciforge.llm.client import LLMClient
    from sciforge.storage.protocol import BatchStorageBackend

logger = structlog.get_logger()


# ── 数据结构 ──────────────────────────────────────────


@dataclass
class PipelineResult:
    """单个 logical_document 的处理结果"""

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


@dataclass
class ResumeReplayState:
    """各 stage mirror 的最新 item_id，用于恢复挂起队列。"""

    stage1_last_item_id: int = -1
    stage1_review_last_item_id: int = -1
    stage2_last_item_id: int = -1
    stage2_review_last_item_id: int = -1
    persisted_last_item_id: int = -1


class NoopStorageBackend:
    """dry-run 存储后端：执行完整 pipeline，但不写入数据库。"""

    def save_batch(self, kps: list[KnowledgePoint], questions: list[Question]) -> int:
        return len(kps) + len(questions)

    def find_duplicates(self, content: str, item_type: str = "all") -> list[str]:
        del content, item_type
        return []

    def count_existing_content(self, contents: list[str], scope: str) -> int:
        del contents, scope
        return 0

    def save_figures(self, figures: list[Figure]) -> int:
        return len(figures)

    def save_question(self, question: Question) -> str:
        return question.id

    def load_question(self, id: str) -> Question:
        raise FileNotFoundError(f"Question {id} not found in dry-run storage")

    def search_questions(self, filters: dict[str, object]) -> list[str]:
        del filters
        return []

    def question_count(self) -> int:
        return 0

    def save_knowledge_point(self, kp: KnowledgePoint) -> str:
        return kp.id

    def load_knowledge_point(self, id: str) -> KnowledgePoint:
        raise FileNotFoundError(f"KnowledgePoint {id} not found in dry-run storage")

    def search_knowledge(self, filters: dict[str, object]) -> list[str]:
        del filters
        return []

    def knowledge_count(self) -> int:
        return 0


# ── 全局 order_index 分配器 ───────────────────────────


class OrderIndexAllocator:
    """跨文件全局单调 order_index 分配器

    在目录并发处理时，多个 process_file() 共享一个实例，
    避免各文件从相同 max_order_index 起步导致 ID 重复。
    """

    def __init__(self, start: int) -> None:
        self._next = start
        self._lock = asyncio.Lock()

    async def reserve(self, count: int) -> int:
        """预留 *count* 个连续 ID，返回起始值。

        count=0 时仍至少推进 1（用于获取初始起点）。
        """
        async with self._lock:
            base = self._next
            self._next += max(count, 1)
            return base

    @property
    def current(self) -> int:
        """当前下一个可用 ID（只读，不保证并发安全）"""
        return self._next


# ── Orchestrator ──────────────────────────────────────


class PipelineOrchestrator:
    """5 阶段并发流水线编排器"""

    _PART_RE = re.compile(r"^(.+?)_part_(\d+)_(\d+)$", re.IGNORECASE)
    _ITEM_FILE_RE = re.compile(r"^item_(\d+)\.json$")

    def __init__(
        self,
        config: AppConfig,
        llm_client: LLMClient | None = None,
        storage: BatchStorageBackend | None = None,
        db_path: Path | None = None,
        llm_clients: dict[str, LLMClient] | None = None,
        pdf_backend: str = "vlm",
        discipline: Discipline = Discipline.MATH,
    ) -> None:
        self.config = config
        self.llm = llm_client
        self.storage = storage
        self.db_path = db_path
        self._llm_clients = llm_clients or {}
        self._pdf_backend = pdf_backend
        self.discipline = discipline

        log_dir = str(config.llm_log_dir)
        log_interval = config.llm_log_interval
        for c in self._llm_clients.values():
            c.enable_pipeline_logging(log_dir, log_interval)
        if self.llm:
            self.llm.enable_pipeline_logging(log_dir, log_interval)

    def _get_llm(self, task: str) -> LLMClient | None:
        return self._llm_clients.get(task) or self.llm

    # ── 单 logical_document 入口 ──────────────────────

    async def process_file(
        self,
        file_path: Path | list[Path],
        *,
        order_index_allocator: OrderIndexAllocator | None = None,
    ) -> PipelineResult:
        """处理单个 logical_document（单文件或多个分卷）

        Args:
            order_index_allocator: 可选的全局 ID 分配器。process_directory() 并发处理
                多文件时传入共享实例，确保跨文件 order_index 不重复。
        """
        files = [file_path] if isinstance(file_path, Path) else list(file_path)
        if not files:
            raise ValueError("empty file list")

        primary_path = files[0]
        result = PipelineResult(file_path=str(primary_path))
        t0 = time.monotonic()

        # 1. file_hash + state
        file_hash = compute_file_hash(files)
        result.file_hash = file_hash

        state = load_state(self.config.checkpoint_dir, file_hash)
        if state is None:
            state = FileExtractionState(file_path=str(primary_path), file_hash=file_hash)

        if is_state_complete(state):
            if state.phase != "completed":
                state.phase = "completed"
                await save_state_locked(self.config.checkpoint_dir, state, force=True)
            logger.info("pipeline_file_already_done", file=primary_path.name)
            result.items_saved = state.stage3_persisted_count
            result.completed = True
            return result

        logger.info(
            "pipeline_file_start",
            file=primary_path.name,
            file_hash=file_hash,
            files_in_doc=len(files),
            resume=bool(state.vlm_batches_done),
        )

        # 2. storage
        local_backend: SQLiteStorageBackend | None = None
        storage_for_pipeline: BatchStorageBackend | None = self.storage
        if storage_for_pipeline is None and self.db_path is not None:
            local_backend = SQLiteStorageBackend(self.db_path)
            storage_for_pipeline = local_backend
        elif storage_for_pipeline is None:
            storage_for_pipeline = NoopStorageBackend()

        # 3. 各阶段 LLM（可按任务名配置独立 provider/model）
        extract_llm = self._get_llm("extract_and_classify")
        review_llm = self._get_llm("review") or extract_llm
        annotate_llm = self._get_llm("tag") or extract_llm
        annotate_review_llm = self._get_llm("annotate_review") or annotate_llm

        # 4. 构造共享 TagTree（从 DB 加载已有全局树）
        shared_tag_tree = TagTree()
        if state.tag_tree_json:
            try:
                shared_tag_tree = TagTree.from_json(state.tag_tree_json)
            except Exception:
                shared_tag_tree = TagTree()
        elif isinstance(storage_for_pipeline, SQLiteStorageBackend):
            tree_json = storage_for_pipeline.load_discipline_tag_tree(self.discipline.value)
            if tree_json:
                try:
                    shared_tag_tree = TagTree.from_json(tree_json)
                    logger.info(
                        "pipeline_global_tag_tree_loaded",
                        discipline=self.discipline.value,
                        nodes=shared_tag_tree.total_nodes(),
                    )
                except Exception:
                    shared_tag_tree = TagTree()

        # 5. 同步 next_item_id 起点（优先用共享 allocator，避免并发重复）
        if order_index_allocator is not None:
            # 目录并发模式：Stage1Extract 按批次动态 reserve，文件启动时不抢占 ID。
            pass
        elif isinstance(storage_for_pipeline, SQLiteStorageBackend):
            # 单文件模式：直接从 DB 读 max
            try:
                db_max = storage_for_pipeline.max_order_index(self.discipline.value)
                if db_max + 1 > state.next_item_id:
                    state.next_item_id = db_max + 1
                    state.last_persisted_item_id = max(state.last_persisted_item_id, db_max)
            except Exception:
                pass

        # 6. 构造 queue
        q_md: asyncio.Queue[object] = asyncio.Queue(maxsize=self.config.pipeline_queue_size)
        sq_size = int(getattr(self.config, "pipeline_stage_queue_size", 2))
        q_raw: asyncio.Queue[object] = asyncio.Queue(maxsize=sq_size)
        q_rev: asyncio.Queue[object] = asyncio.Queue(maxsize=sq_size)
        q_ann: asyncio.Queue[object] = asyncio.Queue(maxsize=sq_size)
        q_done: asyncio.Queue[object] = asyncio.Queue(maxsize=sq_size)

        tasks: list[asyncio.Task[None]] = []
        resume_state = self._build_resume_replay_state(state)
        state.last_persisted_item_id = max(
            state.last_persisted_item_id,
            resume_state.persisted_last_item_id,
        )

        try:
            # 7. 5 个 Stage（任一 LLM 缺失时走 drain 兜底）
            if extract_llm is None:
                logger.warning("pipeline_no_llm_drain_queue", file=primary_path.name)
                tasks.append(
                    asyncio.create_task(
                        self._drain_queue_chain(q_md, [q_raw, q_rev, q_ann, q_done]),
                        name="drain_chain",
                    )
                )
            else:
                s1_ex = Stage1Extract(
                    llm_client=extract_llm,
                    config=self.config,
                    checkpoint_root=self.config.checkpoint_dir,
                    discipline=self.discipline,
                    order_index_allocator=order_index_allocator,
                )
                s1_rev = Stage1Review(
                    llm_client=review_llm,  # type: ignore[arg-type]
                    config=self.config,
                    checkpoint_root=self.config.checkpoint_dir,
                    discipline=self.discipline,
                )
                # REC-09：Stage2 读 tag_tree 只读快照（消除 Stage3 每条 insert 触发的
                # 序列化缓存失效 + 读写隔离）；Stage3 仍持有 live 树做 insert_path。
                tag_tree_view = TagTreeSnapshot(
                    shared_tag_tree,
                    refresh_gap=int(getattr(self.config, "tag_tree_snapshot_refresh_gap", 16)),
                )
                s2_an = Stage2Annotate(
                    llm_client=annotate_llm,  # type: ignore[arg-type]
                    config=self.config,
                    checkpoint_root=self.config.checkpoint_dir,
                    discipline=self.discipline,
                    tag_tree=tag_tree_view,
                )
                s2_rev = Stage2Review(
                    llm_client=annotate_review_llm,  # type: ignore[arg-type]
                    config=self.config,
                    checkpoint_root=self.config.checkpoint_dir,
                    discipline=self.discipline,
                    tag_tree=tag_tree_view,
                )
                s3 = Stage3Persist(
                    storage=storage_for_pipeline,
                    config=self.config,
                    checkpoint_root=self.config.checkpoint_dir,
                    discipline=self.discipline,
                    tag_tree=shared_tag_tree,
                )

                tasks.append(asyncio.create_task(s3.run(q_done, state), name="stage3_persist"))
                await self._replay_stage_outputs(
                    state.file_hash,
                    "stage2_review",
                    ReviewedAnnotatedItem,
                    resume_state.persisted_last_item_id,
                    q_done,
                )
                tasks.append(
                    asyncio.create_task(s2_rev.run(q_ann, q_done, state), name="stage2_review")
                )
                await self._replay_stage_outputs(
                    state.file_hash,
                    "stage2",
                    AnnotatedItem,
                    resume_state.stage2_review_last_item_id,
                    q_ann,
                )
                tasks.append(
                    asyncio.create_task(s2_an.run(q_rev, q_ann, state), name="stage2_annotate")
                )
                await self._replay_stage_outputs(
                    state.file_hash,
                    "stage1_review",
                    ReviewedItem,
                    resume_state.stage2_last_item_id,
                    q_rev,
                )
                tasks.append(
                    asyncio.create_task(s1_rev.run(q_raw, q_rev, state), name="stage1_review")
                )
                await self._replay_stage1_as_batches(
                    state.file_hash,
                    resume_state.stage1_review_last_item_id,
                    q_raw,
                )
                tasks.append(
                    asyncio.create_task(s1_ex.run(q_md, q_raw, state), name="stage1_extract")
                )

            # 8. Task0 Producer
            if self._is_cache_complete(files, state):
                logger.info(
                    "pipeline_task0_skip_replay",
                    file=primary_path.name,
                    reason="task0_completed" if state.task0_completed else "cache_scan",
                )
                tasks.append(
                    asyncio.create_task(
                        self._task0_replay_from_cache(files, state, q_md, result),
                        name="task0_replay",
                    )
                )
            else:
                tasks.append(
                    asyncio.create_task(
                        self._task0_producer(files, state, q_md, result),
                        name="task0_producer",
                    )
                )

            # 9. 等待全部 Task
            await asyncio.gather(*tasks)

            # 10. tag_tree 回写 DB
            if (
                isinstance(storage_for_pipeline, SQLiteStorageBackend)
                and shared_tag_tree.total_nodes() > 0
            ):
                try:
                    storage_for_pipeline.save_discipline_tag_tree(
                        self.discipline.value, shared_tag_tree
                    )
                except Exception as exc:
                    logger.warning("tag_tree_save_failed", error=str(exc))

            # 10.5 编号连续性核对（#2/#8 召回兜底，不阻断入库）
            if isinstance(storage_for_pipeline, SQLiteStorageBackend):
                try:
                    # REC-17：全表读放线程池，避免阻塞目录并发下其他文件的协程
                    rows = await asyncio.to_thread(
                        storage_for_pipeline.numbering_rows,
                        str(primary_path),
                        self.discipline.value,
                    )
                    report = audit_numbering(rows)
                    write_numbering_report(report, self.config.checkpoint_dir, file_hash)
                    if report["suspected_gaps"]:
                        logger.warning(
                            "pipeline_numbering_gaps",
                            file=primary_path.name,
                            gap_groups=len(report["suspected_gaps"]),
                            sample=report["suspected_gaps"][:3],
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("numbering_audit_failed", error=str(exc))

            # 10.6 跨章节题答回填（#3 不分离）：章末/附录答案并回题干
            if isinstance(storage_for_pipeline, SQLiteStorageBackend) and getattr(
                self.config, "answer_linking_enabled", True
            ):
                try:
                    questions = await asyncio.to_thread(
                        storage_for_pipeline.load_questions_by_source,
                        str(primary_path),
                        self.discipline.value,
                    )
                    link = link_answers(questions)

                    def _apply_links() -> None:
                        for qid, sol in link.backfills:
                            storage_for_pipeline.update_question_solution(qid, sol)
                        for did in link.delete_ids:
                            storage_for_pipeline.delete_question(did)

                    # REC-17：回填/删除写操作整体放线程池
                    await asyncio.to_thread(_apply_links)
                    if link.backfills:
                        logger.info(
                            "pipeline_answer_linked",
                            file=primary_path.name,
                            linked=len(link.backfills),
                            removed=len(link.delete_ids),
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("answer_linking_failed", error=str(exc))

            # 10.7 结构化关系推断（#6 知识图谱）：corollary_of / tests / successor 边
            if isinstance(storage_for_pipeline, SQLiteStorageBackend) and getattr(
                self.config, "relation_inference_enabled", True
            ):
                try:
                    rel_rows = await asyncio.to_thread(
                        storage_for_pipeline.relation_rows, str(primary_path), self.discipline.value
                    )
                    edges = infer_structural_relations(rel_rows)
                    if edges:
                        added = storage_for_pipeline.save_relations(edges, self.discipline.value)
                        logger.info(
                            "pipeline_relations_inferred",
                            file=primary_path.name,
                            edges=len(edges),
                            added=added,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("relation_inference_failed", error=str(exc))

            # 10.8 文档级 reconciliation 复核（#7）：重复条目 + 类型误判，写报告不改库
            if isinstance(storage_for_pipeline, SQLiteStorageBackend) and getattr(
                self.config, "reconciliation_enabled", True
            ):
                try:
                    rec_rows = await asyncio.to_thread(
                        storage_for_pipeline.reconciliation_rows,
                        str(primary_path),
                        self.discipline.value,
                    )
                    report = reconcile(rec_rows)
                    import json as _json

                    rec_path = self.config.checkpoint_dir / file_hash / "reconciliation_report.json"
                    rec_path.parent.mkdir(parents=True, exist_ok=True)
                    rec_path.write_text(
                        _json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    s = report["summary"]
                    if s["duplicate_groups"] or s["type_mismatches"]:
                        logger.warning(
                            "pipeline_reconciliation_issues",
                            file=primary_path.name,
                            duplicate_groups=s["duplicate_groups"],
                            type_mismatches=s["type_mismatches"],
                        )
                    # REC-15：开启 autofix 时，把高置信 kp_type_mismatch 回改 DB
                    # （首标记强规则；category 跨表/近重复仍只报告，不自动改）
                    if getattr(self.config, "reconciliation_autofix", False):
                        fixes = [
                            m
                            for m in report["type_mismatches"]
                            if m.get("issue") == "kp_type_mismatch" and m.get("expected")
                        ]

                        def _apply_kp_fixes(items: list[dict[str, Any]]) -> None:
                            for m in items:
                                storage_for_pipeline.update_kp_type(m["id"], m["expected"])

                        if fixes:
                            await asyncio.to_thread(_apply_kp_fixes, fixes)
                            logger.info(
                                "pipeline_reconciliation_autofixed",
                                file=primary_path.name,
                                kp_type_fixed=len(fixes),
                            )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("reconciliation_failed", error=str(exc))

            # 11. 若 stage3 全部完成 → 标记 completed
            if is_state_complete(state):
                state.phase = "completed"
                await save_state_locked(self.config.checkpoint_dir, state, force=True)

        except BaseException as exc:
            logger.error("pipeline_task_exception", error=str(exc), exc_info=True)
            result.errors.append(str(exc))
            # 兜底：所有 queue 塞 SENTINEL 避免剩余协程卡死
            for q in (q_md, q_raw, q_rev, q_ann, q_done):
                try:
                    q.put_nowait(SENTINEL)
                except Exception:
                    pass
            for t in tasks:
                if not t.done():
                    t.cancel()
            try:
                await save_state_locked(self.config.checkpoint_dir, state, force=True)
            except Exception:
                pass
            raise
        finally:
            if local_backend is not None:
                local_backend.close()

        # 12. result 同步
        result.failed_batches = list(state.failed_batches)
        result.failed_blocks = list(state.failed_blocks)
        result.items_saved = state.stage3_persisted_count
        result.completed = is_state_complete(state)
        result.errors.extend(state.errors)
        result.elapsed_sec = time.monotonic() - t0

        logger.info(
            "pipeline_file_complete",
            file=primary_path.name,
            elapsed_sec=round(result.elapsed_sec, 2),
            blocks_pushed=result.blocks_pushed,
            items_saved=result.items_saved,
            failed_batches=len(result.failed_batches),
            failed_blocks=len(result.failed_blocks),
            errors=len(result.errors),
            completed=result.completed,
        )
        return result

    # ── Task0: Producer ───────────────────────────────

    async def _task0_producer(
        self,
        files: list[Path],
        state: FileExtractionState,
        queue: asyncio.Queue[object],
        result: PipelineResult,
    ) -> None:
        """识别每个文件每 N 页一批，按顺序推入 queue"""
        producer_had_error = False
        try:
            block_index = 0
            done_keys = {tuple(t) for t in state.vlm_batches_done}

            for file_idx, f in enumerate(files):
                fmt = self._detect_format(f)
                output_dir = recognize_dir(
                    self.config.checkpoint_dir, state.file_hash, file_idx, f.stem
                )
                already_done_pairs: list[tuple[int, int]] = [
                    (ps, pe) for (fi, ps, pe) in done_keys if fi == file_idx
                ]

                vlm_llm = self._get_llm("vlm_convert")
                try:
                    recognizer = get_recognizer(
                        fmt,
                        self._pdf_backend,
                        vlm_llm,
                        self.config,
                        discipline=self.discipline,
                        file_path=f,
                    )
                except Exception as exc:
                    logger.error(
                        "pipeline_recognizer_init_failed",
                        file=f.name,
                        format=fmt.value,
                        error=str(exc),
                    )
                    result.errors.append(f"recognizer_init({f.name}): {exc}")
                    producer_had_error = True
                    continue

                logger.info(
                    "task0_file_start",
                    file=f.name,
                    file_idx=file_idx,
                    format=fmt.value,
                    backend=self._pdf_backend,
                    cached_batches=len(already_done_pairs),
                )

                async for batch in recognizer.recognize_in_batches(
                    f,
                    batch_pages=self.config.pages_per_batch,
                    output_dir=output_dir,
                    already_done=already_done_pairs,
                    max_retries=self.config.vlm_max_retries,
                ):
                    if batch.is_failed:
                        failed_key = (file_idx, batch.page_start, batch.page_end)
                        if failed_key not in {tuple(t) for t in state.failed_batches}:
                            state.failed_batches.append(failed_key)
                        producer_had_error = True
                        result.failed_batches.append(failed_key)
                        result.errors.append(
                            f"batch_failed({f.name}): pages {batch.page_start}-{batch.page_end}"
                        )
                        continue

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
                    result.blocks_pushed = block_index
                    result.pages_recognized = max(result.pages_recognized, batch.page_end)

                    key = (file_idx, batch.page_start, batch.page_end)
                    if key not in done_keys:
                        done_keys.add(key)
                        state.vlm_batches_done.append(key)
                        await save_state_locked(self.config.checkpoint_dir, state)

                    await queue.put(msb)

        except Exception as exc:
            logger.error("task0_producer_failed", error=str(exc), exc_info=True)
            result.errors.append(f"producer: {exc}")
            producer_had_error = True
        finally:
            if not producer_had_error:
                state.task0_completed = True
            try:
                await save_state_locked(self.config.checkpoint_dir, state, force=True)
            except Exception:
                pass
            await queue.put(SENTINEL)

    # ── Task0: Replay (已识别直通) ──────────────────────

    async def _task0_replay_from_cache(
        self,
        files: list[Path],
        state: FileExtractionState,
        queue: asyncio.Queue[object],
        result: PipelineResult,
    ) -> None:
        """不打开原始文件，直接从缓存 md 按顺序推入 queue"""
        replay_had_error = False
        try:
            block_index = 0
            for file_idx, f in enumerate(files):
                batches = enum_cached_batches_for_file(
                    self.config.checkpoint_dir, state.file_hash, file_idx, f.stem
                )
                if not batches:
                    logger.warning("task0_replay_cache_missing", file=f.name)
                    replay_had_error = True
                    continue

                for page_start, page_end, mdp in batches:
                    try:
                        text = mdp.read_text(encoding="utf-8")
                    except OSError as exc:
                        logger.warning(
                            "task0_replay_read_failed",
                            file=f.name,
                            md=mdp.name,
                            error=str(exc),
                        )
                        replay_had_error = True
                        continue
                    if not text.strip():
                        continue

                    msb = MdBlockSource(
                        file_path=str(f),
                        page_start=page_start,
                        page_end=page_end,
                        md_path=str(mdp),
                        md_text=text,
                        block_index=block_index,
                        section_boundaries=detect_section_boundaries(text.splitlines()),
                    )
                    block_index += 1
                    result.blocks_pushed = block_index
                    result.pages_recognized = max(result.pages_recognized, page_end)
                    await queue.put(msb)

            logger.info(
                "task0_replay_done",
                file_hash=state.file_hash,
                blocks=block_index,
            )
        except Exception as exc:
            logger.error("task0_replay_failed", error=str(exc), exc_info=True)
            result.errors.append(f"replay: {exc}")
            replay_had_error = True
        finally:
            if not replay_had_error:
                state.task0_completed = True
            try:
                await save_state_locked(self.config.checkpoint_dir, state, force=True)
            except Exception:
                pass
            await queue.put(SENTINEL)

    def _is_cache_complete(
        self,
        files: list[Path],
        state: FileExtractionState,
    ) -> bool:
        if state.task0_completed:
            return True
        if state.failed_batches:
            return False

        for file_idx, f in enumerate(files):
            batches = [
                (ps, pe)
                for ps, pe, _ in enum_cached_batches_for_file(
                    self.config.checkpoint_dir, state.file_hash, file_idx, f.stem
                )
            ]
            if not batches:
                return False

            fmt = self._detect_format(f)
            if fmt in (DocumentFormat.MARKDOWN, DocumentFormat.TEX):
                if len(batches) != 1:
                    return False
                continue
            if fmt == DocumentFormat.PDF:
                total_pages = pdf_page_count(f)
                if total_pages is None:
                    return False
                base = batches[0][0]
                if base not in (0, 1):
                    return False
                expected = base
                last_pe = -1
                for ps, pe in batches:
                    if ps != expected:
                        return False
                    last_pe = pe
                    expected = pe + 1
                target = total_pages if base == 1 else total_pages - 1
                if last_pe != target:
                    return False
                continue
            return False
        return True

    # ── 兜底：无 LLM 场景 drain 整条 pipeline ───────────

    async def _drain_queue_chain(
        self,
        source: asyncio.Queue[object],
        downstream: list[asyncio.Queue[object]],
    ) -> None:
        """无 LLM 场景：把 source 的所有 msg 抛掉，然后给 downstream 依次 put SENTINEL"""
        drained = 0
        while True:
            msg = await source.get()
            if msg is SENTINEL:
                break
            drained += 1
        for q in downstream:
            await q.put(SENTINEL)
        logger.info("pipeline_drain_done", blocks_drained=drained)

    # ── 目录处理 ──────────────────────────────────────

    async def process_directory(
        self,
        dir_path: Path,
        # REC-30：与 AppConfig.max_concurrent_files 默认对齐（消除 4 vs 8 漂移）
        max_concurrent_files: int = 8,
    ) -> list[PipelineResult]:
        files = sorted(self._collect_files(dir_path), key=self._natural_sort_key)
        grouped = self._group_part_files(files)

        dir_progress = self._load_directory_progress(dir_path)

        work_items: list[tuple[int, Path | list[Path], str]] = []
        progress_dirty = False
        for i, item in enumerate(grouped):
            pkey = str(item[0]) if isinstance(item, list) else str(item)
            try:
                cur_hash = compute_file_hash(item if isinstance(item, list) else item)
            except Exception:
                cur_hash = ""
            if (
                pkey in dir_progress.completed_files
                and dir_progress.completed_files[pkey] == cur_hash
                and cur_hash
            ):
                if self._has_completed_checkpoint(cur_hash):
                    logger.debug("pipeline_dir_skip_completed", file=pkey)
                    continue
                logger.warning(
                    "pipeline_dir_stale_completed_entry",
                    file=pkey,
                    file_hash=cur_hash,
                )
                dir_progress.completed_files.pop(pkey, None)
                progress_dirty = True
            work_items.append((i, item, pkey))

        if progress_dirty:
            self._save_directory_progress(dir_path, dir_progress)

        logger.info(
            "pipeline_directory_start",
            dir=str(dir_path),
            total_groups=len(grouped),
            remaining=len(work_items),
            skipped=len(grouped) - len(work_items),
            max_concurrent=max_concurrent_files,
        )

        # 全局 order_index 分配器：避免并发文件分配到重复 ID
        storage_for_dir: SQLiteStorageBackend | None = (
            self.storage if isinstance(self.storage, SQLiteStorageBackend) else None
        )
        if storage_for_dir is None and self.db_path is not None:
            storage_for_dir = SQLiteStorageBackend(self.db_path)

        def _scan_allocator_start() -> int:
            """预扫 DB MAX(order_index) + 各文件 checkpoint/镜像最大 id（确定分配起点）。"""
            result = 0
            if storage_for_dir is not None:
                try:
                    result = storage_for_dir.max_order_index(self.discipline.value) + 1
                except Exception:
                    result = 0
            for _, item, _ in work_items:
                try:
                    item_hash = compute_file_hash(item if isinstance(item, list) else item)
                    item_state = load_state(self.config.checkpoint_dir, item_hash)
                    if item_state is not None:
                        result = max(
                            result,
                            item_state.next_item_id,
                            item_state.last_persisted_item_id + 1,
                        )
                    for stage_dir in (
                        "stage1",
                        "stage1_review",
                        "stage2",
                        "stage2_review",
                        "persisted",
                    ):
                        result = max(
                            result, self._latest_mirrored_item_id(item_hash, stage_dir) + 1
                        )
                except Exception:
                    pass
            return result

        # REC-17：整段预扫是 O(总条目数) 的同步磁盘遍历，放线程池避免阻塞 event loop 启动
        allocator_start = await asyncio.to_thread(_scan_allocator_start)
        allocator = OrderIndexAllocator(start=allocator_start)

        sem = asyncio.Semaphore(max_concurrent_files)
        results: list[PipelineResult] = [
            PipelineResult(file_path=pkey) for _, _, pkey in work_items
        ]

        async def _process_one(idx: int, item: Path | list[Path], pkey: str) -> None:
            async with sem:
                try:
                    r = await self.process_file(item, order_index_allocator=allocator)
                    results[idx] = r
                    if r.completed:
                        dir_progress.completed_files[pkey] = r.file_hash
                        dir_progress.failed_files.pop(pkey, None)
                    else:
                        dir_progress.completed_files.pop(pkey, None)
                        dir_progress.failed_files[pkey] = "incomplete"
                    self._save_directory_progress(dir_path, dir_progress)
                except Exception as exc:
                    logger.error("pipeline_file_failed", file=pkey, error=str(exc))
                    results[idx] = PipelineResult(file_path=pkey, errors=[str(exc)])
                    dir_progress.completed_files.pop(pkey, None)
                    dir_progress.failed_files[pkey] = str(exc)
                    self._save_directory_progress(dir_path, dir_progress)

        tasks = [_process_one(idx, item, pkey) for idx, (_, item, pkey) in enumerate(work_items)]
        await asyncio.gather(*tasks)
        return results

    # ── 工具方法 ──────────────────────────────────────

    @classmethod
    def _group_part_files(cls, files: list[Path]) -> list[Path | list[Path]]:
        """将分卷文件按系列分组，保持原始顺序"""
        groups: dict[str, list[tuple[int, Path]]] = {}
        standalone: list[tuple[int, Path]] = []

        for idx, f in enumerate(files):
            m = cls._PART_RE.match(f.stem)
            if m:
                key = f"{f.parent}/{m.group(1)}{f.suffix}"
                groups.setdefault(key, []).append((int(m.group(2)), f))
            else:
                standalone.append((idx, f))

        all_entries: list[tuple[int, Path | list[Path]]] = []
        for entries in groups.values():
            entries.sort(key=lambda x: x[0])
            first_idx = files.index(entries[0][1])
            all_entries.append((first_idx, [e[1] for e in entries]))
        for idx, f in standalone:
            all_entries.append((idx, f))
        all_entries.sort(key=lambda x: x[0])
        return [e[1] for e in all_entries]

    @staticmethod
    def _detect_format(file_path: Path) -> DocumentFormat:
        suffix = file_path.suffix.lower()
        return {
            ".tex": DocumentFormat.TEX,
            ".md": DocumentFormat.MARKDOWN,
            ".markdown": DocumentFormat.MARKDOWN,
            ".pdf": DocumentFormat.PDF,
            ".docx": DocumentFormat.DOCX,
            ".csv": DocumentFormat.CSV,
            ".pptx": DocumentFormat.PPTX,
            ".epub": DocumentFormat.EPUB,
        }.get(suffix, DocumentFormat.MARKDOWN)

    @staticmethod
    def _collect_files(dir_path: Path) -> list[Path]:
        extensions = {".tex", ".md", ".markdown", ".pdf", ".docx", ".pptx"}
        return [
            p
            for p in dir_path.rglob("*")
            if p.is_file() and p.suffix.lower() in extensions and not p.name.startswith(".")
        ]

    @staticmethod
    def _natural_sort_key(path: Path) -> tuple[str, list[int | str]]:
        parts: list[int | str] = []
        for seg in re.split(r"(\d+)", path.stem):
            parts.append(int(seg) if seg.isdigit() else seg.lower())
        return (str(path.parent), parts)

    def _load_directory_progress(self, dir_path: Path) -> DirectoryProgress:
        import hashlib
        import json

        dir_hash = hashlib.sha256(str(dir_path.resolve()).encode()).hexdigest()[:12]
        progress_file = self.config.checkpoint_dir / f"dir_{dir_hash}.json"
        if progress_file.exists():
            try:
                data = json.loads(progress_file.read_text(encoding="utf-8"))
                return DirectoryProgress(**data)
            except Exception:
                pass
        return DirectoryProgress(dir_path=str(dir_path))

    def _save_directory_progress(self, dir_path: Path, progress: DirectoryProgress) -> None:
        import hashlib

        dir_hash = hashlib.sha256(str(dir_path.resolve()).encode()).hexdigest()[:12]
        self.config.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        progress_file = self.config.checkpoint_dir / f"dir_{dir_hash}.json"
        progress_file.write_text(progress.model_dump_json(indent=2), encoding="utf-8")

    def _build_resume_replay_state(self, state: FileExtractionState) -> ResumeReplayState:
        persisted_mirror_last = self._latest_mirrored_item_id(state.file_hash, "persisted")
        return ResumeReplayState(
            stage1_last_item_id=self._latest_mirrored_item_id(state.file_hash, "stage1"),
            stage1_review_last_item_id=self._latest_mirrored_item_id(
                state.file_hash, "stage1_review"
            ),
            stage2_last_item_id=self._latest_mirrored_item_id(state.file_hash, "stage2"),
            stage2_review_last_item_id=self._latest_mirrored_item_id(
                state.file_hash, "stage2_review"
            ),
            persisted_last_item_id=max(state.last_persisted_item_id, persisted_mirror_last),
        )

    async def _replay_stage_outputs(
        self,
        file_hash: str,
        stage_dir: str,
        model_cls: type[object],
        after_item_id: int,
        out_queue: asyncio.Queue[object],
    ) -> None:
        replayed = 0
        for item_id, payload_path in self._iter_mirrored_item_files(file_hash, stage_dir):
            if item_id <= after_item_id:
                continue
            try:
                payload_text = payload_path.read_text(encoding="utf-8")
                payload = model_cls.model_validate_json(payload_text)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning(
                    "pipeline_resume_replay_failed",
                    stage=stage_dir,
                    item_id=item_id,
                    path=str(payload_path),
                    error=str(exc),
                )
                continue
            await out_queue.put(payload)
            replayed += 1

        if replayed > 0:
            logger.info(
                "pipeline_resume_replay_done",
                file_hash=file_hash,
                stage=stage_dir,
                after_item_id=after_item_id,
                replayed=replayed,
            )

    async def _replay_stage1_as_batches(
        self,
        file_hash: str,
        after_item_id: int,
        out_queue: asyncio.Queue[object],
    ) -> None:
        """把 stage1 镜像（每 item 一个 dict 文件）按 item 包成单元素 ExtractedBatch 重放进 q_raw。

        v3 批量化后 q_raw 单元为 ExtractedBatch；resume 时把每条已镜像但 stage1_review
        尚未消费的 item 包成单元素 batch 推入。注意此路径下 review 拿不到原始 window_lines
        （为节省镜像体积，stage1 mirror 不存 window 全文），review 会因原文比对不可用而
        近似直通；这是 resume 边缘场景的可接受妥协。
        """
        import json as _json_mod

        from sciforge.pipeline.llm_schemas import SingleItemExtractLLM

        replayed = 0
        for item_id, payload_path in self._iter_mirrored_item_files(file_hash, "stage1"):
            if item_id <= after_item_id:
                continue
            try:
                data = _json_mod.loads(payload_path.read_text(encoding="utf-8"))
                # 兼容老格式（ExtractedSingleItem dump）和新格式（手写 dict）
                if "item" in data and isinstance(data["item"], dict):
                    item = SingleItemExtractLLM.model_validate(data["item"])
                else:
                    raise ValueError("snapshot 缺少 'item' 字段")
                window_line_range = data.get("window_line_range") or [0, 0]
                if isinstance(window_line_range, list):
                    window_line_range_t = (
                        int(window_line_range[0]),
                        int(window_line_range[1]),
                    )
                else:
                    window_line_range_t = (0, 0)
                batch = ExtractedBatch(
                    item_ids=[item_id],
                    items=[item],
                    window_lines=list(data.get("window_lines", [])),
                    window_line_range=window_line_range_t,
                    window_last_line=int(data.get("window_last_line", 0)),
                    # REC-11：恢复 stop_reason，使 Stage1Review 条件化跳过判定不被默认值污染
                    stop_reason=str(data.get("stop_reason", "all_complete")),
                    block_index=int(data.get("block_index", 0)),
                    source_file=str(data.get("source_file", "")),
                    page_start=data.get("page_start"),
                    page_end=data.get("page_end"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "pipeline_resume_stage1_replay_failed",
                    item_id=item_id,
                    path=str(payload_path),
                    error=str(exc),
                )
                continue
            await out_queue.put(batch)
            replayed += 1

        if replayed > 0:
            logger.info(
                "pipeline_resume_replay_done",
                file_hash=file_hash,
                stage="stage1",
                after_item_id=after_item_id,
                replayed=replayed,
            )

    def _iter_mirrored_item_files(self, file_hash: str, stage_dir: str) -> list[tuple[int, Path]]:
        target_dir = self.config.checkpoint_dir / file_hash / "items" / stage_dir
        if not target_dir.exists():
            return []

        results: list[tuple[int, Path]] = []
        for payload_path in sorted(target_dir.glob("item_*.json")):
            match = self._ITEM_FILE_RE.match(payload_path.name)
            if match is None:
                continue
            results.append((int(match.group(1)), payload_path))
        return results

    def _latest_mirrored_item_id(self, file_hash: str, stage_dir: str) -> int:
        files = self._iter_mirrored_item_files(file_hash, stage_dir)
        if not files:
            return -1
        return files[-1][0]

    def _has_completed_checkpoint(self, file_hash: str) -> bool:
        try:
            return is_state_complete(load_state(self.config.checkpoint_dir, file_hash))
        except Exception as exc:
            logger.warning(
                "pipeline_dir_progress_state_check_failed",
                file_hash=file_hash,
                error=str(exc),
            )
            return False

    # ── 清理 checkpoint（--restart 用）──────────────────

    def purge_checkpoint(self, file_path: Path | list[Path]) -> None:
        files = [file_path] if isinstance(file_path, Path) else list(file_path)
        file_hash = compute_file_hash(files)
        d = state_dir(self.config.checkpoint_dir, file_hash)
        if d.exists():
            import shutil

            shutil.rmtree(d)
            logger.info("checkpoint_purged", file_hash=file_hash, path=str(d))

    def purge_directory_checkpoints(self, dir_path: Path) -> None:
        import hashlib

        files = sorted(self._collect_files(dir_path), key=self._natural_sort_key)
        grouped = self._group_part_files(files)
        for item in grouped:
            try:
                self.purge_checkpoint(item)
            except Exception as exc:
                logger.warning(
                    "purge_checkpoint_failed",
                    file=str(item[0]) if isinstance(item, list) else str(item),
                    error=str(exc),
                )

        dir_hash = hashlib.sha256(str(dir_path.resolve()).encode()).hexdigest()[:12]
        progress_file = self.config.checkpoint_dir / f"dir_{dir_hash}.json"
        if progress_file.exists():
            progress_file.unlink()
            logger.info("directory_progress_purged", path=str(progress_file))
