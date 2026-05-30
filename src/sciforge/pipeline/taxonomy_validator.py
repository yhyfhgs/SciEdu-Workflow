"""TaxonomyValidator — 对照 taxonomy.json 校验 + 模糊匹配修正 taxonomy_path

加载 src/sciforge/config/taxonomy.json，构建合法路径集合，对 LLM 输出的
taxonomy_path 做精确匹配 / 前缀匹配 / 模糊匹配。
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

logger = structlog.get_logger()

# 默认 taxonomy.json 路径
_DEFAULT_TAXONOMY_PATH = Path(__file__).parent.parent / "config" / "taxonomy.json"


def _levenshtein(a: str, b: str) -> int:
    """最小编辑距离（Levenshtein），纯 Python 实现"""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


class TaxonomyValidator:
    """对照 taxonomy.json 校验并修正 taxonomy_path"""

    def __init__(self, taxonomy_path: Path | str | None = None) -> None:
        self._valid_paths: set[str] = set()
        self._valid_prefixes: set[str] = set()  # 所有中间节点路径
        self._path_labels: dict[str, str] = {}  # path → _label
        self._leaf_paths: list[str] = []  # 所有叶节点路径（用于 fuzzy 候选）

        path = Path(taxonomy_path) if taxonomy_path else _DEFAULT_TAXONOMY_PATH
        if path.exists():
            self._load(path)
        else:
            logger.warning("taxonomy_json_not_found", path=str(path))

    def _load(self, path: Path) -> None:
        """解析 taxonomy.json 构建路径集合"""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self._walk(data, [])
        logger.info(
            "taxonomy_loaded",
            total_paths=len(self._valid_paths),
            leaf_paths=len(self._leaf_paths),
        )

    def _walk(self, node: dict[str, object] | list[object], prefix: list[str]) -> None:
        """递归遍历 taxonomy tree，收集所有合法路径"""
        if isinstance(node, list):
            # 叶数组：每个元素是一个最终子主题
            for item in node:
                full = prefix + [str(item)]
                p = ".".join(full)
                self._valid_paths.add(p)
                self._leaf_paths.append(p)
            return

        if not isinstance(node, dict):
            return

        for key, value in node.items():
            if key == "_label":
                # _label 不是路径节点，是显示名
                self._path_labels[".".join(prefix)] = str(value)
                continue

            current = prefix + [key]
            p = ".".join(current)
            self._valid_paths.add(p)
            self._valid_prefixes.add(p)

            if isinstance(value, dict):
                self._walk(value, current)
            elif isinstance(value, list):
                self._walk(value, current)

    @property
    def empty(self) -> bool:
        return not self._valid_paths

    def validate(self, raw_path: str) -> tuple[str, bool]:
        """校验并修正 taxonomy_path。

        Returns:
            (修正后路径, 是否发生修正)
        """
        if self.empty or not raw_path:
            return raw_path, False

        path = self._normalize(raw_path)

        # 1. 精确匹配
        if path in self._valid_paths:
            return path, False

        # 2. 前缀匹配（LLM 给了更深的路径，截断到最长合法前缀）
        best_prefix = self._longest_valid_prefix(path)
        if best_prefix and best_prefix != path:
            logger.info("taxonomy_prefix_match", raw=raw_path, corrected=best_prefix)
            return best_prefix, True

        # 3. 模糊匹配（每段 edit_distance <= 2）
        fuzzy = self._fuzzy_match(path)
        if fuzzy:
            logger.info("taxonomy_fuzzy_match", raw=raw_path, corrected=fuzzy)
            return fuzzy, True

        # 无匹配：保留原值
        logger.debug("taxonomy_no_match", path=raw_path)
        return path, False

    def serialize_for_prompt(
        self,
        discipline: str,
        max_depth: int = 3,
        branch_filter: str | None = None,
    ) -> str:
        """将指定学科的 taxonomy 序列化为紧凑路径列表，用于 prompt 注入。

        Args:
            discipline: 学科标识 (如 "math")
            max_depth: 最大展示深度（从 discipline 根节点算起）
            branch_filter: 可选的分支过滤器 (如 "analysis")。
                若提供，只输出 ``discipline.branch_filter.*`` 路径。
                若 branch 不存在于 taxonomy 中，fallback 到全量输出。

        Returns:
            紧凑的路径列表字符串
        """
        if self.empty:
            return "(无 taxonomy 数据)"

        # 确定过滤前缀
        if branch_filter:
            branch_prefix = f"{discipline}.{branch_filter}"
            # 验证分支是否存在
            has_branch = any(p.startswith(branch_prefix) for p in self._valid_paths)
            if has_branch:
                filter_prefix = branch_prefix
            else:
                filter_prefix = discipline + "."  # fallback 到全量
        else:
            filter_prefix = discipline + "."

        lines: list[str] = []
        for p in sorted(self._valid_paths):
            if not p.startswith(filter_prefix) and p != discipline:
                continue
            # 计算相对于 discipline 的深度
            parts = p.split(".")
            if len(parts) > max_depth + 1:  # +1 for discipline itself
                continue
            indent = "  " * (len(parts) - 1)
            label = self._path_labels.get(p, "")
            if label:
                lines.append(f"{indent}- {parts[-1]} ({label})")
            else:
                lines.append(f"{indent}- {parts[-1]}")

        return "\n".join(lines) if lines else f"({discipline} 无 taxonomy)"

    @staticmethod
    def _normalize(raw: str) -> str:
        """统一分隔符为 '.'，小写化，strip"""
        path = raw.strip().lower()
        path = path.replace("/", ".").replace(">", ".").replace(":", ".")
        path = path.strip(".")
        while ".." in path:
            path = path.replace("..", ".")
        return path

    def _longest_valid_prefix(self, path: str) -> str:
        """找到最长的合法前缀路径"""
        parts = path.split(".")
        best = ""
        for i in range(1, len(parts) + 1):
            candidate = ".".join(parts[:i])
            if candidate in self._valid_paths:
                best = candidate
        return best

    def _fuzzy_match(self, path: str) -> str:
        """按段做 Levenshtein 模糊匹配，总距离阈值 = 段数 * 2"""
        parts = path.split(".")
        if not parts:
            return ""

        best_path = ""
        best_dist = float("inf")
        max_total_dist = len(parts) * 2

        for candidate in self._valid_paths:
            c_parts = candidate.split(".")
            if abs(len(c_parts) - len(parts)) > 1:
                continue

            # 按段对齐比较
            min_len = min(len(parts), len(c_parts))
            total_dist = 0
            too_far = False
            for i in range(min_len):
                d = _levenshtein(parts[i], c_parts[i])
                if d > 2:  # 单段容忍上限
                    too_far = True
                    break
                total_dist += d
            if too_far:
                continue

            # 长度差异惩罚
            total_dist += abs(len(parts) - len(c_parts)) * 2

            if total_dist < best_dist and total_dist <= max_total_dist:
                best_dist = total_dist
                best_path = candidate

        return best_path
