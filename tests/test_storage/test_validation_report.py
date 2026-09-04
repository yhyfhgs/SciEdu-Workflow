"""parse validate 体检报告（只读）回归测试"""

from __future__ import annotations

from pathlib import Path

from sciforge.core.models import Discipline, KnowledgePoint, Question
from sciforge.storage.sqlite_backend import SQLiteStorageBackend


def _seed(db: SQLiteStorageBackend) -> None:
    db.save_batch(
        [
            KnowledgePoint(
                id="t1",
                content="定理A",
                kp_type="theorem",
                number="3.1",
                source_file="f.md",
                order_index=0,
                discipline=Discipline.MATH,
            ),
            KnowledgePoint(
                id="c1",
                content="推论A",
                kp_type="corollary",
                number="3.2",
                source_file="f.md",
                order_index=1,
                discipline=Discipline.MATH,
            ),
        ],
        [
            Question(
                id="q1",
                content="计算题",
                question_type="calculation",
                question_form="choice",
                number="3.3",
                solution_content="解：A",
                source_file="f.md",
                order_index=2,
                discipline=Discipline.MATH,
            ),
            Question(
                id="q2",
                content="证明题（无解答）",
                question_type="proof",
                number="3.4",
                solution_content=None,
                source_file="f.md",
                order_index=3,
                discipline=Discipline.MATH,
            ),
        ],
    )


def test_validation_report_core_counts(tmp_path: Path) -> None:
    db = SQLiteStorageBackend(tmp_path / "v.db")
    _seed(db)
    rep = db.validation_report()
    db.close()

    assert rep["kp_total"] == 2
    assert rep["q_total"] == 2
    assert rep["kp_by_type"]["theorem"] == 1
    assert rep["kp_by_type"]["corollary"] == 1
    assert rep["q_by_type"]["calculation"] == 1
    assert rep["q_by_form"]["choice"] == 1  # #5 形式维度
    assert rep["q_with_solution"] == 1
    assert rep["q_missing_solution"] == 1
    # 题号规范化覆盖（"3.1" 等会派生 number_canonical）
    assert rep["kp_canonical_cov"] == 2
    assert rep["dirty_canonical"] == 0  # 全是 "3.x" 干净格式


def test_validation_report_flags_dirty_canonical(tmp_path: Path) -> None:
    db = SQLiteStorageBackend(tmp_path / "dirty.db")
    # 直接构造一个 number_canonical 含中文的脏条目（绕过派生逻辑）
    kp = KnowledgePoint(
        id="d1", content="x", kp_type="theorem", source_file="f.md", discipline=Discipline.MATH
    )
    object.__setattr__(kp, "number_canonical", "第3题")
    db.save_batch([kp], [])
    rep = db.validation_report()
    db.close()
    assert rep["dirty_canonical"] == 1  # GLOB 命中非 [0-9.] 字符


def test_validation_report_no_dup_hash(tmp_path: Path) -> None:
    db = SQLiteStorageBackend(tmp_path / "dup.db")
    _seed(db)
    # 再写一条与 t1 内容相同的 KP → UNIQUE(content_hash) 应忽略，无重复组
    db.save_batch(
        [
            KnowledgePoint(
                id="t1dup",
                content="定理A",
                kp_type="theorem",
                source_file="f.md",
                order_index=9,
                discipline=Discipline.MATH,
            )
        ],
        [],
    )
    rep = db.validation_report()
    db.close()
    assert rep["dup_hash_groups"] == 0  # #8 写入去重生效


def test_validation_report_relations(tmp_path: Path) -> None:
    db = SQLiteStorageBackend(tmp_path / "rel.db")
    _seed(db)
    db.save_relations(
        [
            {
                "src_id": "c1",
                "dst_id": "t1",
                "relation_type": "corollary_of",
                "confidence": 0.9,
                "source": "structural",
                "note": "",
            }
        ],
        "math",
    )
    rep = db.validation_report()
    db.close()
    assert rep["relations_total"] == 1
    assert rep["relations_by_type"]["corollary_of"] == 1


def test_validation_report_exposes_ingestion_governance(tmp_path: Path) -> None:
    db = SQLiteStorageBackend(tmp_path / "ingestion.db")
    db.save_batch(
        [KnowledgePoint(content="可发布知识点", ingestion_status="accepted")],
        [Question(content="待修复题目", ingestion_status="needs_patch")],
    )
    rep = db.validation_report()
    db.close()

    assert rep["kp_by_ingestion_status"] == {"accepted": 1}
    assert rep["q_by_ingestion_status"] == {"needs_patch": 1}
    assert rep["nonaccepted_total"] == 1
