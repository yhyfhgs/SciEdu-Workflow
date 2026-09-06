"""REC-29 — load_questions_by_discipline 跨 source 汇聚（题集/答案册分文件联动基础）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.core.models import Discipline, Question
from sciforge.storage.sqlite_backend import SQLiteStorageBackend


@pytest.fixture
def backend(tmp_path: Path):
    be = SQLiteStorageBackend(tmp_path / "d.db")
    yield be
    be.close()


def test_load_by_discipline_spans_sources(backend: SQLiteStorageBackend) -> None:
    backend.save_batch(
        [], [Question(content="题1", discipline=Discipline.MATH, source_file="exercises.pdf")]
    )
    backend.save_batch(
        [], [Question(content="答1", discipline=Discipline.MATH, source_file="answers.pdf")]
    )
    backend.save_batch(
        [], [Question(content="物理题", discipline=Discipline.PHYSICS, source_file="phys.pdf")]
    )

    math_qs = backend.load_questions_by_discipline("math")
    assert len(math_qs) == 2  # 跨两个 source 文件
    sources = {q.source_file for q in math_qs}
    assert sources == {"exercises.pdf", "answers.pdf"}

    all_qs = backend.load_questions_by_discipline(None)
    assert len(all_qs) == 3
