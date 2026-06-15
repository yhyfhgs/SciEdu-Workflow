"""REC-10 — count_existing_content 批量去重计数。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.core.models import Discipline, KnowledgePoint, Question
from sciforge.storage.sqlite_backend import SQLiteStorageBackend


@pytest.fixture
def backend(tmp_path: Path):
    be = SQLiteStorageBackend(tmp_path / "c.db")
    yield be
    be.close()


def test_count_existing_content_questions(backend: SQLiteStorageBackend) -> None:
    backend.save_batch([], [Question(content="题A", discipline=Discipline.MATH)])
    backend.save_batch([], [Question(content="题B", discipline=Discipline.MATH)])
    # 两条已存在 + 一条新 → 已存在计 2
    n = backend.count_existing_content(["题A", "题B", "全新题C"], "question")
    assert n == 2


def test_count_existing_content_knowledge_scope_isolated(backend: SQLiteStorageBackend) -> None:
    backend.save_batch([KnowledgePoint(content="定义X", discipline=Discipline.MATH)], [])
    # 同内容查 question scope → 0（按表隔离）
    assert backend.count_existing_content(["定义X"], "knowledge") == 1
    assert backend.count_existing_content(["定义X"], "question") == 0


def test_count_existing_content_empty(backend: SQLiteStorageBackend) -> None:
    assert backend.count_existing_content([], "question") == 0


def test_count_existing_content_dedupes_input_hashes(backend: SQLiteStorageBackend) -> None:
    backend.save_batch([], [Question(content="重复题", discipline=Discipline.MATH)])
    # 入参重复内容按 hash 去重，只算一次
    assert backend.count_existing_content(["重复题", "重复题"], "question") == 1
