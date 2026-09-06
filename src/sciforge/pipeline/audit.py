"""Pipeline 进度盘点

扫描 Raw Data + checkpoint + SQLite，生成每个 logical_document 的：
- Task0 (VLM 识别) 状态：done / in_progress / not_started / no_checkpoint
- 5 阶段 pipeline 状态：done / in_progress / not_started
- 入库条目数（state 权威）

v6 schema 已删除 source_file 列，DB 侧无法再按文件匹配历史条目，
因此本报告的"DB 历史"字段统一为学科总计数。
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
from sciforge.pipeline.recognizers import pdf_page_count
from sciforge.pipeline.resume_state import (
    compute_file_hash,
    enum_cached_batches_for_file,
    is_state_complete,
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
    pipeline_status: str = "not_started"  # done / in_progress / not_started
    current_block: int = 0
    saved_items: int = 0  # 来自 state.stage3_persisted_count（权威）
    failed_blocks: int = 0
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


def _count_db_items_by_discipline(db_path: Path, discipline: str) -> int:
    """查询 SQLite 统计指定学科的 knowledge_points + questions 数量（v6 schema）"""
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return 0
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM knowledge_points WHERE discipline = ?", (discipline,)
        )
        kp_count = int(cur.fetchone()[0])
        cur = conn.execute("SELECT COUNT(*) FROM questions WHERE discipline = ?", (discipline,))
        q_count = int(cur.fetchone()[0])
        return kp_count + q_count
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def _pdf_total_pages(file_path: Path) -> int | None:
    """读 PDF 总页数，失败时返回 None"""
    return pdf_page_count(file_path)


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
            elif fmt in (DocumentFormat.MARKDOWN, DocumentFormat.TEX, DocumentFormat.IMAGE):
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

        # 5. 判定 pipeline 状态（state 是权威来源）
        if state is not None:
            entry.current_block = state.current_block_idx
            entry.saved_items = state.stage3_persisted_count
            entry.failed_blocks = len(state.failed_blocks)
            if state.errors:
                entry.errors.extend(state.errors[:3])

            if is_state_complete(state):
                entry.pipeline_status = "done"
            elif state.current_block_idx > 0 or state.stage3_persisted_count > 0:
                entry.pipeline_status = "in_progress"
            else:
                entry.pipeline_status = "not_started"

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
    pipe_done = sum(1 for e in entries if e.pipeline_status == "done")
    pipe_progress = sum(1 for e in entries if e.pipeline_status == "in_progress")
    pipe_not_started = sum(1 for e in entries if e.pipeline_status == "not_started")
    total_items = sum(e.saved_items for e in entries)

    lines.extend(
        [
            "## 概览",
            "",
            "| 阶段 | 完成 | 进行中 | 未开始 |",
            "|---|---|---|---|",
            f"| Task0 (VLM 识别) | {task0_done} | {task0_progress} | {task0_not_started} |",
            f"| 5 Stage Pipeline | {pipe_done} | {pipe_progress} | {pipe_not_started} |",
            "",
            f"State 入库条目累计：**{total_items}**",
            "",
            "## 详细进度",
            "",
            "| # | logical_document | 文件数 | Task0 | Batches | Pipeline | Items | Hash |",
            "|---|---|---|---|---|---|---|---|",
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
            f"{e.task0_status} | {batches_col} | {e.pipeline_status} | "
            f"{e.saved_items} | `{short_hash}` |"
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

    # 推荐下一步：Task0 完成但 pipeline 未完
    replay_candidates = [
        e for e in entries if e.task0_status == "done" and e.pipeline_status != "done"
    ]
    if replay_candidates:
        lines.extend(["## 推荐下一步：已识别直通 (Task0 完成，Pipeline 未完)", ""])
        for e in replay_candidates:
            lines.append(
                f"- `{e.logical_name}` (hash={e.file_hash[:8]}, "
                f"{e.saved_items} items saved): 可用 `parse run` 触发直通"
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
    pipe_done = sum(1 for e in entries if e.pipeline_status == "done")
    pipe_progress = sum(1 for e in entries if e.pipeline_status == "in_progress")
    state_items = sum(e.saved_items for e in entries)

    console.print()
    console.print(f"[bold cyan]Pipeline 进度盘点[/]  —  {len(entries)} logical documents")
    console.print(
        f"Task0: [green]{task0_done} done[/] · [yellow]{task0_progress} in_progress[/]   "
        f"Pipeline: [green]{pipe_done} done[/] · [yellow]{pipe_progress} in_progress[/]"
    )
    console.print(f"State items: [cyan]{state_items}[/]")

    table = Table(title="Progress Detail", show_lines=False, expand=True)
    table.add_column("#", style="dim", justify="right", no_wrap=True)
    table.add_column("logical_document", style="cyan", ratio=3, no_wrap=False)
    table.add_column("N", justify="right", no_wrap=True)
    table.add_column("Task0", justify="center", no_wrap=True)
    table.add_column("Batches", justify="right", no_wrap=True)
    table.add_column("Pipeline", justify="center", no_wrap=True)
    table.add_column("Items", justify="right", no_wrap=True)

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
            f"[{_style(e.pipeline_status)}]{e.pipeline_status}[/]",
            str(e.saved_items),
        )

    console.print(table)


# ── 同义异名审计 ──────────────────────────────────────────


def _levenshtein(a: str, b: str) -> int:
    """最小编辑距离"""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


@dataclass
class SimilarTagPair:
    """一对疑似同义异名的 tag 段"""

    seg_a: str
    seg_b: str
    english_distance: int
    chinese_distance: int
    count_a: int
    count_b: int
    reason: str  # english_close / chinese_identical / english_identical_diff_chinese


def audit_tag_similarity(
    db_path: Path,
    max_english_distance: int = 2,
    max_chinese_distance: int = 1,
) -> list[SimilarTagPair]:
    """扫描 DB 中所有 tag 段，找出疑似同义异名的段对。

    检测规则：
    - `english_close`: english 部分编辑距离 ≤ max_english_distance 且中文相同
    - `chinese_identical`: 中文完全相同但 english 不同
      （如 `matrix:矩阵` vs `matrices:矩阵`）
    - `english_identical_diff_chinese`: english 相同但中文不同（严重问题）
      （如 `algebra:代数` vs `algebra:高等代数`）

    Returns:
        列表按 (english_distance, -count_a-count_b) 升序
    """
    import json as _json_mod

    if not db_path.exists():
        return []

    # 收集所有 tag 段及其出现频次
    seg_counts: dict[str, int] = {}
    conn = sqlite3.connect(str(db_path))
    try:
        for table in ("knowledge_points", "questions"):
            for (tags_json,) in conn.execute(f"SELECT tags FROM {table}").fetchall():  # noqa: S608
                if not tags_json:
                    continue
                try:
                    tags = _json_mod.loads(tags_json)
                except (ValueError, TypeError):
                    continue
                if not isinstance(tags, list):
                    continue
                for seg in tags:
                    if isinstance(seg, str) and ":" in seg:
                        seg_counts[seg] = seg_counts.get(seg, 0) + 1
    finally:
        conn.close()

    # 按 english 分组
    by_english: dict[str, list[str]] = {}
    by_chinese: dict[str, list[str]] = {}
    for seg in seg_counts:
        parts = seg.split(":", 1)
        if len(parts) != 2:
            continue
        eng, cn = parts
        by_english.setdefault(eng, []).append(seg)
        by_chinese.setdefault(cn, []).append(seg)

    pairs: list[SimilarTagPair] = []
    seen: set[tuple[str, ...]] = set()

    # 1. english 相同但中文不同（严重）
    for eng, segs in by_english.items():
        if len(segs) < 2:
            continue
        for i, s1 in enumerate(segs):
            for s2 in segs[i + 1 :]:
                key = tuple(sorted((s1, s2)))
                if key in seen:
                    continue
                seen.add(key)
                pairs.append(
                    SimilarTagPair(
                        seg_a=s1,
                        seg_b=s2,
                        english_distance=0,
                        chinese_distance=_levenshtein(s1.split(":", 1)[1], s2.split(":", 1)[1]),
                        count_a=seg_counts[s1],
                        count_b=seg_counts[s2],
                        reason="english_identical_diff_chinese",
                    )
                )

    # 2. 中文相同但 english 不同
    for cn, segs in by_chinese.items():
        if len(segs) < 2:
            continue
        for i, s1 in enumerate(segs):
            for s2 in segs[i + 1 :]:
                key = tuple(sorted((s1, s2)))
                if key in seen:
                    continue
                seen.add(key)
                eng_a, eng_b = s1.split(":", 1)[0], s2.split(":", 1)[0]
                pairs.append(
                    SimilarTagPair(
                        seg_a=s1,
                        seg_b=s2,
                        english_distance=_levenshtein(eng_a, eng_b),
                        chinese_distance=0,
                        count_a=seg_counts[s1],
                        count_b=seg_counts[s2],
                        reason="chinese_identical",
                    )
                )

    # 3. english 编辑距离近且中文编辑距离近
    all_segs = list(seg_counts.keys())
    for i, s1 in enumerate(all_segs):
        eng_a, cn_a = s1.split(":", 1)
        for s2 in all_segs[i + 1 :]:
            key = tuple(sorted((s1, s2)))
            if key in seen:
                continue
            eng_b, cn_b = s2.split(":", 1)
            eng_d = _levenshtein(eng_a, eng_b)
            if eng_d == 0 or eng_d > max_english_distance:
                continue
            cn_d = _levenshtein(cn_a, cn_b)
            if cn_d > max_chinese_distance:
                continue
            seen.add(key)
            pairs.append(
                SimilarTagPair(
                    seg_a=s1,
                    seg_b=s2,
                    english_distance=eng_d,
                    chinese_distance=cn_d,
                    count_a=seg_counts[s1],
                    count_b=seg_counts[s2],
                    reason="english_close",
                )
            )

    pairs.sort(key=lambda p: (p.english_distance + p.chinese_distance, -(p.count_a + p.count_b)))
    return pairs


def print_tag_similarity_report(pairs: list[SimilarTagPair], console: Console) -> None:
    """以 Rich table 格式打印同义异名审计结果"""
    from rich.table import Table

    if not pairs:
        console.print("[green]未发现疑似同义异名的 tag 段对。[/]")
        return

    table = Table(title=f"Tag 同义异名审计（共 {len(pairs)} 对）")
    table.add_column("Reason", style="cyan")
    table.add_column("Seg A", style="white")
    table.add_column("Count A", justify="right")
    table.add_column("Seg B", style="white")
    table.add_column("Count B", justify="right")
    table.add_column("English d", justify="right")
    table.add_column("Chinese d", justify="right")

    for p in pairs:
        reason_style = {
            "english_identical_diff_chinese": "bold red",
            "chinese_identical": "yellow",
            "english_close": "white",
        }.get(p.reason, "white")
        table.add_row(
            f"[{reason_style}]{p.reason}[/]",
            p.seg_a,
            str(p.count_a),
            p.seg_b,
            str(p.count_b),
            str(p.english_distance),
            str(p.chinese_distance),
        )

    console.print(table)
