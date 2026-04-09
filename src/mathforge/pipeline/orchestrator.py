"""PipelineOrchestrator — asyncio.Queue 流水线编排器

新架构：
  Task0 [VlmStream] → StreamingLineBuffer（可选）
  Task1 [ChunkExtractor] → QueueA(ChunkExtractionResult)
  Task2 [ItemProcessor + TreeBuilder] → QueueB + DocumentTree
  Task3 [collect backpressure]
  Post: GraphBuilder (tree → graph) + EntityBuilder → Storage
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from mathforge.core.models import (
    ChunkExtractionResult,
    DirectoryProgress,
    DocumentFormat,
    ExtractedItem,
    KnowledgePoint,
    PipelineRunProgress,
    Question,
    Solution,
)
from mathforge.pipeline.chunk_extractor import ChunkExtractor, StreamingLineBuffer
from mathforge.pipeline.entity_builder import build_entities
from mathforge.pipeline.graph_builder import GraphBuilder
from mathforge.pipeline.item_unifier import ItemUnifier
from mathforge.pipeline.metrics import LiveProgress
from mathforge.pipeline.tree_builder import TreeBuilder

if TYPE_CHECKING:
    from mathforge.config.settings import AppConfig
    from mathforge.llm.client import LLMClient
    from mathforge.pipeline.agents.item_processor import ItemProcessingResult
    from mathforge.storage.protocol import StorageBackend

logger = structlog.get_logger()


@dataclass
class PipelineResult:
    """单个文件的处理结果"""

    file_path: str = ""
    chunks_count: int = 0
    items_extracted: int = 0
    items_processed: int = 0
    items_stored: int = 0
    knowledge_points: list[KnowledgePoint] = field(default_factory=list)
    questions: list[tuple[Question, Solution | None]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    extract_sec: float = 0.0
    process_sec: float = 0.0
    graph_sec: float = 0.0


# Queue 消息类型

class _Sentinel:
    __slots__ = ()


_SENTINEL = _Sentinel()
_QueueA_Item = ChunkExtractionResult | _Sentinel
_QueueB_Item = tuple[ExtractedItem, "ItemProcessingResult"] | _Sentinel


def _fallback_proc(item: ExtractedItem) -> ItemProcessingResult:
    """无 LLM 时的 fallback 处理结果"""
    from mathforge.core.models import CorrectionResult, ItemProcessingResult, TagSet

    return ItemProcessingResult(
        correction=CorrectionResult(
            corrected_content=item.content,
            overall_quality="D",
        ),
        classification=TagSet(),
        item_subtype=item.item_type,
    )


class PipelineOrchestrator:
    """流水线编排器"""

    def __init__(
        self,
        config: AppConfig,
        llm_client: LLMClient | None = None,
        storage: StorageBackend | None = None,
        llm_clients: dict[str, LLMClient] | None = None,
        pdf_backend: str = "auto",
    ) -> None:
        self.config = config
        self.llm = llm_client
        self.storage = storage
        self._llm_clients = llm_clients or {}
        self._pdf_backend = pdf_backend

    def _get_llm(self, task: str) -> LLMClient | None:
        return self._llm_clients.get(task) or self.llm

    async def process_file(
        self, file_path: Path, *, content_override: str | None = None
    ) -> PipelineResult:
        """处理单个文件"""
        from mathforge.pipeline.metrics import PipelineMetrics

        t0 = time.monotonic()
        result = PipelineResult(file_path=str(file_path))
        metrics = PipelineMetrics()
        metrics.start()

        # 检测格式
        source_format = self._detect_format(file_path)

        # 判断流式 VLM
        use_streaming_vlm = (
            self.config.vlm_streaming
            and self._pdf_backend == "vlm"
            and source_format in (DocumentFormat.PDF, DocumentFormat.DOCX)
            and content_override is None
        )
        vlm_llm = self._get_llm("vlm_convert") if use_streaming_vlm else None
        use_streaming_vlm = use_streaming_vlm and vlm_llm is not None and vlm_llm.supports_vision

        if use_streaming_vlm:
            content = None
            file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()[:16]
            logger.info(
                "pipeline_file_start",
                file=file_path.name,
                format=source_format.value,
                mode="streaming_vlm",
            )
        else:
            if content_override is not None:
                content = content_override
            else:
                content = await self._convert_to_text(file_path, source_format)
            file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
            logger.info(
                "pipeline_file_start",
                file=file_path.name,
                format=source_format.value,
                lines=len(content.splitlines()),
            )

        # Checkpoint
        progress = self._load_checkpoint(file_hash)
        if progress and progress.stage == "completed":
            logger.info("pipeline_file_skipped_completed", file=file_path.name)
            return result

        # Queues
        queue_a: asyncio.Queue[_QueueA_Item] = asyncio.Queue(
            maxsize=self.config.pipeline_queue_size
        )
        queue_b: asyncio.Queue[_QueueB_Item] = asyncio.Queue(
            maxsize=self.config.pipeline_queue_size
        )

        # 共享状态
        timing: dict[str, float] = {}
        collected: list[tuple[ExtractedItem, ItemProcessingResult]] = []
        completed_chunk_ids: list[str] = list(progress.completed_block_ids) if progress else []
        tree_builder = TreeBuilder(str(file_path), storage=self.storage)

        # 如果有 checkpoint 中的 tree，恢复
        if progress and progress.tree_state:
            try:
                tree_builder = TreeBuilder.deserialize(
                    progress.tree_state, str(file_path), storage=self.storage
                )
            except Exception:
                logger.warning("tree_state_restore_failed", exc_info=True)

        # 进度计数器
        live = LiveProgress()
        stop_event = asyncio.Event()

        # 构建 Tasks
        if use_streaming_vlm:
            line_buffer = StreamingLineBuffer()
            tasks = [
                asyncio.create_task(
                    self._task_vlm_stream(file_path, line_buffer, live=live)
                ),
                asyncio.create_task(
                    self._task_chunk_extract(
                        file_path, None, source_format, queue_a, result, timing,
                        live=live, line_buffer=line_buffer,
                    )
                ),
                asyncio.create_task(
                    self._task_process_tree(
                        queue_a, queue_b, result, timing, collected,
                        completed_chunk_ids, file_hash, tree_builder,
                        live=live,
                    )
                ),
                asyncio.create_task(self._task_collect(queue_b, timing)),
            ]
            task_names = ["vlm_stream", "chunk_extract", "process_tree", "collect"]
        else:
            tasks = [
                asyncio.create_task(
                    self._task_chunk_extract(
                        file_path, content, source_format, queue_a, result, timing,
                        live=live,
                    )
                ),
                asyncio.create_task(
                    self._task_process_tree(
                        queue_a, queue_b, result, timing, collected,
                        completed_chunk_ids, file_hash, tree_builder,
                        live=live,
                    )
                ),
                asyncio.create_task(self._task_collect(queue_b, timing)),
            ]
            task_names = ["chunk_extract", "process_tree", "collect"]

        daemon = asyncio.create_task(
            self._progress_daemon(queue_a, queue_b, live, stop_event)
        )

        # 等待
        task_results = await asyncio.gather(*tasks, return_exceptions=True)
        stop_event.set()
        await daemon
        for i, tr in enumerate(task_results):
            if isinstance(tr, BaseException):
                tname = task_names[i] if i < len(task_names) else f"task_{i}"
                logger.error("pipeline_task_failed", task=tname, error=str(tr))
                result.errors.append(f"task_{tname}: {tr}")

        # Graph 构建（基于 tree）
        t_graph = time.monotonic()
        if collected:
            try:
                unifier = ItemUnifier()
                unified_items = unifier.unify_batch([
                    (ei.to_raw_item(), pr, ChunkExtractionResult(chunk_id=ei.source_chunk_id))
                    for ei, pr in collected
                ])

                graph_llm = self._get_llm("infer_relations")
                graph_builder = GraphBuilder(graph_llm)
                doc_tree = tree_builder.get_tree()

                if graph_llm:
                    unified_items = await graph_builder.build_relations(
                        doc_tree, unified_items
                    )
                else:
                    unified_items = graph_builder.build_relations_sync(
                        doc_tree, unified_items
                    )

                kps, qpairs = build_entities(unified_items)
                result.knowledge_points = kps
                result.questions = qpairs

                self._update_relations(kps, qpairs)
            except Exception as e:
                logger.error("pipeline_graph_failed", exc_info=True)
                result.errors.append(str(e))

        timing["graph"] = time.monotonic() - t_graph

        # 最终 checkpoint
        self._save_checkpoint(
            file_hash, result,
            stage="completed",
            completed_chunk_ids=completed_chunk_ids,
            tree_state=tree_builder.serialize(),
        )

        metrics.finish()
        metrics.log_summary()
        try:
            metrics.save(self.config.checkpoint_dir / file_hash / "metrics.json")
        except Exception:
            pass

        result.elapsed_sec = time.monotonic() - t0
        result.extract_sec = timing.get("extract", 0.0)
        result.process_sec = timing.get("process", 0.0)
        result.graph_sec = timing.get("graph", 0.0)

        logger.info(
            "pipeline_complete",
            file=file_path.name,
            total_sec=round(result.elapsed_sec, 2),
            extract_sec=round(result.extract_sec, 2),
            process_sec=round(result.process_sec, 2),
            graph_sec=round(result.graph_sec, 2),
            chunks=result.chunks_count,
            items=result.items_extracted,
            stored=result.items_stored,
            kps=len(result.knowledge_points),
            questions=len(result.questions),
            errors=len(result.errors),
        )

        return result

    # ── Task 1: Chunk 提取 ──

    async def _task_chunk_extract(
        self,
        file_path: Path,
        content: str | None,
        source_format: DocumentFormat,
        queue_a: asyncio.Queue,
        result: PipelineResult,
        timing: dict[str, float],
        *,
        live: LiveProgress | None = None,
        line_buffer: StreamingLineBuffer | None = None,
    ) -> None:
        """Task 1: ChunkExtractor → QueueA"""
        t0 = time.monotonic()
        try:
            extractor = ChunkExtractor(self._get_llm("split"), self.config)
            chunk_count = 0
            if line_buffer is not None:
                gen = extractor.extract_file_streaming(file_path, line_buffer, source_format)
            else:
                assert content is not None
                gen = extractor.extract_file(file_path, content, source_format)
            async for chunk_result in gen:
                await queue_a.put(chunk_result)
                chunk_count += 1
                if live:
                    live.blocks_split = chunk_count
            result.chunks_count = chunk_count
        except Exception as e:
            logger.error("task_chunk_extract_failed", file=file_path.name, exc_info=True)
            result.errors.append(f"chunk_extract: {e}")
            if live:
                live.errors += 1
        finally:
            await queue_a.put(_SENTINEL)
            timing["extract"] = time.monotonic() - t0

    # ── Task 2: 处理 + 树构建 ──

    async def _task_process_tree(
        self,
        queue_a: asyncio.Queue,
        queue_b: asyncio.Queue,
        result: PipelineResult,
        timing: dict[str, float],
        collected: list,
        completed_chunk_ids: list[str],
        file_hash: str,
        tree_builder: TreeBuilder,
        *,
        live: LiveProgress | None = None,
    ) -> None:
        """Task 2: QueueA → ItemProcessor + TreeBuilder → QueueB"""
        _done_chunks = set(completed_chunk_ids)
        t_process = 0.0

        # 懒加载 ItemProcessor
        processor = None
        process_llm = self._get_llm("process_item")
        if process_llm:
            from mathforge.pipeline.agents.item_processor import ItemProcessor
            processor = ItemProcessor(process_llm)

        sem = asyncio.Semaphore(self.config.max_concurrent_llm_calls)

        try:
            while True:
                msg = await queue_a.get()
                if isinstance(msg, _Sentinel):
                    break

                chunk: ChunkExtractionResult = msg

                if chunk.chunk_id in _done_chunks:
                    logger.debug("chunk_skipped_checkpoint", chunk_id=chunk.chunk_id[:8])
                    continue

                # 记录提取的 items
                items = chunk.items
                result.items_extracted += len(items)
                if live:
                    live.items_extracted += len(items)
                    for ei in items:
                        live.record_type(ei.item_type)

                # 处理每个 item
                tp0 = time.monotonic()
                chunk_results: list[tuple[int, ExtractedItem, Any]] = []

                if processor:
                    from mathforge.pipeline.agents.item_processor import ProcessingContext

                    async def process_one(ei: ExtractedItem, idx: int) -> None:
                        try:
                            raw = ei.to_raw_item()
                            ctx = ProcessingContext(
                                source_type=chunk.structural_context.document_type,
                                chapter_title=chunk.structural_context.chapter_title,
                                section_title=chunk.structural_context.section_title,
                                structural_context=chunk.structural_context,
                            )
                            paired = ei.paired_content.content if ei.paired_content else ""
                            async with sem:
                                proc_result = await processor.process(raw, ctx, paired)
                        except Exception as exc:
                            logger.error(
                                "item_process_exception",
                                item_id=ei.item_id[:8],
                                error=str(exc),
                            )
                            proc_result = _fallback_proc(ei)
                            if live:
                                live.errors += 1
                        chunk_results.append((idx, ei, proc_result))

                    item_tasks = [process_one(ei, idx) for idx, ei in enumerate(items)]
                    if item_tasks:
                        await asyncio.gather(*item_tasks)
                else:
                    for idx, ei in enumerate(items):
                        chunk_results.append((idx, ei, _fallback_proc(ei)))

                t_process += time.monotonic() - tp0

                # 按原始索引排序
                chunk_results.sort(key=lambda x: x[0])

                for _, ei, proc_result in chunk_results:
                    tup = (ei, proc_result)
                    collected.append(tup)
                    await queue_b.put(tup)

                result.items_processed += len(chunk_results)
                if live:
                    live.items_processed += len(chunk_results)
                    for _, _ei, _pr in chunk_results:
                        live.record_quality(_pr.correction.overall_quality)

                # 质量过滤
                valid_items = [
                    (ei, pr) for _, ei, pr in chunk_results
                    if pr.correction.overall_quality != "F"
                    and ei.item_type not in ("text", "remark")
                ]

                # 增量入库 + 树构建
                if self.storage and valid_items:
                    try:
                        batch_tuples = [
                            (ei.to_raw_item(), pr, ChunkExtractionResult(chunk_id=chunk.chunk_id))
                            for ei, pr in valid_items
                        ]
                        block_unified = ItemUnifier().unify_batch(batch_tuples)
                        block_kps, block_qs = build_entities(block_unified)

                        # 树构建
                        for unified in block_unified:
                            item_type = "question" if unified.item_category == "question" else "kp"
                            tree_builder.insert(
                                unified.item_id, item_type, chunk.structural_context
                            )

                        # 存储
                        if hasattr(self.storage, "save_batch"):
                            saved = await asyncio.to_thread(
                                self.storage.save_batch, block_kps, block_qs
                            )
                        else:
                            saved = 0
                            for kp in block_kps:
                                await asyncio.to_thread(self.storage.save_knowledge_point, kp)
                                saved += 1
                            for q, sol in block_qs:
                                await asyncio.to_thread(self.storage.save_question, q, sol)
                                saved += 1
                        result.items_stored += saved
                        if live:
                            live.items_stored += saved

                        _done_chunks.add(chunk.chunk_id)
                        completed_chunk_ids.append(chunk.chunk_id)
                        if file_hash:
                            self._save_checkpoint(
                                file_hash, result,
                                stage="process_tree",
                                completed_chunk_ids=list(_done_chunks),
                                tree_state=tree_builder.serialize(),
                            )
                        logger.info(
                            "chunk_items_stored",
                            chunk_id=chunk.chunk_id[:8],
                            kps=len(block_kps),
                            questions=len(block_qs),
                        )
                    except Exception:
                        logger.error(
                            "chunk_save_failed",
                            chunk_id=chunk.chunk_id[:8],
                            exc_info=True,
                        )
                        result.errors.append(f"chunk_save[{chunk.chunk_id[:8]}]: failed")
                        if live:
                            live.errors += 1

        except Exception as e:
            logger.error("task_process_tree_failed", exc_info=True)
            result.errors.append(f"process_tree: {e}")
            if live:
                live.errors += 1
        finally:
            await queue_b.put(_SENTINEL)
            timing["process"] = t_process

    # ── Task 3: Collect (背压) ──

    async def _task_collect(
        self,
        queue_b: asyncio.Queue,
        timing: dict[str, float],
    ) -> None:
        """Task 3: 消费 QueueB 提供背压"""
        t0 = time.monotonic()
        while True:
            msg = await queue_b.get()
            if isinstance(msg, _Sentinel):
                break
        timing["collect"] = time.monotonic() - t0

    # ── 进度守护 ──

    async def _progress_daemon(
        self,
        queue_a: asyncio.Queue,
        queue_b: asyncio.Queue,
        progress: LiveProgress,
        stop_event: asyncio.Event,
    ) -> None:
        """定时输出管线进度摘要"""
        interval = self.config.progress_log_interval
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass

            elapsed = time.monotonic() - progress.start_time
            log_kwargs: dict[str, Any] = {
                "elapsed_sec": round(elapsed, 1),
                "blocks_split": progress.blocks_split,
                "items_extracted": progress.items_extracted,
                "items_processed": progress.items_processed,
                "items_stored": progress.items_stored,
                "queue_a": queue_a.qsize(),
                "queue_b": queue_b.qsize(),
                "errors": progress.errors,
            }
            if progress.type_counts:
                log_kwargs["types"] = dict(progress.type_counts)
            if progress.quality_counts:
                log_kwargs["quality"] = dict(progress.quality_counts)
            if progress.vlm_pages_total > 0:
                log_kwargs["vlm_pages"] = (
                    f"{progress.vlm_pages_done}/{progress.vlm_pages_total}"
                )
            if progress.files_total > 0:
                log_kwargs["files"] = (
                    f"{progress.files_completed}/{progress.files_total}"
                )

            logger.info("pipeline_progress", **log_kwargs)

    # ── Task 0: 流式 VLM ──

    async def _task_vlm_stream(
        self,
        file_path: Path,
        line_buffer: StreamingLineBuffer,
        live: LiveProgress | None = None,
    ) -> None:
        """Task 0: VLM 流式转换"""
        from mathforge.pipeline.vlm_converter import VlmConverter

        vlm_llm = self._get_llm("vlm_convert")
        assert vlm_llm is not None

        converter = VlmConverter(
            llm_client=vlm_llm,
            checkpoint_dir=self.config.checkpoint_dir,
            output_dir=self.config.data_dir,
            dpi=self.config.vlm_dpi,
            max_concurrent=self.config.vlm_max_concurrent,
            pages_per_batch=self.config.vlm_pages_per_batch,
        )

        try:
            if live:
                import fitz
                doc_probe = fitz.open(str(file_path))
                live.vlm_pages_total = doc_probe.page_count
                doc_probe.close()

            async for md_chunk in converter.convert_file_streaming(file_path):
                line_buffer.append_chunk(md_chunk)
                if live:
                    live.vlm_pages_done += converter.pages_per_batch
                    live.vlm_pages_done = min(live.vlm_pages_done, live.vlm_pages_total)
        except Exception as e:
            logger.error("vlm_stream_failed", file=file_path.name, error=str(e))
            if live:
                live.errors += 1
        finally:
            line_buffer.finish()

    # ── 目录处理 ──

    _PART_RE = re.compile(r"^(.+?)_part_(\d+)_(\d+)$", re.IGNORECASE)

    @classmethod
    def _group_part_files(cls, files: list[Path]) -> list[Path | list[Path]]:
        """将分页文件按系列分组"""
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
    def _merge_part_files(part_files: list[Path]) -> tuple[str, str]:
        """按顺序拼接分页文件"""
        parts: list[str] = []
        group_name = part_files[0].stem.rsplit("_part_", 1)[0]
        _heading_re = r"^#{1,6}\s|^\*\*定[理义]|^\*\*Theorem|^\*\*Definition"

        for f in part_files:
            content = f.read_text(encoding="utf-8").strip()
            if not content:
                continue
            if parts:
                prev_tail = parts[-1].rstrip().splitlines()[-1] if parts[-1].strip() else ""
                curr_head = content.lstrip().splitlines()[0] if content.strip() else ""
                is_continuation = (
                    prev_tail
                    and not prev_tail.rstrip().endswith(("。", ".", "$$", "```"))
                    and not re.match(_heading_re, curr_head)
                )
                parts.append(content if is_continuation else "\n\n" + content)
            else:
                parts.append(content)

        return "".join(parts), group_name

    async def process_directory(
        self,
        dir_path: Path,
        max_concurrent_files: int = 5,
    ) -> list[PipelineResult]:
        """处理目录下所有文件"""
        files = sorted(self._collect_files(dir_path), key=self._natural_sort_key)
        grouped = self._group_part_files(files)

        dir_progress = self._load_directory_progress(dir_path)

        work_items: list[tuple[int, Path | list[Path], str]] = []
        for i, item in enumerate(grouped):
            pkey = str(item[0]) if isinstance(item, list) else str(item)
            if pkey in dir_progress.completed_files:
                try:
                    if isinstance(item, list):
                        raw = "".join(f.read_bytes().decode() for f in item)
                        cur_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
                    else:
                        cur_hash = hashlib.sha256(item.read_bytes()).hexdigest()[:16]
                except Exception:
                    cur_hash = ""
                if dir_progress.completed_files[pkey] == cur_hash:
                    logger.debug("pipeline_file_skipped", file=pkey)
                    continue
            work_items.append((i, item, pkey))

        skipped = len(grouped) - len(work_items)
        logger.info(
            "pipeline_directory_start",
            dir=str(dir_path),
            total_files=len(grouped),
            remaining=len(work_items),
            skipped=skipped,
            max_concurrent=max_concurrent_files,
        )

        sem = asyncio.Semaphore(max_concurrent_files)
        results: list[PipelineResult] = [
            PipelineResult(file_path=str(item[0]) if isinstance(item, list) else str(item))
            for item in grouped
        ]

        async def _process_one(idx: int, item: Path | list[Path], pkey: str) -> None:
            async with sem:
                try:
                    if isinstance(item, list):
                        merged_content, group_name = self._merge_part_files(item)
                        logger.info(
                            "pipeline_merged_parts",
                            group=group_name,
                            part_count=len(item),
                            merged_lines=len(merged_content.splitlines()),
                        )
                        results[idx] = await self.process_file(
                            item[0], content_override=merged_content
                        )
                    else:
                        results[idx] = await self.process_file(item)
                    try:
                        if isinstance(item, list):
                            raw = "".join(f.read_bytes().decode() for f in item)
                            fhash = hashlib.sha256(raw.encode()).hexdigest()[:16]
                        else:
                            fhash = hashlib.sha256(item.read_bytes()).hexdigest()[:16]
                    except Exception:
                        fhash = ""
                    dir_progress.completed_files[pkey] = fhash
                    self._save_directory_progress(dir_path, dir_progress)
                except Exception as e:
                    name = pkey.split("/")[-1] if "/" in pkey else pkey
                    logger.error("pipeline_file_failed", file=name, error=str(e))
                    results[idx] = PipelineResult(file_path=pkey, errors=[str(e)])
                    dir_progress.failed_files[pkey] = str(e)

        tasks = [_process_one(idx, item, pkey) for idx, item, pkey in work_items]
        await asyncio.gather(*tasks)

        return results

    # ── 辅助方法 ──

    async def _convert_to_text(self, file_path: Path, fmt: DocumentFormat) -> str:
        """Stage 0: 格式转换为文本/Markdown"""
        if fmt in (DocumentFormat.TEX, DocumentFormat.MARKDOWN):
            return file_path.read_text(encoding="utf-8")

        if self._pdf_backend == "vlm" and fmt in (DocumentFormat.PDF, DocumentFormat.DOCX):
            vlm_llm = self._get_llm("vlm_convert")
            if vlm_llm and vlm_llm.supports_vision:
                from mathforge.pipeline.vlm_converter import VlmConverter
                converter = VlmConverter(
                    llm_client=vlm_llm,
                    checkpoint_dir=self.config.checkpoint_dir,
                    output_dir=self.config.data_dir,
                    dpi=self.config.vlm_dpi,
                    max_concurrent=self.config.vlm_max_concurrent,
                    pages_per_batch=self.config.vlm_pages_per_batch,
                )
                return await converter.convert_file_async(file_path)
            else:
                logger.warning("vlm_backend_no_vision_support", file=file_path.name)

        if fmt == DocumentFormat.PDF:
            from mathforge.pipeline.pdf_processor import PdfProcessor
            processor = PdfProcessor(checkpoint_dir=self.config.checkpoint_dir)
            blocks = processor.process_pdf(file_path, backend=self._pdf_backend)
            return "\n\n".join(b.content for b in blocks if b.content.strip())

        if fmt == DocumentFormat.DOCX:
            from mathforge.pipeline.docx_processor import process_docx
            blocks = process_docx(file_path)
            return "\n\n".join(b.content for b in blocks if b.content.strip())

        if fmt == DocumentFormat.CSV:
            return file_path.read_text(encoding="utf-8")

        return file_path.read_text(encoding="utf-8")

    @staticmethod
    def _detect_format(file_path: Path) -> DocumentFormat:
        suffix = file_path.suffix.lower()
        fmt_map = {
            ".tex": DocumentFormat.TEX,
            ".md": DocumentFormat.MARKDOWN,
            ".markdown": DocumentFormat.MARKDOWN,
            ".pdf": DocumentFormat.PDF,
            ".docx": DocumentFormat.DOCX,
            ".csv": DocumentFormat.CSV,
            ".pptx": DocumentFormat.PPTX,
            ".epub": DocumentFormat.EPUB,
        }
        return fmt_map.get(suffix, DocumentFormat.MARKDOWN)

    @staticmethod
    def _collect_files(dir_path: Path) -> list[Path]:
        extensions = {".tex", ".md", ".markdown", ".pdf", ".docx", ".csv"}
        files: list[Path] = []
        for p in dir_path.rglob("*"):
            if p.is_file() and p.suffix.lower() in extensions and not p.name.startswith("."):
                files.append(p)
        return files

    @staticmethod
    def _natural_sort_key(path: Path) -> tuple[str, list[int | str]]:
        parts: list[int | str] = []
        for seg in re.split(r"(\d+)", path.stem):
            parts.append(int(seg) if seg.isdigit() else seg.lower())
        return (str(path.parent), parts)

    def _update_relations(
        self,
        kps: list[KnowledgePoint],
        qpairs: list[tuple[Question, Solution | None]],
    ) -> None:
        if not self.storage:
            return
        if hasattr(self.storage, "update_relations_batch"):
            items: list[tuple[str, str, list]] = []
            for kp in kps:
                if kp.relations:
                    items.append((kp.id, "kp", kp.relations))
            for q, _ in qpairs:
                if q.relations:
                    items.append((q.id, "question", q.relations))
            if items:
                try:
                    self.storage.update_relations_batch(items)
                    logger.info("relations_updated", count=len(items))
                except Exception:
                    logger.error("relations_update_failed", exc_info=True)

    def _load_directory_progress(self, dir_path: Path) -> DirectoryProgress:
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
        dir_hash = hashlib.sha256(str(dir_path.resolve()).encode()).hexdigest()[:12]
        self.config.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        progress_file = self.config.checkpoint_dir / f"dir_{dir_hash}.json"
        progress_file.write_text(progress.model_dump_json(indent=2), encoding="utf-8")

    def _load_checkpoint(self, file_hash: str) -> PipelineRunProgress | None:
        cp_dir = self.config.checkpoint_dir / file_hash
        progress_file = cp_dir / "progress.json"
        if progress_file.exists():
            try:
                data = json.loads(progress_file.read_text(encoding="utf-8"))
                return PipelineRunProgress(**data)
            except Exception:
                pass
        return None

    def _save_checkpoint(
        self,
        file_hash: str,
        result: PipelineResult,
        stage: str = "completed",
        completed_chunk_ids: list[str] | None = None,
        tree_state: str | None = None,
    ) -> None:
        cp_dir = self.config.checkpoint_dir / file_hash
        cp_dir.mkdir(parents=True, exist_ok=True)

        progress = PipelineRunProgress(
            file_path=result.file_path,
            file_hash=file_hash,
            stage=stage,
            blocks_completed=result.chunks_count,
            completed_block_ids=completed_chunk_ids or [],
            items_extracted=result.items_extracted,
            items_processed=result.items_processed,
            items_stored=result.items_stored,
            errors=result.errors,
            last_checkpoint_at=datetime.now(),
            tree_state=tree_state,
        )

        (cp_dir / "progress.json").write_text(
            progress.model_dump_json(indent=2), encoding="utf-8",
        )
