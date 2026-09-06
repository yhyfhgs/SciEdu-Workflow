"""REC-09 — TagTreeSnapshot 只读快照视图"""

from __future__ import annotations

from sciforge.pipeline.tag_tree import TagTree, TagTreeSnapshot


def _seg(branch: str) -> list[str]:
    return ["math:数学", f"{branch}:分支"]


def test_snapshot_reflects_initial_state() -> None:
    live = TagTree()
    live.insert_path(["math:数学", "algebra:代数"])
    snap = TagTreeSnapshot(live, refresh_gap=4)
    out = snap.serialize_for_prompt()
    assert "math:数学" in out
    assert "algebra:代数" in out


def test_snapshot_is_stable_below_refresh_gap() -> None:
    """live 新增 < refresh_gap 时，快照保持不变（不被 Stage3 每条 insert 抖动）。"""
    live = TagTree()
    live.insert_path(["math:数学", "algebra:代数"])
    snap = TagTreeSnapshot(live, refresh_gap=5)
    before = snap.serialize_for_prompt()

    # live 再插 3 条（< refresh_gap=5）
    for branch in ("analysis", "geometry", "topology"):
        live.insert_path(_seg(branch))

    after = snap.serialize_for_prompt()
    assert after == before  # 快照稳定，未刷新
    assert "analysis:分支" not in after


def test_snapshot_refreshes_after_gap() -> None:
    """live 增长达到 refresh_gap 后，快照刷新纳入新分支。"""
    live = TagTree()
    live.insert_path(["math:数学", "algebra:代数"])
    snap = TagTreeSnapshot(live, refresh_gap=3)
    _ = snap.serialize_for_prompt()

    for branch in ("analysis", "geometry", "topology"):  # 3 次 → 触发刷新
        live.insert_path(_seg(branch))

    after = snap.serialize_for_prompt()
    assert "analysis:分支" in after


def test_snapshot_isolated_from_live_mutation() -> None:
    """快照是独立树：刷新前 live 的改动不污染当前快照序列化结果。"""
    live = TagTree()
    live.insert_path(["math:数学", "algebra:代数"])
    snap = TagTreeSnapshot(live, refresh_gap=100)
    out1 = snap.serialize_for_prompt()
    live.insert_path(["physics:物理", "mechanics:力学"])
    out2 = snap.serialize_for_prompt()
    assert out1 == out2  # 未达 gap，快照不含 physics
    assert "physics:物理" not in out2


def test_bare_tagtree_satisfies_readable() -> None:
    """裸 TagTree 也满足 Stage2 注入所需的只读接口（测试/独立调用路径）。"""
    live = TagTree()
    live.insert_path(["math:数学", "algebra:代数"])
    # 直接当 readable 用
    assert "algebra:代数" in live.serialize_for_prompt()
