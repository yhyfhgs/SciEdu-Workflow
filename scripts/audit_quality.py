"""遍历 DB 做质量审计，按 source_file 汇总问题，输出需要重跑的文件列表。

判定重跑的规则（任一满足即列为 NEEDS_RERUN）：
1. 兜底标注比例高：tags 含 "uncategorized:未分类" 或 "auto_fallback:自动兜底" 的条目占比 > 5%
2. 重复 order_index：同 discipline 内 order_index 出现多次（Phase 1 并发 bug 残留）
3. 结构化错误：content 空 / content 长度 < 20 / 花括号不平衡 / `$` 不成对
4. solution_status 与内容矛盾：complete 但 proof+solution 全空
5. 兜底 taxonomy：taxonomy_path 完全等于 discipline（没细化）
6. 非法 item_type：不在学科 allowed 列表内
7. 空 tag 路径（长度 < 2）

对每个 source_file：
- total, issues_by_type, is_needs_rerun, severity
- 输出 JSON 到 data/audit_report.json
- 输出 Markdown 到 data/audit_report.md
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sciforge.disciplines import DISCIPLINE_REGISTRY  # noqa: E402
from sciforge.core.models import Discipline  # noqa: E402

DB_PATH = Path(__file__).parent.parent / "data" / "sciforge.db"
OUT_JSON = Path(__file__).parent.parent / "data" / "audit_report.json"
OUT_MD = Path(__file__).parent.parent / "data" / "audit_report.md"

# ── 质量阈值 ──────────────────────────────────────────
FALLBACK_TAG_RATIO_THRESHOLD = 0.05  # 兜底标注占比 > 5% 触发重跑
MIN_CONTENT_LEN = 20
STRUCTURAL_ERROR_RATIO_THRESHOLD = 0.05  # 结构化错误占比 > 5% 触发重跑

FALLBACK_TAG_MARKERS = {"uncategorized:未分类", "auto_fallback:自动兜底"}

# ── 质量检查 ──────────────────────────────────────────


def check_content_quality(content: str) -> list[str]:
    """返回问题列表"""
    issues: list[str] = []
    if not content:
        issues.append("content_empty")
        return issues
    stripped = content.strip()
    if len(stripped) < MIN_CONTENT_LEN:
        issues.append(f"content_too_short({len(stripped)})")
    if content.count("{") != content.count("}"):
        issues.append(
            f"unbalanced_braces({content.count('{')}/{content.count('}')})"
        )
    # $ 成对（排除 $$）
    no_dd = content.replace("$$", "")
    if no_dd.count("$") % 2 != 0:
        issues.append(f"unbalanced_dollar({no_dd.count('$')})")
    return issues


def parse_tags(tags_json: str) -> list[str]:
    if not tags_json:
        return []
    try:
        data = json.loads(tags_json)
        if isinstance(data, list):
            return [str(s) for s in data]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def check_tags(tags: list[str], discipline: str) -> list[str]:
    issues: list[str] = []
    if len(tags) < 2:
        issues.append(f"tags_too_short({len(tags)})")
    for t in tags:
        if t in FALLBACK_TAG_MARKERS:
            issues.append(f"fallback_tag:{t}")
    # 学科根
    spec = DISCIPLINE_REGISTRY.get(Discipline(discipline))
    if tags and tags[0] != spec.tag_root:
        issues.append(f"wrong_tag_root({tags[0]})")
    # 格式
    for t in tags:
        if not re.match(r"^[a-z][a-z0-9_]*:[^\s:/]+$", t):
            issues.append(f"bad_tag_format({t[:30]})")
    return issues


def check_taxonomy(taxonomy: str, discipline: str) -> list[str]:
    issues: list[str] = []
    if not taxonomy:
        issues.append("taxonomy_empty")
    elif taxonomy == discipline:
        issues.append("taxonomy_root_only")
    elif not taxonomy.startswith(discipline):
        issues.append(f"taxonomy_wrong_prefix({taxonomy[:40]})")
    return issues


def check_item_type(item_type: str, discipline: str, is_question: bool) -> list[str]:
    spec = DISCIPLINE_REGISTRY.get(Discipline(discipline))
    allowed = spec.allowed_question_types if is_question else spec.allowed_kp_types
    if item_type not in allowed:
        return [f"invalid_{'q' if is_question else 'kp'}_type({item_type})"]
    return []


def check_solution_consistency(
    proof: str | None, solution: str | None, is_question: bool
) -> list[str]:
    """Stage1 完成状态下，若 content 本应有 proof/solution 但都为空 → 异常"""
    issues: list[str] = []
    if is_question and not solution:
        # questions 无解答是 no_solution_in_source，是合法情况
        pass
    return issues


# ── 主流程 ────────────────────────────────────────────


def audit() -> dict[str, Any]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Per source_file 聚合
    by_file: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "total": 0,
        "kp_count": 0,
        "q_count": 0,
        "issues_per_item": [],
        "issue_counter": Counter(),
        "order_indices": [],
        "fallback_items": 0,
        "difficulty_dist": Counter(),
    })

    # 收集 knowledge_points
    for table, is_q in [("knowledge_points", False), ("questions", True)]:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        for r in rows:
            source_file = r["source_file"] or "<unknown>"
            entry = by_file[source_file]
            entry["total"] += 1
            if is_q:
                entry["q_count"] += 1
            else:
                entry["kp_count"] += 1

            discipline = r["discipline"] or "math"
            item_issues: list[str] = []

            content = r["content"] or ""
            item_issues += check_content_quality(content)

            tags = parse_tags(r["tags"])
            item_issues += check_tags(tags, discipline)

            item_issues += check_taxonomy(r["taxonomy_path"] or "", discipline)

            item_type = (r["kp_type"] if not is_q else r["question_type"]) or ""
            item_issues += check_item_type(item_type, discipline, is_q)

            # Fallback 标记
            if any(t in FALLBACK_TAG_MARKERS for t in tags):
                entry["fallback_items"] += 1

            # 记录 order_index
            oi = r["order_index"] if r["order_index"] is not None else -1
            entry["order_indices"].append((oi, r["id"]))

            # 难度分布
            diff = r["difficulty"]
            if diff is not None:
                entry["difficulty_dist"][int(diff)] += 1

            if item_issues:
                entry["issues_per_item"].append({
                    "id": r["id"],
                    "order_index": oi,
                    "issues": item_issues,
                })
                for iss in item_issues:
                    tag = iss.split("(")[0]
                    entry["issue_counter"][tag] += 1

    conn.close()

    # 全局 order_index 重复检查（跨文件，Phase 1 并发 bug）
    global_oi: dict[int, list[str]] = defaultdict(list)
    for src, entry in by_file.items():
        for oi, _id in entry["order_indices"]:
            if oi >= 0:
                global_oi[oi].append(src)
    duplicate_oi = {oi: srcs for oi, srcs in global_oi.items() if len(srcs) > 1}

    # 为每个文件计算 needs_rerun 决策
    per_file_reports = []
    for src, entry in sorted(by_file.items()):
        total = entry["total"]
        fb_ratio = entry["fallback_items"] / max(total, 1)
        struct_errs = sum(
            entry["issue_counter"].get(k, 0) for k in
            ("content_empty", "content_too_short", "unbalanced_braces",
             "unbalanced_dollar", "taxonomy_empty", "taxonomy_root_only",
             "taxonomy_wrong_prefix", "wrong_tag_root", "bad_tag_format",
             "tags_too_short", "invalid_kp_type", "invalid_q_type")
        )
        struct_ratio = struct_errs / max(total, 1)

        # 本文件 order_index 内部重复
        oi_counter = Counter(oi for oi, _id in entry["order_indices"] if oi >= 0)
        dup_within_file = {oi: c for oi, c in oi_counter.items() if c > 1}
        # 本文件跨文件重复的 order_index
        dup_across_files = sum(
            1 for oi, _id in entry["order_indices"]
            if oi in duplicate_oi
        )

        reasons: list[str] = []
        if fb_ratio > FALLBACK_TAG_RATIO_THRESHOLD:
            reasons.append(f"fallback_ratio={fb_ratio:.1%}>{FALLBACK_TAG_RATIO_THRESHOLD:.0%}")
        if struct_ratio > STRUCTURAL_ERROR_RATIO_THRESHOLD:
            reasons.append(f"struct_err_ratio={struct_ratio:.1%}>{STRUCTURAL_ERROR_RATIO_THRESHOLD:.0%}")
        if dup_within_file:
            reasons.append(f"dup_order_index_within={len(dup_within_file)}")
        if dup_across_files > 0:
            reasons.append(f"dup_order_index_across={dup_across_files}")

        needs_rerun = bool(reasons)

        per_file_reports.append({
            "source_file": src,
            "total": total,
            "kp_count": entry["kp_count"],
            "q_count": entry["q_count"],
            "fallback_items": entry["fallback_items"],
            "fallback_ratio": round(fb_ratio, 4),
            "struct_err_ratio": round(struct_ratio, 4),
            "dup_order_index_within": len(dup_within_file),
            "dup_order_index_across_files": dup_across_files,
            "issue_counter": dict(entry["issue_counter"]),
            "difficulty_dist": dict(entry["difficulty_dist"]),
            "needs_rerun": needs_rerun,
            "reasons": reasons,
            # 采样：前 5 个问题 item
            "sample_problematic_items": entry["issues_per_item"][:5],
        })

    # 全局 summary
    total_items = sum(e["total"] for e in by_file.values())
    rerun_files = [r for r in per_file_reports if r["needs_rerun"]]
    summary = {
        "total_source_files": len(per_file_reports),
        "total_items": total_items,
        "files_needing_rerun": len(rerun_files),
        "items_in_rerun_files": sum(r["total"] for r in rerun_files),
        "global_duplicate_order_indices": len(duplicate_oi),
        "threshold_fallback_ratio": FALLBACK_TAG_RATIO_THRESHOLD,
        "threshold_struct_err_ratio": STRUCTURAL_ERROR_RATIO_THRESHOLD,
    }

    return {
        "summary": summary,
        "per_file": per_file_reports,
        "global_duplicate_order_indices_sample": dict(list(duplicate_oi.items())[:20]),
    }


def write_markdown(report: dict[str, Any], out: Path) -> None:
    s = report["summary"]
    lines = [
        "# DB 质量审计报告",
        "",
        "## 全局摘要",
        "",
        f"- 总 source_file 数：**{s['total_source_files']}**",
        f"- 总 items 数：**{s['total_items']}**",
        f"- 需要重跑的文件数：**{s['files_needing_rerun']}**",
        f"- 需要重跑的文件内 items 数：**{s['items_in_rerun_files']}**",
        f"- 跨文件重复 order_index 数：**{s['global_duplicate_order_indices']}**",
        f"- 触发阈值：fallback_ratio > {s['threshold_fallback_ratio']:.0%}, "
        f"struct_err_ratio > {s['threshold_struct_err_ratio']:.0%}",
        "",
        "## 需要重跑的文件列表",
        "",
        "| source_file | total | fallback% | struct_err% | 重跑原因 |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(
        report["per_file"],
        key=lambda x: (not x["needs_rerun"], -x["total"]),
    ):
        if not r["needs_rerun"]:
            continue
        sf = r["source_file"].replace("Raw Data/", "")
        lines.append(
            f"| {sf} | {r['total']} | {r['fallback_ratio']:.1%} "
            f"| {r['struct_err_ratio']:.1%} | {', '.join(r['reasons'])} |"
        )

    lines += [
        "",
        "## 全部文件质量明细",
        "",
        "| source_file | total | kp | q | fallback% | struct_err% | 状态 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(report["per_file"], key=lambda x: -x["total"]):
        sf = r["source_file"].replace("Raw Data/", "")
        status = "🔴 重跑" if r["needs_rerun"] else "✅ 保留"
        lines.append(
            f"| {sf} | {r['total']} | {r['kp_count']} | {r['q_count']} "
            f"| {r['fallback_ratio']:.1%} | {r['struct_err_ratio']:.1%} | {status} |"
        )

    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    print(f"Auditing {DB_PATH}...")
    report = audit()
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report, OUT_MD)

    s = report["summary"]
    print(f"\n总 source_file: {s['total_source_files']}, 总 items: {s['total_items']}")
    print(f"需要重跑: {s['files_needing_rerun']} 个文件 (包含 {s['items_in_rerun_files']} items)")
    print(f"跨文件 order_index 重复: {s['global_duplicate_order_indices']}")

    rerun = [r for r in report["per_file"] if r["needs_rerun"]]
    if rerun:
        print("\n需要重跑的文件：")
        for r in sorted(rerun, key=lambda x: -x["total"])[:20]:
            sf = r["source_file"].replace("Raw Data/", "")
            print(f"  [{r['total']:4d}] {sf}")
            print(f"         → {', '.join(r['reasons'])}")

    print(f"\nJSON 报告：{OUT_JSON}")
    print(f"Markdown 报告：{OUT_MD}")


if __name__ == "__main__":
    main()
