"""结构化关系推断（#6 知识图谱）

从已入库条目的结构信息（kind/type/subject/seq_*/order_index）确定性地推断
条目间的关系边，写入 ``item_relations`` 表。**不调用 LLM**，纯规则、可测、幂等
（UNIQUE(src,dst,type) + INSERT OR IGNORE 去重）。

推断规则（均在同一 ``(subject, seq_chapter, seq_section)`` 分组内、按 order_index 排序）：

- **corollary_of**：``kp_type=corollary`` 的知识点 → 最近的前置 theorem/proposition/lemma
- **tests**：题目（question）→ 最近的前置知识点（该题考查的概念）
- **successor**：同节内相邻两个知识点 A→B（阅读/教学顺序骨架，低置信，供导航）

跨度保护：仅当候选与当前条目的 order_index 间隔 ≤ ``_MAX_GAP`` 时才连边，
避免无编号（seq 全 None）时把同 subject 大桶里相距很远的条目误连。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from sciforge.core.models import RelationType

# theorem 类知识点（corollary 的连接目标）
_THEOREM_LIKE = frozenset({"theorem", "proposition", "lemma"})

# order_index 跨度保护：超过则不连边（防无编号时跨主题误连）
_MAX_GAP_COROLLARY = 5
_MAX_GAP_TESTS = 10
_MAX_GAP_SUCCESSOR = 3


def _edge(
    src_id: str,
    dst_id: str,
    rel: RelationType,
    confidence: float,
    note: str,
) -> dict[str, Any]:
    return {
        "src_id": src_id,
        "dst_id": dst_id,
        "relation_type": rel.value,
        "confidence": confidence,
        "source": "structural",
        "note": note,
    }


def _nearest_preceding(
    items: list[dict[str, Any]],
    idx: int,
    pred: Callable[[dict[str, Any]], bool],
    max_gap: int,
) -> dict[str, Any] | None:
    """从 idx-1 往前找第一个满足 pred 的条目，且 order_index 跨度 ≤ max_gap。"""
    cur_oi = items[idx]["order_index"]
    for j in range(idx - 1, -1, -1):
        cand = items[j]
        if cur_oi - cand["order_index"] > max_gap:
            break
        if pred(cand):
            return cand
    return None


def infer_structural_relations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从结构行推断关系边。

    Args:
        rows: 每项含 ``id`` / ``kind`` ("kp"|"question") / ``type`` (kp_type|question_type) /
            ``subject`` / ``seq_chapter`` / ``seq_section`` / ``order_index``。

    Returns:
        边 dict 列表：``src_id`` / ``dst_id`` / ``relation_type`` / ``confidence`` /
        ``source`` / ``note``。已按 (src,dst,type) 去重。
    """
    groups: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = (r.get("subject") or "", r.get("seq_chapter"), r.get("seq_section"))
        groups[key].append(r)

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(e: dict[str, Any]) -> None:
        if e["src_id"] == e["dst_id"]:
            return
        k = (e["src_id"], e["dst_id"], e["relation_type"])
        if k in seen:
            return
        seen.add(k)
        edges.append(e)

    for items in groups.values():
        items.sort(key=lambda x: x["order_index"])
        prev_kp_idx: int | None = None  # 同节上一个知识点的位置（successor 骨架用）

        for i, it in enumerate(items):
            is_kp = it["kind"] == "kp"

            if is_kp and it["type"] == "corollary":
                dst = _nearest_preceding(
                    items, i,
                    lambda x: x["kind"] == "kp" and x["type"] in _THEOREM_LIKE,
                    _MAX_GAP_COROLLARY,
                )
                if dst:
                    _add(_edge(it["id"], dst["id"], RelationType.COROLLARY_OF, 0.9,
                               "corollary 紧跟定理类知识点"))

            if it["kind"] == "question":
                dst = _nearest_preceding(
                    items, i, lambda x: x["kind"] == "kp", _MAX_GAP_TESTS
                )
                if dst:
                    _add(_edge(it["id"], dst["id"], RelationType.TESTS, 0.6,
                               "题目考查前置知识点"))

            if is_kp:
                if (
                    prev_kp_idx is not None
                    and it["order_index"] - items[prev_kp_idx]["order_index"]
                    <= _MAX_GAP_SUCCESSOR
                ):
                    _add(_edge(items[prev_kp_idx]["id"], it["id"], RelationType.SUCCESSOR,
                               0.5, "同节相邻知识点（阅读顺序）"))
                prev_kp_idx = i

    return edges
