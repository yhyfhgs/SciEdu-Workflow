"""TreeBuilder — 增量文档结构树构建器

根据 Task1 输出的 StructuralContext，增量构建 书→章→节→小节→条目 的层级树。
支持序列化/反序列化用于 checkpoint 恢复。
支持增量持久化到数据库。
"""

from __future__ import annotations

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

    设计原则：
    - 章节编号+标题作为去重 key，相同 key 不重复创建
    - 新 chapter 自动重置 section/subsection 指针
    - 新 section 自动重置 subsection 指针
    - 空标题/编号的 context 不创建对应层级节点
    - 即使没有 storage 也正常构建内存中的树
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

        # 当前层级指针
        self._current_chapter_id: str | None = None
        self._current_section_id: str | None = None
        self._current_subsection_id: str | None = None

        # 去重索引：(level, number, title) → node_id
        self._node_keys: dict[tuple[str, str, str], str] = {}

        # 节点内 item 计数（用于排序 position）
        self._node_item_count: dict[str, int] = {}

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
            ctx: 文档结构上下文（可能部分为空）
        """
        # 按层级逐级确保节点存在（只在有实质内容时创建）
        chapter_text = (ctx.chapter_number or "").strip() + (ctx.chapter_title or "").strip()
        section_text = (ctx.section_number or "").strip() + (ctx.section_title or "").strip()
        subsection_text = (ctx.subsection_number or "").strip() + (ctx.subsection_title or "").strip()

        if chapter_text:
            self._ensure_chapter(ctx)
        if section_text:
            self._ensure_section(ctx)
        if subsection_text:
            self._ensure_subsection(ctx)

        # 挂载到最深可用节点
        target_id = (
            self._current_subsection_id
            or self._current_section_id
            or self._current_chapter_id
            or self._tree.root_id
        )

        node = self._tree.nodes[target_id]
        if item_id not in self._tree.item_to_node:
            node.item_ids.append(item_id)
            self._tree.item_to_node[item_id] = target_id

            count = self._node_item_count.get(target_id, 0)
            self._node_item_count[target_id] = count + 1

            self._persist_placement(item_id, item_type, target_id, count)

        return target_id

    def get_tree(self) -> DocumentTree:
        """获取当前构建的完整树"""
        return self._tree

    @property
    def stats(self) -> dict[str, int]:
        """树的统计信息"""
        levels: dict[str, int] = {}
        for node in self._tree.nodes.values():
            levels[node.level] = levels.get(node.level, 0) + 1
        return {
            "total_nodes": len(self._tree.nodes),
            "total_items": len(self._tree.item_to_node),
            **levels,
        }

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
        """从 JSON 反序列化恢复"""
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

        # 恢复当前层级指针 — 按 position 排序找到最后一个
        builder._current_chapter_id = None
        builder._current_section_id = None
        builder._current_subsection_id = None

        chapters = sorted(
            [n for n in builder._tree.nodes.values() if n.level == "chapter"],
            key=lambda n: n.position,
        )
        if chapters:
            builder._current_chapter_id = chapters[-1].node_id
            sections = sorted(
                [n for n in builder._tree.nodes.values()
                 if n.level == "section" and n.parent_id == builder._current_chapter_id],
                key=lambda n: n.position,
            )
            if sections:
                builder._current_section_id = sections[-1].node_id
                subsections = sorted(
                    [n for n in builder._tree.nodes.values()
                     if n.level == "subsection" and n.parent_id == builder._current_section_id],
                    key=lambda n: n.position,
                )
                if subsections:
                    builder._current_subsection_id = subsections[-1].node_id

        return builder

    # ── 层级节点管理 ──

    def _ensure_chapter(self, ctx: StructuralContext) -> None:
        """确保 chapter 节点存在"""
        title = (ctx.chapter_title or "").strip()
        number = (ctx.chapter_number or "").strip()
        if not title and not number:
            return

        key = ("chapter", number, title)
        if key in self._node_keys:
            new_chapter_id = self._node_keys[key]
            if new_chapter_id != self._current_chapter_id:
                # 切换到不同的已有 chapter：重置下级指针
                self._current_chapter_id = new_chapter_id
                self._current_section_id = None
                self._current_subsection_id = None
            return

        # 新 chapter：重置 section/subsection
        self._current_section_id = None
        self._current_subsection_id = None

        node = TaxonomyNode(
            node_id=str(uuid.uuid4()),
            level="chapter",
            title=title,
            number=number,
            parent_id=self._tree.root_id,
            source_file=self._source_file,
            position=self._count_children(self._tree.root_id, "chapter"),
        )

        self._tree.nodes[node.node_id] = node
        self._tree.nodes[self._tree.root_id].children_ids.append(node.node_id)
        self._node_keys[key] = node.node_id
        self._current_chapter_id = node.node_id

        self._persist_node(node)
        logger.info("tree_chapter_created", number=number, title=title)

    def _ensure_section(self, ctx: StructuralContext) -> None:
        """确保 section 节点存在"""
        title = (ctx.section_title or "").strip()
        number = (ctx.section_number or "").strip()
        if not title and not number:
            return

        # 没有 chapter 时，先创建一个默认 chapter
        if not self._current_chapter_id:
            self._ensure_chapter(StructuralContext(
                chapter_title=ctx.chapter_title or "未分类",
                chapter_number=ctx.chapter_number or "",
            ))

        key = ("section", number, title)
        if key in self._node_keys:
            new_section_id = self._node_keys[key]
            if new_section_id != self._current_section_id:
                self._current_section_id = new_section_id
                self._current_subsection_id = None
            return

        # 新 section：重置 subsection
        self._current_subsection_id = None

        node = TaxonomyNode(
            node_id=str(uuid.uuid4()),
            level="section",
            title=title,
            number=number,
            parent_id=self._current_chapter_id,
            source_file=self._source_file,
            position=self._count_children(self._current_chapter_id, "section"),
        )

        self._tree.nodes[node.node_id] = node
        self._tree.nodes[self._current_chapter_id].children_ids.append(node.node_id)
        self._node_keys[key] = node.node_id
        self._current_section_id = node.node_id

        self._persist_node(node)
        logger.info("tree_section_created", number=number, title=title)

    def _ensure_subsection(self, ctx: StructuralContext) -> None:
        """确保 subsection 节点存在"""
        title = (ctx.subsection_title or "").strip()
        number = (ctx.subsection_number or "").strip()
        if not title and not number:
            return

        if not self._current_section_id:
            return

        key = ("subsection", number, title)
        if key in self._node_keys:
            self._current_subsection_id = self._node_keys[key]
            return

        node = TaxonomyNode(
            node_id=str(uuid.uuid4()),
            level="subsection",
            title=title,
            number=number,
            parent_id=self._current_section_id,
            source_file=self._source_file,
            position=self._count_children(self._current_section_id, "subsection"),
        )

        self._tree.nodes[node.node_id] = node
        self._tree.nodes[self._current_section_id].children_ids.append(node.node_id)
        self._node_keys[key] = node.node_id
        self._current_subsection_id = node.node_id

        self._persist_node(node)
        logger.debug("tree_subsection_created", number=number, title=title)

    # ── 工具方法 ──

    def _count_children(self, parent_id: str | None, level: str) -> int:
        """计算某个父节点下指定层级的子节点数"""
        if not parent_id:
            return 0
        return sum(
            1 for n in self._tree.nodes.values()
            if n.level == level and n.parent_id == parent_id
        )

    def _persist_node(self, node: TaxonomyNode) -> None:
        """增量持久化节点到数据库"""
        if self._storage and hasattr(self._storage, "save_tree_node"):
            try:
                self._storage.save_tree_node(node)
            except Exception:
                logger.warning("tree_node_persist_failed", node_id=node.node_id[:8])

    def _persist_placement(
        self, item_id: str, item_type: str, node_id: str, position: int
    ) -> None:
        """增量持久化 item 挂载关系到数据库"""
        if self._storage and hasattr(self._storage, "save_item_placement"):
            try:
                self._storage.save_item_placement(item_id, item_type, node_id, position)
            except Exception:
                logger.warning("tree_placement_persist_failed", item_id=item_id[:8])
