"""Legacy checkpoint 迁移

把旧架构的 `<hash>/vlm/page_NNN-NNN.md`（0-based 页码、单文件）迁移到新架构的
`<hash>/recognize/file_000_<safe_stem>/page_NNN-NNN.md`（1-based 页码、支持分卷）。

流程：
1. 扫描 `<checkpoint_dir>/*/vlm/` 作为 legacy 来源
2. 扫描 Raw Data 下所有 PDF，用 `compute_file_hash` 建立 hash→file 映射
3. 反向匹配每个 legacy checkpoint 到原始 PDF 文件
4. 把 md 文件 copy 到新目录，页码根据是否 0-based 偏移 +1
5. 重建 state.json：`task0_completed=True`，`vlm_batches_done` 用迁移后的 1-based 元组，
   Task1 字段清零（等用户重新跑 extraction）
6. 可选：迁移后删除旧的 vlm/ 目录
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import structlog

from sciforge.core.models import FileExtractionState
from sciforge.pipeline.orchestrator import PipelineOrchestrator
from sciforge.pipeline.resume_state import (
    compute_file_hash,
    is_state_complete,
    load_state,
    recognize_dir,
    save_state,
)

logger = structlog.get_logger()


@dataclass
class MigrationResult:
    """单个 legacy checkpoint 的迁移结果"""

    file_hash: str
    legacy_dir: str
    matched_file: str = ""
    status: str = "pending"  # migrated / would_migrate / orphan / skipped / error
    batches_migrated: int = 0
    new_dir: str = ""
    base_offset: int = 0  # 0-based 时为 1，1-based 时为 0
    error: str = ""


def discover_legacy_checkpoints(checkpoint_dir: Path) -> list[tuple[str, Path]]:
    """枚举 `<checkpoint_dir>/*/vlm/` 作为 legacy 来源

    返回 [(file_hash, legacy_vlm_dir), ...]，按 file_hash 排序。
    只算有 page_*.md 的目录。
    """
    if not checkpoint_dir.exists():
        return []
    results: list[tuple[str, Path]] = []
    for d in sorted(checkpoint_dir.iterdir()):
        if not d.is_dir():
            continue
        vlm_dir = d / "vlm"
        if not vlm_dir.is_dir():
            continue
        if any(vlm_dir.glob("page_*.md")):
            results.append((d.name, vlm_dir))
    return results


def build_hash_to_file_map(raw_data_dir: Path) -> dict[str, Path]:
    """扫描 Raw Data 下所有 PDF，计算 hash，建立映射

    legacy checkpoint 均为单文件（旧代码不支持多分卷），所以只处理单个 PDF，
    不走 `_group_part_files`。
    """
    if not raw_data_dir.exists():
        return {}
    mapping: dict[str, Path] = {}
    for f in PipelineOrchestrator._collect_files(raw_data_dir):
        if f.suffix.lower() != ".pdf":
            continue
        try:
            h = compute_file_hash(f)
        except Exception as exc:
            logger.warning("migrate_hash_failed", file=str(f), error=str(exc))
            continue
        mapping[h] = f
    return mapping


def _parse_page_range(name: str) -> tuple[int, int] | None:
    """从 `page_NNN-NNN` 文件名解析 (start, end)；失败返回 None"""
    try:
        _, rng = name.split("_", 1)
        ps_str, pe_str = rng.split("-")
        return int(ps_str), int(pe_str)
    except ValueError:
        return None


def _enum_legacy_mds(legacy_dir: Path) -> list[tuple[int, int, Path]]:
    """枚举 legacy dir 下的 page_*.md，按 (ps, pe) 排序"""
    entries: list[tuple[int, int, Path]] = []
    for mdp in legacy_dir.glob("page_*.md"):
        parsed = _parse_page_range(mdp.stem)
        if parsed is None:
            continue
        ps, pe = parsed
        entries.append((ps, pe, mdp))
    entries.sort(key=lambda x: (x[0], x[1]))
    return entries


def migrate_legacy_checkpoint(
    checkpoint_dir: Path,
    raw_data_dir: Path,
    *,
    dry_run: bool = False,
    delete_legacy: bool = False,
    force: bool = False,
) -> list[MigrationResult]:
    """迁移所有 legacy checkpoint 到新架构

    Args:
        checkpoint_dir: checkpoint 根目录
        raw_data_dir: Raw Data 根目录（用于反向匹配源文件）
        dry_run: True 时不落盘，只展示将要做的事
        delete_legacy: 迁移成功后是否删除 vlm/ 目录（默认保留作为备份）
        force: 已迁移（新目录有内容 / Task1 已跑过）的 checkpoint 也强制重跑

    Returns: 每个 legacy checkpoint 的迁移结果列表
    """
    legacy_list = discover_legacy_checkpoints(checkpoint_dir)
    hash_map = build_hash_to_file_map(raw_data_dir)

    results: list[MigrationResult] = []

    for file_hash, legacy_dir in legacy_list:
        result = MigrationResult(
            file_hash=file_hash,
            legacy_dir=str(legacy_dir),
        )

        matched = hash_map.get(file_hash)
        if matched is None:
            result.status = "orphan"
            result.error = "no matching PDF in Raw Data"
            logger.warning("migrate_orphan", hash=file_hash)
            results.append(result)
            continue
        result.matched_file = str(matched)

        legacy_mds = _enum_legacy_mds(legacy_dir)
        if not legacy_mds:
            result.status = "skipped"
            result.error = "legacy dir has no parsable page_*.md"
            results.append(result)
            continue

        # 判断 0-based 还是 1-based
        first_ps = legacy_mds[0][0]
        if first_ps == 0:
            offset = 1
        elif first_ps == 1:
            offset = 0
        else:
            result.status = "error"
            result.error = f"unexpected first page {first_ps} (not 0/1)"
            results.append(result)
            continue
        result.base_offset = offset

        # 新目录（仅 dry_run 不创建）
        if dry_run:
            new_dir_path = (
                checkpoint_dir / file_hash / "recognize" / f"file_000_{matched.stem[:50]}"
            )
        else:
            new_dir_path = recognize_dir(checkpoint_dir, file_hash, 0, matched.stem)
        result.new_dir = str(new_dir_path)

        # 幂等性检查：如果新目录已有 page_*.md 或 Task1 已经有进度，跳过
        existing_state = load_state(checkpoint_dir, file_hash)
        already_has_new_files = new_dir_path.exists() and any(new_dir_path.glob("page_*.md"))
        task1_in_progress = existing_state is not None and (
            existing_state.current_block_idx > 0
            or existing_state.stage3_persisted_count > 0
            or is_state_complete(existing_state)
        )

        if (already_has_new_files or task1_in_progress) and not force:
            result.status = "skipped"
            reasons: list[str] = []
            if already_has_new_files:
                reasons.append("new recognize/ already populated")
            if task1_in_progress:
                reasons.append("Task1 has progress")
            result.error = "already migrated (" + "; ".join(reasons) + "); use --force to override"
            results.append(result)
            continue

        # 计算新 batch 元组
        new_batches: list[tuple[int, int, Path]] = []
        for ps, pe, mdp in legacy_mds:
            new_ps = ps + offset
            new_pe = pe + offset
            new_name = f"page_{new_ps:03d}-{new_pe:03d}.md"
            new_path = new_dir_path / new_name
            new_batches.append((new_ps, new_pe, new_path))

        if dry_run:
            result.status = "would_migrate"
            result.batches_migrated = len(new_batches)
            results.append(result)
            continue

        # 实际迁移
        try:
            for (_ps, _pe, src_md), (_new_ps, _new_pe, dst) in zip(
                legacy_mds, new_batches, strict=True
            ):
                shutil.copy2(str(src_md), str(dst))
                result.batches_migrated += 1
        except Exception as exc:
            result.status = "error"
            result.error = f"copy failed: {exc}"
            logger.error("migrate_copy_failed", hash=file_hash, error=str(exc))
            results.append(result)
            continue

        # 重建 state.json
        try:
            state = existing_state or FileExtractionState(
                file_path=str(matched),
                file_hash=file_hash,
            )
            state.file_path = str(matched)
            state.task0_completed = True
            state.vlm_batches_done = [(0, new_ps, new_pe) for new_ps, new_pe, _ in new_batches]
            state.failed_batches = []
            # Task1 从头开始（用户要求；幂等性已在上面挡掉有进度的情况）
            state.phase = "running"
            state.current_block_idx = 0
            state.line_offset = 0
            state.line_buffer_carry = []
            state.next_item_id = 0
            state.tag_tree_json = ""
            state.stage3_persisted_count = 0
            state.failed_blocks = []
            state.errors = []
            save_state(checkpoint_dir, state)
        except Exception as exc:
            result.status = "error"
            result.error = f"state save failed: {exc}"
            logger.error("migrate_state_save_failed", hash=file_hash, error=str(exc))
            results.append(result)
            continue

        # 清理 legacy 目录
        if delete_legacy:
            try:
                shutil.rmtree(legacy_dir)
            except Exception as exc:
                logger.warning(
                    "migrate_legacy_cleanup_failed",
                    dir=str(legacy_dir),
                    error=str(exc),
                )

        result.status = "migrated"
        logger.info(
            "migrate_done",
            hash=file_hash,
            file=matched.name,
            batches=result.batches_migrated,
            base_offset=offset,
        )
        results.append(result)

    return results
