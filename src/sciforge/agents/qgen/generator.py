"""QuestionGenerator — QGen 第 2 阶段：LLM 生成候选题目

使用 prompts/templates/generation/free_generation.txt 或 variant_generation.txt。
每次生成 n 个候选，温度依次递增（0.7 → 0.75 → 0.8）避免重复。

LLM 输出字段兼容新老 schema：
- `content_latex`（旧） → 存为 Question.content
- `content`（新） → 直接存
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from sciforge.core.models import (
    ContextBundle,
    Discipline,
    GenerationRequest,
    Question,
    VariantStrategy,
)
from sciforge.disciplines import DISCIPLINE_REGISTRY
from sciforge.llm.client import LLMClient
from sciforge.pipeline.retry import call_llm_json_with_retry
from sciforge.prompts.templates import get_generation_prompt_template

logger = structlog.get_logger()


class QuestionGenerator:
    """调用 LLM 生成候选题目"""

    def __init__(self, llm: LLMClient, discipline: Discipline = Discipline.MATH) -> None:
        self.llm = llm
        self.discipline = discipline
        self.spec = DISCIPLINE_REGISTRY.get(discipline)
        # 按学科加载 prompt 模板（含词语替换）
        self._free_template = get_generation_prompt_template(
            "generation/free_generation.txt", discipline
        )
        self._variant_template = get_generation_prompt_template(
            "generation/variant_generation.txt", discipline
        )
        self._difficulty_cal = get_generation_prompt_template(
            "generation/difficulty_calibration.txt", discipline
        )
        # 预加载 8 种变式策略正文（A1-A6/B1-B2），修复"只注入枚举字符串、从未加载策略文件"缺陷。
        # 策略正文作为 .format() 的参数值注入，其内部裸 LaTeX 花括号不会被二次解析。
        self._strategy_templates: dict[VariantStrategy, str] = {
            s: get_generation_prompt_template(f"strategies/{s.value}.txt", discipline)
            for s in VariantStrategy
        }

    async def generate_candidates(
        self,
        request: GenerationRequest,
        context: ContextBundle,
        n: int = 3,
    ) -> list[Question]:
        """生成 n 道候选题"""
        prompt = self._build_prompt(request, context)
        candidates: list[Question] = []

        for i in range(n):
            try:
                result = await call_llm_json_with_retry(
                    self.llm,
                    prompt,
                    max_attempts=2,
                )
                q = self._parse_candidate(result, request)
                if q is not None:
                    candidates.append(q)
                    logger.info(
                        "qgen_candidate_ok",
                        idx=i + 1,
                        difficulty=result.get("estimated_difficulty"),
                    )
            except Exception as exc:
                logger.warning("qgen_candidate_failed", idx=i + 1, error=str(exc)[:200])
        return candidates

    def _build_prompt(self, req: GenerationRequest, ctx: ContextBundle) -> str:
        """构建 prompt（按 variant / free 模式 + 学科参数化）"""
        difficulty_cal = self._difficulty_cal.format(target=req.difficulty_target)
        context_block = ctx.to_prompt_block()

        # 学科上下文：用 discipline spec 的 display_name 替换 "数学"
        subject_text = req.subject or self.spec.display_name

        if req.source_question is not None:
            return self._variant_template.format(
                subject=subject_text,
                source_question_latex=req.source_question.content,
                strategy_instruction=self._strategy_templates.get(
                    req.strategy, req.strategy.value
                ),
                target_difficulty=req.difficulty_target,
                difficulty_calibration=difficulty_cal,
                question_type=req.question_type,
                context_block=context_block,
            )

        return self._free_template.format(
            subject=subject_text,
            topics=", ".join(req.topics),
            target_difficulty=req.difficulty_target,
            question_type=req.question_type,
            strategy_instruction=self._strategy_templates.get(
                req.strategy, req.strategy.value
            ),
            difficulty_calibration=difficulty_cal,
            context_block=context_block,
        )

    def _parse_candidate(
        self,
        data: dict[str, Any],
        req: GenerationRequest,
    ) -> Question | None:
        """LLM JSON → Question"""
        # 兼容新 / 旧 key (content vs content_latex)
        content = str(data.get("content") or data.get("content_latex") or "").strip()
        if not content:
            return None

        q_type = self.spec.question_type_or(req.question_type, "calculation")

        return Question(
            discipline=self.discipline,
            question_type=q_type,
            content=content,
            difficulty=int(data.get("estimated_difficulty") or req.difficulty_target),
            subject=req.subject,
            tags=list(data.get("knowledge_points_used") or []) + req.topics,
        )

    @staticmethod
    def _strip_latex(text: str) -> str:
        """简化去 LaTeX 命令，用于全文检索"""
        text = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", text)
        text = re.sub(r"[\\${}^_]", "", text)
        return text.strip()
