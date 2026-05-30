from __future__ import annotations

import asyncio
from pathlib import Path

from sciforge.config.settings import AppConfig
from sciforge.core.models import Discipline, DocumentFormat
from sciforge.pipeline import recognizers as recognizer_mod
from sciforge.pipeline.vlm_converter import VlmConverter, _OpenedDocument


class _VisionLLM:
    supports_vision = True


def test_get_recognizer_pdf_falls_back_to_pdftotext_when_fitz_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(recognizer_mod, "_has_fitz", lambda: False)
    monkeypatch.setattr(
        recognizer_mod.shutil,
        "which",
        lambda cmd: "/usr/bin/pdftocairo" if cmd in {"pdftocairo", "pdftoppm"} else None,
    )

    recognizer = recognizer_mod.get_recognizer(
        DocumentFormat.PDF,
        backend="vlm",
        llm_client=_VisionLLM(),
        config=AppConfig(),
        discipline=Discipline.MATH,
    )

    from sciforge.pipeline.recognizers import VlmRecognizer

    assert isinstance(recognizer, VlmRecognizer)


def test_get_recognizer_pdf_uses_pdftotext_without_vlm(monkeypatch) -> None:
    monkeypatch.setattr(recognizer_mod, "_has_fitz", lambda: False)
    monkeypatch.setattr(
        recognizer_mod.shutil,
        "which",
        lambda cmd: "/usr/bin/pdftotext" if cmd == "pdftotext" else None,
    )

    recognizer = recognizer_mod.get_recognizer(
        DocumentFormat.PDF,
        backend="pymupdf",
        llm_client=None,
        config=AppConfig(),
        discipline=Discipline.MATH,
    )

    assert isinstance(recognizer, recognizer_mod.PdfToTextRecognizer)


def test_pdf_page_count_uses_pdfinfo_when_fitz_missing(monkeypatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(recognizer_mod, "_has_fitz", lambda: False)
    monkeypatch.setattr(recognizer_mod.shutil, "which", lambda cmd: "/usr/bin/pdfinfo")

    class _Result:
        returncode = 0
        stdout = "Title: sample\nPages:          12\n"

    monkeypatch.setattr(recognizer_mod.subprocess, "run", lambda *args, **kwargs: _Result())

    assert recognizer_mod.pdf_page_count(pdf_path) == 12


def test_pdftotext_recognizer_batches(monkeypatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    output_dir = tmp_path / "recognize"

    monkeypatch.setattr(recognizer_mod, "pdf_page_count", lambda _: 3)

    recognizer = recognizer_mod.PdfToTextRecognizer(command="/usr/bin/pdftotext")
    monkeypatch.setattr(
        recognizer,
        "_extract_page_range",
        lambda file_path, page_start, page_end: f"pages {page_start}-{page_end}",
    )

    async def _collect() -> list[recognizer_mod.BatchResult]:
        batches: list[recognizer_mod.BatchResult] = []
        async for batch in recognizer.recognize_in_batches(
            pdf_path,
            batch_pages=2,
            output_dir=output_dir,
            already_done=[],
        ):
            batches.append(batch)
        return batches

    batches = asyncio.run(_collect())

    assert [(b.page_start, b.page_end, b.md_text) for b in batches] == [
        (1, 2, "pages 1-2"),
        (3, 3, "pages 3-3"),
    ]
    assert (output_dir / "page_001-002.md").read_text(encoding="utf-8") == "pages 1-2"


def test_vlm_converter_uses_poppler_renderer_without_fitz() -> None:
    converter = VlmConverter.__new__(VlmConverter)
    converter.dpi = 144
    converter._render_pdf_page_with_poppler = lambda pdf_path, page_number: (
        f"png-{page_number}".encode()
    )

    doc = _OpenedDocument(pdf_path=Path("/tmp/fake.pdf"), page_count=2, fitz_doc=None)

    images = converter._render_batch_images(doc, [0, 1])

    assert images == [b"png-1", b"png-2"]


def test_vlm_converter_strips_figure_placeholders_without_fitz() -> None:
    converter = VlmConverter.__new__(VlmConverter)
    doc = _OpenedDocument(pdf_path=Path("/tmp/fake.pdf"), page_count=1, fitz_doc=None)

    md = "before ![示意图]({{FIGURE:10,10,20,20}}) after"
    cleaned = converter._crop_figures_from_md(md, doc, [0], "fake", Path("/tmp/assets"))

    assert cleaned == "before [图：示意图] after"
