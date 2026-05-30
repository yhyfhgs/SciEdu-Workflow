"""Pipeline 断点续传状态的加载/保存助手

State 对象本身定义在 sciforge.core.models.FileExtractionState；本模块提供：
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
    from sciforge.core.models import FileExtractionState

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


_CURRENT_STATE_SCHEMA_VERSION = 2


class StateSchemaMismatch(Exception):  # noqa: N818
    """state.json schema 与当前代码不兼容（需 --restart）"""


def is_state_complete(state: FileExtractionState | None) -> bool:
    """判断一个 logical_document 是否已经完整跑完 pipeline。

    关键：``failed_batches``（Task0/VLM 识别失败）和 ``failed_blocks``（Stage1
    提取失败）都非空视为**未完成**，避免 429 / 临时错误导致大量跳过后被错误
    标记为 completed，下次 resume 时略过 raw data 内容。

    仅当 state.phase == "completed"（外部显式标记）时也视为完成，但这个显式
    标记在 orchestrator 里只有 ``is_state_complete`` 返回 True 时才会被设置，
    所以不会出现"phase=completed 但 failed_blocks 非空"的情况——除非人工
    (或老版本代码) 误设，此时降级判定也能正确让 pipeline 重跑。
    """
    if state is None:
        return False
    full = (
        state.task0_completed
        and not state.failed_batches
        and not state.failed_blocks
        and state.stage1_completed
        and state.stage3_completed
    )
    if full:
        return True
    # 显式 completed 标记仅在失败队列全空时认可
    return state.phase == "completed" and not state.failed_batches and not state.failed_blocks


def load_state(checkpoint_root: Path, file_hash: str) -> FileExtractionState | None:
    """加载已有 state；不存在返回 None；schema 不兼容则抛出 StateSchemaMismatch"""
    from sciforge.core.models import FileExtractionState

    f = state_file(checkpoint_root, file_hash)
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("state_load_failed", file_hash=file_hash, error=str(exc))
        return None

    # 检测旧 schema（三段式 pipeline 留下的 state）
    legacy_fields = (
        "order_index_next",
        "current_task2_block_idx",
        "task1_saved_items",
        "saved_item_count",
        "format_spec_json",
    )
    has_legacy = any(k in data for k in legacy_fields)
    data_version = data.get("schema_version", 1)
    if has_legacy or data_version < _CURRENT_STATE_SCHEMA_VERSION:
        raise StateSchemaMismatch(
            f"检测到旧版 state.json (schema_version={data_version})，"
            f"当前代码要求 v{_CURRENT_STATE_SCHEMA_VERSION}。请删除 checkpoint 后加 --restart：\n"
            f"  rm -rf {checkpoint_root / file_hash}\n"
            f"  uv run sciforge parse run ... --restart"
        )

    try:
        state = FileExtractionState(**data)
    except ValueError as exc:
        logger.warning("state_validate_failed", file_hash=file_hash, error=str(exc))
        return None

    if is_state_complete(state) and state.phase != "completed":
        state.phase = "completed"
        try:
            save_state(checkpoint_root, state)
        except OSError as exc:
            logger.warning(
                "state_complete_autofix_save_failed",
                file_hash=file_hash,
                error=str(exc),
            )
        else:
            logger.info("state_complete_autofix", file_hash=file_hash)
    return state


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
    checkpoint_root: Path | None,
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

    checkpoint_root 为 None 时为 no-op（用于 dry-run / 单测）。
    """
    if checkpoint_root is None:
        return
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


def enum_cached_batches_for_file(
    checkpoint_root: Path,
    file_hash: str,
    file_idx: int,
    file_stem: str,
) -> list[tuple[int, int, Path]]:
    """列出某个文件（logical_document 中第 file_idx 个）的全部缓存 batch

    支持两种 checkpoint 格式（以保留历史 VLM 运行的产物）：

    1. **新格式**（当前代码产出）：`<hash>/recognize/file_NNN_<stem>/page_NNN-NNN.md`，
       页码是 1-based。
    2. **旧格式**（已淘汰但仍然存在的 VLM 运行产物）：`<hash>/vlm/page_NNN-NNN.md`，
       页码通常是 0-based，没有 file_NNN 子目录（旧代码不支持多分卷）。
       仅在 `file_idx == 0` 时才扫描。

    返回 [(page_start, page_end, md_path), ...]，按 (page_start, page_end) 排序。
    包括空 md（大小为 0）—— 它们可能是封面/空白页，连续性判断需要它们存在。
    Replay 时调用方应过滤空内容。
    """
    results: list[tuple[int, int, Path]] = []

    def _collect(rdir: Path) -> None:
        if not rdir.exists():
            return
        for mdp in rdir.glob("page_*.md"):
            try:
                _, rng = mdp.stem.split("_", 1)
                ps_str, pe_str = rng.split("-")
                ps = int(ps_str)
                pe = int(pe_str)
            except ValueError:
                continue
            results.append((ps, pe, mdp))

    # 新格式
    new_dir = recognize_dir(checkpoint_root, file_hash, file_idx, file_stem)
    _collect(new_dir)

    # 旧格式（legacy）：只在新格式没有内容且 file_idx == 0 时才接管
    if not results and file_idx == 0:
        legacy_dir = state_dir(checkpoint_root, file_hash) / "vlm"
        _collect(legacy_dir)

    results.sort(key=lambda x: (x[0], x[1]))
    return results
