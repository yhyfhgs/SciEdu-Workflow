"""Pipeline 进度盘点

扫描 Raw Data + checkpoint + SQLite，生成每个 logical_document 的：
- Task0 (VLM 识别) 状态：done / in_progress / not_started / no_checkpoint
- Task1 (条目提取) 状态：done / in_progress / not_started
- 入库条目数

模块同时提供 `audit_directory` / `format_audit_report` / `print_audit_table` 三个入口
供 CLI 使用。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from sciforge.core.models import DocumentFormat
from sciforge.pipeline.orchestrator import PipelineOrchestrator
from sciforge.pipeline.resume_state import (
    compute_file_hash,
    enum_cached_batches_for_file,
    load_state,
)

if TYPE_CHECKING:
    from rich.console import Console

logger = structlog.get_logger()

_PART_RE = re.compile(r"^(.+?)_part_(\d+)_(\d+)$", re.IGNORECASE)


@dataclass
class FileAuditEntry:
    """单个 logical_document 的审计结果"""

    logical_name: str
    files_in_doc: list[str] = field(default_factory=list)
    file_hash: str = ""
    checkpoint_path: str = ""
    task0_status: str = "not_started"  # done / in_progress / not_started / no_checkpoint
    task0_batches_done: int = 0
    task0_batches_expected: int | None = None
    task1_status: str = "not_started"  # done / in_progress / not_started
    task1_current_block: int = 0
    task1_saved_items: int = 0  # 来自 state.saved_item_count（权威）
    task1_failed_blocks: int = 0
    db_legacy_items: int = 0  # DB 中与该 logical_document 的 source_file 匹配的历史条目
    errors: list[str] = field(default_factory=list)


# ── 内部辅助 ──────────────────────────────────────────────


def _resolve_logical_name(item: Path | list[Path]) -> str:
    """返回 logical_document 的可读名字"""
    if isinstance(item, list):
        first = item[0]
        m = _PART_RE.match(first.stem)
        if m:
            return f"{m.group(1)}{first.suffix} (×{len(item)} 卷)"
        return f"{first.stem}{first.suffix} (×{len(item)})"
    return item.name


def _format_relative(path: Path, anchor: Path) -> str:
    """尽量返回相对 anchor 的相对路径；如果不在 anchor 内则返回绝对路径"""
    try:
        return str(path.relative_to(anchor))
    except ValueError:
        return str(path)


def _count_items_by_source_files(db_path: Path, source_files: list[str]) -> int:
    """查询 SQLite 按 source_file 统计 knowledge_points + questions 数量"""
    if not db_path.exists() or not source_files:
        return 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return 0
    try:
        placeholders = ",".join("?" * len(source_files))
        cur = conn.execute(
            f"SELECT COUNT(*) FROM knowledge_points WHERE source_file IN ({placeholders})",
            source_files,
        )
        kp_count = int(cur.fetchone()[0])
        cur = conn.execute(
            f"SELECT COUNT(*) FROM questions WHERE source_file IN ({placeholders})",
            source_files,
        )
        q_count = int(cur.fetchone()[0])
        return kp_count + q_count
    finally:
        conn.close()


def _pdf_total_pages(file_path: Path) -> int | None:
    """读 PDF 总页数，失败时返回 None"""
    try:
        import fitz

        with fitz.open(str(file_path)) as doc:
            total = int(doc.page_count)
    except Exception:
        return None
    return total if total > 0 else None


def _is_batches_complete_for_pdf(
    batches: list[tuple[int, int]],
    total_pages: int,
) -> bool:
    """判断 batch 列表是否连续覆盖整个 PDF（自动识别 0-based / 1-based）"""
    if not batches:
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
    return last_pe == target


# ── 主入口 ────────────────────────────────────────────────


def audit_directory(
    raw_data_dir: Path,
    checkpoint_dir: Path,
    db_path: Path,
    pages_per_batch: int = 5,
) -> list[FileAuditEntry]:
    """扫描 raw_data_dir + checkpoint + db，生成全量审计结果"""
    if not raw_data_dir.exists():
        logger.warning("audit_raw_data_missing", dir=str(raw_data_dir))
        return []

    files = sorted(
        PipelineOrchestrator._collect_files(raw_data_dir),
        key=PipelineOrchestrator._natural_sort_key,
    )
    grouped = PipelineOrchestrator._group_part_files(files)

    anchor = raw_data_dir.parent if raw_data_dir.parent != raw_data_dir else raw_data_dir

    entries: list[FileAuditEntry] = []
    for item in grouped:
        file_list: list[Path] = item if isinstance(item, list) else [item]
        entry = FileAuditEntry(
            logical_name=_resolve_logical_name(item),
            files_in_doc=[_format_relative(f, anchor) for f in file_list],
        )

        # 1. 计算 file_hash
        try:
            entry.file_hash = compute_file_hash(file_list)
        except Exception as exc:
            entry.errors.append(f"compute_file_hash: {exc}")
            entry.task0_status = "no_checkpoint"
            entries.append(entry)
            continue

        ckpt_subdir = checkpoint_dir / entry.file_hash
        if ckpt_subdir.exists():
            entry.checkpoint_path = _format_relative(ckpt_subdir, anchor)

        # 2. 加载 state.json
        state = load_state(checkpoint_dir, entry.file_hash)

        # 3. 统计 Task0 缓存情况：支持新旧两种 checkpoint 格式
        total_done = 0
        total_expected: int | None = 0
        per_file_complete: list[bool] = []
        for file_idx, f in enumerate(file_list):
            batches = enum_cached_batches_for_file(
                checkpoint_dir, entry.file_hash, file_idx, f.stem
            )
            total_done += len(batches)
            fmt = PipelineOrchestrator._detect_format(f)

            if fmt == DocumentFormat.PDF:
                total_pages = _pdf_total_pages(f)
                if total_pages is None:
                    # 无法估算预期 batch 数，但如果已有 batches 可做完整性判断
                    if total_expected is not None:
                        total_expected = None
                    per_file_complete.append(False)
                    continue
                # 预期 batch 数：按 pages_per_batch 向上取整（仅用于显示）
                if total_expected is not None:
                    total_expected += (total_pages + pages_per_batch - 1) // pages_per_batch
                pdf_batches = [(ps, pe) for ps, pe, _ in batches]
                per_file_complete.append(_is_batches_complete_for_pdf(pdf_batches, total_pages))
            elif fmt in (DocumentFormat.MARKDOWN, DocumentFormat.TEX):
                if total_expected is not None:
                    total_expected += 1
                per_file_complete.append(len(batches) >= 1)
            else:
                total_expected = None
                per_file_complete.append(False)

        entry.task0_batches_done = total_done
        entry.task0_batches_expected = total_expected

        # 4. 判定 Task0 状态
        all_complete = bool(per_file_complete) and all(per_file_complete)
        if state is not None and state.task0_completed:
            entry.task0_status = "done"
        elif total_done == 0:
            entry.task0_status = (
                "no_checkpoint" if state is None and not ckpt_subdir.exists() else "not_started"
            )
        elif all_complete:
            entry.task0_status = "done"  # 缓存完整
        else:
            entry.task0_status = "in_progress"

        # 5. 判定 Task1 状态（state 是权威来源）
        if state is not None:
            entry.task1_current_block = state.current_block_idx
            entry.task1_saved_items = state.saved_item_count
            entry.task1_failed_blocks = len(state.failed_blocks)
            if state.errors:
                entry.errors.extend(state.errors[:3])

            if state.phase == "completed":
                entry.task1_status = "done"
            elif state.current_block_idx > 0 or state.saved_item_count > 0:
                entry.task1_status = "in_progress"
            else:
                entry.task1_status = "not_started"

        # 6. DB 中按 source_file 查找历史 items（迁移后可能残留，不改 task1_status）
        source_files_abs = [str(f.resolve()) for f in file_list]
        entry.db_legacy_items = _count_items_by_source_files(db_path, source_files_abs)
        # 只有在 state 缺失时才用 DB 数推断 Task1 状态（state 在是权威）
        if state is None and entry.db_legacy_items > 0:
            entry.task1_saved_items = entry.db_legacy_items
            entry.task1_status = "in_progress"

        entries.append(entry)

    return entries


# ── 报告生成 ──────────────────────────────────────────────


def format_audit_report(
    entries: list[FileAuditEntry],
    raw_data_dir: Path,
    checkpoint_dir: Path,
    db_path: Path,
) -> str:
    """生成 Markdown 格式的报告文本"""
    lines: list[str] = [
        "# Pipeline 进度盘点报告",
        "",
        f"- 生成时间：`{datetime.now().isoformat(timespec='seconds')}`",
        f"- Raw Data 目录：`{raw_data_dir}`",
        f"- Checkpoint 目录：`{checkpoint_dir}`",
        f"- 数据库：`{db_path}`",
        f"- logical_document 总数：**{len(entries)}**",
        "",
    ]

    task0_done = sum(1 for e in entries if e.task0_status == "done")
    task0_progress = sum(1 for e in entries if e.task0_status == "in_progress")
    task0_not_started = sum(
        1 for e in entries if e.task0_status in ("not_started", "no_checkpoint")
    )
    task1_done = sum(1 for e in entries if e.task1_status == "done")
    task1_progress = sum(1 for e in entries if e.task1_status == "in_progress")
    task1_not_started = sum(1 for e in entries if e.task1_status == "not_started")
    total_items = sum(e.task1_saved_items for e in entries)

    db_total = sum(e.db_legacy_items for e in entries)

    lines.extend(
        [
            "## 概览",
            "",
            "| 阶段 | 完成 | 进行中 | 未开始 |",
            "|---|---|---|---|",
            f"| Task0 (VLM 识别) | {task0_done} | {task0_progress} | {task0_not_started} |",
            f"| Task1 (条目提取) | {task1_done} | {task1_progress} | {task1_not_started} |",
            "",
            f"State 入库条目累计：**{total_items}**",
            f"DB 历史条目（按 source_file 匹配）：**{db_total}**",
            "",
            "## 详细进度",
            "",
            "| # | logical_document | 文件数 | Task0 | Batches | Task1 | State | DB | Hash |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )

    for i, e in enumerate(entries, 1):
        batches_col = (
            f"{e.task0_batches_done}/{e.task0_batches_expected}"
            if e.task0_batches_expected is not None
            else f"{e.task0_batches_done}/?"
        )
        short_hash = e.file_hash[:8] if e.file_hash else "—"
        name_escaped = e.logical_name.replace("|", "\\|")
        lines.append(
            f"| {i} | {name_escaped} | {len(e.files_in_doc)} | "
            f"{e.task0_status} | {batches_col} | {e.task1_status} | "
            f"{e.task1_saved_items} | {e.db_legacy_items} | `{short_hash}` |"
        )
    lines.append("")

    # 多卷详细
    multi_vol = [e for e in entries if len(e.files_in_doc) > 1]
    if multi_vol:
        lines.extend(["## 多卷文档详细", ""])
        for i, e in enumerate(entries, 1):
            if len(e.files_in_doc) > 1:
                lines.append(f"### [{i}] {e.logical_name}")
                for f in e.files_in_doc:
                    lines.append(f"- `{f}`")
                lines.append("")

    # 推荐下一步：Task0 完成但 Task1 未完
    replay_candidates = [
        e for e in entries if e.task0_status == "done" and e.task1_status != "done"
    ]
    if replay_candidates:
        lines.extend(["## 推荐下一步：已识别直通 (Task0 完成，Task1 未完)", ""])
        for e in replay_candidates:
            lines.append(
                f"- `{e.logical_name}` (hash={e.file_hash[:8]}, "
                f"{e.task1_saved_items} items saved): 可用 `parse run` 触发直通"
            )
        lines.append("")

    # Errors
    with_errors = [e for e in entries if e.errors]
    if with_errors:
        lines.extend(["## 错误记录", ""])
        for e in with_errors:
            lines.append(f"### {e.logical_name}")
            for err in e.errors:
                lines.append(f"- {err}")
            lines.append("")

    return "\n".join(lines)


def print_audit_table(entries: list[FileAuditEntry], console: Console) -> None:
    """用 rich.Table 在终端展示进度概览"""
    from rich.table import Table

    task0_done = sum(1 for e in entries if e.task0_status == "done")
    task0_progress = sum(1 for e in entries if e.task0_status == "in_progress")
    task1_done = sum(1 for e in entries if e.task1_status == "done")
    task1_progress = sum(1 for e in entries if e.task1_status == "in_progress")
    state_items = sum(e.task1_saved_items for e in entries)
    db_items = sum(e.db_legacy_items for e in entries)

    console.print()
    console.print(f"[bold cyan]Pipeline 进度盘点[/]  —  {len(entries)} logical documents")
    console.print(
        f"Task0: [green]{task0_done} done[/] · [yellow]{task0_progress} in_progress[/]   "
        f"Task1: [green]{task1_done} done[/] · [yellow]{task1_progress} in_progress[/]"
    )
    console.print(f"State items: [cyan]{state_items}[/]   DB legacy items: [cyan]{db_items}[/]")

    table = Table(title="Progress Detail", show_lines=False, expand=True)
    table.add_column("#", style="dim", justify="right", no_wrap=True)
    table.add_column("logical_document", style="cyan", ratio=3, no_wrap=False)
    table.add_column("N", justify="right", no_wrap=True)
    table.add_column("Task0", justify="center", no_wrap=True)
    table.add_column("Batches", justify="right", no_wrap=True)
    table.add_column("Task1", justify="center", no_wrap=True)
    table.add_column("State", justify="right", no_wrap=True)
    table.add_column("DB", justify="right", no_wrap=True)

    def _style(status: str) -> str:
        return {
            "done": "green",
            "in_progress": "yellow",
            "not_started": "dim white",
            "no_checkpoint": "red",
        }.get(status, "white")

    for i, e in enumerate(entries, 1):
        batches_col = (
            f"{e.task0_batches_done}/{e.task0_batches_expected}"
            if e.task0_batches_expected is not None
            else f"{e.task0_batches_done}/?"
        )
        table.add_row(
            str(i),
            e.logical_name,
            str(len(e.files_in_doc)),
            f"[{_style(e.task0_status)}]{e.task0_status}[/]",
            batches_col,
            f"[{_style(e.task1_status)}]{e.task1_status}[/]",
            str(e.task1_saved_items),
            str(e.db_legacy_items),
        )

    console.print(table)
