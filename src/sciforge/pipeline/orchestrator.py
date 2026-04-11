"""PipelineOrchestrator — Producer-Consumer 流水线编排器

架构:
  Task0 (Producer): 文档识别 → asyncio.Queue
  Task1 (Consumer): 滑动窗口提取 + 校正 + 打标签 + 入库

两个 Task 通过单个 queue 串联，支持背压、断点恢复、异常重试。
目录模式下，多个 logical_document 可以并发跑，每个 document 跑一对 Task0+Task1。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from mathforge.core.models import (
    DirectoryProgress,
    DocumentFormat,
    FileExtractionState,
    MdBlockSource,
)
from mathforge.pipeline.recognizers import get_recognizer
from mathforge.pipeline.resume_state import (
    compute_file_hash,
    load_state,
    recognize_dir,
    save_state_locked,
    state_dir,
)
from mathforge.pipeline.task1_extractor import SENTINEL, Task1Extractor
from mathforge.storage.sqlite_backend import SQLiteStorageBackend

if TYPE_CHECKING:
    from mathforge.config.settings import AppConfig
    from mathforge.llm.client import LLMClient
    from mathforge.storage.protocol import BatchStorageBackend

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
    failed_batches: list[tuple[int, int, int]] = field(default_factory=list)
    failed_blocks: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0


# ── Orchestrator ──────────────────────────────────────


class PipelineOrchestrator:
    """Producer-Consumer 流水线编排器"""

    _PART_RE = re.compile(r"^(.+?)_part_(\d+)_(\d+)$", re.IGNORECASE)

    def __init__(
        self,
        config: AppConfig,
        llm_client: LLMClient | None = None,
        storage: BatchStorageBackend | None = None,
        db_path: Path | None = None,
        llm_clients: dict[str, LLMClient] | None = None,
        pdf_backend: str = "vlm",
    ) -> None:
        """
        Args:
            storage: 共享 backend 实例（dry-run / 测试场景下传入）
            db_path: SQLite 路径，每个 process_file 会在内部构造独立 backend
                    （目录并发模式下避免多协程共享 sqlite3.Connection 的线程风险）
        """
        self.config = config
        self.llm = llm_client
        self.storage = storage
        self.db_path = db_path
        self._llm_clients = llm_clients or {}
        self._pdf_backend = pdf_backend

    def _get_llm(self, task: str) -> LLMClient | None:
        return self._llm_clients.get(task) or self.llm

    # ── 单 logical_document 入口 ──────────────────────

    async def process_file(
        self,
        file_path: Path | list[Path],
    ) -> PipelineResult:
        """处理单个 logical_document（单文件或多个分卷）"""
        files = [file_path] if isinstance(file_path, Path) else list(file_path)
        if not files:
            raise ValueError("empty file list")

        primary_path = files[0]
        result = PipelineResult(file_path=str(primary_path))
        t0 = time.monotonic()

        # 1. 计算 file_hash 并加载 state
        file_hash = compute_file_hash(files)
        result.file_hash = file_hash

        state = load_state(self.config.checkpoint_dir, file_hash)
        if state is None:
            state = FileExtractionState(file_path=str(primary_path), file_hash=file_hash)

        if state.phase == "completed":
            logger.info("pipeline_file_already_done", file=primary_path.name)
            result.items_saved = state.saved_item_count
            return result

        logger.info(
            "pipeline_file_start",
            file=primary_path.name,
            file_hash=file_hash,
            files_in_doc=len(files),
            resume=bool(state.vlm_batches_done),
        )

        # 2. 为本 logical_document 构造独立 storage（或复用共享实例）
        local_backend: SQLiteStorageBackend | None = None
        storage_for_task1: BatchStorageBackend | None = self.storage
        if self.storage is None and self.db_path is not None:
            local_backend = SQLiteStorageBackend(self.db_path)
            storage_for_task1 = local_backend

        # 3. 准备 queue
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=self.config.pipeline_queue_size)

        # 4. 启动 Producer + Consumer
        producer_task = asyncio.create_task(
            self._task0_producer(files, state, queue, result)
        )

        extract_llm = self._get_llm("extract_and_classify")
        consumer_task: asyncio.Task[None] | None = None
        if extract_llm is None:
            logger.warning("pipeline_no_llm", file=primary_path.name)
        else:
            extractor = Task1Extractor(
                llm_client=extract_llm,
                config=self.config,
                storage=storage_for_task1,
                checkpoint_root=self.config.checkpoint_dir,
            )
            consumer_task = asyncio.create_task(
                self._task1_consumer(extractor, queue, state, result)
            )

        # 5. 等待两个任务
        try:
            await producer_task
            if consumer_task is not None:
                await consumer_task
        except Exception as exc:
            logger.error("pipeline_task_exception", error=str(exc), exc_info=True)
            result.errors.append(str(exc))
            # 异常退出时强制刷一次 state（覆盖 C.3 节流保存的漏洞）
            try:
                await save_state_locked(self.config.checkpoint_dir, state, force=True)
            except Exception:
                pass
        finally:
            if local_backend is not None:
                local_backend.close()

        # 6. 同步状态到 result
        result.failed_batches = list(state.failed_batches)
        result.failed_blocks = list(state.failed_blocks)
        result.items_saved = state.saved_item_count
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
        try:
            block_index = 0
            # state 中 vlm_batches_done 序列化为 list[list]，转 set[tuple] 便于 O(1) lookup
            done_keys = {tuple(t) for t in state.vlm_batches_done}

            for file_idx, f in enumerate(files):
                fmt = self._detect_format(f)

                # 当前文件的识别 batch 输出目录
                output_dir = recognize_dir(
                    self.config.checkpoint_dir,
                    state.file_hash,
                    file_idx,
                    f.stem,
                )

                # 已识别的 (page_start, page_end)（针对当前 file_idx）
                already_done_pairs: list[tuple[int, int]] = [
                    (ps, pe) for (fi, ps, pe) in done_keys if fi == file_idx
                ]

                vlm_llm = self._get_llm("vlm_convert")
                try:
                    recognizer = get_recognizer(
                        fmt, self._pdf_backend, vlm_llm, self.config
                    )
                except Exception as exc:
                    logger.error(
                        "pipeline_recognizer_init_failed",
                        file=f.name,
                        format=fmt.value,
                        error=str(exc),
                    )
                    result.errors.append(f"recognizer_init({f.name}): {exc}")
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
                    msb = MdBlockSource(
                        file_path=str(f),
                        page_start=batch.page_start,
                        page_end=batch.page_end,
                        md_path=str(batch.md_path),
                        md_text=batch.md_text,
                        block_index=block_index,
                    )
                    block_index += 1
                    result.blocks_pushed = block_index
                    result.pages_recognized = max(
                        result.pages_recognized, batch.page_end
                    )

                    # 记录到 state.vlm_batches_done
                    key = (file_idx, batch.page_start, batch.page_end)
                    if key not in done_keys:
                        done_keys.add(key)
                        state.vlm_batches_done.append(key)
                        await save_state_locked(self.config.checkpoint_dir, state)

                    # 推入 queue（背压）
                    await queue.put(msb)

        except Exception as exc:
            logger.error("task0_producer_failed", error=str(exc), exc_info=True)
            result.errors.append(f"producer: {exc}")
        finally:
            # SENTINEL 推送后强制刷盘，覆盖节流保存
            try:
                await save_state_locked(self.config.checkpoint_dir, state, force=True)
            except Exception:
                pass
            await queue.put(SENTINEL)

    # ── Task1: Consumer ───────────────────────────────

    async def _task1_consumer(
        self,
        extractor: Task1Extractor,
        queue: asyncio.Queue[object],
        state: FileExtractionState,
        result: PipelineResult,
    ) -> None:
        """从 queue 取 mdblock，提取并入库"""
        try:
            async for batch in extractor.run(queue, state):
                logger.debug(
                    "task1_batch_yielded",
                    kp=batch.kp_count,
                    q=batch.question_count,
                    order=batch.order_index_range,
                )
        except Exception as exc:
            logger.error("task1_consumer_failed", error=str(exc), exc_info=True)
            result.errors.append(f"consumer: {exc}")

    # ── 目录处理 ──────────────────────────────────────

    async def process_directory(
        self,
        dir_path: Path,
        max_concurrent_files: int = 4,
    ) -> list[PipelineResult]:
        """处理目录下所有文件（按 logical_document 分组并发）"""
        files = sorted(self._collect_files(dir_path), key=self._natural_sort_key)
        grouped = self._group_part_files(files)

        dir_progress = self._load_directory_progress(dir_path)

        # 过滤已完成的（按内容 hash 去重）
        work_items: list[tuple[int, Path | list[Path], str]] = []
        for i, item in enumerate(grouped):
            pkey = str(item[0]) if isinstance(item, list) else str(item)
            try:
                cur_hash = compute_file_hash(
                    item if isinstance(item, list) else item
                )
            except Exception:
                cur_hash = ""
            if (
                pkey in dir_progress.completed_files
                and dir_progress.completed_files[pkey] == cur_hash
                and cur_hash
            ):
                logger.debug("pipeline_dir_skip_completed", file=pkey)
                continue
            work_items.append((i, item, pkey))

        logger.info(
            "pipeline_directory_start",
            dir=str(dir_path),
            total_groups=len(grouped),
            remaining=len(work_items),
            skipped=len(grouped) - len(work_items),
            max_concurrent=max_concurrent_files,
        )

        sem = asyncio.Semaphore(max_concurrent_files)
        results: list[PipelineResult] = [
            PipelineResult(file_path=pkey) for _, _, pkey in work_items
        ]

        async def _process_one(idx: int, item: Path | list[Path], pkey: str) -> None:
            async with sem:
                try:
                    r = await self.process_file(item)
                    results[idx] = r
                    dir_progress.completed_files[pkey] = r.file_hash
                    self._save_directory_progress(dir_path, dir_progress)
                except Exception as exc:
                    logger.error("pipeline_file_failed", file=pkey, error=str(exc))
                    results[idx] = PipelineResult(file_path=pkey, errors=[str(exc)])
                    dir_progress.failed_files[pkey] = str(exc)
                    self._save_directory_progress(dir_path, dir_progress)

        tasks = [
            _process_one(idx, item, pkey)
            for idx, (_, item, pkey) in enumerate(work_items)
        ]
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
            if p.is_file()
            and p.suffix.lower() in extensions
            and not p.name.startswith(".")
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

    def _save_directory_progress(
        self, dir_path: Path, progress: DirectoryProgress
    ) -> None:
        import hashlib

        dir_hash = hashlib.sha256(str(dir_path.resolve()).encode()).hexdigest()[:12]
        self.config.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        progress_file = self.config.checkpoint_dir / f"dir_{dir_hash}.json"
        progress_file.write_text(
            progress.model_dump_json(indent=2), encoding="utf-8"
        )

    # ── 清理 checkpoint（--restart 用）──────────────────

    def purge_checkpoint(self, file_path: Path | list[Path]) -> None:
        """清空指定 logical_document 的 checkpoint"""
        files = [file_path] if isinstance(file_path, Path) else list(file_path)
        file_hash = compute_file_hash(files)
        d = state_dir(self.config.checkpoint_dir, file_hash)
        if d.exists():
            import shutil

            shutil.rmtree(d)
            logger.info("checkpoint_purged", file_hash=file_hash, path=str(d))

    def purge_directory_checkpoints(self, dir_path: Path) -> None:
        """清空目录下所有 logical_document 的 checkpoint 以及目录进度文件"""
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
