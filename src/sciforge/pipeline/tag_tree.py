"""TagTree — 轻量层级标签树，专门用于 prompt 注入

约定:
- 每个 tag = 单一 'english:中文' 段，如 'math:数学', 'group_theory:群论'
- 一个条目的 tags 字段是一条**有序段列表**，从领域 (root) 到关键词 (leaf)
- 多个条目的路径合并成一棵树
- 序列化为 prompt 文本时使用紧凑的缩进 Markdown 列表

序列化大小可配置（max_depth / max_nodes），超限按 item_count 降序保留。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# 单段标签的合法格式: 小写英文标识符 + ':' + 中文翻译
_SEGMENT_RE = re.compile(r"^[a-z][a-z0-9_]*:[^\s:/]+$")


@dataclass
class TagNode:
    """Tag 树节点"""

    segment: str = ""
    children: dict[str, TagNode] = field(default_factory=dict)
    item_count: int = 0  # 该路径下挂载的条目计数


class TagTree:
    """专门用于 prompt 注入的轻量 tag 树（不做检索查询）"""

    def __init__(self) -> None:
        self.root = TagNode(segment="")

    # ── 插入 ───────────────────────────────────────────

    def insert_path(self, segments: list[str]) -> None:
        """插入一条完整路径，segments 是有序的 'english:中文' 段列表"""
        if not segments:
            return
        node = self.root
        for raw in segments:
            seg = raw.strip()
            if not self.is_valid_segment(seg):
                continue  # 跳过非法段（防御性）
            if seg not in node.children:
                node.children[seg] = TagNode(segment=seg)
            node = node.children[seg]
            node.item_count += 1

    def merge(self, other: TagTree) -> None:
        """合并另一颗 tree（用于跨 batch 聚合）"""
        self._merge_node(self.root, other.root)

    def _merge_node(self, dst: TagNode, src: TagNode) -> None:
        for seg, src_child in src.children.items():
            if seg not in dst.children:
                dst.children[seg] = TagNode(segment=seg)
            dst_child = dst.children[seg]
            dst_child.item_count += src_child.item_count
            self._merge_node(dst_child, src_child)

    # ── 查询/统计 ──────────────────────────────────────

    def total_nodes(self) -> int:
        return self._count_nodes(self.root) - 1  # 不计 root

    def _count_nodes(self, node: TagNode) -> int:
        return 1 + sum(self._count_nodes(c) for c in node.children.values())

    def total_paths(self) -> int:
        """叶节点数 = 路径数"""
        return self._count_leaves(self.root)

    def _count_leaves(self, node: TagNode) -> int:
        if not node.children:
            return 0 if node is self.root else 1
        return sum(self._count_leaves(c) for c in node.children.values())

    # ── 序列化为 prompt 文本 ────────────────────────────

    def serialize_for_prompt(
        self, max_depth: int = 4, max_nodes: int = 200
    ) -> str:
        """输出为缩进 Markdown 列表，紧凑/人类可读/token 节省

        - max_depth: 限制最大展示深度
        - max_nodes: 限制总节点数（按 item_count 降序保留最热子树）
        - 被截断的位置用 '  - ...' 占位
        """
        if not self.root.children:
            return "(空，本次提取将创建第一批 tag)"

        # 1. 收集允许展示的节点集合（按 item_count 降序优先保留热门）
        # 热度裁剪避免 prompt 过长 — 树扩张后只注入最常用的子树
        allowed: set[int] = set()  # 用 id() 标识节点（调用栈内节点不会 GC，安全）
        nodes_with_depth: list[tuple[TagNode, int, TagNode]] = []  # (node, depth, parent)
        self._collect_nodes(self.root, 0, max_depth, nodes_with_depth)

        nodes_with_depth.sort(key=lambda x: -x[0].item_count)
        for node, _, _ in nodes_with_depth[:max_nodes]:
            allowed.add(id(node))

        # 2. 递归输出（保留原始树结构，但只展示 allowed 中的节点）
        lines: list[str] = []
        self._render(self.root, 0, max_depth, allowed, lines)
        return "\n".join(lines) if lines else "(空)"

    def _collect_nodes(
        self,
        node: TagNode,
        depth: int,
        max_depth: int,
        out: list[tuple[TagNode, int, TagNode]],
    ) -> None:
        if depth >= max_depth and node is not self.root:
            return
        for child in node.children.values():
            out.append((child, depth + 1, node))
            self._collect_nodes(child, depth + 1, max_depth, out)

    def _render(
        self,
        node: TagNode,
        depth: int,
        max_depth: int,
        allowed: set[int],
        lines: list[str],
    ) -> None:
        if depth > max_depth:
            return

        if node is self.root:
            # 直接展开 children
            for child in node.children.values():
                self._render(child, depth + 1, max_depth, allowed, lines)
            return

        indent = "  " * (depth - 1)
        marker = f"({node.item_count})" if node.item_count > 0 else ""
        lines.append(f"{indent}- {node.segment} {marker}".rstrip())

        if depth >= max_depth:
            if node.children:
                lines.append(f"{indent}  - ...")
            return

        # 子节点：先输出 allowed 中的，然后如果有被裁掉的就 ... 占位
        shown_count = 0
        hidden_count = 0
        for child in node.children.values():
            if id(child) in allowed:
                self._render(child, depth + 1, max_depth, allowed, lines)
                shown_count += 1
            else:
                hidden_count += 1
        if hidden_count > 0:
            lines.append(f"{indent}  - ... ({hidden_count} more)")

    # ── JSON 持久化 ────────────────────────────────────

    def to_json(self) -> str:
        return json.dumps(self._node_to_dict(self.root), ensure_ascii=False)

    def _node_to_dict(self, node: TagNode) -> dict[str, object]:
        return {
            "segment": node.segment,
            "item_count": node.item_count,
            "children": {seg: self._node_to_dict(c) for seg, c in node.children.items()},
        }

    @classmethod
    def from_json(cls, s: str) -> TagTree:
        if not s:
            return cls()
        tree = cls()
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return tree
        tree.root = cls._dict_to_node(data)
        return tree

    @staticmethod
    def _dict_to_node(data: dict[str, object]) -> TagNode:
        raw_count = data.get("item_count", 0)
        item_count = int(raw_count) if isinstance(raw_count, (int, float)) else 0
        node = TagNode(
            segment=str(data.get("segment", "")),
            item_count=item_count,
        )
        children = data.get("children", {})
        if isinstance(children, dict):
            for seg, child_data in children.items():
                if isinstance(child_data, dict):
                    node.children[seg] = TagTree._dict_to_node(child_data)
        return node

    # ── 段格式校验 ─────────────────────────────────────

    @staticmethod
    def is_valid_segment(seg: str) -> bool:
        """检验单段格式：english:中文

        - english 部分：[a-z][a-z0-9_]*
        - 半角冒号
        - 中文部分：非空，不含空白/冒号/斜杠
        """
        return bool(_SEGMENT_RE.match(seg))
