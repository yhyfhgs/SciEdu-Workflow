"""Evolver — QGen 进化闭环：把"求解失败 / 验证未通过"的候选题定向改进成下一代候选

修复缺陷②：QGen 此前只能"生成 → 验证 → 丢弃"，缺少把不合格候选**改进重投**的闭环。
本模块据失败类型选择对应改进 prompt，调用 LLM 产出改进后的题面，封装为带
``parent_id`` / ``generation`` 链路的 ``CandidateRecord``，供上层重新求解/验证。

两个改进 prompt（按学科加载，词语替换见 templates.get_generation_prompt_template）：
- ``evolution/improve_from_solver.txt`` —— 求解引擎置信度不足时的诊断式改进
  占位符：``{original_question_latex}`` / ``{confidence}`` / ``{solver_feedback}``
- ``evolution/improve_from_verification.txt`` —— 自动验证未通过时的精准修复
  占位符：``{original_question_latex}`` / ``{passed_checks}`` / ``{failed_checks}`` /
  ``{suggestions}``

两模板输出 JSON 同构：``content_latex`` / ``changes_made`` / ``solution_sketch`` /
``knowledge_points_used`` / ``methods_required`` / ``estimated_difficulty``。
"""

from __future__ import annotations

from typing import Any, Literal

import structlog

from sciforge.core.models import (
    CandidateRecord,
    ContextBundle,
    Discipline,
    Question,
)
from sciforge.llm.client import LLMClient
from sciforge.pipeline.retry import call_llm_json_with_retry
from sciforge.prompts.templates import get_generation_prompt_template

logger = structlog.get_logger()

FailureKind = Literal["solver", "verification"]


class Evolver:
    """对失败候选题做定向改进，产出下一代 ``CandidateRecord``。"""

    def __init__(self, llm: LLMClient, discipline: Discipline = Discipline.MATH) -> None:
        self.llm = llm
        self.discipline = discipline
        # 按学科加载两个改进模板（含词语替换；裸 LaTeX 花括号作为 .format 参数值注入安全）
        self._solver_template = get_generation_prompt_template(
            "evolution/improve_from_solver.txt", discipline
        )
        self._verification_template = get_generation_prompt_template(
            "evolution/improve_from_verification.txt", discipline
        )

    async def evolve(
        self,
        candidate: Question,
        *,
        failure_kind: FailureKind,
        failure_detail: str,
        parent_generation: int = 0,
        context: ContextBundle | None = None,
    ) -> CandidateRecord | None:
        """把失败候选改进为下一代候选。

        Args:
            candidate: 待改进的原始题目
            failure_kind: ``"solver"`` 求解失败 / ``"verification"`` 验证未通过
            failure_detail: 失败详情（求解反馈或验证建议），注入对应模板
            parent_generation: 父候选所属代数；新候选 ``generation = parent_generation + 1``
            context: 可选上下文（当前未注入模板，预留以兼容签名约定）

        Returns:
            改进后的 ``CandidateRecord``；LLM 调用或解析失败时返回 ``None``（不抛）。
        """
        prompt = self._build_prompt(candidate, failure_kind, failure_detail)

        try:
            result = await call_llm_json_with_retry(self.llm, prompt, max_attempts=2)
        except Exception as exc:
            logger.warning(
                "evolve_llm_failed",
                failure_kind=failure_kind,
                parent_id=candidate.id,
                error=str(exc)[:200],
            )
            return None

        improved = self._parse_improved(result, candidate)
        if improved is None:
            logger.warning(
                "evolve_parse_failed",
                failure_kind=failure_kind,
                parent_id=candidate.id,
            )
            return None

        directive = str(result.get("changes_made") or "").strip()
        record = CandidateRecord(
            question=improved,
            parent_id=candidate.id,
            generation=parent_generation + 1,
            improvement_strategy=failure_kind,
            improvement_directive=directive,
        )
        logger.info(
            "evolve_done",
            failure_kind=failure_kind,
            parent_id=candidate.id,
            generation=record.generation,
            difficulty=improved.difficulty,
        )
        return record

    def _build_prompt(
        self,
        candidate: Question,
        failure_kind: FailureKind,
        failure_detail: str,
    ) -> str:
        """按失败类型选模板，并以模板真实占位符 .format 注入。"""
        if failure_kind == "solver":
            return self._solver_template.format(
                original_question_latex=candidate.content,
                confidence="low",
                solver_feedback=failure_detail,
            )
        return self._verification_template.format(
            original_question_latex=candidate.content,
            passed_checks="",
            failed_checks=failure_detail,
            suggestions=failure_detail,
        )

    def _parse_improved(
        self,
        data: dict[str, Any],
        candidate: Question,
    ) -> Question | None:
        """LLM JSON → 改进后的 Question（沿用原候选的学科/类型/学科领域/标签）。"""
        # 兼容新/旧 key：content_latex（模板输出）优先，content 兜底
        content = str(data.get("content_latex") or data.get("content") or "").strip()
        if not content:
            return None

        difficulty = self._coerce_difficulty(data.get("estimated_difficulty"), candidate.difficulty)

        return Question(
            discipline=candidate.discipline,
            question_type=candidate.question_type,
            question_form=candidate.question_form,
            content=content,
            options=(
                [str(option) for option in data.get("options") or []]
                if "options" in data
                else list(candidate.options)
            ),
            correct_option=(
                str(data["correct_option"])
                if data.get("correct_option") is not None
                else candidate.correct_option
            ),
            blanks=(int(data["blanks"]) if data.get("blanks") is not None else candidate.blanks),
            difficulty=difficulty,
            subject=candidate.subject,
            tags=list(candidate.tags),
        )

    @staticmethod
    def _coerce_difficulty(raw: Any, fallback: int) -> int:
        """把 LLM 的 estimated_difficulty 收敛到 [1,10]，非法时回退父候选难度。"""
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return fallback
        return max(1, min(10, value))
