"""VLM 转换器单元测试"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mathforge.pipeline.vlm_converter import VlmConverter


@pytest.fixture
def mock_llm():
    """模拟支持 vision 的 LLM client"""
    client = MagicMock()
    client.supports_vision = True
    client.generate_with_image = AsyncMock(return_value="# Page content\n\nSome math $x^2$")
    client.config = MagicMock(provider=MagicMock(value="anthropic"))
    return client


@pytest.fixture
def converter(mock_llm, tmp_path):
    return VlmConverter(
        llm_client=mock_llm,
        checkpoint_dir=tmp_path / "checkpoints",
        dpi=72,
        max_concurrent=1,
    )


# ── 页面合并 ─────────────────────────────────────────


class TestMergePages:
    def test_merge_single_page(self, converter):
        result = converter._merge_pages({0: "# Title\n\nContent"}, total_pages=1)
        assert "# Title" in result
        assert "Content" in result

    def test_merge_multiple_pages(self, converter):
        pages = {
            0: "# Chapter 1\n\nFirst page",
            1: "Continuation of chapter 1",
            2: "# Chapter 2\n\nNew chapter",
        }
        result = converter._merge_pages(pages, total_pages=3)
        assert "# Chapter 1" in result
        assert "<!-- page 2 -->" in result
        assert "<!-- page 3 -->" in result

    def test_merge_skips_empty_pages(self, converter):
        pages = {0: "Content", 1: "", 2: "More content"}
        result = converter._merge_pages(pages, total_pages=3)
        assert "Content" in result
        assert "More content" in result


# ── 边界去重 ─────────────────────────────────────────


class TestDeduplicateBoundary:
    def test_no_overlap(self):
        prev = "line1\nline2\nline3\nline4"
        curr = "line5\nline6\nline7"
        result = VlmConverter._deduplicate_boundary(prev, curr)
        assert result == curr

    def test_with_overlap(self):
        prev = "line1\nline2\nline3\nline4"
        curr = "line2\nline3\nline4\nline5\nline6"
        result = VlmConverter._deduplicate_boundary(prev, curr)
        assert "line5" in result
        assert "line6" in result
        # 重复的 line2-4 应被移除
        lines = result.strip().splitlines()
        assert "line2" not in lines or "line4" not in lines

    def test_short_text_no_dedup(self):
        prev = "a\nb"
        curr = "a\nb\nc"
        result = VlmConverter._deduplicate_boundary(prev, curr)
        assert result == curr


# ── Checkpoint ────────────────────────────────────────


class TestCheckpoint:
    def test_save_and_load_checkpoint(self, converter, tmp_path):
        vlm_dir = tmp_path / "checkpoints" / "testhash" / "vlm"
        converter._save_page_checkpoint(vlm_dir, 0, "# Page 1", 3)
        converter._save_page_checkpoint(vlm_dir, 1, "# Page 2", 3)

        completed = converter._load_completed_pages(vlm_dir)
        assert 0 in completed
        assert 1 in completed
        assert completed[0] == "# Page 1"
        assert completed[1] == "# Page 2"

        # progress.json 应包含正确信息
        progress = json.loads((vlm_dir / "progress.json").read_text())
        assert progress["total_pages"] == 3
        assert progress["completed_pages"] == [0, 1]

    def test_load_empty_checkpoint(self, converter, tmp_path):
        vlm_dir = tmp_path / "nonexistent"
        completed = converter._load_completed_pages(vlm_dir)
        assert completed == {}


# ── 文件哈希 ─────────────────────────────────────────


class TestFileHash:
    def test_deterministic_hash(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"hello world")
        h1 = VlmConverter._compute_file_hash(f)
        h2 = VlmConverter._compute_file_hash(f)
        assert h1 == h2
        assert len(h1) == 16

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.write_bytes(b"content A")
        f2.write_bytes(b"content B")
        assert VlmConverter._compute_file_hash(f1) != VlmConverter._compute_file_hash(f2)


# ── 尾部行提取 ───────────────────────────────────────


class TestTailLines:
    def test_tail_lines(self):
        text = "\n".join(f"line{i}" for i in range(20))
        result = VlmConverter._tail_lines(text, n=3)
        assert result == "line17\nline18\nline19"

    def test_tail_lines_short(self):
        text = "only one line"
        result = VlmConverter._tail_lines(text, n=5)
        assert result == "only one line"

    def test_tail_lines_empty(self):
        assert VlmConverter._tail_lines("") == ""


# ── 异步转换集成 ─────────────────────────────────────


class TestConvertAsync:
    @pytest.mark.asyncio
    async def test_convert_with_mock_pdf(self, mock_llm, tmp_path):
        """用 mock 渲染测试完整异步转换流程"""
        converter = VlmConverter(
            llm_client=mock_llm,
            checkpoint_dir=tmp_path / "cp",
            dpi=72,
            max_concurrent=1,
        )

        # Mock _render_pages 返回 2 页假数据
        converter._render_pages = MagicMock(return_value=[(0, b"fake_png_0"), (1, b"fake_png_1")])

        fake_path = tmp_path / "test.pdf"
        fake_path.write_bytes(b"fake pdf content")

        result = await converter.convert_file_async(fake_path)

        assert mock_llm.generate_with_image.call_count == 2
        assert "Page content" in result
        assert "<!-- page 2 -->" in result

    @pytest.mark.asyncio
    async def test_resume_from_checkpoint(self, mock_llm, tmp_path):
        """测试从 checkpoint 恢复时不重复调用 VLM"""
        converter = VlmConverter(
            llm_client=mock_llm,
            checkpoint_dir=tmp_path / "cp",
            dpi=72,
            max_concurrent=1,
        )
        converter._render_pages = MagicMock(
            return_value=[(0, b"fake_png_0"), (1, b"fake_png_1"), (2, b"fake_png_2")]
        )

        fake_path = tmp_path / "test.pdf"
        fake_path.write_bytes(b"fake pdf content")
        file_hash = converter._compute_file_hash(fake_path)

        # 预写 page 0 的 checkpoint
        vlm_dir = tmp_path / "cp" / file_hash / "vlm"
        vlm_dir.mkdir(parents=True)
        (vlm_dir / "page_000.md").write_text("# Cached page 0")
        (vlm_dir / "progress.json").write_text(
            json.dumps({"total_pages": 3, "completed_pages": [0]})
        )

        result = await converter.convert_file_async(fake_path)

        # page 0 已有 checkpoint，只应调用 page 1 和 2
        assert mock_llm.generate_with_image.call_count == 2
        assert "Cached page 0" in result
