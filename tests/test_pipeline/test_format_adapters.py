"""Contract tests for deterministic, structure-preserving format adapters."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from sciforge.pipeline.format_adapters import (
    AdapterLimits,
    DelimitedTextAdapter,
    EpubAdapter,
    HtmlAdapter,
    JsonAdapter,
    XlsxAdapter,
)


def _write_zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def _minimal_xlsx_members(
    *,
    relationship_target: str = "worksheets/sheet1.xml",
) -> dict[str, str]:
    return {
        "xl/workbook.xml": """
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets><sheet name="Scores" sheetId="1" r:id="rId1"/></sheets>
            </workbook>
        """,
        "xl/_rels/workbook.xml.rels": f"""
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
                Target="{relationship_target}"/>
            </Relationships>
        """,
        "xl/sharedStrings.xml": """
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                 count="3" uniqueCount="3">
              <si><t>Name</t></si><si><t>Score</t></si><si><t>Alice</t></si>
            </sst>
        """,
        "xl/worksheets/sheet1.xml": """
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
                <row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><f>20+22</f><v>42</v></c></row>
                <row r="3">
                  <c r="A3" t="inlineStr"><is><t>Bob</t></is></c>
                  <c r="B3" t="b"><v>1</v></c>
                </row>
              </sheetData>
            </worksheet>
        """,
    }


def _minimal_epub_members() -> dict[str, str]:
    return {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": """
            <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
              <rootfiles>
                <rootfile full-path="OEBPS/content.opf"
                  media-type="application/oebps-package+xml"/>
              </rootfiles>
            </container>
        """,
        "OEBPS/content.opf": """
            <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
              <manifest>
                <item id="chapter-one" href="text/chapter1.xhtml"
                  media-type="application/xhtml+xml"/>
              </manifest>
              <spine><itemref idref="chapter-one"/></spine>
            </package>
        """,
        "OEBPS/text/chapter1.xhtml": """
            <html xmlns="http://www.w3.org/1999/xhtml"><body>
              <h1>First Chapter</h1>
              <p>Let <strong>x</strong> equal 2.</p>
              <script>window.evil = true;</script>
            </body></html>
        """,
    }


def test_csv_preserves_quoted_cells_and_chunks_rows(tmp_path: Path) -> None:
    source = tmp_path / "quoted.csv"
    source.write_text(
        "name,note,value\n"
        'Alice,"comma, stays",1\n'
        'Bob,"line one\nline two",2\n'
        'Carol,"pipe | stays",3\n',
        encoding="utf-8",
    )

    blocks = list(DelimitedTextAdapter(AdapterLimits(rows_per_block=2)).to_markdown_blocks(source))

    assert len(blocks) == 2
    assert [block.index for block in blocks] == [0, 1]
    assert blocks[0].source_locator == "quoted.csv:rows=2-3"
    assert "| Alice | comma, stays | 1 |" in blocks[0].markdown
    assert "| Bob | line one<br>line two | 2 |" in blocks[0].markdown
    assert blocks[1].source_locator == "quoted.csv:rows=4-4"
    assert "| Carol | pipe \\| stays | 3 |" in blocks[1].markdown
    assert "| name | note | value |" in blocks[1].markdown


def test_tsv_uses_explicit_tab_delimiter_with_quoted_tabs(tmp_path: Path) -> None:
    source = tmp_path / "quoted.tsv"
    source.write_text('term\tdescription\nalpha\t"one\ttwo"\n', encoding="utf-8")

    [block] = list(
        DelimitedTextAdapter(
            AdapterLimits(),
            delimiter="\t",
        ).to_markdown_blocks(source)
    )

    assert "| term | description |" in block.markdown
    assert "| alpha | one\ttwo |" in block.markdown


def test_html_removes_executable_content_and_preserves_structure(tmp_path: Path) -> None:
    source = tmp_path / "lesson.html"
    source.write_text(
        """
        <html><head><style>.secret {display:none}</style></head><body>
          <h1>Algebra</h1>
          <script>alert('do not retain')</script>
          <noscript>fallback noise</noscript>
          <p>Solve <strong>x + 1 = 2</strong>.</p>
          <table>
            <tr><th>Symbol</th><th>Value</th></tr>
            <tr><td>x</td><td>1</td></tr>
          </table>
        </body></html>
        """,
        encoding="utf-8",
    )

    [block] = list(HtmlAdapter(AdapterLimits()).to_markdown_blocks(source))

    assert "# Algebra" in block.markdown
    assert "**x + 1 = 2**" in block.markdown
    assert "| Symbol | Value |" in block.markdown
    assert "| x | 1 |" in block.markdown
    assert "alert" not in block.markdown
    assert "fallback noise" not in block.markdown
    assert "display:none" not in block.markdown


def test_json_array_of_objects_becomes_chunked_tables(tmp_path: Path) -> None:
    source = tmp_path / "questions.json"
    source.write_text(
        '[{"id": 1, "prompt": "A|B"}, {"id": 2, "prompt": "C"}, {"id": 3, "prompt": "D"}]',
        encoding="utf-8",
    )

    blocks = list(JsonAdapter(AdapterLimits(rows_per_block=2)).to_markdown_blocks(source))

    assert len(blocks) == 2
    assert "| id | prompt |" in blocks[0].markdown
    assert "| 1 | A\\|B |" in blocks[0].markdown
    assert "| 3 | D |" in blocks[1].markdown
    assert blocks[1].source_locator == "questions.json:rows=4-4"


def test_jsonl_chunks_records_and_reports_invalid_line(tmp_path: Path) -> None:
    source = tmp_path / "events.jsonl"
    source.write_text(
        '{"kind":"question","id":1}\n{"kind":"answer","id":2}\n{"kind":"review","id":3}\n',
        encoding="utf-8",
    )

    blocks = list(
        JsonAdapter(AdapterLimits(rows_per_block=2), json_lines=True).to_markdown_blocks(source)
    )

    assert len(blocks) == 2
    assert blocks[0].source_locator == "events.jsonl:records=1-2"
    assert "**kind**: question" in blocks[0].markdown
    assert "**id**: 2" in blocks[0].markdown
    assert blocks[1].source_locator == "events.jsonl:records=3-3"

    source.write_text('{"ok": true}\nnot-json\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="JSONL 第 2 行无效"):
        list(JsonAdapter(AdapterLimits(), json_lines=True).to_markdown_blocks(source))


@pytest.mark.parametrize(
    "relationship_target",
    ["worksheets/sheet1.xml", "/xl/worksheets/sheet1.xml"],
)
def test_minimal_xlsx_preserves_sheet_values_and_formulas(
    tmp_path: Path,
    relationship_target: str,
) -> None:
    source = tmp_path / "scores.xlsx"
    _write_zip(source, _minimal_xlsx_members(relationship_target=relationship_target))

    [block] = list(XlsxAdapter(AdapterLimits()).to_markdown_blocks(source))

    assert block.index == 0
    assert block.title == "Sheet: Scores"
    assert block.source_locator == "scores.xlsx:sheet=Scores:rows=2-3"
    assert "| Name | Score |" in block.markdown
    assert "| Alice | =20+22 → 42 |" in block.markdown
    assert "| Bob | TRUE |" in block.markdown


def test_minimal_epub_follows_spine_and_converts_xhtml(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    _write_zip(source, _minimal_epub_members())

    [block] = list(EpubAdapter(AdapterLimits()).to_markdown_blocks(source))

    assert block.index == 0
    assert block.title == "chapter1"
    assert block.source_locator == "book.epub:chapter=OEBPS/text/chapter1.xhtml"
    assert "# First Chapter" in block.markdown
    assert "Let **x** equal 2." in block.markdown
    assert "window.evil" not in block.markdown


def test_archive_path_traversal_is_rejected_before_parsing(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.xlsx"
    _write_zip(source, {"../outside.xml": "not allowed"})

    with pytest.raises(RuntimeError, match=r"unsafe archive path: \.\./outside\.xml"):
        list(XlsxAdapter(AdapterLimits()).to_markdown_blocks(source))


def test_archive_uncompressed_size_limit_is_enforced(tmp_path: Path) -> None:
    source = tmp_path / "oversized.epub"
    _write_zip(source, {"payload.txt": "x" * 32})

    with pytest.raises(RuntimeError, match=r"archive uncompressed bytes 32 > limit 16"):
        list(EpubAdapter(AdapterLimits(max_archive_bytes=16)).to_markdown_blocks(source))
