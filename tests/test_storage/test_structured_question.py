"""REC-19 — 选择/填空题结构化字段(options/correct_option/blanks)往返。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.core.models import Discipline, Question
from sciforge.pipeline.llm_schemas import SingleItemExtractLLM
from sciforge.storage.sqlite_backend import SQLiteStorageBackend


@pytest.fixture
def backend(tmp_path: Path):
    be = SQLiteStorageBackend(tmp_path / "q.db")
    yield be
    be.close()


def test_choice_question_options_roundtrip(backend: SQLiteStorageBackend) -> None:
    q = Question(
        content="下列哪个是素数？",
        discipline=Discipline.MATH,
        question_form="choice",
        options=["A. 4", "B. 6", "C. 7", "D. 9"],
        correct_option="C",
    )
    backend.save_batch([], [q])
    got = backend.load_question(q.id)
    assert got.options == ["A. 4", "B. 6", "C. 7", "D. 9"]
    assert got.correct_option == "C"
    assert got.blanks is None


def test_fill_blank_question_blanks_roundtrip(backend: SQLiteStorageBackend) -> None:
    q = Question(
        content="圆周率约等于 ____。",
        discipline=Discipline.MATH,
        question_form="fill_blank",
        blanks=1,
    )
    backend.save_batch([], [q])
    got = backend.load_question(q.id)
    assert got.blanks == 1
    assert got.options == []
    assert got.correct_option is None


def test_default_question_has_empty_struct_fields(backend: SQLiteStorageBackend) -> None:
    q = Question(content="证明 √2 是无理数。", discipline=Discipline.MATH)
    backend.save_batch([], [q])
    got = backend.load_question(q.id)
    assert got.options == []
    assert got.correct_option is None
    assert got.blanks is None


def test_extract_schema_carries_struct_fields() -> None:
    item = SingleItemExtractLLM(
        category="question",
        item_type="exercise",
        question_form="choice",
        options=["A. 1", "B. 2"],
        correct_option="B",
        corrected_content="选择题题面",
        solution_status="no_solution_in_source",
        start_line=1,
        end_line=3,
    )
    assert item.options == ["A. 1", "B. 2"]
    assert item.correct_option == "B"
