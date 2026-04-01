"""层级标签 Trie：支持精确/前缀/子串/模糊搜索"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TagNode:
    """Trie 节点"""
    segment: str = ""
    children: dict[str, TagNode] = field(default_factory=dict)
    item_ids: set[str] = field(default_factory=set)
    full_path: str = ""


class TagTrie:
    """层级标签 Trie

    支持如下标签路径格式:
    - "数学分析/极限/数列极限/单调有界"
    - 分隔符为 "/"

    搜索能力:
    - 精确匹配: 完整路径命中
    - 前缀匹配: 查询 "数学分析/极限" 返回所有极限子节点下的 item
    - 子串匹配: 查询 "极限" 返回所有路径中含 "极限" 段的 item
    - 模糊匹配: 编辑距离容忍的搜索
    - 深度查询: 返回指定深度的所有标签
    """

    def __init__(self, separator: str = "/") -> None:
        self.root = TagNode(segment="<root>")
        self.separator = separator
        self._all_nodes: list[TagNode] = []

    def insert(self, tag_path: str, item_id: str) -> None:
        """插入一个标签路径与关联的 item ID"""
        segments = [s.strip() for s in tag_path.split(self.separator) if s.strip()]
        if not segments:
            return

        node = self.root
        accumulated = []
        for seg in segments:
            accumulated.append(seg)
            if seg not in node.children:
                child = TagNode(
                    segment=seg,
                    full_path=self.separator.join(accumulated),
                )
                node.children[seg] = child
                self._all_nodes.append(child)
            node = node.children[seg]
            node.item_ids.add(item_id)

    def remove(self, tag_path: str, item_id: str) -> None:
        """从标签路径中移除一个 item ID"""
        segments = [s.strip() for s in tag_path.split(self.separator) if s.strip()]
        node = self.root
        for seg in segments:
            if seg not in node.children:
                return
            node = node.children[seg]
            node.item_ids.discard(item_id)

    def search_exact(self, tag_path: str) -> set[str]:
        """精确匹配：返回完全命中该路径的叶节点 item ID"""
        node = self._find_node(tag_path)
        if node is None:
            return set()
        return set(node.item_ids)

    def search_prefix(self, prefix: str) -> set[str]:
        """前缀匹配：返回前缀路径下所有子节点的 item ID"""
        node = self._find_node(prefix)
        if node is None:
            return set()
        return self._collect_subtree(node)

    def search_substring(self, fragment: str) -> set[str]:
        """子串匹配：返回任意路径段包含 fragment 的 item ID"""
        results: set[str] = set()
        for node in self._all_nodes:
            if fragment in node.segment:
                results.update(self._collect_subtree(node))
        return results

    def search_fuzzy(self, query: str, max_distance: int = 2) -> set[str]:
        """模糊匹配：编辑距离 <= max_distance 的路径段的 item ID"""
        results: set[str] = set()
        for node in self._all_nodes:
            if _levenshtein(query, node.segment) <= max_distance:
                results.update(self._collect_subtree(node))
        return results

    def search_multi(
        self,
        queries: list[str],
        mode: str = "union",
    ) -> set[str]:
        """多标签搜索

        Args:
            queries: 标签查询列表
            mode: "union"(并集) 或 "intersection"(交集)
        """
        if not queries:
            return set()

        sets: list[set[str]] = []
        for q in queries:
            found = self.search_prefix(q)
            if not found:
                found = self.search_substring(q)
            sets.append(found)

        if mode == "intersection":
            result = sets[0]
            for s in sets[1:]:
                result &= s
            return result
        else:
            result: set[str] = set()
            for s in sets:
                result |= s
            return result

    def get_at_depth(self, depth: int) -> dict[str, set[str]]:
        """返回指定深度的所有标签及其 item ID"""
        result: dict[str, set[str]] = {}
        self._collect_at_depth(self.root, 0, depth, result)
        return result

    def get_children(self, tag_path: str) -> list[str]:
        """返回给定路径的直接子标签名"""
        node = self._find_node(tag_path)
        if node is None:
            return []
        return list(node.children.keys())

    def all_paths(self) -> list[str]:
        """返回所有叶节点的完整路径"""
        paths: list[str] = []
        self._collect_leaf_paths(self.root, paths)
        return paths

    def item_count(self) -> int:
        """返回 Trie 中不重复 item 的总数"""
        all_ids: set[str] = set()
        for node in self._all_nodes:
            all_ids.update(node.item_ids)
        return len(all_ids)

    # ── 内部方法 ─────────────────────────────────────────

    def _find_node(self, tag_path: str) -> TagNode | None:
        """沿路径查找节点"""
        segments = [s.strip() for s in tag_path.split(self.separator) if s.strip()]
        node = self.root
        for seg in segments:
            if seg not in node.children:
                return None
            node = node.children[seg]
        return node

    def _collect_subtree(self, node: TagNode) -> set[str]:
        """收集节点及所有后代的 item ID"""
        result = set(node.item_ids)
        for child in node.children.values():
            result.update(self._collect_subtree(child))
        return result

    def _collect_at_depth(
        self,
        node: TagNode,
        current_depth: int,
        target_depth: int,
        result: dict[str, set[str]],
    ) -> None:
        """递归收集指定深度的节点"""
        if current_depth == target_depth:
            if node.full_path:
                result[node.full_path] = set(node.item_ids)
            return
        for child in node.children.values():
            self._collect_at_depth(child, current_depth + 1, target_depth, result)

    def _collect_leaf_paths(self, node: TagNode, paths: list[str]) -> None:
        """收集所有叶节点路径"""
        if not node.children and node.full_path:
            paths.append(node.full_path)
        for child in node.children.values():
            self._collect_leaf_paths(child, paths)


def _levenshtein(s1: str, s2: str) -> int:
    """计算两个字符串的编辑距离"""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row

    return prev_row[-1]
