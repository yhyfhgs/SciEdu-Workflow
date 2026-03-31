"""Word (.docx) 处理器 — 优先 pandoc，降级 python-docx"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import structlog

from mathforge.core.exceptions import DocxParseError
from mathforge.core.models import MdBlock
from mathforge.pipeline.md_parser import parse_file as parse_md_content

logger = structlog.get_logger()


def _has_pandoc() -> bool:
    return shutil.which("pandoc") is not None


def process_docx_pandoc(filepath: Path) -> str:
    """使用 pandoc 将 .docx 转换为 Markdown+LaTeX"""
    if not _has_pandoc():
        raise DocxParseError("pandoc 未安装。请执行: brew install pandoc")

    result = subprocess.run(
        [
            "pandoc",
            "-f", "docx",
            "-t", "markdown+tex_math_dollars",
            "--wrap=none",
            str(filepath),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        raise DocxParseError(f"pandoc 转换失败: {result.stderr[:500]}")

    return result.stdout


def process_docx_python(filepath: Path) -> str:
    """使用 python-docx 提取内容（降级方案，不支持数学公式转换）"""
    try:
        from docx import Document
    except ImportError:
        raise DocxParseError("python-docx 未安装。请执行: uv pip install python-docx")

    doc = Document(str(filepath))
    lines: list[str] = []

    for para in doc.paragraphs:
        style = para.style.name if para.style else ""
        text = para.text.strip()
        if not text:
            lines.append("")
            continue

        # 标题映射
        if "Heading 1" in style or "标题 1" in style:
            lines.append(f"# {text}")
        elif "Heading 2" in style or "标题 2" in style:
            lines.append(f"## {text}")
        elif "Heading 3" in style or "标题 3" in style:
            lines.append(f"### {text}")
        else:
            lines.append(text)

    return "\n".join(lines)


def process_docx(filepath: Path, backend: str = "auto") -> list[MdBlock]:
    """处理 .docx 文件，返回解析后的块"""
    if not filepath.exists():
        raise DocxParseError(f"文件不存在: {filepath}")
    if not filepath.suffix.lower() == ".docx":
        raise DocxParseError(f"不是 .docx 文件: {filepath}")

    # 选择后端
    if backend == "auto":
        backend = "pandoc" if _has_pandoc() else "python-docx"

    if backend == "pandoc":
        md_content = process_docx_pandoc(filepath)
    else:
        md_content = process_docx_python(filepath)

    logger.info(
        "docx_converted",
        file=filepath.name,
        backend=backend,
        content_length=len(md_content),
    )

    # 将转换后的 Markdown 交给 MD 解析器
    blocks = parse_md_content(filepath, content=md_content, source_type="textbook")
    return blocks
