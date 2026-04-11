"""Pipeline 断点续传状态的加载/保存助手

State 对象本身定义在 mathforge.core.models.FileExtractionState；本模块提供：
- compute_file_hash: 单文件 / 多卷文件的内容哈希
- state_dir: checkpoint 目录路径
- load_state / save_state: 原子加载与保存（tmp + os.replace）
- async-safe locking via asyncio.Lock per file_hash
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time as _time
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from mathforge.core.models import FileExtractionState

# C.3 节流保存：每个 file_hash 最近一次刷盘的 monotonic 时间戳
_LAST_SAVED_AT: dict[str, float] = {}
_DEFAULT_FLUSH_INTERVAL_SEC = 3.0

logger = structlog.get_logger()


# ── 哈希计算 ───────────────────────────────────────────


def compute_file_hash(paths: Path | Iterable[Path]) -> str:
    """计算单文件或一组分卷文件的统一哈希（前 16 位）

    多个文件按传入顺序拼接 hash，因此分卷顺序变化会得到不同 hash。
    """
    h = hashlib.sha256()
    files = [paths] if isinstance(paths, Path) else list(paths)
    for f in files:
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(8192), b""):
                h.update(chunk)
    return h.hexdigest()[:16]


# ── State 文件路径 ─────────────────────────────────────


def state_dir(checkpoint_root: Path, file_hash: str) -> Path:
    """单个 logical document 的 checkpoint 目录"""
    d = checkpoint_root / file_hash
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_file(checkpoint_root: Path, file_hash: str) -> Path:
    return state_dir(checkpoint_root, file_hash) / "state.json"


def recognize_dir(checkpoint_root: Path, file_hash: str, file_idx: int, file_stem: str) -> Path:
    """单个分卷文件的识别 batch 目录"""
    safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in file_stem)[:50]
    d = state_dir(checkpoint_root, file_hash) / "recognize" / f"file_{file_idx:03d}_{safe_stem}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── 加载 / 保存 ────────────────────────────────────────


def load_state(checkpoint_root: Path, file_hash: str) -> FileExtractionState | None:
    """加载已有 state；不存在或损坏时返回 None"""
    from mathforge.core.models import FileExtractionState

    f = state_file(checkpoint_root, file_hash)
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return FileExtractionState(**data)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("state_load_failed", file_hash=file_hash, error=str(exc))
        return None


def save_state(checkpoint_root: Path, state: FileExtractionState) -> None:
    """原子写入：先写 .tmp 再 os.replace"""
    from datetime import datetime

    state.last_checkpoint_at = datetime.now()
    f = state_file(checkpoint_root, state.file_hash)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, f)


# ── 进程内 lock（Producer 与 Consumer 并发写 state）──────


class StateLockRegistry:
    """每个 file_hash 一把 asyncio.Lock，避免 Producer/Consumer 同时写 state"""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, file_hash: str) -> asyncio.Lock:
        if file_hash not in self._locks:
            self._locks[file_hash] = asyncio.Lock()
        return self._locks[file_hash]


# 全局单例
_LOCKS = StateLockRegistry()


async def save_state_locked(
    checkpoint_root: Path,
    state: FileExtractionState,
    *,
    force: bool = False,
    min_interval_sec: float = _DEFAULT_FLUSH_INTERVAL_SEC,
) -> None:
    """并发安全的 save_state，默认带节流

    除非 force=True，同一 file_hash 在 min_interval_sec 秒内只保存一次，
    避免长文件高频磁盘写入（典型 300 页 PDF 会产生 100+ 次写入）。

    force=True 用于：Producer 完成 / Consumer 完成 / 异常退出等关键点，
    保证至少丢失不超过 min_interval_sec 秒的状态。
    """
    async with _LOCKS.get(state.file_hash):
        if not force:
            now = _time.monotonic()
            last = _LAST_SAVED_AT.get(state.file_hash, 0.0)
            if (now - last) < min_interval_sec:
                return
        save_state(checkpoint_root, state)
        _LAST_SAVED_AT[state.file_hash] = _time.monotonic()


# ── 工具：从磁盘扫描已识别的 batch ──────────────────────


def scan_recognized_batches(
    checkpoint_root: Path, file_hash: str
) -> list[tuple[int, int, int, Path]]:
    """扫描某个 logical document 已识别的所有 batch

    返回 [(file_idx, page_start, page_end, md_path), ...]，按 (file_idx, page_start) 排序。
    """
    base = state_dir(checkpoint_root, file_hash) / "recognize"
    if not base.exists():
        return []

    results: list[tuple[int, int, int, Path]] = []
    for file_dir in sorted(base.iterdir()):
        if not file_dir.is_dir() or not file_dir.name.startswith("file_"):
            continue
        try:
            file_idx = int(file_dir.name.split("_")[1])
        except (ValueError, IndexError):
            continue
        for md_file in sorted(file_dir.glob("page_*.md")):
            stem = md_file.stem  # page_001-005
            try:
                _, range_part = stem.split("_", 1)
                start_str, end_str = range_part.split("-")
                page_start = int(start_str)
                page_end = int(end_str)
            except ValueError:
                continue
            results.append((file_idx, page_start, page_end, md_file))

    results.sort(key=lambda x: (x[0], x[1]))
    return results
