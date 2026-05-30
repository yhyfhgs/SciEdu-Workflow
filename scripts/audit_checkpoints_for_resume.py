"""扫描所有 checkpoint/<file_hash>/state.json，找出：

1. phase='completed' 但 failed_blocks / failed_batches 非空 → 被旧代码错判为完成
2. stage3_completed=True 但 failed_blocks 非空 → 旧 is_state_complete 会漏判

对这些文件：
- 把 phase 降级为 'running'（不强制清 failed_blocks，保留记录）
- 从 dir_progress.completed_files 移除 → 下次 pipeline 会重新处理
- 可选清理 failed_blocks 让重跑全量（--clean-failed-blocks）

默认 dry-run，加 --apply 真实修改。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
CHECKPOINT_ROOT = PROJECT_ROOT / "data" / "checkpoint"


def scan() -> list[dict]:
    results: list[dict] = []
    for sd in sorted(CHECKPOINT_ROOT.iterdir()):
        if not sd.is_dir():
            continue
        state_f = sd / "state.json"
        if not state_f.exists():
            continue
        try:
            s = json.loads(state_f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        fb = s.get("failed_blocks", [])
        fbatch = s.get("failed_batches", [])
        phase = s.get("phase")
        stage3 = s.get("stage3_completed")
        stage1 = s.get("stage1_completed")
        has_failures = bool(fb or fbatch)
        if has_failures:
            results.append({
                "hash": sd.name,
                "file_path": s.get("file_path", ""),
                "phase": phase,
                "stage1_completed": stage1,
                "stage3_completed": stage3,
                "failed_blocks": fb,
                "failed_batches": fbatch,
                "stage3_persisted_count": s.get("stage3_persisted_count", 0),
                "errors": s.get("errors", [])[:3],
            })
    return results


def apply_reset(affected: list[dict], clean_failed: bool) -> int:
    """把 affected 文件从 dir_progress 移除，降 phase"""
    count = 0
    # 1. 改 state.json
    for a in affected:
        state_f = CHECKPOINT_ROOT / a["hash"] / "state.json"
        if not state_f.exists():
            continue
        s = json.loads(state_f.read_text(encoding="utf-8"))
        s["phase"] = "running"
        if clean_failed:
            s["failed_blocks"] = []
            s["failed_batches"] = []
        state_f.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        count += 1

    # 2. 从 dir_progress.completed_files 移除
    for dp_file in CHECKPOINT_ROOT.glob("dir_*.json"):
        if dp_file.name.endswith(".bak"):
            continue
        dp = json.loads(dp_file.read_text(encoding="utf-8"))
        completed = dp.get("completed_files", {})
        failed = dp.setdefault("failed_files", {})
        removed = 0
        for a in affected:
            src = a["file_path"]
            if src in completed:
                completed.pop(src)
                failed[src] = "rate_limit_budget_or_quota_failure"
                removed += 1
        if removed > 0:
            backup = dp_file.with_suffix(dp_file.suffix + ".before_429_fix.bak")
            if not backup.exists():
                shutil.copy(dp_file, backup)
            dp_file.write_text(json.dumps(dp, ensure_ascii=False, indent=2), encoding="utf-8")
    return count


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="真实执行修改（默认 dry-run）")
    p.add_argument(
        "--clean-failed-blocks",
        action="store_true",
        help="额外：清空 failed_blocks/batches 让重跑从头检查",
    )
    args = p.parse_args()

    affected = scan()
    print(f"\nCheckpoint 扫描：{len(list(CHECKPOINT_ROOT.glob('*/')))} 个")
    print(f"发现有 failed_blocks/batches 的文件：{len(affected)} 个\n")

    if not affected:
        print("✅ 所有 checkpoint 都干净，无需处理")
        return

    for a in affected:
        print(f"  [{a['hash']}]")
        print(f"    file: {a['file_path']}")
        print(f"    phase: {a['phase']}  stage1: {a['stage1_completed']}  stage3: {a['stage3_completed']}")
        print(f"    failed_blocks: {len(a['failed_blocks'])}  failed_batches: {len(a['failed_batches'])}")
        print(f"    stage3_persisted_count: {a['stage3_persisted_count']}")
        if a["errors"]:
            print(f"    errors (前 3): {a['errors']}")

    if not args.apply:
        print(f"\n[dry-run] 会把 {len(affected)} 个 checkpoint 的 phase 降为 'running'")
        print("          并从 directory_progress.completed_files 中移除它们")
        if args.clean_failed_blocks:
            print("          且清空 failed_blocks/batches（从头重跑）")
        print("\n用 --apply 真正执行")
        sys.exit(0)

    count = apply_reset(affected, clean_failed=args.clean_failed_blocks)
    print(f"\n✅ 已重置 {count} 个 checkpoint 等待 pipeline 重跑")


if __name__ == "__main__":
    main()
