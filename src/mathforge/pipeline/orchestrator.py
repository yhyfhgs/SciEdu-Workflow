"""PipelineOrchestrator — asyncio.Queue 流水线编排器

三个并发 Task 通过 Queue 实现流水线：
  Task1 [Splitter] → QueueA → Task2 [Extractor+Processor] → QueueB → Task3 [Unifier]
最后 RelationBuilder (批量) + EntityBuilder → Storage
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from mathforge.core.models import (
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
from mathforge.pipeline.relation_builder import RelationBuilder

if TYPE_CHECKING:
    from mathforge.config.settings import AppConfig
    from mathforge.llm.client import LLMClient
    from mathforge.pipeline.agents.item_processor import ItemProcessingResult
    from mathforge.storage.file_backend import FileStorageBackend

logger = structlog.get_logger()


@dataclass
class PipelineResult:
    """单个文件的处理结果"""

    file_path: str = ""
    blocks_count: int = 0
    items_extracted: int = 0
    items_processed: int = 0
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


# Queue 消息类型
_SENTINEL = None
_QueueA_Item = MdBlockV2 | None
_QueueB_Item = tuple[RawItem, "ItemProcessingResult", MdBlockV2] | None


class PipelineOrchestrator:
    """流水线编排器"""

    def __init__(
        self,
        config: AppConfig,
        llm_client: LLMClient | None = None,
        storage: FileStorageBackend | None = None,
        llm_clients: dict[str, LLMClient] | None = None,
        pdf_backend: str = "auto",
    ) -> None:
        self.config = config
        self.llm = llm_client
        self.storage = storage
        self._llm_clients = llm_clients or {}
        self._pdf_backend = pdf_backend

    def _get_llm(self, task: str) -> LLMClient | None:
        """按任务获取 LLM client，fallback 到默认 client"""
        return self._llm_clients.get(task) or self.llm

    async def process_file(self, file_path: Path) -> PipelineResult:
        """处理单个文件"""
        t0 = time.monotonic()
        result = PipelineResult(file_path=str(file_path))

        # 检测格式并转换为文本
        source_format = self._detect_format(file_path)
        content = self._convert_to_text(file_path, source_format)

        logger.info(
            "pipeline_file_start",
            file=file_path.name,
            format=source_format.value,
            lines=len(content.splitlines()),
        )

        # Checkpoint
        file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        self._load_checkpoint(file_hash)  # TODO: use for resume

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

        # 启动三个并发 Task
        tasks = [
            asyncio.create_task(
                self._task_split(file_path, content, source_format, queue_a, result, timing)
            ),
            asyncio.create_task(
                self._task_extract_process(queue_a, queue_b, result, timing, collected)
            ),
            asyncio.create_task(self._task_unify(queue_b, result, timing, collected)),
        ]

        # 等待全部完成
        await asyncio.gather(*tasks, return_exceptions=True)

        # 关系建立（批量，非流水线）
        t_rel = time.monotonic()
        if collected and result.errors.__len__() == 0:
            try:
                unifier = ItemUnifier()
                unified_items = unifier.unify_batch(collected)

                rel_llm = self._get_llm("infer_relations")
                rel_builder = RelationBuilder(rel_llm)
                if rel_llm:
                    unified_items = await rel_builder.build_relations(unified_items)
                else:
                    unified_items = rel_builder.build_relations_sync(unified_items)

                kps, qpairs = build_entities(unified_items)
                result.knowledge_points = kps
                result.questions = qpairs
            except Exception as e:
                logger.error("pipeline_unify_entity_failed", exc_info=True)
                result.errors.append(str(e))

        timing["relation"] = time.monotonic() - t_rel

        # 存储
        if self.storage:
            self._save_results(result)

        # 保存 checkpoint
        self._save_checkpoint(file_hash, result)

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
            kps=len(result.knowledge_points),
            questions=len(result.questions),
            errors=len(result.errors),
        )

        return result

    async def process_directory(self, dir_path: Path) -> list[PipelineResult]:
        """处理目录下所有文件"""
        results: list[PipelineResult] = []

        files = sorted(self._collect_files(dir_path))
        logger.info("pipeline_directory_start", dir=str(dir_path), files=len(files))

        for f in files:
            try:
                r = await self.process_file(f)
                results.append(r)
            except Exception as e:
                logger.error("pipeline_file_failed", file=f.name, error=str(e))
                results.append(PipelineResult(file_path=str(f), errors=[str(e)]))

        return results

    # ── Task 1: 拆分 ──

    async def _task_split(
        self,
        file_path: Path,
        content: str,
        source_format: DocumentFormat,
        queue_a: asyncio.Queue,
        result: PipelineResult,
        timing: dict[str, float],
    ) -> None:
        """Task 1: BlockSplitter → Queue_A"""
        t0 = time.monotonic()
        try:
            splitter = BlockSplitter(self._get_llm("split"), self.config)
            block_count = 0
            async for block in splitter.split_file(file_path, content, source_format):
                await queue_a.put(block)
                block_count += 1
            result.blocks_count = block_count
        except Exception as e:
            logger.error("task_split_failed", file=file_path.name, exc_info=True)
            result.errors.append(f"split: {e}")
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
    ) -> None:
        """Task 2: Queue_A → MetaExtractor + ItemProcessor → Queue_B"""
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

        while True:
            block = await queue_a.get()
            if block is _SENTINEL:
                await queue_b.put(_SENTINEL)
                break

            # 提取
            te0 = time.monotonic()
            raw_items = extractor.extract(block)
            t_extract += time.monotonic() - te0
            result.items_extracted += len(raw_items)

            # 处理每个 item
            sem = asyncio.Semaphore(self.config.max_concurrent_llm_calls)

            async def process_one(item: RawItem, idx: int) -> None:
                nonlocal t_process
                tp0 = time.monotonic()

                if processor:
                    from mathforge.pipeline.agents.item_processor import ProcessingContext

                    # 构建上下文
                    ctx = ProcessingContext(
                        source_type=block.format_spec.source_type if block.format_spec else "",
                        chapter_title=block.chapter_title,
                        section_title=block.section_title,
                        detected_structure="\n".join(block.format_spec.detected_structure)
                        if block.format_spec
                        else "",
                        prev_type=raw_items[idx - 1].block_type if idx > 0 else "",
                        prev_title=raw_items[idx - 1].number or "" if idx > 0 else "",
                        next_type=raw_items[idx + 1].block_type if idx + 1 < len(raw_items) else "",
                        next_title=(
                            raw_items[idx + 1].number or "" if idx + 1 < len(raw_items) else ""
                        ),
                        format_spec=block.format_spec,
                    )

                    # 获取配对内容
                    paired_content = ""
                    if item.paired_item_id:
                        for ri in raw_items:
                            if ri.item_id == item.paired_item_id:
                                paired_content = ri.content_raw[:2000]
                                break

                    async with sem:
                        proc_result = await processor.process(item, ctx, paired_content)
                else:
                    # 无 LLM 时的默认结果
                    from mathforge.core.models import (
                        CorrectionResult,
                        ItemProcessingResult,
                        TagSet,
                    )

                    proc_result = ItemProcessingResult(
                        correction=CorrectionResult(
                            corrected_content=item.content_raw,
                            overall_quality="C",
                        ),
                        classification=TagSet(),
                        item_subtype=item.block_type,
                    )

                t_process += time.monotonic() - tp0
                result.items_processed += 1
                collected.append((item, proc_result, block))
                await queue_b.put((item, proc_result, block))

            # 并发处理同一 block 内的 items
            tasks = [process_one(item, idx) for idx, item in enumerate(raw_items)]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

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
        """Task 3: 消费 Queue_B（仅记录计数，真正 unify 在 gather 后）"""
        t0 = time.monotonic()
        count = 0
        while True:
            msg = await queue_b.get()
            if msg is _SENTINEL:
                break
            count += 1
        timing["unify"] = time.monotonic() - t0

    # ── 辅助方法 ──

    def _convert_to_text(self, file_path: Path, fmt: DocumentFormat) -> str:
        """Stage 0: 将任意支持格式转换为纯文本/Markdown"""
        if fmt in (DocumentFormat.TEX, DocumentFormat.MARKDOWN):
            return file_path.read_text(encoding="utf-8")

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

    def _save_results(self, result: PipelineResult) -> None:
        """保存到 storage"""
        if not self.storage:
            return

        for kp in result.knowledge_points:
            try:
                self.storage.save_knowledge_point(kp)
            except Exception:
                logger.error("save_kp_failed", kp_id=kp.id, exc_info=True)

        for q, sol in result.questions:
            try:
                self.storage.save_question(q, sol)
            except Exception:
                logger.error("save_question_failed", q_id=q.id, exc_info=True)

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

    def _save_checkpoint(self, file_hash: str, result: PipelineResult) -> None:
        """保存 checkpoint"""
        cp_dir = self.config.checkpoint_dir / file_hash
        cp_dir.mkdir(parents=True, exist_ok=True)

        progress = PipelineRunProgress(
            file_path=result.file_path,
            file_hash=file_hash,
            blocks_completed=result.blocks_count,
            items_extracted=result.items_extracted,
            items_processed=result.items_processed,
            errors=result.errors,
        )

        (cp_dir / "progress.json").write_text(
            progress.model_dump_json(indent=2),
            encoding="utf-8",
        )
