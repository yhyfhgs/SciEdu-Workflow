"""文档级 reconciliation 复核（#7）

入库后对全量条目做**确定性**质量复核，产出报告（默认不改库，零并行风险）：

1. **重复条目**：同 ``(subject, seq_chapter, seq_section, number_canonical)`` 下出现多条
   （精确 content_hash 去重无法捕获的"近重复"——如同一定理被两窗口各提一次、OCR 细微差异）。
2. **类型误判**：条目正文**首标记**（定义/定理/引理/推论/例/习题…）与存储的
   ``category`` / ``kp_type`` 不一致（如正文"定义"开头却存成 theorem，或"习题"被当成 knowledge）。

纯规则、可测、不调用 LLM。报告写 ``reconciliation_report.json``，供人工抽查/后续修正。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

# 正文首标记 → (期望 category, 期望 kp_type|None)
# kp_type=None 表示只约束 category（如习题类不细分 kp_type）
_MARKER_RULES: list[tuple[re.Pattern[str], str, str | None]] = [
    (re.compile(r"^\**\s*(?:定义|Definition)\b", re.I), "knowledge", "definition"),
    (re.compile(r"^\**\s*(?:定理|Theorem)\b", re.I), "knowledge", "theorem"),
    (re.compile(r"^\**\s*(?:引理|Lemma)\b", re.I), "knowledge", "lemma"),
    (re.compile(r"^\**\s*(?:推论|Corollary)\b", re.I), "knowledge", "corollary"),
    (re.compile(r"^\**\s*(?:命题|Proposition)\b", re.I), "knowledge", "proposition"),
    (re.compile(r"^\**\s*(?:性质|Property)\b", re.I), "knowledge", "property"),
    (re.compile(r"^\**\s*(?:公式|Formula)\b", re.I), "knowledge", "formula"),
    (re.compile(r"^\**\s*(?:例题?|Example)\b", re.I), "question", None),
    (re.compile(r"^\**\s*(?:习题|问题|练习|Exercise|Problem)\b", re.I), "question", None),
]


def _expected_from_content(content_head: str) -> tuple[str, str | None] | None:
    """从正文首标记推断 (期望 category, 期望 kp_type)。无法识别返回 None。"""
    head = content_head.lstrip().lstrip("*# 　")
    for pat, cat, kp in _MARKER_RULES:
        if pat.match(head):
            return cat, kp
    return None


def reconcile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """对全量条目做复核。

    Args:
        rows: 每项含 ``id`` / ``kind`` ("kp"|"question") / ``type`` (kp_type|question_type) /
            ``number_canonical`` / ``subject`` / ``seq_chapter`` / ``seq_section`` /
            ``content_head`` (正文前若干字)。

    Returns:
        ``{"duplicate_groups": [...], "type_mismatches": [...], "summary": {...}}``
    """
    # ── 1. 重复条目（同 subject+章+节+规范题号 出现多条）──
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        nc = (r.get("number_canonical") or "").strip()
        if not nc:
            continue  # 无规范题号不参与重复判定（避免误报）
        key = (r.get("subject") or "", r.get("seq_chapter"), r.get("seq_section"), nc)
        groups[key].append(r)

    duplicate_groups = [
        {
            "subject": k[0],
            "chapter": k[1],
            "section": k[2],
            "number_canonical": k[3],
            "ids": [it["id"] for it in items],
            "count": len(items),
        }
        for k, items in sorted(groups.items(), key=lambda kv: str(kv[0]))
        if len(items) > 1
    ]

    # ── 2. 类型误判（首标记 vs 存储 category/kp_type）──
    type_mismatches: list[dict[str, Any]] = []
    for r in rows:
        exp = _expected_from_content(r.get("content_head") or "")
        if exp is None:
            continue
        exp_cat, exp_kp = exp
        actual_cat = "knowledge" if r.get("kind") == "kp" else "question"
        if exp_cat != actual_cat:
            type_mismatches.append(
                {
                    "id": r["id"],
                    "issue": "category_mismatch",
                    "expected": exp_cat,
                    "actual": actual_cat,
                    "content_head": (r.get("content_head") or "")[:40],
                }
            )
        elif exp_cat == "knowledge" and exp_kp and r.get("type") != exp_kp:
            type_mismatches.append(
                {
                    "id": r["id"],
                    "issue": "kp_type_mismatch",
                    "expected": exp_kp,
                    "actual": r.get("type"),
                    "content_head": (r.get("content_head") or "")[:40],
                }
            )

    return {
        "duplicate_groups": duplicate_groups,
        "type_mismatches": type_mismatches,
        "summary": {
            "total_items": len(rows),
            "duplicate_groups": len(duplicate_groups),
            "duplicate_items": sum(g["count"] for g in duplicate_groups),
            "type_mismatches": len(type_mismatches),
        },
    }
