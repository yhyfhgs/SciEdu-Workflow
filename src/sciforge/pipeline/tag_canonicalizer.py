"""TagCanonicalizer — 确定性 tag 受控词表统一（#6）

把 LLM 标注产出的 tag 段（``english:中文``）归并到统一受控词表，消除同义异名，
使全局 tag tree 细粒度且一致，便于构建知识图谱。**不调用 LLM**，纯规则、可测、
运行内幂等。在 Stage3Persist 入库前对每条 tags 归一化。

三层归并（按序）：
1. **格式规范化**：english 段小写化、``-``/空白 → ``_``、strip。
2. **别名表**：``DisciplineSpec.tag_aliases`` 提供的 ``变体english → 规范english``
   （如 ``groups → group_theory``）。
3. **双向标签一致性（first-seen-wins）**：
   - 同一**中文**已出现过 → 复用其规范 english（消除 ``groups:群论`` vs ``group_theory:群论``）。
   - 同一 **english** 已出现过 → 复用其规范中文（消除 ``group_theory:群`` 与 ``:群论`` 并存）。

可用 :meth:`seed_from_tree` 把已累积的 tag_tree（resume 时从 state 加载）注册为既有规范，
保证续跑与历史一致。
"""

from __future__ import annotations

from collections.abc import Mapping

from sciforge.pipeline.tag_tree import TagNode, TagTree


class TagCanonicalizer:
    """tag 段受控词表统一器（Stage3 持有单例，跨 item 累积一致性）"""

    def __init__(self, alias_map: Mapping[str, str] | None = None) -> None:
        # 变体 english → 规范 english
        self._alias: dict[str, str] = {k.lower(): v for k, v in (alias_map or {}).items()}
        # 中文 → 规范 english（first-seen-wins）
        self._zh_to_en: dict[str, str] = {}
        # english → 规范中文（first-seen-wins）
        self._en_to_zh: dict[str, str] = {}

    # ── 既有 tag tree 注册为规范（resume 一致性）──────────────
    def seed_from_tree(self, tree: TagTree) -> None:
        """把现有 tag_tree 的所有段注册为既有规范（不覆盖已注册项）。"""
        self._seed_node(tree.root)

    def _seed_node(self, node: TagNode) -> None:
        for seg, child in node.children.items():
            self._register(seg)
            self._seed_node(child)

    def _register(self, seg: str) -> None:
        if ":" not in seg:
            return
        en, zh = seg.split(":", 1)
        en = self._norm_english(en)
        en = self._alias.get(en, en)
        self._zh_to_en.setdefault(zh, en)
        self._en_to_zh.setdefault(en, zh)

    # ── 归一化 ─────────────────────────────────────────────
    @staticmethod
    def _norm_english(en: str) -> str:
        return en.strip().lower().replace("-", "_").replace(" ", "_")

    def canonicalize_segment(self, seg: str) -> str:
        """把单个 ``english:中文`` 段归一到受控词表。非法段（无冒号）原样返回。"""
        if ":" not in seg:
            return seg
        en, zh = seg.split(":", 1)
        en = self._norm_english(en)
        en = self._alias.get(en, en)
        zh = zh.strip()

        # 中文一致性：同中文优先复用既有 english
        if zh in self._zh_to_en:
            en = self._zh_to_en[zh]
        # english 一致性：同 english 优先复用既有中文
        if en in self._en_to_zh:
            zh = self._en_to_zh[en]

        # 注册（first-seen-wins）
        self._zh_to_en.setdefault(zh, en)
        self._en_to_zh.setdefault(en, zh)
        return f"{en}:{zh}"

    def canonicalize_path(self, segments: list[str]) -> list[str]:
        """归一化一条 tag 路径（保持顺序；跳过空段）。"""
        return [self.canonicalize_segment(s) for s in segments if s and s.strip()]
