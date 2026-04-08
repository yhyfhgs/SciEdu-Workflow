"""CSV 处理器 — 支持多种数学题目 CSV 格式"""

from __future__ import annotations

import csv
from pathlib import Path

import structlog

from mathforge.core.exceptions import CsvParseError
from mathforge.core.models import (
    Question,
    QuestionSource,
    QuestionType,
    Solution,
)

logger = structlog.get_logger()

# 列名映射方案
COLUMN_MAPS: dict[str, dict[str, str]] = {
    "flat": {
        "content": "problem_text",
        "answer": "answer",
        "difficulty": "difficulty",
        "tags": "tags",
        "subject": "subject",
    },
    "latex": {
        "content": "problem_latex",
        "solution": "solution_latex",
        "topic": "topic",
        "difficulty": "difficulty",
    },
    "structured": {
        "content": "stem",
        "solution": "explanation",
        "type": "question_type",
        "difficulty": "difficulty",
    },
}

# 各方案的标志性列名
FORMAT_SIGNATURES: dict[str, set[str]] = {
    "latex": {"problem_latex", "solution_latex"},
    "structured": {"stem", "option_a", "correct"},
    "flat": {"problem_text", "answer"},
}


def _detect_format(headers: list[str]) -> str:
    """从表头自动检测 CSV 格式"""
    header_set = {h.lower().strip() for h in headers}
    for fmt, sig in FORMAT_SIGNATURES.items():
        if sig & header_set:
            return fmt
    return "flat"


def _parse_tags(tags_str: str) -> list[str]:
    """解析标签字符串"""
    if not tags_str:
        return []
    # 支持逗号、分号、竖线分隔
    for sep in [",", ";", "|", "，", "；"]:
        if sep in tags_str:
            return [t.strip() for t in tags_str.split(sep) if t.strip()]
    return [tags_str.strip()] if tags_str.strip() else []


def process_csv(
    filepath: Path,
    format: str = "auto",
    encoding: str = "utf-8",
) -> list[tuple[Question, Solution | None]]:
    """处理 CSV 文件，返回 (Question, Solution) 对列表"""
    if not filepath.exists():
        raise CsvParseError(f"文件不存在: {filepath}")

    results: list[tuple[Question, Solution | None]] = []

    with open(filepath, encoding=encoding, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise CsvParseError(f"CSV 无表头: {filepath}")

        headers = list(reader.fieldnames)
        if format == "auto":
            format = _detect_format(headers)

        col_map = COLUMN_MAPS.get(format)
        if col_map is None:
            raise CsvParseError(f"未知 CSV 格式: {format}")

        content_col = col_map.get("content", "")
        solution_col = col_map.get("solution", col_map.get("answer", ""))
        difficulty_col = col_map.get("difficulty", "")
        tags_col = col_map.get("tags", "")
        type_col = col_map.get("type", "")

        for row_num, row in enumerate(reader, start=2):
            # 提取内容
            content = row.get(content_col, "").strip()
            if not content:
                continue

            # 提取解答
            sol_text = row.get(solution_col, "").strip() if solution_col else ""

            # 提取难度
            difficulty = 5
            if difficulty_col and row.get(difficulty_col):
                try:
                    difficulty = max(1, min(10, int(float(row[difficulty_col]))))
                except (ValueError, TypeError):
                    pass

            # 提取标签
            tags = _parse_tags(row.get(tags_col, "")) if tags_col else []

            # 提取题目类型
            q_type = QuestionType.PROOF
            if type_col and row.get(type_col):
                try:
                    q_type = QuestionType(row[type_col].strip().lower())
                except ValueError:
                    pass

            q_id = row.get("id", f"csv-{filepath.stem}-{row_num:04d}").strip()

            question = Question(
                id=q_id,
                content_latex=content,
                difficulty=difficulty,
                question_type=q_type,
                tags=tags,
                source=QuestionSource(
                    book_title=filepath.stem,
                    page=row_num,
                ),
                is_generated=False,
            )

            solution = None
            if sol_text:
                solution = Solution(
                    id=f"{q_id}-sol",
                    question_id=q_id,
                    solution_latex=sol_text,
                    confidence="medium",
                )

            results.append((question, solution))

    logger.info(
        "csv_parse_complete",
        file=filepath.name,
        format=format,
        questions_extracted=len(results),
    )

    return results
