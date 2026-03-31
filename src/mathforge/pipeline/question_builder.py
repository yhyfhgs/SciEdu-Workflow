"""ParsedProblem → Question + Solution 转换"""

from __future__ import annotations

from mathforge.core.models import (
    ExamType,
    ParsedProblem,
    Question,
    QuestionSource,
    QuestionType,
    Solution,
    Subject,
)


def build_question_id(
    exam_type: ExamType, year: int, month: int | None, number: int
) -> str:
    """生成确定性题目 ID"""
    type_map = {
        ExamType.LINGSHI: "lingshi",
        ExamType.YISHI: "yishi",
        ExamType.NVZI: "nvzi",
    }
    type_str = type_map[exam_type]
    if month is not None:
        return f"xlj-{type_str}-{year}-{month:02d}-{number:03d}"
    return f"xlj-{type_str}-{year}-{number:03d}"


def build_exercise_id(
    exam_type: ExamType, year: int, month: int | None, label: str, index: int
) -> str:
    """生成练习题 ID"""
    type_map = {
        ExamType.LINGSHI: "lingshi",
        ExamType.YISHI: "yishi",
        ExamType.NVZI: "nvzi",
    }
    type_str = type_map[exam_type]
    if month is not None:
        return f"xlj-{type_str}-{year}-{month:02d}-ex{index:03d}"
    return f"xlj-{type_str}-{year}-ex{index:03d}"


def build_question(parsed: ParsedProblem, exercise_index: int = 0) -> tuple[Question, Solution]:
    """将 ParsedProblem 转换为 Question + Solution"""
    if parsed.is_exercise:
        q_id = build_exercise_id(
            parsed.exam_type, parsed.year, parsed.month,
            parsed.exercise_label, exercise_index,
        )
    else:
        q_id = build_question_id(
            parsed.exam_type, parsed.year, parsed.month, parsed.problem_number,
        )

    question = Question(
        id=q_id,
        content_latex=parsed.problem_latex,
        difficulty=parsed.difficulty_estimate,
        question_type=QuestionType.PROOF,
        subject=Subject.ANALYSIS,
        source=QuestionSource(
            exam_name="新领军",
            exam_type=parsed.exam_type,
            year=parsed.year,
            month=parsed.month,
            problem_number=parsed.problem_number if not parsed.is_exercise else None,
            exercise_number=parsed.exercise_label if parsed.is_exercise else None,
        ),
        is_generated=False,
        parent_id=None,
    )

    solution = Solution(
        id=f"{q_id}-sol",
        question_id=q_id,
        solution_latex=parsed.solution_latex,
        confidence="high" if parsed.has_solution else "none",
    )

    return question, solution
