"""PipelineOrchestrator — asyncio.Queue 流水线编排器

三个并发 Task 通过 Queue 实现流水线：
  Task1 [Splitter] → QueueA → Task2 [Extractor+Processor] → QueueB → Task3 [Unifier]
最后 RelationBuilder (批量) + EntityBuilder → Storage

流式 VLM 模式下追加 Task0:
  Task0 [VlmStream] → StreamingLineBuffer → Task1 [Splitter] → ...
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
    DirectoryProgress,
    DocumentFormat,
    KnowledgePoint,
    MdBlockV2,
    PipelineRunProgress,
    Question,
    RawItem,
    Solution,
)
from mathforge.pipeline.block_splitter import BlockSplitter
from mathforge.pipeline.entity_builder import build_entities
from mathforge.pipeline.item_unifier import ItemUnifier
from mathforge.pipeline.meta_extractor import MetaExtractor
from mathforge.pipeline.metrics import LiveProgress
from mathforge.pipeline.relation_builder import RelationBuilder

if TYPE_CHECKING:
    from mathforge.config.settings import AppConfig
    from mathforge.llm.client import LLMClient
    from mathforge.pipeline.agents.item_processor import ItemProcessingResult
    from mathforge.pipeline.block_splitter import StreamingLineBuffer
    from mathforge.storage.protocol import StorageBackend

logger = structlog.get_logger()


@dataclass
class PipelineResult:
    """单个文件的处理结果"""

    file_path: str = ""
    blocks_count: int = 0
    items_extracted: int = 0
    items_processed: int = 0
    items_stored: int = 0
    knowledge_points: list[KnowledgePoint] = field(default_factory=list)
    questions: list[tuple[Question, Solution | None]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    # 各阶段耗时
    split_sec: float = 0.0
    extract_sec: float = 0.0
    process_sec: float = 0.0
    unify_sec: float = 0.0
    relation_sec: float = 0.0


# Queue 消息类型 — 使用专用 sentinel 避免与 None 冲突


class _Sentinel:
    """唯一哨兵对象，标识流结束"""

    __slots__ = ()


_SENTINEL = _Sentinel()
_QueueA_Item = MdBlockV2 | _Sentinel
_QueueB_Item = tuple[RawItem, "ItemProcessingResult", MdBlockV2] | _Sentinel


def _fallback_proc(item: RawItem) -> ItemProcessingResult:
    """无 LLM 或异常时的 fallback 处理结果"""
    from mathforge.core.models import CorrectionResult, ItemProcessingResult, TagSet

    return ItemProcessingResult(
        correction=CorrectionResult(
            corrected_content=item.content_raw,
            overall_quality="D",
        ),
        classification=TagSet(),
        item_subtype=item.block_type,
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

        # FormatSpec 缓存库
        from mathforge.pipeline.format_spec_library import FormatSpecLibrary

        lib_path = config.checkpoint_dir / "format_spec_library.json"
        self._spec_library = FormatSpecLibrary(persist_path=lib_path)

    def _get_llm(self, task: str) -> LLMClient | None:
        """按任务获取 LLM client，fallback 到默认 client"""
        return self._llm_clients.get(task) or self.llm

    async def process_file(
        self, file_path: Path, *, content_override: str | None = None
    ) -> PipelineResult:
        """处理单个文件。

        Args:
            file_path: 文件路径（用于格式检测和日志）
            content_override: 如果提供，跳过文件读取直接使用此内容
                （用于分页文件合并后的处理）
        """
        from mathforge.pipeline.block_splitter import StreamingLineBuffer
        from mathforge.pipeline.metrics import PipelineMetrics

        t0 = time.monotonic()
        result = PipelineResult(file_path=str(file_path))
        metrics = PipelineMetrics()
        metrics.start()

        # 检测格式
        source_format = self._detect_format(file_path)

        # 判断是否使用流式 VLM 管线
        use_streaming_vlm = (
            self.config.vlm_streaming
            and self._pdf_backend == "vlm"
            and source_format in (DocumentFormat.PDF, DocumentFormat.DOCX)
            and content_override is None
        )
        vlm_llm = self._get_llm("vlm_convert") if use_streaming_vlm else None
        use_streaming_vlm = use_streaming_vlm and vlm_llm is not None and vlm_llm.supports_vision

        if use_streaming_vlm:
            # 流式模式：VLM 转换与 BlockSplitter 并行
            content = None
            # checkpoint 使用文件哈希（不依赖内容）
            file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()[:16]
            logger.info(
                "pipeline_file_start",
                file=file_path.name,
                format=source_format.value,
                mode="streaming_vlm",
            )
        else:
            # 传统模式：先完成转换，再进入 pipeline
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

        # Checkpoint — 检查是否已完成
        progress = self._load_checkpoint(file_hash)
        if progress and progress.stage == "completed":
            logger.info("pipeline_file_skipped_completed", file=file_path.name)
            return result

        # 创建 Queues
        queue_a: asyncio.Queue[_QueueA_Item] = asyncio.Queue(
            maxsize=self.config.pipeline_queue_size
        )
        queue_b: asyncio.Queue[_QueueB_Item] = asyncio.Queue(
            maxsize=self.config.pipeline_queue_size
        )

        # 共享状态
        timing: dict[str, float] = {}
        collected: list[tuple[RawItem, ItemProcessingResult, MdBlockV2]] = []
        completed_block_ids: list[str] = list(progress.completed_block_ids) if progress else []

        # 实时进度计数器 + 守护进程
        live = LiveProgress()
        stop_event = asyncio.Event()

        # 构建 task 列表
        if use_streaming_vlm:
            # 流式 VLM 管线：Task0 + Task1 + Task2 + Task3
            line_buffer = StreamingLineBuffer()
            tasks = [
                asyncio.create_task(
                    self._task_vlm_stream(file_path, line_buffer, live=live)
                ),
                asyncio.create_task(
                    self._task_split(
                        file_path, None, source_format, queue_a, result, timing,
                        live=live, line_buffer=line_buffer,
                    )
                ),
                asyncio.create_task(
                    self._task_extract_process(
                        queue_a, queue_b, result, timing, collected,
                        completed_block_ids, file_hash, live=live,
                    )
                ),
                asyncio.create_task(self._task_unify(queue_b, result, timing, collected)),
            ]
        else:
            # 传统管线：Task1 + Task2 + Task3
            tasks = [
                asyncio.create_task(
                    self._task_split(
                        file_path, content, source_format, queue_a, result, timing,
                        live=live,
                    )
                ),
                asyncio.create_task(
                    self._task_extract_process(
                        queue_a, queue_b, result, timing, collected,
                        completed_block_ids, file_hash, live=live,
                    )
                ),
                asyncio.create_task(self._task_unify(queue_b, result, timing, collected)),
            ]

        daemon = asyncio.create_task(
            self._progress_daemon(queue_a, queue_b, live, stop_event)
        )

        # 等待全部完成，检查 task 异常
        task_results = await asyncio.gather(*tasks, return_exceptions=True)
        stop_event.set()
        await daemon
        if use_streaming_vlm:
            task_names = ["vlm_stream", "split", "extract_process", "unify"]
        else:
            task_names = ["split", "extract_process", "unify"]
        for i, tr in enumerate(task_results):
            if isinstance(tr, BaseException):
                tname = task_names[i] if i < len(task_names) else f"task_{i}"
                logger.error("pipeline_task_failed", task=tname, error=str(tr))
                result.errors.append(f"task_{tname}: {tr}")

        # 关系建立（批量，非流水线）— 条目已在 Task 2 中增量入库
        t_rel = time.monotonic()
        if collected:
            try:
                unifier = ItemUnifier()
                unified_items = unifier.unify_batch(collected)

                rel_llm = self._get_llm("infer_relations")
                rel_builder = RelationBuilder(rel_llm)
                if rel_llm:
                    unified_items = await rel_builder.build_relations(unified_items)
                else:
                    unified_items = rel_builder.build_relations_sync(unified_items)

                # 构建最终实体（含关系），更新 result
                kps, qpairs = build_entities(unified_items)
                result.knowledge_points = kps
                result.questions = qpairs

                # 批量更新关系到 DB（条目本体已在 Task 2 中入库）
                self._update_relations(kps, qpairs)
            except Exception as e:
                logger.error("pipeline_unify_entity_failed", exc_info=True)
                result.errors.append(str(e))

        timing["relation"] = time.monotonic() - t_rel

        # 保存最终 checkpoint（标记为完成）
        self._save_checkpoint(
            file_hash,
            result,
            stage="completed",
            completed_block_ids=completed_block_ids,
        )

        # Metrics 汇总
        metrics.finish()
        metrics.log_summary()
        try:
            metrics.save(self.config.checkpoint_dir / file_hash / "metrics.json")
        except Exception:
            pass

        # 记录汇总
        result.elapsed_sec = time.monotonic() - t0
        result.split_sec = timing.get("split", 0.0)
        result.extract_sec = timing.get("extract", 0.0)
        result.process_sec = timing.get("process", 0.0)
        result.unify_sec = timing.get("unify", 0.0)
        result.relation_sec = timing.get("relation", 0.0)

        logger.info(
            "pipeline_complete",
            file=file_path.name,
            total_sec=round(result.elapsed_sec, 2),
            split_sec=round(result.split_sec, 2),
            extract_process_sec=round(result.extract_sec + result.process_sec, 2),
            relation_sec=round(result.relation_sec, 2),
            blocks=result.blocks_count,
            items=result.items_extracted,
            stored=result.items_stored,
            kps=len(result.knowledge_points),
            questions=len(result.questions),
            errors=len(result.errors),
        )

        return result

    # 分页文件名模式：xxx_part_1_30.md → group="xxx", start=1
    _PART_RE = re.compile(r"^(.+?)_part_(\d+)_(\d+)$", re.IGNORECASE)

    @classmethod
    def _group_part_files(cls, files: list[Path]) -> list[Path | list[Path]]:
        """将分页文件按系列分组，独立文件保持原样。

        例：[a_part_1_30.md, a_part_31_60.md, b.tex]
         → [[a_part_1_30.md, a_part_31_60.md], b.tex]
        """
        groups: dict[str, list[tuple[int, Path]]] = {}
        standalone: list[tuple[int, Path]] = []

        for idx, f in enumerate(files):
            m = cls._PART_RE.match(f.stem)
            if m:
                key = f"{f.parent}/{m.group(1)}{f.suffix}"
                groups.setdefault(key, []).append((int(m.group(2)), f))
            else:
                standalone.append((idx, f))

        result: list[Path | list[Path]] = []
        # 按第一个文件在原列表中的位置排序各组
        all_entries: list[tuple[int, Path | list[Path]]] = []
        for entries in groups.values():
            entries.sort(key=lambda x: x[0])  # 按起始页码排序
            first_idx = files.index(entries[0][1])
            all_entries.append((first_idx, [e[1] for e in entries]))
        for idx, f in standalone:
            all_entries.append((idx, f))
        all_entries.sort(key=lambda x: x[0])
        result = [e[1] for e in all_entries]
        return result

    @staticmethod
    def _merge_part_files(part_files: list[Path]) -> tuple[str, str]:
        """按顺序读取分页文件并拼接，返回 (合并内容, 组名)。

        在拼接点插入标记注释以帮助 BlockSplitter 识别续接内容。
        """
        parts: list[str] = []
        group_name = part_files[0].stem.rsplit("_part_", 1)[0]

        for f in part_files:
            content = f.read_text(encoding="utf-8").strip()
            if not content:
                continue
            if parts:
                # 检测跨页续接：上一段末尾非空行 + 当前段开头非标题
                prev_tail = parts[-1].rstrip().splitlines()[-1] if parts[-1].strip() else ""
                curr_head = content.lstrip().splitlines()[0] if content.strip() else ""
                # 如果前文未以句号/段落结束且后文非标题开头，视为续接
                _heading_re = r"^#{1,6}\s|^\*\*定[理义]|^\*\*Theorem|^\*\*Definition"
                is_continuation = (
                    prev_tail
                    and not prev_tail.rstrip().endswith(("。", ".", "$$", "```"))
                    and not re.match(_heading_re, curr_head)
                )
                if is_continuation:
                    # 续接：不加分隔，直接拼（前文末尾换行 + 后文）
                    parts.append(content)
                else:
                    parts.append("\n\n" + content)
            else:
                parts.append(content)

        merged = "".join(parts)
        return merged, group_name

    async def process_directory(
        self,
        dir_path: Path,
        max_concurrent_files: int = 3,
    ) -> list[PipelineResult]:
        """处理目录下所有文件，支持多文件并发 + 目录级断点恢复

        分页文件（xxx_part_N_M.md）会按页码顺序合并后作为单个文档处理，
        确保跨页定理/证明不被截断。
        """
        files = sorted(self._collect_files(dir_path), key=self._natural_sort_key)
        grouped = self._group_part_files(files)

        # 加载目录级进度
        dir_progress = self._load_directory_progress(dir_path)

        # 构建任务列表：(index, file_or_group, progress_key)
        work_items: list[tuple[int, Path | list[Path], str]] = []
        for i, item in enumerate(grouped):
            if isinstance(item, list):
                pkey = str(item[0])  # 用第一个文件路径做 key
            else:
                pkey = str(item)
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
                        # 分页文件组：合并后处理
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
                    # 记录完成
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

    # ── Task 1: 拆分 ──

    async def _task_split(
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
        """Task 1: BlockSplitter → Queue_A

        当 line_buffer 不为 None 时使用流式分块模式。
        """
        t0 = time.monotonic()
        try:
            splitter = BlockSplitter(
                self._get_llm("split"),
                self.config,
                spec_library=self._spec_library,
            )
            block_count = 0
            if line_buffer is not None:
                async for block in splitter.split_file_streaming(
                    file_path, line_buffer, source_format
                ):
                    await queue_a.put(block)
                    block_count += 1
                    if live:
                        live.blocks_split = block_count
            else:
                assert content is not None
                async for block in splitter.split_file(file_path, content, source_format):
                    await queue_a.put(block)
                    block_count += 1
                    if live:
                        live.blocks_split = block_count
            result.blocks_count = block_count
        except Exception as e:
            logger.error("task_split_failed", file=file_path.name, exc_info=True)
            result.errors.append(f"split: {e}")
            if live:
                live.errors += 1
        finally:
            await queue_a.put(_SENTINEL)
            timing["split"] = time.monotonic() - t0

    # ── Task 2: 提取 + 处理 ──

    async def _task_extract_process(
        self,
        queue_a: asyncio.Queue,
        queue_b: asyncio.Queue,
        result: PipelineResult,
        timing: dict[str, float],
        collected: list,
        completed_block_ids: list[str] | None = None,
        file_hash: str = "",
        *,
        live: LiveProgress | None = None,
    ) -> None:
        """Task 2: Queue_A → MetaExtractor + ItemProcessor → Queue_B"""
        _done_blocks = set(completed_block_ids or [])
        t_extract = 0.0
        t_process = 0.0
        extractor = MetaExtractor()

        # 懒加载 ItemProcessor
        processor = None
        process_llm = self._get_llm("process_item")
        if process_llm:
            from mathforge.pipeline.agents.item_processor import (
                ItemProcessor,
            )

            processor = ItemProcessor(process_llm)

        # Semaphore 在 task 级别创建，跨 block 限制并发 LLM 调用
        sem = asyncio.Semaphore(self.config.max_concurrent_llm_calls)

        try:
            while True:
                block = await queue_a.get()
                if isinstance(block, _Sentinel):
                    break

                # Block 级断点跳过
                if block.block_id in _done_blocks:
                    logger.debug(
                        "block_skipped_checkpoint",
                        block_id=block.block_id[:8],
                    )
                    continue

                # 提取
                te0 = time.monotonic()
                raw_items, extraction_report = extractor.extract(block)
                t_extract += time.monotonic() - te0
                result.items_extracted += len(raw_items)
                if live:
                    live.items_extracted += len(raw_items)
                    for ri in raw_items:
                        live.record_type(ri.block_type)

                if extraction_report.issues:
                    for issue in extraction_report.issues:
                        result.errors.append(f"extraction[{block.block_id[:8]}]: {issue}")
                    if live:
                        live.errors += len(extraction_report.issues)

                # 处理 items — 逐条并发模式
                block_results: list[tuple[int, RawItem, Any]] = []
                tp0 = time.monotonic()

                if processor:
                    from mathforge.pipeline.agents.item_processor import ProcessingContext

                    ctx = ProcessingContext(
                        source_type=(block.format_spec.source_type if block.format_spec else ""),
                        chapter_title=block.chapter_title,
                        section_title=block.section_title,
                        detected_structure="\n".join(block.format_spec.detected_structure)
                        if block.format_spec
                        else "",
                        prev_type="",
                        prev_title="",
                        next_type="",
                        next_title="",
                        format_spec=block.format_spec,
                    )

                    def _get_paired(item: RawItem) -> str:
                        if item.paired_item_id:
                            for ri in raw_items:
                                if ri.item_id == item.paired_item_id:
                                    return ri.content_raw
                        return ""

                    async def process_one(item: RawItem, idx: int) -> None:
                        try:
                            c = ProcessingContext(
                                source_type=ctx.source_type,
                                chapter_title=ctx.chapter_title,
                                section_title=ctx.section_title,
                                detected_structure=ctx.detected_structure,
                                prev_type=(raw_items[idx - 1].block_type if idx > 0 else ""),
                                prev_title=(raw_items[idx - 1].number or "" if idx > 0 else ""),
                                next_type=(
                                    raw_items[idx + 1].block_type
                                    if idx + 1 < len(raw_items)
                                    else ""
                                ),
                                next_title=(
                                    raw_items[idx + 1].number or ""
                                    if idx + 1 < len(raw_items)
                                    else ""
                                ),
                                format_spec=ctx.format_spec,
                            )
                            async with sem:
                                proc_result = await processor.process(item, c, _get_paired(item))
                        except Exception as exc:
                            logger.error(
                                "item_process_exception",
                                item_id=item.item_id,
                                error=str(exc),
                            )
                            proc_result = _fallback_proc(item)
                            if live:
                                live.errors += 1
                        block_results.append((idx, item, proc_result))

                    item_tasks = [process_one(item, idx) for idx, item in enumerate(raw_items)]
                    if item_tasks:
                        await asyncio.gather(*item_tasks)
                else:
                    # 无 LLM processor，直接 fallback
                    for idx, item in enumerate(raw_items):
                        block_results.append((idx, item, _fallback_proc(item)))

                t_process += time.monotonic() - tp0

                # 按原始索引排序，保证确定性
                block_results.sort(key=lambda x: x[0])
                block_tuples = []
                for _, item, proc_result in block_results:
                    tup = (item, proc_result, block)
                    collected.append(tup)
                    block_tuples.append(tup)
                    await queue_b.put(tup)

                result.items_processed += len(block_results)
                if live:
                    live.items_processed += len(block_results)
                    for _, _item, _proc in block_results:
                        live.record_quality(_proc.correction.overall_quality)

                # 过滤无效条目
                filtered_types = {"text", "remark"}

                def _is_valid(proc: Any, raw_item: RawItem) -> bool:
                    if proc.correction.overall_quality == "F":
                        return False
                    for issue in proc.correction.semantic_issues:
                        if "非有效" in issue or "无效" in issue:
                            return False
                    if raw_item.block_type in filtered_types:
                        return False
                    return True

                # 逐条输出处理日志
                for item, proc, _blk in block_tuples:
                    content_preview = item.content_raw[:80].replace("\n", " ")
                    quality = proc.correction.overall_quality
                    valid = _is_valid(proc, item)
                    if not valid:
                        if quality == "F":
                            reason = "quality_F"
                        elif item.block_type in filtered_types:
                            reason = f"filtered_type:{item.block_type}"
                        else:
                            reason = "semantic_invalid"
                        logger.info(
                            "item_rejected",
                            item_id=item.item_id[:8],
                            block_type=item.block_type,
                            reason=reason,
                            content=content_preview,
                        )
                    else:
                        logger.info(
                            "item_accepted",
                            item_id=item.item_id[:8],
                            block_type=item.block_type,
                            quality=quality,
                            subtype=(
                                proc.classification.item_subtype
                                if proc.classification
                                else "unknown"
                            ),
                            content=content_preview,
                        )

                block_tuples_valid = [
                    (item, proc, blk) for item, proc, blk in block_tuples if _is_valid(proc, item)
                ]
                if len(block_tuples_valid) < len(block_tuples):
                    filtered = len(block_tuples) - len(block_tuples_valid)
                    logger.info(
                        "quality_gate_filtered",
                        block_id=block.block_id[:8],
                        filtered=filtered,
                    )

                # 增量入库：每个 block 处理完即 unify + save
                if self.storage and block_tuples_valid:
                    try:
                        block_unified = ItemUnifier().unify_batch(block_tuples_valid)
                        block_kps, block_qs = build_entities(block_unified)
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
                        # 记录 block 完成，增量保存 checkpoint
                        _done_blocks.add(block.block_id)
                        if completed_block_ids is not None:
                            completed_block_ids.append(block.block_id)
                        if file_hash:
                            self._save_checkpoint(
                                file_hash,
                                result,
                                stage="extract_process",
                                completed_block_ids=list(_done_blocks),
                            )
                        logger.info(
                            "block_items_stored",
                            block_id=block.block_id[:8],
                            kps=len(block_kps),
                            questions=len(block_qs),
                        )
                    except Exception:
                        logger.error(
                            "block_save_failed",
                            block_id=block.block_id[:8],
                            exc_info=True,
                        )
                        result.errors.append(f"block_save[{block.block_id[:8]}]: failed")
                        if live:
                            live.errors += 1

        except Exception as e:
            logger.error("task_extract_process_failed", exc_info=True)
            result.errors.append(f"extract_process: {e}")
            if live:
                live.errors += 1
        finally:
            await queue_b.put(_SENTINEL)
            timing["extract"] = t_extract
            timing["process"] = t_process

    # ── Task 3: Unify (收集，实际 unify 在 gather 后) ──

    async def _task_unify(
        self,
        queue_b: asyncio.Queue,
        result: PipelineResult,
        timing: dict[str, float],
        collected: list,
    ) -> None:
        """Task 3: 消费 Queue_B 提供背压（真正 unify 在 gather 后）"""
        t0 = time.monotonic()
        while True:
            msg = await queue_b.get()
            if isinstance(msg, _Sentinel):
                break
        timing["unify"] = time.monotonic() - t0

    # ── 进度守护 ──

    async def _progress_daemon(
        self,
        queue_a: asyncio.Queue,
        queue_b: asyncio.Queue,
        progress: LiveProgress,
        stop_event: asyncio.Event,
    ) -> None:
        """定时输出管线进度摘要（不含大段文本内容）"""
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
        """Task 0: VLM 流式转换，每完成一个 batch 即追加到 line_buffer"""
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
            # 预获取总页数用于进度追踪
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

    # ── 辅助方法 ──

    async def _convert_to_text(self, file_path: Path, fmt: DocumentFormat) -> str:
        """Stage 0: 将任意支持格式转换为纯文本/Markdown"""
        if fmt in (DocumentFormat.TEX, DocumentFormat.MARKDOWN):
            return file_path.read_text(encoding="utf-8")

        # VLM 后端（多模态 LLM 逐页识别）
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
                logger.warning(
                    "vlm_backend_no_vision_support",
                    file=file_path.name,
                    provider=vlm_llm.config.provider.value if vlm_llm else "none",
                )

        # MinerU 后端（同时处理 PDF 和 DOCX）
        if self._pdf_backend == "mineru" and fmt in (DocumentFormat.PDF, DocumentFormat.DOCX):
            from mathforge.pipeline.mineru_processor import (
                _is_mineru_available,
                process_with_mineru,
            )

            if _is_mineru_available():
                blocks = process_with_mineru(
                    file_path,
                    backend=self.config.mineru_backend,
                    parse_method=self.config.mineru_parse_method,
                    language=self.config.mineru_language,
                    formula_enable=self.config.mineru_formula_enable,
                    table_enable=self.config.mineru_table_enable,
                    checkpoint_dir=self.config.checkpoint_dir,
                )
                return "\n\n".join(b.content for b in blocks if b.content.strip())
            else:
                logger.warning("mineru_not_installed_fallback", file=file_path.name)

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

        # 未知格式：尝试作为文本读取
        return file_path.read_text(encoding="utf-8")

    @staticmethod
    def _detect_format(file_path: Path) -> DocumentFormat:
        """从文件扩展名检测格式"""
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
        """收集目录下的可处理文件"""
        extensions = {".tex", ".md", ".markdown", ".pdf", ".docx", ".csv"}
        files: list[Path] = []
        for p in dir_path.rglob("*"):
            if p.is_file() and p.suffix.lower() in extensions:
                if not p.name.startswith("."):
                    files.append(p)
        return files

    @staticmethod
    def _natural_sort_key(path: Path) -> tuple[str, list[int | str]]:
        """自然排序：先按父目录分组，再按数字自然排序文件名。

        确保 part_1_30 < part_31_60 < part_121_150 等。
        """
        parts: list[int | str] = []
        for seg in re.split(r"(\d+)", path.stem):
            if seg.isdigit():
                parts.append(int(seg))
            else:
                parts.append(seg.lower())
        return (str(path.parent), parts)

    def _update_relations(
        self,
        kps: list[KnowledgePoint],
        qpairs: list[tuple[Question, Solution | None]],
    ) -> None:
        """批量更新关系到 DB（条目本体已在 Task 2 增量入库）"""
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
        else:
            # FileStorageBackend 等不支持批量更新的后端：重新保存整个条目
            for kp in kps:
                try:
                    self.storage.save_knowledge_point(kp)
                except Exception:
                    logger.error("save_kp_failed", kp_id=kp.id, exc_info=True)
            for q, sol in qpairs:
                try:
                    self.storage.save_question(q, sol)
                except Exception:
                    logger.error("save_question_failed", q_id=q.id, exc_info=True)

    def _load_directory_progress(self, dir_path: Path) -> DirectoryProgress:
        """加载目录级进度"""
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
        """保存目录级进度"""
        dir_hash = hashlib.sha256(str(dir_path.resolve()).encode()).hexdigest()[:12]
        self.config.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        progress_file = self.config.checkpoint_dir / f"dir_{dir_hash}.json"
        progress_file.write_text(progress.model_dump_json(indent=2), encoding="utf-8")

    def _load_checkpoint(self, file_hash: str) -> PipelineRunProgress | None:
        """加载 checkpoint"""
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
        completed_block_ids: list[str] | None = None,
    ) -> None:
        """保存 checkpoint"""
        cp_dir = self.config.checkpoint_dir / file_hash
        cp_dir.mkdir(parents=True, exist_ok=True)

        progress = PipelineRunProgress(
            file_path=result.file_path,
            file_hash=file_hash,
            stage=stage,
            blocks_completed=result.blocks_count,
            completed_block_ids=completed_block_ids or [],
            items_extracted=result.items_extracted,
            items_processed=result.items_processed,
            items_stored=result.items_stored,
            errors=result.errors,
            last_checkpoint_at=datetime.now(),
        )

        (cp_dir / "progress.json").write_text(
            progress.model_dump_json(indent=2),
            encoding="utf-8",
        )
