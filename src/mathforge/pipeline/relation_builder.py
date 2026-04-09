"""RelationBuilder — 条目间关系建立

两阶段：
1. 结构化 pass（无 LLM）：从文档结构直接推断
2. LLM pass：批量推断语义关系
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog

from mathforge.core.models import (
    ItemRelation,
    RelationType,
    UnifiedItemBase,
)
from mathforge.prompts.templates import RELATION_INFERENCE_PROMPT

if TYPE_CHECKING:
    from mathforge.llm.client import LLMClient

logger = structlog.get_logger()


class RelationBuilder:
    """条目间关系建立器"""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm = llm_client

    async def build_relations(
        self,
        items: list[UnifiedItemBase],
        batch_size: int = 10,
    ) -> list[UnifiedItemBase]:
        """构建条目间关系，修改 items in-place"""
        t0 = time.monotonic()

        # Pass 1: 结构化关系
        structural_count = self._build_structural(items)

        # Pass 2: LLM 推断关系
        llm_count = 0
        if self.llm and len(items) >= 2:
            llm_count = await self._build_llm_relations(items, batch_size)

        # 验证
        self._validate(items)

        elapsed = time.monotonic() - t0
        logger.info(
            "relations_built",
            total_items=len(items),
            structural_relations=structural_count,
            llm_relations=llm_count,
            elapsed_sec=round(elapsed, 2),
        )

        return items

    def build_relations_sync(
        self,
        items: list[UnifiedItemBase],
    ) -> list[UnifiedItemBase]:
        """同步版本（仅结构化 pass）"""
        self._build_structural(items)
        self._validate(items)
        return items

    # ── Pass 1: 结构化关系 ──

    def _build_structural(self, items: list[UnifiedItemBase]) -> int:
        """从文档结构直接推断关系"""
        count = 0

        # 同一来源文件的条目
        by_file: dict[str, list[UnifiedItemBase]] = {}
        for item in items:
            key = item.source.file_path if item.source else ""
            by_file.setdefault(key, []).append(item)

        for file_items in by_file.values():
            for i, item in enumerate(file_items):
                cat = item.item_category

                # 相邻知识点 → RELATED
                if cat == "knowledge" and i + 1 < len(file_items):
                    next_item = file_items[i + 1]
                    if next_item.item_category == "knowledge":
                        item.relations.append(
                            ItemRelation(
                                relation_type=RelationType.RELATED,
                                target_id=next_item.item_id,
                                target_title=next_item.title,
                                confidence=0.7,
                                source="structural",
                            )
                        )
                        count += 1

                # 例题 → ILLUSTRATES 前面最近的知识点
                if cat == "example" and i > 0:
                    for j in range(i - 1, max(i - 5, -1), -1):
                        prev = file_items[j]
                        if prev.item_category == "knowledge":
                            item.relations.append(
                                ItemRelation(
                                    relation_type=RelationType.ILLUSTRATES,
                                    target_id=prev.item_id,
                                    target_title=prev.title,
                                    confidence=0.8,
                                    source="structural",
                                )
                            )
                            count += 1
                            break

                # 推论 → COROLLARY_OF 前面最近的定理
                if hasattr(item, "parent_theorem_id") and item.parent_theorem_id is None:
                    for j in range(i - 1, max(i - 5, -1), -1):
                        prev = file_items[j]
                        if hasattr(prev, "kp_type") and prev.kp_type.value in ("theorem", "lemma"):
                            item.relations.append(
                                ItemRelation(
                                    relation_type=RelationType.COROLLARY_OF,
                                    target_id=prev.item_id,
                                    target_title=prev.title,
                                    confidence=0.9,
                                    source="structural",
                                )
                            )
                            item.parent_theorem_id = prev.item_id  # type: ignore[attr-defined]
                            count += 1
                            break

        return count

    # ── Pass 2: LLM 批量推断 ──

    async def _build_llm_relations(self, items: list[UnifiedItemBase], batch_size: int) -> int:
        """批量 LLM 关系推断"""
        assert self.llm is not None

        count = 0
        id_map = {i.item_id: i for i in items}

        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            if len(batch) < 2:
                continue

            items_desc = self._format_items_for_prompt(batch)
            safe_desc = items_desc.replace("{", "{{").replace("}", "}}")
            prompt = RELATION_INFERENCE_PROMPT.format(items_list=safe_desc)

            try:
                result = await self.llm.generate_json(prompt)

                for rel in result.get("relations", []):
                    source_item = id_map.get(rel.get("source_id", ""))
                    target_item = id_map.get(rel.get("target_id", ""))
                    if not source_item or not target_item:
                        continue

                    try:
                        rel_type = RelationType(rel.get("relation_type", "related"))
                    except ValueError:
                        rel_type = RelationType.RELATED

                    source_item.relations.append(
                        ItemRelation(
                            relation_type=rel_type,
                            target_id=target_item.item_id,
                            target_title=target_item.title,
                            confidence=rel.get("confidence", 0.7),
                            source="llm",
                            note=rel.get("note", ""),
                        )
                    )
                    count += 1

            except Exception:
                logger.warning(
                    "relation_llm_batch_failed",
                    batch_start=start,
                    exc_info=True,
                )

        return count

    @staticmethod
    def _format_items_for_prompt(
        items: list[UnifiedItemBase],
        max_content_chars: int = 200,
    ) -> str:
        """格式化 items 列表为 prompt 文本，截断内容避免 token 溢出"""
        lines: list[str] = []
        for item in items:
            cat = item.item_category
            content_preview = (item.content_plain or item.content_latex)[:max_content_chars]
            lines.append(f"[{item.item_id}] {cat}: {item.title}\n  内容: {content_preview}...")
        return "\n\n".join(lines)

    # ── 验证 ──

    def _validate(self, items: list[UnifiedItemBase]) -> None:
        """验证关系约束"""
        all_ids = {i.item_id for i in items}

        for item in items:
            # 移除无效关系：不存在的目标、自引用
            seen: set[tuple[str, str]] = set()
            valid: list[ItemRelation] = []
            for r in item.relations:
                if r.target_id not in all_ids:
                    continue
                if r.target_id == item.item_id:
                    continue  # 过滤自引用
                key = (r.relation_type.value, r.target_id)
                if key in seen:
                    continue  # 去重
                seen.add(key)
                valid.append(r)
            item.relations = valid

        # DAG 检查（PREREQUISITE 无环）
        prereq_graph: dict[str, set[str]] = {}
        for item in items:
            for rel in item.relations:
                if rel.relation_type == RelationType.PREREQUISITE:
                    prereq_graph.setdefault(item.item_id, set()).add(rel.target_id)

        if prereq_graph and self._has_cycle(prereq_graph):
            logger.warning("relation_prerequisite_cycle_detected")
            self._break_cycles(items, prereq_graph)

    @staticmethod
    def _has_cycle(graph: dict[str, set[str]]) -> bool:
        """拓扑排序检测环"""
        visited: set[str] = set()
        in_stack: set[str] = set()

        def dfs(node: str) -> bool:
            if node in in_stack:
                return True
            if node in visited:
                return False
            visited.add(node)
            in_stack.add(node)
            for neighbor in graph.get(node, set()):
                if dfs(neighbor):
                    return True
            in_stack.discard(node)
            return False

        return any(dfs(node) for node in graph if node not in visited)

    @staticmethod
    def _break_cycles(
        items: list[UnifiedItemBase],
        prereq_graph: dict[str, set[str]],
    ) -> None:
        """移除环路中 confidence 最低的边来打破环"""
        from mathforge.core.models import RelationType

        id_to_item = {i.item_id: i for i in items}
        removed = 0

        # 构建 edge → confidence 查找表
        def _edge_confidence(src: str, tgt: str) -> float:
            item = id_to_item.get(src)
            if not item:
                return 0.0
            for r in item.relations:
                if r.relation_type == RelationType.PREREQUISITE and r.target_id == tgt:
                    return r.confidence
            return 0.0

        # 反复检测并打破环，直到无环
        while True:
            cycle_edge = _find_cycle_edge(prereq_graph)
            if not cycle_edge:
                break

            source_id, target_id = cycle_edge

            # 找到 confidence 最低的边来移除
            min_conf = _edge_confidence(source_id, target_id)
            remove_src, remove_tgt = source_id, target_id

            # 如果有 target→source 的反向边且其 confidence 更低，移除那条
            reverse_conf = _edge_confidence(target_id, source_id)
            if (
                target_id in prereq_graph
                and source_id in prereq_graph.get(target_id, set())
                and reverse_conf < min_conf
            ):
                remove_src, remove_tgt = target_id, source_id

            item = id_to_item.get(remove_src)
            if not item:
                prereq_graph.get(remove_src, set()).discard(remove_tgt)
                continue

            # 移除该关系
            item.relations = [
                r
                for r in item.relations
                if not (r.relation_type == RelationType.PREREQUISITE and r.target_id == remove_tgt)
            ]
            prereq_graph.get(remove_src, set()).discard(remove_tgt)
            removed += 1

        if removed:
            logger.info("relation_cycles_broken", edges_removed=removed)


def _find_cycle_edge(
    graph: dict[str, set[str]],
) -> tuple[str, str] | None:
    """找到图中环路上 confidence 最低的一条边（DFS）

    返回 (source, target) 或 None。使用路径栈正确追踪 DFS 路径。
    """
    visited: set[str] = set()
    in_stack: set[str] = set()
    path: list[str] = []  # 当前 DFS 路径

    def dfs(node: str) -> tuple[str, str] | None:
        if node in in_stack:
            # 找到环：path 中从 node 首次出现到末尾 + node 形成环
            # 返回进入环的边 (path[-1], node)
            return (path[-1], node) if path else (node, node)
        if node in visited:
            return None
        visited.add(node)
        in_stack.add(node)
        path.append(node)
        for neighbor in graph.get(node, set()):
            result = dfs(neighbor)
            if result:
                return result
        path.pop()
        in_stack.discard(node)
        return None

    for node in graph:
        if node not in visited:
            result = dfs(node)
            if result:
                return result
    return None
