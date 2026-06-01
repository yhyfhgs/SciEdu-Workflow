"""结构化关系推断（Phase 5 #6 知识图谱）回归测试"""

from __future__ import annotations

from pathlib import Path

from sciforge.core.models import Discipline, KnowledgePoint, Question, RelationType
from sciforge.pipeline.relation_inference import infer_structural_relations
from sciforge.storage.sqlite_backend import SQLiteStorageBackend


def _row(id_, kind, type_, oi, subject="real_analysis:实分析", ch=3, sec=2):
    return {
        "id": id_, "kind": kind, "type": type_, "subject": subject,
        "seq_chapter": ch, "seq_section": sec, "order_index": oi,
    }


def test_corollary_of_links_to_preceding_theorem() -> None:
    rows = [
        _row("t1", "kp", "theorem", 0),
        _row("c1", "kp", "corollary", 1),
    ]
    edges = infer_structural_relations(rows)
    cor = [e for e in edges if e["relation_type"] == RelationType.COROLLARY_OF.value]
    assert len(cor) == 1
    assert cor[0]["src_id"] == "c1" and cor[0]["dst_id"] == "t1"


def test_question_tests_preceding_kp() -> None:
    rows = [
        _row("d1", "kp", "definition", 0),
        _row("q1", "question", "calculation", 1),
    ]
    edges = infer_structural_relations(rows)
    tests = [e for e in edges if e["relation_type"] == RelationType.TESTS.value]
    assert len(tests) == 1
    assert tests[0]["src_id"] == "q1" and tests[0]["dst_id"] == "d1"


def test_successor_backbone_between_consecutive_kps() -> None:
    rows = [
        _row("k1", "kp", "definition", 0),
        _row("k2", "kp", "theorem", 1),
    ]
    edges = infer_structural_relations(rows)
    succ = [e for e in edges if e["relation_type"] == RelationType.SUCCESSOR.value]
    assert len(succ) == 1
    assert succ[0]["src_id"] == "k1" and succ[0]["dst_id"] == "k2"


def test_no_cross_section_edges() -> None:
    rows = [
        _row("t1", "kp", "theorem", 0, ch=3, sec=1),
        _row("c1", "kp", "corollary", 1, ch=3, sec=2),  # 不同节
    ]
    edges = infer_structural_relations(rows)
    assert not [e for e in edges if e["relation_type"] == RelationType.COROLLARY_OF.value]


def test_gap_guard_blocks_distant_links() -> None:
    # 同节但 order_index 跨度超过 tests 上限(10)
    rows = [
        _row("d1", "kp", "definition", 0),
        _row("q1", "question", "calculation", 100),
    ]
    edges = infer_structural_relations(rows)
    assert not edges


def test_no_self_loops_and_dedup() -> None:
    rows = [
        _row("t1", "kp", "theorem", 0),
        _row("c1", "kp", "corollary", 1),
        _row("c2", "kp", "corollary", 2),  # 两个 corollary 都指向 t1
    ]
    edges = infer_structural_relations(rows)
    keys = [(e["src_id"], e["dst_id"], e["relation_type"]) for e in edges]
    assert len(keys) == len(set(keys))  # 无重复
    assert all(e["src_id"] != e["dst_id"] for e in edges)


def test_backend_save_load_relations_idempotent(tmp_path: Path) -> None:
    db = SQLiteStorageBackend(tmp_path / "rel.db")
    db.save_batch(
        [KnowledgePoint(id="t1", content="定理", kp_type="theorem",
                        source_file="f.md", order_index=0, discipline=Discipline.MATH)],
        [Question(id="q1", content="题", question_type="calculation",
                  source_file="f.md", order_index=1, discipline=Discipline.MATH)],
    )
    edges = [
        {"src_id": "q1", "dst_id": "t1", "relation_type": "tests",
         "confidence": 0.6, "source": "structural", "note": ""},
    ]
    added1 = db.save_relations(edges, "math")
    added2 = db.save_relations(edges, "math")  # 重复写
    assert added1 == 1
    assert added2 == 0  # UNIQUE → INSERT OR IGNORE 幂等
    assert db.relation_count() == 1
    loaded = db.load_relations("q1")
    assert loaded[0]["dst_id"] == "t1" and loaded[0]["relation_type"] == "tests"
    db.close()


def test_backend_relation_rows_ordered(tmp_path: Path) -> None:
    db = SQLiteStorageBackend(tmp_path / "rows.db")
    db.save_batch(
        [KnowledgePoint(id="k1", content="def", kp_type="definition", number="3.1",
                        source_file="f.md", order_index=0, discipline=Discipline.MATH)],
        [Question(id="q1", content="q", question_type="proof", number="3.2",
                  source_file="f.md", order_index=1, discipline=Discipline.MATH)],
    )
    rows = db.relation_rows("f.md", "math")
    assert [r["id"] for r in rows] == ["k1", "q1"]  # 按 order_index 升序
    assert rows[0]["kind"] == "kp" and rows[1]["kind"] == "question"
    db.close()
