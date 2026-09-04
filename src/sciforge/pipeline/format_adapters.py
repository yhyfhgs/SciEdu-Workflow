"""M7 通用格式适配器：保留原生结构，确定性转 Markdown batch。

适配器只承担“格式 → 可追溯 Markdown”的职责，不做语义提取，也不调用 LLM。
CSV/TSV、JSON/JSONL、HTML、XLSX、EPUB、TXT/RST 最终都复用现有
``MarkdownRecognizer → Stage1–3`` 主线。

XLSX/EPUB 使用标准库 ZIP/XML 读取，并在打开前执行条目数、总解压体积和路径门禁；
这既减少可选依赖，也避免把不可信文档直接交给外部转换程序。
"""

from __future__ import annotations

import csv
import io
import json
import posixpath
import re
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol
from xml.etree import ElementTree as ET

from sciforge.core.models import DocumentFormat

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig


@dataclass(frozen=True)
class AdaptedBlock:
    """一个可独立 checkpoint 的 Markdown 逻辑块。"""

    index: int
    title: str
    markdown: str
    source_locator: str = ""


@dataclass(frozen=True)
class AdapterLimits:
    rows_per_block: int = 250
    max_input_bytes: int = 64 * 1024 * 1024
    max_archive_bytes: int = 256 * 1024 * 1024
    max_archive_entries: int = 10_000
    max_compression_ratio: float = 500.0
    max_columns: int = 200
    max_json_depth: int = 10

    @classmethod
    def from_config(cls, config: AppConfig) -> AdapterLimits:
        return cls(
            rows_per_block=max(1, int(getattr(config, "adapter_rows_per_block", 250))),
            max_input_bytes=max(1024, int(getattr(config, "adapter_max_input_bytes", 64 << 20))),
            max_archive_bytes=max(
                1024, int(getattr(config, "adapter_max_archive_bytes", 256 << 20))
            ),
            max_archive_entries=max(1, int(getattr(config, "adapter_max_archive_entries", 10_000))),
            max_compression_ratio=max(
                1.0, float(getattr(config, "adapter_max_compression_ratio", 500.0))
            ),
            max_columns=max(1, int(getattr(config, "adapter_max_columns", 200))),
            max_json_depth=max(1, int(getattr(config, "adapter_max_json_depth", 10))),
        )


class FormatAdapter(Protocol):
    formats: frozenset[DocumentFormat]

    def to_markdown_blocks(self, file_path: Path) -> Iterator[AdaptedBlock]: ...


def _read_limited_bytes(path: Path, limit: int) -> bytes:
    size = path.stat().st_size
    if size > limit:
        raise RuntimeError(f"输入文件过大: {size} bytes > adapter limit {limit}")
    return path.read_bytes()


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _escape_cell(value: object) -> str:
    text = str(value if value is not None else "")
    return text.replace("\r", " ").replace("\n", "<br>").replace("|", "\\|").strip()


def _normalize_rows(rows: Sequence[Sequence[object]], max_columns: int) -> list[list[str]]:
    if not rows:
        return []
    width = min(max(len(row) for row in rows), max_columns)
    width = max(1, width)
    return [
        [_escape_cell(row[index]) if index < len(row) else "" for index in range(width)]
        for row in rows
    ]


def _render_normalized_table(normalized: list[list[str]], *, fallback_title: str = "Column") -> str:
    if not normalized:
        return "_(empty table)_"
    header = normalized[0]
    if not any(header):
        header = [f"{fallback_title} {index + 1}" for index in range(len(header))]
    body = normalized[1:]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _render_table(rows: Sequence[Sequence[object]], *, fallback_title: str = "Column") -> str:
    normalized = _normalize_rows(rows, max_columns=max(1, max(map(len, rows), default=1)))
    return _render_normalized_table(normalized, fallback_title=fallback_title)


def _table_blocks(
    rows: Sequence[Sequence[object]],
    *,
    title: str,
    locator_prefix: str,
    limits: AdapterLimits,
) -> Iterator[AdaptedBlock]:
    normalized = _normalize_rows(rows, limits.max_columns)
    if not normalized:
        yield AdaptedBlock(0, title, f"# {title}\n\n_(empty)_", locator_prefix)
        return
    header = normalized[0]
    data = normalized[1:]
    chunks = [
        data[index : index + limits.rows_per_block]
        for index in range(0, len(data), limits.rows_per_block)
    ]
    if not chunks:
        chunks = [[]]
    for index, chunk in enumerate(chunks):
        row_start = 2 + index * limits.rows_per_block
        row_end = row_start + len(chunk) - 1
        locator = f"{locator_prefix}:rows={row_start}-{max(row_start, row_end)}"
        heading = title if len(chunks) == 1 else f"{title} · rows {row_start}-{row_end}"
        markdown = (
            f"# {heading}\n\n<!-- source: {locator} -->\n\n"
            f"{_render_normalized_table([header, *chunk])}"
        )
        yield AdaptedBlock(index, heading, markdown, locator)


class DelimitedTextAdapter:
    formats = frozenset({DocumentFormat.CSV, DocumentFormat.TSV})

    def __init__(self, limits: AdapterLimits, *, delimiter: str | None = None) -> None:
        self.limits = limits
        self.delimiter = delimiter

    def to_markdown_blocks(self, file_path: Path) -> Iterator[AdaptedBlock]:
        text = _decode_text(_read_limited_bytes(file_path, self.limits.max_input_bytes))
        delimiter = self.delimiter
        if delimiter is None:
            try:
                dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;|")
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = "\t" if file_path.suffix.lower() == ".tsv" else ","
        rows = [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
        yield from _table_blocks(
            rows,
            title=f"Table: {file_path.stem}",
            locator_prefix=file_path.name,
            limits=self.limits,
        )


class PlainTextAdapter:
    formats = frozenset({DocumentFormat.TEXT})

    def __init__(self, limits: AdapterLimits) -> None:
        self.limits = limits

    def to_markdown_blocks(self, file_path: Path) -> Iterator[AdaptedBlock]:
        text = _decode_text(_read_limited_bytes(file_path, self.limits.max_input_bytes))
        lines = text.splitlines()
        block_lines = max(200, self.limits.rows_per_block * 4)
        for index, start in enumerate(range(0, max(1, len(lines)), block_lines)):
            chunk = lines[start : start + block_lines]
            locator = f"{file_path.name}:lines={start + 1}-{start + len(chunk)}"
            heading = file_path.stem if index == 0 else f"{file_path.stem} · part {index + 1}"
            yield AdaptedBlock(
                index,
                heading,
                f"# {heading}\n\n<!-- source: {locator} -->\n\n" + "\n".join(chunk),
                locator,
            )


def _json_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _render_json_node(value: object, *, depth: int, max_depth: int) -> list[str]:
    if depth > max_depth:
        return ["- `…`: depth limit reached"]
    if isinstance(value, dict):
        lines: list[str] = []
        for key, child in value.items():
            label = str(key)
            if isinstance(child, (dict, list)):
                lines.append(f"{'#' * min(6, depth + 2)} {label}")
                lines.extend(_render_json_node(child, depth=depth + 1, max_depth=max_depth))
            else:
                lines.append(f"- **{label}**: {_json_scalar(child)}")
        return lines
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            keys: list[str] = []
            for item in value:
                for key in item:
                    if str(key) not in keys:
                        keys.append(str(key))
            rows: list[Sequence[object]] = [keys]
            rows.extend([[item.get(key, "") for key in keys] for item in value])
            return [_render_table(rows)]
        lines = []
        for index, child in enumerate(value):
            if isinstance(child, (dict, list)):
                lines.append(f"{'#' * min(6, depth + 2)} Item {index + 1}")
                lines.extend(_render_json_node(child, depth=depth + 1, max_depth=max_depth))
            else:
                lines.append(f"- {_json_scalar(child)}")
        return lines
    return [_json_scalar(value)]


class JsonAdapter:
    formats = frozenset({DocumentFormat.JSON, DocumentFormat.JSONL})

    def __init__(self, limits: AdapterLimits, *, json_lines: bool = False) -> None:
        self.limits = limits
        self.json_lines = json_lines

    def to_markdown_blocks(self, file_path: Path) -> Iterator[AdaptedBlock]:
        text = _decode_text(_read_limited_bytes(file_path, self.limits.max_input_bytes))
        if self.json_lines or file_path.suffix.lower() in {".jsonl", ".ndjson"}:
            values: list[object] = []
            for line_no, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    values.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"JSONL 第 {line_no} 行无效: {exc}") from exc
            for index, start in enumerate(range(0, len(values), self.limits.rows_per_block)):
                chunk = values[start : start + self.limits.rows_per_block]
                locator = f"{file_path.name}:records={start + 1}-{start + len(chunk)}"
                body = "\n\n".join(
                    _render_json_node(item, depth=0, max_depth=self.limits.max_json_depth)[0]
                    if not isinstance(item, dict)
                    else "\n".join(
                        _render_json_node(item, depth=0, max_depth=self.limits.max_json_depth)
                    )
                    for item in chunk
                )
                yield AdaptedBlock(
                    index,
                    f"JSON records {start + 1}-{start + len(chunk)}",
                    f"# JSON records\n\n<!-- source: {locator} -->\n\n{body}",
                    locator,
                )
            return

        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"JSON 无效: {exc}") from exc
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            keys: list[str] = []
            for item in value:
                for key in item:
                    if str(key) not in keys:
                        keys.append(str(key))
            rows: list[Sequence[object]] = [keys]
            rows.extend([[item.get(key, "") for key in keys] for item in value])
            yield from _table_blocks(
                rows,
                title=f"JSON: {file_path.stem}",
                locator_prefix=file_path.name,
                limits=self.limits,
            )
            return
        body = "\n".join(_render_json_node(value, depth=0, max_depth=self.limits.max_json_depth))
        yield AdaptedBlock(0, file_path.stem, f"# {file_path.stem}\n\n{body}", file_path.name)


class _HTMLToMarkdown(HTMLParser):
    """小型结构保留解析器；不执行脚本，不下载外部资源。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.table_rows: list[list[str]] | None = None
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None
        self.link_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag in {"script", "style", "noscript", "template"}:
            script_type = (attrs_map.get("type") or "").lower()
            if tag != "script" or "math/tex" not in script_type:
                self.skip_depth += 1
                return
        if self.skip_depth:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n" + "#" * int(tag[1]) + " ")
        elif tag in {"p", "div", "section", "article", "header", "footer"}:
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "code":
            self.parts.append("`")
        elif tag == "pre":
            self.parts.append("\n```\n")
        elif tag == "a":
            self.link_href = attrs_map.get("href") or ""
        elif tag == "img":
            alt = attrs_map.get("alt") or "image"
            src = attrs_map.get("src") or ""
            self.parts.append(f"![{alt}]({src})")
        elif tag == "table":
            self.table_rows = []
        elif tag == "tr" and self.table_rows is not None:
            self.current_row = []
        elif tag in {"th", "td"} and self.current_row is not None:
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "code":
            self.parts.append("`")
        elif tag == "pre":
            self.parts.append("\n```\n")
        elif tag == "a" and self.link_href:
            self.parts.append(f" ({self.link_href})")
            self.link_href = ""
        elif tag in {"th", "td"} and self.current_cell is not None:
            assert self.current_row is not None
            self.current_row.append(" ".join("".join(self.current_cell).split()))
            self.current_cell = None
        elif tag == "tr" and self.current_row is not None:
            assert self.table_rows is not None
            self.table_rows.append(self.current_row)
            self.current_row = None
        elif tag == "table" and self.table_rows is not None:
            self.parts.append("\n\n" + _render_table(self.table_rows) + "\n\n")
            self.table_rows = None

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        target = self.current_cell if self.current_cell is not None else self.parts
        target.append(unescape(data))

    def markdown(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_markdown(text: str) -> str:
    parser = _HTMLToMarkdown()
    parser.feed(text)
    parser.close()
    return parser.markdown()


class HtmlAdapter:
    formats = frozenset({DocumentFormat.HTML})

    def __init__(self, limits: AdapterLimits) -> None:
        self.limits = limits

    def to_markdown_blocks(self, file_path: Path) -> Iterator[AdaptedBlock]:
        text = _decode_text(_read_limited_bytes(file_path, self.limits.max_input_bytes))
        markdown = html_to_markdown(text)
        yield AdaptedBlock(0, file_path.stem, markdown, file_path.name)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _safe_zip(path: Path, limits: AdapterLimits) -> zipfile.ZipFile:
    if path.stat().st_size > limits.max_input_bytes:
        raise RuntimeError(
            f"archive input bytes {path.stat().st_size} > limit {limits.max_input_bytes}"
        )
    archive = zipfile.ZipFile(path)
    infos = archive.infolist()
    if len(infos) > limits.max_archive_entries:
        archive.close()
        raise RuntimeError(f"archive entries {len(infos)} > limit {limits.max_archive_entries}")
    total = 0
    for info in infos:
        pure = PurePosixPath(info.filename)
        if pure.is_absolute() or ".." in pure.parts:
            archive.close()
            raise RuntimeError(f"unsafe archive path: {info.filename}")
        if (
            info.file_size > 0
            and info.compress_size > 0
            and info.file_size / info.compress_size > limits.max_compression_ratio
        ):
            archive.close()
            raise RuntimeError(
                f"archive compression ratio for {info.filename} exceeds "
                f"{limits.max_compression_ratio:g}"
            )
        total += info.file_size
        if total > limits.max_archive_bytes:
            archive.close()
            raise RuntimeError(
                f"archive uncompressed bytes {total} > limit {limits.max_archive_bytes}"
            )
    return archive


def _column_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Za-z]+", cell_ref)
    if letters is None:
        return 0
    value = 0
    for char in letters.group().upper():
        value = value * 26 + (ord(char) - ord("A") + 1)
    return max(0, value - 1)


class XlsxAdapter:
    formats = frozenset({DocumentFormat.XLSX})

    def __init__(self, limits: AdapterLimits) -> None:
        self.limits = limits

    @staticmethod
    def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        return [
            "".join(node.text or "" for node in item.iter() if _local_name(node.tag) == "t")
            for item in root
            if _local_name(item.tag) == "si"
        ]

    @staticmethod
    def _sheet_paths(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {
            rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
            for rel in rels
            if _local_name(rel.tag) == "Relationship"
        }
        rel_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        result: list[tuple[str, str]] = []
        for sheet in workbook.iter():
            if _local_name(sheet.tag) != "sheet":
                continue
            target = rel_map.get(sheet.attrib.get(rel_attr, ""), "")
            if not target:
                continue
            # OPC absolute part names begin with '/'; relative targets are resolved
            # against the workbook part's parent (``xl/``).
            normalized = (
                posixpath.normpath(target.lstrip("/"))
                if target.startswith("/")
                else posixpath.normpath(posixpath.join("xl", target))
            )
            if normalized == ".." or normalized.startswith("../"):
                raise RuntimeError(f"unsafe worksheet relationship target: {target}")
            result.append((sheet.attrib.get("name", "Sheet"), normalized))
        return result

    def _sheet_rows(
        self,
        archive: zipfile.ZipFile,
        path: str,
        shared: list[str],
    ) -> list[list[object]]:
        rows: list[list[object]] = []
        for event, element in ET.iterparse(io.BytesIO(archive.read(path)), events=("end",)):
            if event != "end" or _local_name(element.tag) != "row":
                continue
            cells: dict[int, str] = {}
            for cell in element:
                if _local_name(cell.tag) != "c":
                    continue
                column = _column_index(cell.attrib.get("r", ""))
                if column >= self.limits.max_columns:
                    continue
                cell_type = cell.attrib.get("t", "")
                value_node = next((n for n in cell if _local_name(n.tag) == "v"), None)
                formula_node = next((n for n in cell if _local_name(n.tag) == "f"), None)
                inline = "".join(
                    node.text or "" for node in cell.iter() if _local_name(node.tag) == "t"
                )
                raw = value_node.text if value_node is not None and value_node.text else ""
                if cell_type == "s" and raw:
                    try:
                        value = shared[int(raw)]
                    except (ValueError, IndexError):
                        value = raw
                elif cell_type == "inlineStr":
                    value = inline
                elif cell_type == "b":
                    value = "TRUE" if raw == "1" else "FALSE"
                else:
                    value = raw
                if formula_node is not None and formula_node.text:
                    value = f"={formula_node.text}" + (f" → {value}" if value else "")
                cells[column] = value
            if cells:
                width = min(max(cells) + 1, self.limits.max_columns)
                rows.append([cells.get(index, "") for index in range(width)])
            element.clear()
        return rows

    def to_markdown_blocks(self, file_path: Path) -> Iterator[AdaptedBlock]:
        with _safe_zip(file_path, self.limits) as archive:
            shared = self._shared_strings(archive)
            block_index = 0
            for sheet_name, sheet_path in self._sheet_paths(archive):
                rows = self._sheet_rows(archive, sheet_path, shared)
                for block in _table_blocks(
                    rows,
                    title=f"Sheet: {sheet_name}",
                    locator_prefix=f"{file_path.name}:sheet={sheet_name}",
                    limits=self.limits,
                ):
                    yield AdaptedBlock(
                        block_index,
                        block.title,
                        block.markdown,
                        block.source_locator,
                    )
                    block_index += 1


class EpubAdapter:
    formats = frozenset({DocumentFormat.EPUB})

    def __init__(self, limits: AdapterLimits) -> None:
        self.limits = limits

    @staticmethod
    def _rootfile(archive: zipfile.ZipFile) -> str:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        for node in container.iter():
            if _local_name(node.tag) == "rootfile" and node.attrib.get("full-path"):
                return node.attrib["full-path"]
        raise RuntimeError("EPUB container 未声明 rootfile")

    def to_markdown_blocks(self, file_path: Path) -> Iterator[AdaptedBlock]:
        with _safe_zip(file_path, self.limits) as archive:
            rootfile = self._rootfile(archive)
            package = ET.fromstring(archive.read(rootfile))
            base = posixpath.dirname(rootfile)
            manifest: dict[str, tuple[str, str]] = {}
            spine: list[str] = []
            for node in package.iter():
                name = _local_name(node.tag)
                if name == "item":
                    manifest[node.attrib.get("id", "")] = (
                        node.attrib.get("href", ""),
                        node.attrib.get("media-type", ""),
                    )
                elif name == "itemref" and node.attrib.get("idref"):
                    spine.append(node.attrib["idref"])
            if not spine:
                spine = [
                    item_id
                    for item_id, (_href, media) in manifest.items()
                    if media in {"application/xhtml+xml", "text/html"}
                ]
            block_index = 0
            for item_id in spine:
                href, media = manifest.get(item_id, ("", ""))
                if not href or media not in {"application/xhtml+xml", "text/html"}:
                    continue
                chapter_path = posixpath.normpath(posixpath.join(base, href))
                if chapter_path not in archive.namelist():
                    continue
                html = _decode_text(archive.read(chapter_path))
                markdown = html_to_markdown(html)
                locator = f"{file_path.name}:chapter={chapter_path}"
                yield AdaptedBlock(
                    block_index,
                    PurePosixPath(href).stem,
                    f"<!-- source: {locator} -->\n\n{markdown}",
                    locator,
                )
                block_index += 1


def get_format_adapter(fmt: DocumentFormat, config: AppConfig) -> FormatAdapter | None:
    limits = AdapterLimits.from_config(config)
    if fmt == DocumentFormat.CSV:
        return DelimitedTextAdapter(limits)
    if fmt == DocumentFormat.TSV:
        return DelimitedTextAdapter(limits, delimiter="\t")
    if fmt == DocumentFormat.JSON:
        return JsonAdapter(limits)
    if fmt == DocumentFormat.JSONL:
        return JsonAdapter(limits, json_lines=True)
    if fmt == DocumentFormat.HTML:
        return HtmlAdapter(limits)
    if fmt == DocumentFormat.XLSX:
        return XlsxAdapter(limits)
    if fmt == DocumentFormat.EPUB:
        return EpubAdapter(limits)
    if fmt == DocumentFormat.TEXT:
        return PlainTextAdapter(limits)
    return None
