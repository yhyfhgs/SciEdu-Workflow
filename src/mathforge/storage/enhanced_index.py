"""增强索引：在 IndexManager 基础上集成 TagTrie + bisect 难度查询"""

from __future__ import annotations

import bisect
import re
from pathlib import Path

import structlog

from mathforge.storage.index import IndexManager
from mathforge.storage.tag_trie import TagTrie

logger = structlog.get_logger()


class EnhancedIndexManager(IndexManager):
    """增强索引管理器

    在 IndexManager 的 JSON 索引基础上增加:
    - TagTrie: 层级标签的精确/前缀/子串/模糊搜索
    - sorted list + bisect: O(log n) 难度范围查询
    - 标签冷启动: 从结构化 ID 和标题自动提取层级标签
    """

    def __init__(self, index_path: Path) -> None:
        super().__init__(index_path)
        self._tag_trie = TagTrie()
        self._difficulty_sorted: list[tuple[int, str]] = []  # (difficulty, id)
        self._built = False

    def _ensure_built(self) -> None:
        """确保辅助索引已构建"""
        if self._built:
            return
        data = self._load()
        self._build_difficulty_index(data)
        self._build_tag_trie(data)
        self._built = True

    def _rebuild(self) -> None:
        """重建辅助索引"""
        data = self._load()
        self._build_difficulty_index(data)
        self._build_tag_trie(data)
        self._built = True

    # ── 覆写 add/remove 以维护辅助索引 ───────────────────

    def add(self, id: str, metadata: dict) -> None:
        super().add(id, metadata)
        # 增量更新辅助索引
        difficulty = metadata.get("difficulty", 5)
        bisect.insort(self._difficulty_sorted, (difficulty, id))

        tags = metadata.get("tags", [])
        if tags:
            for tag in tags:
                self._tag_trie.insert(tag, id)
        else:
            # 冷启动：从 ID 提取标签
            inferred = self._infer_tags_from_id(id, metadata)
            for tag in inferred:
                self._tag_trie.insert(tag, id)

    def remove(self, id: str) -> bool:
        meta = self.get(id)
        result = super().remove(id)
        if result and meta:
            difficulty = meta.get("difficulty", 5)
            try:
                self._difficulty_sorted.remove((difficulty, id))
            except ValueError:
                pass
        return result

    # ── 增强查询 ─────────────────────────────────────────

    def query(self, filters: dict) -> list[str]:
        """增强查询：联合 trie 标签搜索 + bisect 难度范围"""
        self._ensure_built()

        candidate_sets: list[set[str]] = []

        # 标签搜索（通过 trie）
        tags = filters.get("tags")
        if tags and isinstance(tags, list) and any(tags):
            tag_ids = self._tag_trie.search_multi(tags, mode="union")
            if not tag_ids:
                # trie 未命中，fallback 到子串搜索
                for tag in tags:
                    tag_ids |= self._tag_trie.search_substring(tag)
            candidate_sets.append(tag_ids)

        # 难度范围（通过 bisect）
        difficulty_range = filters.get("difficulty_range")
        if difficulty_range:
            d_min, d_max = difficulty_range
            ids_in_range = self._query_difficulty_range(d_min, d_max)
            candidate_sets.append(ids_in_range)

        # 其他精确过滤
        other_filters = {
            k: v for k, v in filters.items() if k not in ("tags", "difficulty_range", "exclude_ids")
        }

        if candidate_sets:
            # 交集
            result_ids = candidate_sets[0]
            for s in candidate_sets[1:]:
                result_ids &= s
        elif other_filters:
            # 无标签/难度过滤，走全量扫描
            result_ids = set(self._load().keys())
        else:
            return list(self._load().keys())

        # 精确过滤
        if other_filters:
            data = self._load()
            result_ids = {
                id
                for id in result_ids
                if id in data and self._matches_exact(data[id], other_filters)
            }

        return list(result_ids)

    def query_hierarchical(
        self,
        tag_queries: list[str],
        difficulty_range: tuple[int, int] | None = None,
        depth: int | None = None,
        mode: str = "union",
    ) -> list[str]:
        """层级感知的查询接口

        Args:
            tag_queries: 标签查询列表（支持层级路径和片段）
            difficulty_range: 难度范围 (min, max)
            depth: 限制标签搜索深度（None 不限制）
            mode: "union" 或 "intersection"
        """
        self._ensure_built()

        # 标签搜索
        if depth is not None:
            depth_tags = self._tag_trie.get_at_depth(depth)
            tag_ids: set[str] = set()
            for q in tag_queries:
                for path, ids in depth_tags.items():
                    if q in path:
                        tag_ids |= ids
        else:
            tag_ids = self._tag_trie.search_multi(tag_queries, mode=mode)
            if not tag_ids:
                for q in tag_queries:
                    tag_ids |= self._tag_trie.search_substring(q)

        # 难度过滤
        if difficulty_range:
            d_min, d_max = difficulty_range
            diff_ids = self._query_difficulty_range(d_min, d_max)
            tag_ids &= diff_ids

        return list(tag_ids)

    def search_fuzzy(self, query: str, max_distance: int = 2) -> list[str]:
        """模糊标签搜索"""
        self._ensure_built()
        return list(self._tag_trie.search_fuzzy(query, max_distance))

    @property
    def tag_trie(self) -> TagTrie:
        """暴露 trie 供外部直接使用"""
        self._ensure_built()
        return self._tag_trie

    # ── 辅助索引构建 ─────────────────────────────────────

    def _build_difficulty_index(self, data: dict) -> None:
        """构建 difficulty 排序索引"""
        self._difficulty_sorted = sorted(
            (meta.get("difficulty", 5), id) for id, meta in data.items()
        )

    def _build_tag_trie(self, data: dict) -> None:
        """构建标签 trie，含冷启动逻辑"""
        self._tag_trie = TagTrie()
        bootstrapped = 0

        for id, meta in data.items():
            tags = meta.get("tags", [])
            if tags:
                for tag in tags:
                    self._tag_trie.insert(tag, id)
            else:
                # 冷启动：从结构化 ID 推断标签
                inferred = self._infer_tags_from_id(id, meta)
                for tag in inferred:
                    self._tag_trie.insert(tag, id)
                if inferred:
                    bootstrapped += 1

        if bootstrapped:
            logger.info("tag_trie_bootstrap", bootstrapped=bootstrapped, total=len(data))

    def _query_difficulty_range(self, d_min: int, d_max: int) -> set[str]:
        """bisect 查询难度范围"""
        left = bisect.bisect_left(self._difficulty_sorted, (d_min,))
        right = bisect.bisect_right(self._difficulty_sorted, (d_max + 1,))
        return {id for _, id in self._difficulty_sorted[left:right]}

    @staticmethod
    def _matches_exact(meta: dict, filters: dict) -> bool:
        """精确字段匹配（非 tags/difficulty_range 的其他字段）"""
        for key, value in filters.items():
            if meta.get(key) != value:
                return False
        return True

    @staticmethod
    def _infer_tags_from_id(id: str, meta: dict) -> list[str]:
        """从结构化 ID 和元数据推断层级标签

        ID 格式示例:
        - kp-analysis-1.3.1  -> 数学分析/第1章/第3节
        - kp-linear_algebra-2.5  -> 高等代数/第2章/第5节
        - xlj-lingshi-2021-03-001  -> 考试题/零试/2021
        """
        tags: list[str] = []

        # 知识点 ID 模式: kp-<subject>-<chapter>.<section>[.<number>]
        kp_match = re.match(r"kp-(\w+)-([\d.]+)", id)
        if kp_match:
            subject_key = kp_match.group(1)
            numbering = kp_match.group(2)
            parts = numbering.split(".")

            subject_map = {
                "analysis": "数学分析",
                "linear_algebra": "高等代数",
                "group": "群论",
            }
            subject_name = subject_map.get(subject_key, subject_key)

            path_parts = [subject_name]
            if len(parts) >= 1:
                path_parts.append(f"第{parts[0]}章")
            if len(parts) >= 2:
                path_parts.append(f"第{parts[1]}节")

            tags.append("/".join(path_parts))

            # 用标题补充更具体的标签
            title = meta.get("title", "")
            if title:
                # 提取括号中的名称，如 "定义 1.2.6 (子空间)" -> "子空间"
                name_match = re.search(r"[（(](.+?)[）)]", title)
                if name_match:
                    specific = name_match.group(1).strip()
                    tags.append("/".join(path_parts + [specific]))

            return tags

        # 题目 ID 模式: xlj-<type>-<year>-<month>-<number>
        q_match = re.match(r"xlj-(\w+)-(\d{4})", id)
        if q_match:
            exam_type = q_match.group(1)
            year = q_match.group(2)
            type_map = {
                "lingshi": "零试",
                "yishi": "一试",
                "nvzi": "女子",
            }
            type_name = type_map.get(exam_type, exam_type)
            tags.append(f"考试题/{type_name}/{year}")
            return tags

        return tags
