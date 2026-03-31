"""Stage 2: 题目生成器"""

from __future__ import annotations

import re

import structlog

from mathforge.core.models import (
    ContextBundle,
    GenerationRequest,
    Question,
)
from mathforge.llm import LLMClient, LLMConfig
from mathforge.prompts import (
    DIFFICULTY_CALIBRATION,
    FREE_GENERATION_PROMPT,
    STRATEGY_INSTRUCTIONS,
    VARIANT_GENERATION_PROMPT,
)

logger = structlog.get_logger()


class QuestionGenerator:
    """调用 LLM 生成候选题目"""

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient(LLMConfig.for_task("generate"))

    async def generate_candidates(
        self,
        request: GenerationRequest,
        context: ContextBundle,
        n: int = 3,
        extra_temperature: float = 0.0,
    ) -> list[Question]:
        """异步生成 n 道候选题"""
        prompt = self._build_prompt(request, context)
        candidates = []

        for i in range(n):
            temp = self.llm.config.temperature + extra_temperature + i * 0.05
            temp = min(temp, 1.0)
            try:
                result = await self.llm.generate_json(prompt=prompt, temperature=temp)
                q = self._parse_candidate(result, request)
                if q:
                    candidates.append(q)
                    logger.info(
                        "candidate_generated",
                        index=i + 1,
                        difficulty=result.get("estimated_difficulty"),
                    )
            except Exception as e:
                logger.warning("candidate_failed", index=i + 1, error=str(e))

        return candidates

    def generate_candidates_sync(
        self,
        request: GenerationRequest,
        context: ContextBundle,
        n: int = 3,
        extra_temperature: float = 0.0,
    ) -> list[Question]:
        """同步生成 n 道候选题"""
        prompt = self._build_prompt(request, context)
        candidates = []

        for i in range(n):
            temp = self.llm.config.temperature + extra_temperature + i * 0.05
            temp = min(temp, 1.0)
            try:
                result = self.llm.generate_json_sync(prompt=prompt, temperature=temp)
                q = self._parse_candidate(result, request)
                if q:
                    candidates.append(q)
                    logger.info(
                        "candidate_generated",
                        index=i + 1,
                        difficulty=result.get("estimated_difficulty"),
                    )
            except Exception as e:
                logger.warning("candidate_failed", index=i + 1, error=str(e))

        return candidates

    def _build_prompt(self, req: GenerationRequest, ctx: ContextBundle) -> str:
        """根据模式构建 Prompt"""
        strategy_instruction = STRATEGY_INSTRUCTIONS.get(req.strategy.value, "")
        difficulty_cal = DIFFICULTY_CALIBRATION.format(target=req.difficulty_target)
        context_block = ctx.to_prompt_block()

        if req.source_question:
            return VARIANT_GENERATION_PROMPT.format(
                subject=req.subject.value,
                source_question_latex=req.source_question.content_latex,
                strategy_instruction=strategy_instruction,
                target_difficulty=req.difficulty_target,
                difficulty_calibration=difficulty_cal,
                question_type=req.question_type.value,
                context_block=context_block,
            )
        else:
            return FREE_GENERATION_PROMPT.format(
                subject=req.subject.value,
                topics=", ".join(req.topics),
                target_difficulty=req.difficulty_target,
                question_type=req.question_type.value,
                strategy_instruction=strategy_instruction,
                difficulty_calibration=difficulty_cal,
                context_block=context_block,
            )

    def _parse_candidate(self, data: dict, req: GenerationRequest) -> Question | None:
        """将 LLM JSON 输出解析为 Question 对象"""
        content = data.get("content_latex", "").strip()
        if not content:
            return None

        return Question(
            content_latex=content,
            content_plain=self._strip_latex(content),
            difficulty=data.get("estimated_difficulty", req.difficulty_target),
            question_type=req.question_type,
            subject=req.subject,
            tags=list(dict.fromkeys(data.get("knowledge_points_used", []) + req.topics)),
            is_generated=True,
            parent_id=req.source_question.id if req.source_question else None,
            generation_meta={
                "strategy": req.strategy.value,
                "solution_sketch": data.get("solution_sketch", ""),
                "methods_required": data.get("methods_required", []),
                "design_rationale": data.get("design_rationale", ""),
            },
        )

    @staticmethod
    def _strip_latex(text: str) -> str:
        """简单去 LaTeX 命令，用于全文检索"""
        text = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", text)
        text = re.sub(r"[\\${}^_]", "", text)
        return text.strip()
