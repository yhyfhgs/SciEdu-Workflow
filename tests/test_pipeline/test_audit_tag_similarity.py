"""测试 audit_tag_similarity 同义异名检测"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from sciforge.pipeline.audit import audit_tag_similarity


def _make_db(tmp_path: Path, kp_rows: list[dict], q_rows: list[dict]) -> Path:
    """用最小 schema 构造临时 DB 写入测试 tags"""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE knowledge_points (id TEXT PRIMARY KEY, tags TEXT DEFAULT '[]')")
    conn.execute("CREATE TABLE questions (id TEXT PRIMARY KEY, tags TEXT DEFAULT '[]')")
    for i, row in enumerate(kp_rows):
        conn.execute(
            "INSERT INTO knowledge_points (id, tags) VALUES (?, ?)",
            (f"k{i}", json.dumps(row.get("tags", []), ensure_ascii=False)),
        )
    for i, row in enumerate(q_rows):
        conn.execute(
            "INSERT INTO questions (id, tags) VALUES (?, ?)",
            (f"q{i}", json.dumps(row.get("tags", []), ensure_ascii=False)),
        )
    conn.commit()
    conn.close()
    return db


class TestAuditTagSimilarity:
    def test_chinese_identical_different_english(self, tmp_path: Path) -> None:
        """matrix:矩阵 vs matrices:矩阵 → 应被检测"""
        db = _make_db(
            tmp_path,
            kp_rows=[{"tags": ["math:数学", "linear_algebra:线性代数", "matrix:矩阵"]}],
            q_rows=[{"tags": ["math:数学", "linear_algebra:线性代数", "matrices:矩阵"]}],
        )
        pairs = audit_tag_similarity(db)
        assert any(
            p.reason == "chinese_identical"
            and {p.seg_a, p.seg_b} == {"matrix:矩阵", "matrices:矩阵"}
            for p in pairs
        )

    def test_english_identical_different_chinese(self, tmp_path: Path) -> None:
        """algebra:代数 vs algebra:高等代数 → 应被检测为严重"""
        db = _make_db(
            tmp_path,
            kp_rows=[{"tags": ["math:数学", "algebra:代数"]}],
            q_rows=[{"tags": ["math:数学", "algebra:高等代数"]}],
        )
        pairs = audit_tag_similarity(db)
        assert any(p.reason == "english_identical_diff_chinese" for p in pairs)

    def test_english_close_chinese_same(self, tmp_path: Path) -> None:
        """counting:计数 vs counts:计数 → 中文相同触发 chinese_identical"""
        db = _make_db(
            tmp_path,
            kp_rows=[{"tags": ["math:数学", "counting:计数"]}],
            q_rows=[{"tags": ["math:数学", "counts:计数"]}],
        )
        pairs = audit_tag_similarity(db)
        # english 距离 counting→counts 是 3（超过 max=2）→ 但中文相同进入 chinese_identical
        assert any(p.reason == "chinese_identical" for p in pairs)

    def test_empty_db_returns_empty(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path, kp_rows=[], q_rows=[])
        assert audit_tag_similarity(db) == []

    def test_no_duplicates_returns_empty(self, tmp_path: Path) -> None:
        db = _make_db(
            tmp_path,
            kp_rows=[{"tags": ["math:数学", "algebra:代数", "groups:群论"]}],
            q_rows=[{"tags": ["math:数学", "analysis:分析", "limits:极限"]}],
        )
        pairs = audit_tag_similarity(db)
        # 所有段都不相似
        assert pairs == []

    def test_count_aggregated(self, tmp_path: Path) -> None:
        """多次出现的段应累加计数"""
        db = _make_db(
            tmp_path,
            kp_rows=[
                {"tags": ["math:数学", "matrix:矩阵"]},
                {"tags": ["math:数学", "matrix:矩阵"]},
                {"tags": ["math:数学", "matrix:矩阵"]},
            ],
            q_rows=[{"tags": ["math:数学", "matrices:矩阵"]}],
        )
        pairs = audit_tag_similarity(db)
        target = next(p for p in pairs if {p.seg_a, p.seg_b} == {"matrix:矩阵", "matrices:矩阵"})
        counts = {target.seg_a: target.count_a, target.seg_b: target.count_b}
        assert counts["matrix:矩阵"] == 3
        assert counts["matrices:矩阵"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
