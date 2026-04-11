"""Tests for CLI book manifest loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from sciforge.cli.parse import _load_book_manifest


def _touch(p: Path, content: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_yaml_list_top(tmp_path: Path) -> None:
    manifest = tmp_path / "book.yaml"
    manifest.write_text("- chap1.md\n- chap2.md\n- chap3.md\n", encoding="utf-8")
    # 创建引用文件以便后续使用（_load_book_manifest 本身不校验存在）
    for name in ("chap1.md", "chap2.md", "chap3.md"):
        _touch(tmp_path / name)

    result = _load_book_manifest(manifest)
    assert [p.name for p in result] == ["chap1.md", "chap2.md", "chap3.md"]
    # 相对路径应被解析成绝对路径（相对于 manifest 所在目录）
    assert all(p.is_absolute() for p in result)


def test_yaml_files_key(tmp_path: Path) -> None:
    manifest = tmp_path / "book.yaml"
    manifest.write_text(
        "name: my book\nfiles:\n  - a.md\n  - b.md\n",
        encoding="utf-8",
    )
    result = _load_book_manifest(manifest)
    assert [p.name for p in result] == ["a.md", "b.md"]


def test_yaml_chapters_key_with_dict_items(tmp_path: Path) -> None:
    manifest = tmp_path / "book.yaml"
    manifest.write_text(
        "chapters:\n  - path: chap1.md\n    title: Intro\n  - file: chap2.md\n",
        encoding="utf-8",
    )
    result = _load_book_manifest(manifest)
    assert [p.name for p in result] == ["chap1.md", "chap2.md"]


def test_json_format(tmp_path: Path) -> None:
    manifest = tmp_path / "book.json"
    manifest.write_text('{"files": ["a.md", "b.md"]}', encoding="utf-8")
    result = _load_book_manifest(manifest)
    assert [p.name for p in result] == ["a.md", "b.md"]


def test_absolute_paths_preserved(tmp_path: Path) -> None:
    abs_path = (tmp_path / "absolute.md").resolve()
    manifest = tmp_path / "book.yaml"
    manifest.write_text(f"- {abs_path}\n", encoding="utf-8")
    result = _load_book_manifest(manifest)
    assert result[0] == abs_path


def test_relative_paths_resolved_against_manifest_parent(
    tmp_path: Path,
) -> None:
    sub = tmp_path / "bookdir"
    sub.mkdir()
    manifest = sub / "book.yaml"
    manifest.write_text("- chap1.md\n", encoding="utf-8")

    result = _load_book_manifest(manifest)
    assert result[0].parent == sub.resolve()
    assert result[0].name == "chap1.md"


def test_empty_entries_skipped(tmp_path: Path) -> None:
    manifest = tmp_path / "book.yaml"
    # YAML 中 `- ""` 会被 yaml.safe_load 转为空字符串
    manifest.write_text('- "chap1.md"\n- ""\n- "chap2.md"\n', encoding="utf-8")
    result = _load_book_manifest(manifest)
    assert [p.name for p in result] == ["chap1.md", "chap2.md"]


def test_malformed_yaml_raises_bad_parameter(tmp_path: Path) -> None:
    manifest = tmp_path / "book.yaml"
    # 顶层既不是 dict 也不是 list
    manifest.write_text("not a list or dict", encoding="utf-8")
    with pytest.raises(typer.BadParameter):
        _load_book_manifest(manifest)


def test_yaml_files_not_a_list_raises(tmp_path: Path) -> None:
    manifest = tmp_path / "book.yaml"
    manifest.write_text('files: "not a list"\n', encoding="utf-8")
    with pytest.raises(typer.BadParameter):
        _load_book_manifest(manifest)


def test_yaml_hash_matches_group_part_files(tmp_path: Path) -> None:
    """yaml 清单加载后的 file_hash 与 _group_part_files 聚合一致

    这是关键的集成约束：run-book --manifest 和 parse run <dir> 必须生成
    同一个 file_hash，这样 checkpoint 才能被共享和直通。
    """
    from sciforge.pipeline.orchestrator import PipelineOrchestrator
    from sciforge.pipeline.resume_state import compute_file_hash

    book_dir = tmp_path / "book_md"
    book_dir.mkdir()
    # 故意按字典序乱（part_10 会排在 part_2 之前）
    files = [
        book_dir / "book_part_1_10.md",
        book_dir / "book_part_21_30.md",
        book_dir / "book_part_11_20.md",
    ]
    for f in files:
        f.write_text(f.stem, encoding="utf-8")

    manifest = book_dir / "book.yaml"
    # yaml 按起始页升序列出（与 _group_part_files 排序一致）
    manifest.write_text(
        "files:\n  - book_part_1_10.md\n  - book_part_11_20.md\n  - book_part_21_30.md\n",
        encoding="utf-8",
    )

    yaml_files = _load_book_manifest(manifest)
    yaml_hash = compute_file_hash(yaml_files)

    collected = sorted(
        PipelineOrchestrator._collect_files(tmp_path),
        key=PipelineOrchestrator._natural_sort_key,
    )
    grouped = PipelineOrchestrator._group_part_files(collected)
    # 只会有一个分组（book_*）
    book_group = [g for g in grouped if isinstance(g, list)][0]
    group_hash = compute_file_hash(book_group)

    assert yaml_hash == group_hash, (
        f"yaml hash ({yaml_hash}) must match group hash ({group_hash}) "
        "so run-book and parse run <dir> share the same checkpoint"
    )
