"""根据 audit_report.json 清理需要重跑的文件。

执行三件事：
1. 从 DB 删除所有需重跑文件的 knowledge_points / questions 条目
2. 删除对应的 checkpoint/<file_hash>/ 目录
3. 从 directory_progress.json 的 completed_files 中移除该 entry

这样 run_pipeline.sh 再跑时会：
- 这些文件重新进入 work_items
- 没有旧 checkpoint → 从 task0 开始
- DB 里无残留 → 新 OrderIndexAllocator 从 DB max + 1 开始分配（保留文件的 order_index 不冲突）

用法：
    python scripts/reset_for_rerun.py --dry-run    # 预览
    python scripts/reset_for_rerun.py              # 真实执行
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "sciforge.db"
CHECKPOINT_ROOT = PROJECT_ROOT / "data" / "checkpoint"
AUDIT_REPORT = PROJECT_ROOT / "data" / "audit_report.json"
BACKUP_SUFFIX = ".before_reset.bak"


def load_audit() -> list[dict]:
    if not AUDIT_REPORT.exists():
        print(f"❌ 审计报告不存在：{AUDIT_REPORT}")
        print("   请先运行：python scripts/audit_quality.py")
        sys.exit(1)
    report = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    return [r for r in report["per_file"] if r["needs_rerun"]]


def load_directory_progress() -> tuple[Path, dict]:
    """找到 data/checkpoint/dir_*.json（可能多个）"""
    candidates = list(CHECKPOINT_ROOT.glob("dir_*.json"))
    if not candidates:
        print("⚠️  未找到 directory_progress 文件；将仅清理 DB 和 checkpoint/<hash>/")
        return Path(), {}
    # 一般只有一个（对应 "Raw Data" 目录的 dir_hash）
    progress_file = candidates[0]
    data = json.loads(progress_file.read_text(encoding="utf-8"))
    return progress_file, data


def find_file_hashes(
    rerun_files: list[dict], dir_progress: dict
) -> dict[str, str]:
    """source_file → file_hash 映射。优先从 dir_progress.completed_files 取。"""
    mapping: dict[str, str] = {}
    completed = dir_progress.get("completed_files", {})
    for r in rerun_files:
        src = r["source_file"]
        if src in completed:
            mapping[src] = completed[src]
    # 对 dir_progress 中没记录的，扫 checkpoint/<hash>/state.json 匹配 file_path
    if len(mapping) < len(rerun_files):
        unknown = [r["source_file"] for r in rerun_files if r["source_file"] not in mapping]
        for ck_dir in CHECKPOINT_ROOT.iterdir():
            if not ck_dir.is_dir():
                continue
            state_file = ck_dir / "state.json"
            if not state_file.exists():
                continue
            try:
                s = json.loads(state_file.read_text(encoding="utf-8"))
                fp = s.get("file_path", "")
                if fp in unknown:
                    mapping[fp] = ck_dir.name
            except (json.JSONDecodeError, OSError):
                continue
    return mapping


def delete_db_entries(
    conn: sqlite3.Connection, source_files: list[str], dry_run: bool
) -> dict[str, int]:
    """删除 knowledge_points + questions 中 source_file 匹配的条目"""
    removed: dict[str, int] = {"knowledge_points": 0, "questions": 0}
    placeholders = ",".join("?" for _ in source_files)

    for table in ("knowledge_points", "questions"):
        cur = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE source_file IN ({placeholders})",  # noqa: S608
            source_files,
        )
        count = cur.fetchone()[0]
        removed[table] = count
        if not dry_run and count > 0:
            conn.execute(
                f"DELETE FROM {table} WHERE source_file IN ({placeholders})",  # noqa: S608
                source_files,
            )
            # FTS5 mirror 表同步（如果存在）
            try:
                conn.execute(
                    f"DELETE FROM {table}_fts "
                    f"WHERE rowid IN (SELECT rowid FROM {table} WHERE source_file IN ({placeholders}))",  # noqa: S608
                    source_files,
                )
            except sqlite3.OperationalError:
                pass  # 无 fts 表
    return removed


def delete_checkpoint_dirs(
    file_hashes: dict[str, str], dry_run: bool
) -> tuple[int, int]:
    """删除 checkpoint/<file_hash>/ 目录"""
    deleted = 0
    missing = 0
    for src, h in file_hashes.items():
        ck_dir = CHECKPOINT_ROOT / h
        if not ck_dir.exists():
            missing += 1
            continue
        if not dry_run:
            shutil.rmtree(ck_dir)
        deleted += 1
    return deleted, missing


def update_directory_progress(
    progress_file: Path, dir_progress: dict, rerun_srcs: list[str], dry_run: bool
) -> int:
    """从 dir_progress.completed_files 移除 rerun 文件，加入 failed_files 以便 pipeline 感知"""
    if not progress_file.exists():
        return 0
    completed = dir_progress.setdefault("completed_files", {})
    failed = dir_progress.setdefault("failed_files", {})
    removed_count = 0
    for src in rerun_srcs:
        if src in completed:
            completed.pop(src)
            failed[src] = "reset_for_quality_rerun"
            removed_count += 1
    if not dry_run and removed_count > 0:
        backup = progress_file.with_suffix(progress_file.suffix + BACKUP_SUFFIX)
        if not backup.exists():
            shutil.copy(progress_file, backup)
        progress_file.write_text(
            json.dumps(dir_progress, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return removed_count


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="只打印改动不执行")
    p.add_argument(
        "--purge-all",
        action="store_true",
        help="危险：清空所有 DB 条目 + 全部 checkpoint（忽略 audit）",
    )
    args = p.parse_args()

    dry = args.dry_run
    prefix = "[DRY-RUN] " if dry else ""

    print(f"\n{prefix}=== reset_for_rerun ===\n")
    print(f"DB: {DB_PATH}")
    print(f"Checkpoint root: {CHECKPOINT_ROOT}")

    if args.purge_all:
        print("\n⚠️  --purge-all 模式：将清空所有数据")
        if not dry:
            resp = input("确认清空？输入 'YES' 继续：")
            if resp != "YES":
                print("已取消")
                return
        # 删 DB items
        conn = sqlite3.connect(DB_PATH)
        kp = conn.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()[0]
        q = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        if not dry:
            conn.execute("DELETE FROM knowledge_points")
            conn.execute("DELETE FROM questions")
            for fts in ("knowledge_points_fts", "questions_fts"):
                try:
                    conn.execute(f"DELETE FROM {fts}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()
        conn.close()
        # 删所有 checkpoint（保留 taxonomy.json 等）
        ck_dirs = [d for d in CHECKPOINT_ROOT.iterdir() if d.is_dir()]
        ck_files = list(CHECKPOINT_ROOT.glob("dir_*.json"))
        if not dry:
            for d in ck_dirs:
                shutil.rmtree(d)
            for f in ck_files:
                f.unlink()
        print(f"{prefix}DB 删除: {kp} knowledge_points + {q} questions")
        print(f"{prefix}Checkpoint 删除: {len(ck_dirs)} file checkpoints + {len(ck_files)} dir progress files")
        return

    # 正常模式
    rerun_files = load_audit()
    if not rerun_files:
        print("✅ 审计报告无需重跑的文件")
        return

    print(f"\n需要重跑的文件：{len(rerun_files)} 个")

    progress_file, dir_progress = load_directory_progress()
    print(f"\n发现 directory_progress: {progress_file}")
    print(f"  completed_files 数量: {len(dir_progress.get('completed_files', {}))}")

    rerun_srcs = [r["source_file"] for r in rerun_files]
    file_hashes = find_file_hashes(rerun_files, dir_progress)

    print(f"\n可解析 file_hash 的文件: {len(file_hashes)} / {len(rerun_srcs)}")
    missing_hash = [s for s in rerun_srcs if s not in file_hashes]
    if missing_hash:
        print(f"⚠️  {len(missing_hash)} 个文件未找到 file_hash（checkpoint 可能早已删除）：")
        for s in missing_hash[:5]:
            print(f"    - {s}")

    # 1. DB
    print(f"\n{prefix}1. 清理 DB items...")
    conn = sqlite3.connect(DB_PATH)
    removed = delete_db_entries(conn, rerun_srcs, dry_run=dry)
    if not dry:
        conn.commit()
    conn.close()
    print(f"   knowledge_points: {removed['knowledge_points']} 条")
    print(f"   questions: {removed['questions']} 条")

    # 2. Checkpoint
    print(f"\n{prefix}2. 删除 checkpoint 目录...")
    deleted, missing = delete_checkpoint_dirs(file_hashes, dry_run=dry)
    print(f"   已删除: {deleted} 个 checkpoint 目录")
    if missing:
        print(f"   缺失: {missing} 个 file_hash 的目录已不存在（OK）")

    # 3. Directory progress
    print(f"\n{prefix}3. 更新 directory_progress...")
    removed_entries = update_directory_progress(
        progress_file, dir_progress, rerun_srcs, dry_run=dry
    )
    print(f"   从 completed_files 移除: {removed_entries} 个 entry")
    if not dry and removed_entries > 0:
        print(f"   已备份原文件到 {progress_file.name}{BACKUP_SUFFIX}")

    print(f"\n{prefix}✅ 完成")
    if dry:
        print("\n（dry-run 模式，未实际修改。确认后去掉 --dry-run 执行。）")
    else:
        print("\n现在运行 bash run_pipeline.sh 重新跑需重跑的文件：")
        print("  bash run_pipeline.sh")
        print("\n保留的 1 个文件（无需重跑）在 DB 中保留，OrderIndexAllocator 会从 DB max+1 开始分配。")


if __name__ == "__main__":
    main()
