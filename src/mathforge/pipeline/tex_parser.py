"""TeX 文件解析器 — 从新领军真题 TeX 文件中提取结构化题目"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from mathforge.core.exceptions import TexParseError
from mathforge.core.models import ExamType, ParsedProblem, TexExamMetadata
from mathforge.pipeline.tex_cleaner import clean_latex

logger = structlog.get_logger()

# ── 文件名分类正则 ─────────────────────────────────────

FILENAME_PATTERNS: dict[ExamType, re.Pattern[str]] = {
    ExamType.LINGSHI: re.compile(r"^(\d{4})\.(\d{1,2})\.tex$"),
    ExamType.YISHI: re.compile(r"^(\d{4})一试\.tex$"),
    ExamType.NVZI: re.compile(r"^(\d{4})女子\.tex$"),
}

# ── 内容提取正则 ──────────────────────────────────────

SECTION_PATTERN = re.compile(r"\\section\*?\{(.+?)\}")
SECTION_YEAR_PATTERN = re.compile(r"(\d{4})年?(?:(\d{1,2})月)?")

PROBLEM_PATTERN = re.compile(
    r"\\begin\{problem\}\[第(\d+)题\](.*?)\\end\{problem\}", re.DOTALL
)
EXERCISE_PATTERN = re.compile(
    r"\\begin\{exercise\}\[([^\]]+)\](.*?)\\end\{exercise\}", re.DOTALL
)
SOLUTION_PATTERN = re.compile(
    r"\\begin\{solution\}(?:\[([^\]]*)\])?(.*?)\\end\{solution\}", re.DOTALL
)
BOXED_SOLUTION_PATTERN = re.compile(
    r"\\begin\{boxedsolution\}(?:\[([^\]]*)\])?(.*?)\\end\{boxedsolution\}", re.DOTALL
)

# 跳过的文件
SKIP_FILES = {"main.tex"}


def classify_file(filename: str) -> tuple[ExamType, int, int | None] | None:
    """从文件名推断考试类型、年份、月份。返回 None 表示无法分类。"""
    if filename in SKIP_FILES or not filename.endswith(".tex"):
        return None

    for exam_type, pattern in FILENAME_PATTERNS.items():
        m = pattern.match(filename)
        if m:
            year = int(m.group(1))
            month = int(m.group(2)) if exam_type == ExamType.LINGSHI else None
            return exam_type, year, month

    # 特殊文件: 202510new.tex 等
    special = re.match(r"^(\d{4})(\d{1,2})?(\w*)\.tex$", filename)
    if special:
        year = int(special.group(1)[:4])
        month_str = special.group(2) or special.group(1)[4:]
        month = int(month_str) if month_str and month_str.isdigit() else None
        return ExamType.LINGSHI, year, month

    return None


def extract_metadata(filepath: Path, content: str) -> TexExamMetadata:
    """从文件名和 \\section 标题提取元数据"""
    filename = filepath.name
    classified = classify_file(filename)

    if classified is None:
        raise TexParseError(str(filepath), detail=f"无法分类文件: {filename}")

    exam_type, year, month = classified

    # 尝试从 section 标题中提取更精确的信息
    sections = SECTION_PATTERN.findall(content)
    section_title = sections[-1] if sections else ""

    # 从 section 标题中检测考试类型
    if section_title:
        if "一试" in section_title:
            exam_type = ExamType.YISHI
        elif "女子" in section_title:
            exam_type = ExamType.NVZI

        # 从 section 标题提取年月
        ym = SECTION_YEAR_PATTERN.search(section_title)
        if ym:
            year = int(ym.group(1))
            if ym.group(2):
                month = int(ym.group(2))

    return TexExamMetadata(
        year=year,
        exam_type=exam_type,
        month=month,
        source_file=str(filepath),
        section_title=section_title,
    )


def _strip_preamble(content: str) -> str:
    """若文件含独立 \\documentclass，去除 preamble"""
    if content.lstrip().startswith("\\documentclass"):
        doc_begin = re.search(r"\\begin\{document\}", content)
        doc_end = re.search(r"\\end\{document\}", content)
        if doc_begin:
            start = doc_begin.end()
            end = doc_end.start() if doc_end else len(content)
            return content[start:end]
    return content


def _find_blocks(pattern: re.Pattern[str], content: str) -> list[tuple[int, int, re.Match[str]]]:
    """查找所有匹配块及其位置 (start, end, match)"""
    results = []
    for m in pattern.finditer(content):
        results.append((m.start(), m.end(), m))
    return results


def _assign_difficulty(exam_type: ExamType, position: int, total: int) -> int:
    """基于考试类型和题目位置分配难度"""
    if exam_type == ExamType.LINGSHI:
        base, max_add, lo, hi = 3, 3, 3, 6
    elif exam_type == ExamType.YISHI:
        base, max_add, lo, hi = 5, 3, 5, 8
    else:  # NVZI
        base, max_add, lo, hi = 5, 3, 5, 8

    ratio = position / max(total, 1)
    raw = base + max_add * ratio
    return max(lo, min(hi, round(raw)))


def parse_file(filepath: Path) -> tuple[TexExamMetadata, list[ParsedProblem]]:
    """解析单个 TeX 文件，返回 (元数据, 题目列表)"""
    content = filepath.read_text(encoding="utf-8")
    content = _strip_preamble(content)

    metadata = extract_metadata(filepath, content)

    # 查找所有块
    problems = _find_blocks(PROBLEM_PATTERN, content)
    exercises = _find_blocks(EXERCISE_PATTERN, content)
    solutions = _find_blocks(SOLUTION_PATTERN, content)
    boxed_sols = _find_blocks(BOXED_SOLUTION_PATTERN, content)

    # 合并所有 solution 来源
    all_solutions = solutions + boxed_sols
    all_solutions.sort(key=lambda x: x[0])

    # 合并所有题目块 (problem + exercise) 按位置排序
    all_items: list[tuple[int, int, str, str, bool]] = []
    # (start, end, number_or_label, latex_content, is_exercise)
    for start, end, m in problems:
        num = m.group(1)
        body = m.group(2).strip()
        all_items.append((start, end, num, body, False))
    for start, end, m in exercises:
        label = m.group(1)
        body = m.group(2).strip()
        all_items.append((start, end, label, body, True))

    all_items.sort(key=lambda x: x[0])

    # 配对: 为每个 item 找到紧跟其后的 solution(s)
    results: list[ParsedProblem] = []
    total_problems = sum(1 for _, _, _, _, is_ex in all_items if not is_ex)

    problem_index = 0
    for i, (item_start, item_end, num_or_label, body, is_exercise) in enumerate(all_items):
        # 找当前 item 之后、下一个 item 之前的所有 solution
        next_item_start = all_items[i + 1][0] if i + 1 < len(all_items) else len(content)

        item_solutions: list[str] = []
        for sol_start, sol_end, sol_match in all_solutions:
            if sol_start > item_end and sol_start < next_item_start:
                sol_body = sol_match.group(2).strip()
                sol_label = sol_match.group(1) or ""
                if sol_label:
                    item_solutions.append(f"% {sol_label}\n{sol_body}")
                else:
                    item_solutions.append(sol_body)

        combined_solution = "\n\n".join(item_solutions)

        # 解析题号
        if is_exercise:
            # 尝试从 label 中提取关联的题号
            parent_num_match = re.search(r"第(\d+)题", num_or_label)
            parent_num = int(parent_num_match.group(1)) if parent_num_match else None
            # 使用一个合成编号
            prob_num = 1000 + i  # exercise 使用高编号避免冲突
        else:
            prob_num = int(num_or_label)
            parent_num = None
            problem_index += 1

        difficulty = _assign_difficulty(
            metadata.exam_type,
            problem_index if not is_exercise else problem_index,
            total_problems,
        )

        results.append(ParsedProblem(
            problem_number=prob_num,
            problem_latex=clean_latex(body),
            solution_latex=clean_latex(combined_solution) if combined_solution else "",
            has_solution=bool(combined_solution),
            source_file=str(filepath),
            exam_type=metadata.exam_type,
            year=metadata.year,
            month=metadata.month,
            difficulty_estimate=difficulty,
            is_exercise=is_exercise,
            exercise_label=num_or_label if is_exercise else "",
            parent_problem_number=parent_num,
        ))

    metadata.total_problems = total_problems
    metadata.total_exercises = sum(1 for r in results if r.is_exercise)

    logger.info(
        "tex_parse_complete",
        source_file=filepath.name,
        exam_type=metadata.exam_type.value,
        problems_extracted=metadata.total_problems,
        exercises_extracted=metadata.total_exercises,
    )

    return metadata, results


def parse_directory(dirpath: Path) -> list[tuple[TexExamMetadata, list[ParsedProblem]]]:
    """解析目录下所有 TeX 文件"""
    results = []
    tex_files = sorted(dirpath.glob("*.tex"))

    for filepath in tex_files:
        if filepath.name in SKIP_FILES:
            continue
        if classify_file(filepath.name) is None:
            logger.warning("tex_skip_unknown", filename=filepath.name)
            continue
        try:
            meta, problems = parse_file(filepath)
            results.append((meta, problems))
        except TexParseError as e:
            logger.error("tex_parse_error", error=str(e))

    return results
