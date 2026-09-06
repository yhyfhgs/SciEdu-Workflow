"""REC-18 — 图表落库 + 从条目正文抽取图片链接。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.core.models import Discipline, Figure, Question
from sciforge.pipeline.stages.stage3_persist import _extract_figures
from sciforge.storage.sqlite_backend import SQLiteStorageBackend


@pytest.fixture
def backend(tmp_path: Path):
    be = SQLiteStorageBackend(tmp_path / "f.db")
    yield be
    be.close()


def test_figure_roundtrip(backend: SQLiteStorageBackend) -> None:
    q = Question(content="带图题", discipline=Discipline.MATH)
    backend.save_batch([], [q])
    n = backend.save_figures(
        [
            Figure(item_id=q.id, item_kind="question", image_path="assets/f1.png", caption="图1"),
        ]
    )
    assert n == 1
    figs = backend.figures_for_item(q.id)
    assert len(figs) == 1
    assert figs[0].image_path == "assets/f1.png"
    assert figs[0].caption == "图1"


def test_figure_dedup_idempotent(backend: SQLiteStorageBackend) -> None:
    q = Question(content="带图题", discipline=Discipline.MATH)
    backend.save_batch([], [q])
    f = Figure(item_id=q.id, image_path="assets/f1.png")
    backend.save_figures([f])
    backend.save_figures([f])  # 同 (item_id, image_path) → UNIQUE 幂等
    assert len(backend.figures_for_item(q.id)) == 1


def test_extract_figures_from_markdown() -> None:
    content = "如图 ![受力分析](assets/fig_3.png) 所示，又见 ![](b.jpg)。无图文本。"
    figs = _extract_figures(content, "q1", "question", "paper.pdf")
    assert [f.image_path for f in figs] == ["assets/fig_3.png", "b.jpg"]
    assert figs[0].caption == "受力分析"


def test_extract_figures_empty() -> None:
    assert _extract_figures("纯文本无图", "q1", "question", "x.pdf") == []
