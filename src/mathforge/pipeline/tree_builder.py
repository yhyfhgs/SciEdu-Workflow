"""TreeBuilder — 增量文档结构树构建器

根据 Task1 输出的 StructuralContext，增量构建 章→节→小节→条目 的层级树。
支持序列化/反序列化用于 checkpoint 恢复。
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

import structlog

from mathforge.core.models import (
    DocumentTree,
    StructuralContext,
    TaxonomyNode,
)

if TYPE_CHECKING:
    from mathforge.storage.protocol import StorageBackend

logger = structlog.get_logger()


class TreeBuilder:
    """增量文档结构树构建器。

    生命周期 = 一个文件的处理期间。
    通过 StructuralContext 自动创建章/节/小节节点，将 item 挂载到最深层级。
    """

    def __init__(
        self,
        source_file: str,
        storage: StorageBackend | None = None,
    ) -> None:
        self._tree = DocumentTree()
        self._storage = storage
        self._source_file = source_file

        # 创建根节点（book 级别）
        root = TaxonomyNode(
            node_id=str(uuid.uuid4()),
            level="book",
            title=source_file,
            source_file=source_file,
        )
        self._tree.root_id = root.node_id
        self._tree.nodes[root.node_id] = root

        # 当前层级跟踪
        self._current_chapter_id: str | None = None
        self._current_section_id: str | None = None
        self._current_subsection_id: str | None = None

        # 去重：(level, number, title) → node_id
        self._node_keys: dict[tuple[str, str, str], str] = {}

        # 节点内 item 计数（用于排序 position）
        self._node_item_count: dict[str, int] = {}

        if self._storage:
            self._persist_node(root)

    def insert(
        self,
        item_id: str,
        item_type: str,
        ctx: StructuralContext,
    ) -> str:
        """将 item 插入树中，返回挂载的 node_id。

        Args:
            item_id: 条目 ID
            item_type: "kp" 或 "question"
            ctx: 文档结构上下文
        """
        # 确保层级节点存在
        if ctx.chapter_title or ctx.chapter_number:
            self._ensure_chapter(ctx)
        if ctx.section_title or ctx.section_number:
            self._ensure_section(ctx)
        if ctx.subsection_title or ctx.subsection_number:
            self._ensure_subsection(ctx)

        # 挂载到最深可用节点
        target_id = (
            self._current_subsection_id
            or self._current_section_id
            or self._current_chapter_id
            or self._tree.root_id
        )

        node = self._tree.nodes[target_id]
        node.item_ids.append(item_id)
        self._tree.item_to_node[item_id] = target_id

        # 计算 position
        count = self._node_item_count.get(target_id, 0)
        self._node_item_count[target_id] = count + 1

        if self._storage:
            self._persist_placement(item_id, item_type, target_id, count)

        return target_id

    def get_tree(self) -> DocumentTree:
        """获取当前构建的完整树"""
        return self._tree

    def serialize(self) -> str:
        """序列化为 JSON（用于 checkpoint）"""
        return self._tree.model_dump_json()

    @classmethod
    def deserialize(
        cls,
        data: str,
        source_file: str,
        storage: StorageBackend | None = None,
    ) -> TreeBuilder:
        """从 JSON 反序列化恢复（用于 checkpoint resume）"""
        builder = cls.__new__(cls)
        builder._tree = DocumentTree.model_validate_json(data)
        builder._storage = storage
        builder._source_file = source_file
        builder._node_keys = {}
        builder._node_item_count = {}

        # 重建内部索引
        for node in builder._tree.nodes.values():
            key = (node.level, node.number, node.title)
            builder._node_keys[key] = node.node_id
            builder._node_item_count[node.node_id] = len(node.item_ids)

        # 恢复当前层级指针（使用最后一个节点）
        builder._current_chapter_id = None
        builder._current_section_id = None
        builder._current_subsection_id = None

        chapters = [
            n for n in builder._tree.nodes.values() if n.level == "chapter"
        ]
        if chapters:
            builder._current_chapter_id = chapters[-1].node_id
            sections = [
                n for n in builder._tree.nodes.values()
                if n.level == "section" and n.parent_id == builder._current_chapter_id
            ]
            if sections:
                builder._current_section_id = sections[-1].node_id
                subsections = [
                    n for n in builder._tree.nodes.values()
                    if n.level == "subsection" and n.parent_id == builder._current_section_id
                ]
                if subsections:
                    builder._current_subsection_id = subsections[-1].node_id

        return builder

    # ── 内部方法 ──

    def _ensure_chapter(self, ctx: StructuralContext) -> None:
        """确保 chapter 节点存在，如果是新章则创建"""
        key = ("chapter", ctx.chapter_number, ctx.chapter_title)
        if key in self._node_keys:
            self._current_chapter_id = self._node_keys[key]
            return

        # 新章节：重置 section/subsection
        self._current_section_id = None
        self._current_subsection_id = None

        node = TaxonomyNode(
            node_id=str(uuid.uuid4()),
            level="chapter",
            title=ctx.chapter_title,
            number=ctx.chapter_number,
            parent_id=self._tree.root_id,
            source_file=self._source_file,
            position=len([
                n for n in self._tree.nodes.values()
                if n.level == "chapter" and n.parent_id == self._tree.root_id
            ]),
        )

        self._tree.nodes[node.node_id] = node
        self._tree.nodes[self._tree.root_id].children_ids.append(node.node_id)
        self._node_keys[key] = node.node_id
        self._current_chapter_id = node.node_id

        if self._storage:
            self._persist_node(node)

        logger.debug(
            "tree_chapter_created",
            title=ctx.chapter_title,
            number=ctx.chapter_number,
        )

    def _ensure_section(self, ctx: StructuralContext) -> None:
        """确保 section 节点存在"""
        if not self._current_chapter_id:
            return

        key = ("section", ctx.section_number, ctx.section_title)
        if key in self._node_keys:
            self._current_section_id = self._node_keys[key]
            return

        # 新小节：重置 subsection
        self._current_subsection_id = None

        node = TaxonomyNode(
            node_id=str(uuid.uuid4()),
            level="section",
            title=ctx.section_title,
            number=ctx.section_number,
            parent_id=self._current_chapter_id,
            source_file=self._source_file,
            position=len([
                n for n in self._tree.nodes.values()
                if n.level == "section" and n.parent_id == self._current_chapter_id
            ]),
        )

        self._tree.nodes[node.node_id] = node
        self._tree.nodes[self._current_chapter_id].children_ids.append(node.node_id)
        self._node_keys[key] = node.node_id
        self._current_section_id = node.node_id

        if self._storage:
            self._persist_node(node)

        logger.debug(
            "tree_section_created",
            title=ctx.section_title,
            number=ctx.section_number,
        )

    def _ensure_subsection(self, ctx: StructuralContext) -> None:
        """确保 subsection 节点存在"""
        if not self._current_section_id:
            return

        key = ("subsection", ctx.subsection_number, ctx.subsection_title)
        if key in self._node_keys:
            self._current_subsection_id = self._node_keys[key]
            return

        node = TaxonomyNode(
            node_id=str(uuid.uuid4()),
            level="subsection",
            title=ctx.subsection_title,
            number=ctx.subsection_number,
            parent_id=self._current_section_id,
            source_file=self._source_file,
            position=len([
                n for n in self._tree.nodes.values()
                if n.level == "subsection" and n.parent_id == self._current_section_id
            ]),
        )

        self._tree.nodes[node.node_id] = node
        self._tree.nodes[self._current_section_id].children_ids.append(node.node_id)
        self._node_keys[key] = node.node_id
        self._current_subsection_id = node.node_id

        if self._storage:
            self._persist_node(node)

        logger.debug(
            "tree_subsection_created",
            title=ctx.subsection_title,
            number=ctx.subsection_number,
        )

    def _persist_node(self, node: TaxonomyNode) -> None:
        """增量持久化节点到数据库"""
        if self._storage and hasattr(self._storage, "save_tree_node"):
            try:
                self._storage.save_tree_node(node)
            except Exception:
                logger.warning("tree_node_persist_failed", node_id=node.node_id[:8], exc_info=True)

    def _persist_placement(
        self, item_id: str, item_type: str, node_id: str, position: int
    ) -> None:
        """增量持久化 item 挂载关系到数据库"""
        if self._storage and hasattr(self._storage, "save_item_placement"):
            try:
                self._storage.save_item_placement(item_id, item_type, node_id, position)
            except Exception:
                logger.warning(
                    "tree_placement_persist_failed",
                    item_id=item_id[:8],
                    exc_info=True,
                )
