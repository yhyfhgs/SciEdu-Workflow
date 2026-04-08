"""MinerU 处理器 — PDF/DOCX 到 Markdown 的高精度转换后端

通过 MinerU 的 do_parse API 将文档转为 Markdown，再经 md_parser 解析为 MdBlock 列表。
支持 checkpoint 缓存，避免重复处理。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from pathlib import Path

import structlog

from mathforge.core.exceptions import DocxParseError, PdfParseError
from mathforge.core.models import ImageAsset, MdBlock

logger = structlog.get_logger()


def _is_mineru_available() -> bool:
    """检查 mineru 包是否可用"""
    try:
        import mineru.cli.common  # type: ignore[import-not-found]  # noqa: F401

        return True
    except ImportError:
        return False


def _compute_file_hash(file_path: Path) -> str:
    """计算文件哈希（前 1MB + 文件大小）"""
    h = hashlib.sha256()
    size = file_path.stat().st_size
    h.update(str(size).encode())
    with open(file_path, "rb") as f:
        h.update(f.read(1024 * 1024))
    return h.hexdigest()[:16]


def _make_parse_error(filepath: Path, message: str) -> PdfParseError | DocxParseError:
    """根据文件后缀创建对应的解析异常"""
    if filepath.suffix.lower() == ".docx":
        return DocxParseError(message)
    return PdfParseError(message)


def _rewrite_image_paths(md_text: str, image_dir: Path) -> tuple[str, list[ImageAsset]]:
    """将 Markdown 中的相对图片路径改写为绝对路径，并收集 ImageAsset 列表"""
    assets: list[ImageAsset] = []
    pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

    def _replace(m: re.Match[str]) -> str:
        alt_text = m.group(1)
        rel_path = m.group(2)
        abs_path = image_dir / rel_path
        if abs_path.exists():
            assets.append(
                ImageAsset(
                    file_path=str(abs_path),
                    description=alt_text,
                )
            )
            return f"![{alt_text}]({abs_path})"
        return m.group(0)

    rewritten = pattern.sub(_replace, md_text)
    return rewritten, assets


def process_with_mineru(
    filepath: Path,
    *,
    backend: str = "pipeline",
    parse_method: str = "auto",
    language: str = "ch",
    formula_enable: bool = True,
    table_enable: bool = True,
    start_page_id: int = 0,
    end_page_id: int | None = None,
    checkpoint_dir: Path | None = None,
) -> list[MdBlock]:
    """通过 MinerU 解析 PDF/DOCX 文件，返回 MdBlock 列表

    Args:
        filepath: 输入文件路径
        backend: MinerU 后端 (pipeline / vlm-auto-engine / hybrid-auto-engine)
        parse_method: 解析方式 (auto / txt / ocr)
        language: OCR 语言代码
        formula_enable: 是否启用公式提取
        table_enable: 是否启用表格提取
        start_page_id: 起始页码（0-indexed）
        end_page_id: 结束页码（None 表示全部）
        checkpoint_dir: checkpoint 目录，用于缓存结果
    """
    try:
        from mineru.cli.common import do_parse, read_fn  # noqa: F811
    except ImportError:
        raise _make_parse_error(
            filepath,
            f'{filepath}: 需要安装 mineru: uv pip install -U "mineru[all]"',
        )

    from mathforge.pipeline.md_parser import parse_file as parse_md_content

    file_hash = _compute_file_hash(filepath)

    # 检查 checkpoint 缓存
    if checkpoint_dir is not None:
        cache_file = checkpoint_dir / file_hash / "mineru_output.md"
        if cache_file.exists():
            md_text = cache_file.read_text(encoding="utf-8")
            if md_text.strip():
                logger.info("mineru_cache_hit", file=filepath.name, hash=file_hash)
                image_dir = cache_file.parent / "mineru"
                md_text, _ = _rewrite_image_paths(md_text, image_dir)
                return parse_md_content(filepath, content=md_text, source_type="textbook")

    # 准备输出目录
    if checkpoint_dir is None:
        tmp_dir_str = tempfile.mkdtemp(prefix="mathforge-mineru-")
        output_dir = Path(tmp_dir_str)
        use_temp = True
    else:
        output_dir = checkpoint_dir / file_hash / "mineru"
        output_dir.mkdir(parents=True, exist_ok=True)
        use_temp = False

    try:
        # 读取文件
        pdf_bytes: bytes = read_fn(str(filepath))

        logger.info(
            "mineru_parse_start",
            file=filepath.name,
            backend=backend,
            method=parse_method,
            language=language,
        )

        # 调用 MinerU
        do_parse(
            output_dir=str(output_dir),
            pdf_file_names=[filepath.stem],
            pdf_bytes_list=[pdf_bytes],
            p_lang_list=[language],
            backend=backend,
            parse_method=parse_method,
            formula_enable=formula_enable,
            table_enable=table_enable,
            f_dump_md=True,
            f_dump_middle_json=False,
            f_dump_model_output=False,
            f_dump_orig_pdf=False,
            f_dump_content_list=False,
            f_draw_layout_bbox=False,
            f_draw_span_bbox=False,
            start_page_id=start_page_id,
            end_page_id=end_page_id,
        )

        # 定位输出 Markdown 文件
        # MinerU 输出结构: output_dir/{stem}/{method}/{stem}.md
        md_file = output_dir / filepath.stem / parse_method / f"{filepath.stem}.md"
        if not md_file.exists():
            # fallback: glob 查找
            md_files = list(output_dir.rglob("*.md"))
            if md_files:
                md_file = md_files[0]
            else:
                raise _make_parse_error(filepath, f"{filepath}: MinerU 未产出 Markdown 文件")

        md_text = md_file.read_text(encoding="utf-8")

        if not md_text.strip():
            logger.warning("mineru_empty_output", file=filepath.name)
            return []

        # 处理图片引用
        image_dir = md_file.parent
        md_text, assets = _rewrite_image_paths(md_text, image_dir)

        logger.info(
            "mineru_parse_complete",
            file=filepath.name,
            md_lines=len(md_text.splitlines()),
            images=len(assets),
        )

        # 缓存结果
        if checkpoint_dir is not None:
            cache_file = checkpoint_dir / file_hash / "mineru_output.md"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(md_text, encoding="utf-8")

        # 解析为 MdBlock
        return parse_md_content(filepath, content=md_text, source_type="textbook")

    except (PdfParseError, DocxParseError):
        raise
    except Exception as e:
        raise _make_parse_error(filepath, f"{filepath}: MinerU 处理失败: {e}") from e
    finally:
        if use_temp:
            shutil.rmtree(output_dir, ignore_errors=True)
