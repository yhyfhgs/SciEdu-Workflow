"""Tag 受控词表统一（Phase 5 #6）回归测试"""

from __future__ import annotations

from sciforge.pipeline.tag_canonicalizer import TagCanonicalizer
from sciforge.pipeline.tag_tree import TagTree


def test_english_format_normalized() -> None:
    c = TagCanonicalizer()
    assert c.canonicalize_segment("Group-Theory:群论") == "group_theory:群论"
    assert c.canonicalize_segment("REAL ANALYSIS:实分析") == "real_analysis:实分析"


def test_alias_map_applied() -> None:
    c = TagCanonicalizer({"groups": "group_theory", "ode": "differential_equations"})
    assert c.canonicalize_segment("groups:群论") == "group_theory:群论"
    assert c.canonicalize_segment("ode:常微分方程") == "differential_equations:常微分方程"


def test_chinese_label_consistency() -> None:
    """同中文不同 english → 统一到首见 english。"""
    c = TagCanonicalizer()
    first = c.canonicalize_segment("group_theory:群论")
    second = c.canonicalize_segment("groups_alt:群论")  # 同中文"群论"
    assert first == "group_theory:群论"
    assert second == "group_theory:群论"  # 复用首见 english


def test_english_label_consistency() -> None:
    """同 english 不同中文 → 统一到首见中文。"""
    c = TagCanonicalizer()
    first = c.canonicalize_segment("group_theory:群论")
    second = c.canonicalize_segment("group_theory:群")  # 同 english
    assert first == "group_theory:群论"
    assert second == "group_theory:群论"  # 复用首见中文


def test_canonicalize_path_preserves_order() -> None:
    c = TagCanonicalizer({"groups": "group_theory"})
    path = ["math:数学", "abstract_algebra:抽象代数", "groups:群论"]
    out = c.canonicalize_path(path)
    assert out == ["math:数学", "abstract_algebra:抽象代数", "group_theory:群论"]


def test_seed_from_tree_registers_existing() -> None:
    """已累积 tag_tree 的段成为既有规范，后续变体归并到它。"""
    tree = TagTree()
    tree.insert_path(["math:数学", "abstract_algebra:抽象代数", "group_theory:群论"])
    c = TagCanonicalizer()
    c.seed_from_tree(tree)
    # 新条目用了变体中文写法但同 english → 复用既有中文
    assert c.canonicalize_segment("group_theory:群") == "group_theory:群论"
    # 同中文不同 english → 复用既有 english
    assert c.canonicalize_segment("grp:群论") == "group_theory:群论"


def test_invalid_segment_passthrough() -> None:
    c = TagCanonicalizer()
    assert c.canonicalize_segment("无冒号段") == "无冒号段"
    assert c.canonicalize_path(["", "  ", "math:数学"]) == ["math:数学"]
