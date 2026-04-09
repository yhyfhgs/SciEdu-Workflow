"""GraphBuilder — 知识图谱构建器

基于 DocumentTree 的树结构：
Phase 1: 树内结构关系（无 LLM）
Phase 2: 跨分支 LLM 推断（tree → graph）
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog

from mathforge.core.models import (
    DocumentTree,
    ItemRelation,
    RelationType,
    TaxonomyNode,
    UnifiedItemBase,
)

if TYPE_CHECKING:
    from mathforge.llm.client import LLMClient

logger = structlog.get_logger()


class GraphBuilder:
    """知识图谱构建器 — 将文档结构树扩展为带跨分支关系的图"""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm = llm_client

    async def build_relations(
        self,
        tree: DocumentTree,
        items: list[UnifiedItemBase],
        batch_size: int = 10,
    ) -> list[UnifiedItemBase]:
        """构建完整的知识图谱关系。

        Phase 1: 树内结构关系（无 LLM）
        Phase 2: 跨分支 LLM 推断
        """
        id_to_item = {item.item_id: item for item in items}

        # Phase 1: 结构关系
        t0 = time.monotonic()
        structural_count = self._build_structural_relations(tree, id_to_item)
        logger.info(
            "graph_structural_relations",
            count=structural_count,
            elapsed_sec=round(time.monotonic() - t0, 2),
        )

        # Phase 2: 跨分支 LLM 推断
        if self.llm and len(items) > 1:
            t1 = time.monotonic()
            cross_count = await self._build_cross_branch_relations(
                tree, id_to_item, batch_size
            )
            logger.info(
                "graph_cross_branch_relations",
                count=cross_count,
                elapsed_sec=round(time.monotonic() - t1, 2),
            )

        # 去重和验证
        self._validate(items, id_to_item)

        return items

    def build_relations_sync(
        self,
        tree: DocumentTree,
        items: list[UnifiedItemBase],
    ) -> list[UnifiedItemBase]:
        """同步版本（仅 Phase 1 结构关系）"""
        id_to_item = {item.item_id: item for item in items}
        self._build_structural_relations(tree, id_to_item)
        self._validate(items, id_to_item)
        return items

    # ── Phase 1: 结构关系 ──

    def _build_structural_relations(
        self,
        tree: DocumentTree,
        id_to_item: dict[str, UnifiedItemBase],
    ) -> int:
        """基于树结构推断同分支内的关系（无 LLM）"""
        count = 0

        for node in tree.nodes.values():
            if not node.item_ids:
                continue

            node_items = [
                id_to_item[iid] for iid in node.item_ids if iid in id_to_item
            ]
            if len(node_items) < 2:
                continue

            for i, item in enumerate(node_items):
                # 相邻知识点 → RELATED
                if i > 0 and item.item_category == "knowledge":
                    prev = node_items[i - 1]
                    if prev.item_category == "knowledge":
                        self._add_relation(
                            item, prev.item_id, prev.title,
                            RelationType.RELATED, 0.7, "structural",
                        )
                        count += 1

                # Example → 最近的 KP → ILLUSTRATES
                if item.item_category == "example":
                    for j in range(i - 1, max(i - 5, -1), -1):
                        candidate = node_items[j]
                        if candidate.item_category == "knowledge":
                            self._add_relation(
                                item, candidate.item_id, candidate.title,
                                RelationType.ILLUSTRATES, 0.8, "structural",
                            )
                            count += 1
                            break

                # Exercise → 同 section KP → TESTS
                if item.item_category == "question":
                    for j in range(i - 1, max(i - 8, -1), -1):
                        candidate = node_items[j]
                        if candidate.item_category == "knowledge":
                            self._add_relation(
                                item, candidate.item_id, candidate.title,
                                RelationType.TESTS, 0.7, "structural",
                            )
                            count += 1
                            break

                # Corollary → Theorem → COROLLARY_OF
                if hasattr(item, "kp_type") and str(getattr(item, "kp_type", "")) == "corollary":
                    for j in range(i - 1, max(i - 3, -1), -1):
                        candidate = node_items[j]
                        if (
                            hasattr(candidate, "kp_type")
                            and str(getattr(candidate, "kp_type", ""))
                            in ("theorem", "lemma", "proposition")
                        ):
                            self._add_relation(
                                item, candidate.item_id, candidate.title,
                                RelationType.COROLLARY_OF, 0.9, "structural",
                            )
                            count += 1
                            break

        return count

    # ── Phase 2: 跨分支 LLM ──

    async def _build_cross_branch_relations(
        self,
        tree: DocumentTree,
        id_to_item: dict[str, UnifiedItemBase],
        batch_size: int,
    ) -> int:
        """跨分支 LLM 推断关系"""
        from mathforge.prompts.templates import CROSS_BRANCH_RELATIONS_PROMPT

        # 收集每个 chapter/section 的 items
        branches: list[tuple[str, list[str]]] = []
        for node in tree.nodes.values():
            if node.level in ("chapter", "section") and node.item_ids:
                path = self._get_branch_path(tree, node.node_id)
                branches.append((path, node.item_ids))

        if len(branches) < 2:
            return 0

        count = 0
        for i in range(len(branches)):
            for j in range(i + 1, len(branches)):
                path_a, items_a_ids = branches[i]
                path_b, items_b_ids = branches[j]

                # 只比较有实际内容的分支
                items_a = [id_to_item[iid] for iid in items_a_ids if iid in id_to_item]
                items_b = [id_to_item[iid] for iid in items_b_ids if iid in id_to_item]

                if not items_a or not items_b:
                    continue

                # 限制每分支最多 batch_size 个 items
                items_a = items_a[:batch_size]
                items_b = items_b[:batch_size]

                try:
                    relations = await self._infer_cross_branch(
                        path_a, items_a, path_b, items_b
                    )
                    for rel in relations:
                        src_id = rel.get("source_id", "")
                        tgt_id = rel.get("target_id", "")
                        if src_id in id_to_item and tgt_id in id_to_item:
                            src_item = id_to_item[src_id]
                            tgt_item = id_to_item[tgt_id]
                            try:
                                rtype = RelationType(rel.get("relation_type", "related"))
                            except ValueError:
                                rtype = RelationType.RELATED
                            self._add_relation(
                                src_item, tgt_id, tgt_item.title,
                                rtype,
                                rel.get("confidence", 0.7),
                                "llm",
                                rel.get("note", ""),
                            )
                            count += 1
                except Exception:
                    logger.warning(
                        "cross_branch_infer_failed",
                        branch_a=path_a[:50],
                        branch_b=path_b[:50],
                        exc_info=True,
                    )

        return count

    async def _infer_cross_branch(
        self,
        path_a: str,
        items_a: list[UnifiedItemBase],
        path_b: str,
        items_b: list[UnifiedItemBase],
    ) -> list[dict[str, Any]]:
        """调用 LLM 推断两个分支间的关系"""
        from mathforge.prompts.templates import CROSS_BRANCH_RELATIONS_PROMPT

        assert self.llm is not None

        def fmt(items: list[UnifiedItemBase]) -> str:
            parts = []
            for item in items:
                preview = (item.content_plain or item.content_latex or "")[:150]
                parts.append(f"[{item.item_id}] {item.item_category}: {item.title}\n  {preview}")
            return "\n\n".join(parts)

        safe_a = fmt(items_a).replace("{", "{{").replace("}", "}}")
        safe_b = fmt(items_b).replace("{", "{{").replace("}", "}}")

        prompt = CROSS_BRANCH_RELATIONS_PROMPT.format(
            branch_a_path=path_a,
            branch_a_items=safe_a,
            branch_b_path=path_b,
            branch_b_items=safe_b,
        )

        result = await self.llm.generate_json(prompt)
        return result.get("relations", [])

    # ── 工具方法 ──

    @staticmethod
    def _add_relation(
        item: UnifiedItemBase,
        target_id: str,
        target_title: str,
        relation_type: RelationType,
        confidence: float,
        source: str,
        note: str = "",
    ) -> None:
        """向 item 添加一条关系"""
        item.relations.append(
            ItemRelation(
                relation_type=relation_type,
                target_id=target_id,
                target_title=target_title,
                confidence=confidence,
                source=source,
                note=note,
            )
        )

    @staticmethod
    def _get_branch_path(tree: DocumentTree, node_id: str) -> str:
        """获取节点的完整路径（如 "Chapter 3: 连续函数 > §3.2 一致连续性"）"""
        parts: list[str] = []
        current = node_id
        while current:
            node = tree.nodes.get(current)
            if not node or node.level == "book":
                break
            label = f"{node.number} {node.title}".strip()
            parts.append(label)
            current = node.parent_id or ""
        return " > ".join(reversed(parts))

    @staticmethod
    def _validate(
        items: list[UnifiedItemBase],
        id_to_item: dict[str, UnifiedItemBase],
    ) -> None:
        """验证关系：去重、移除无效引用"""
        valid_ids = set(id_to_item.keys())
        for item in items:
            seen: set[tuple[str, str]] = set()
            valid_rels: list[ItemRelation] = []
            for rel in item.relations:
                key = (rel.relation_type.value, rel.target_id)
                if rel.target_id in valid_ids and rel.target_id != item.item_id and key not in seen:
                    seen.add(key)
                    valid_rels.append(rel)
            item.relations = valid_rels
