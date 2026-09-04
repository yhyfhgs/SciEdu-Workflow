"""QuestionGenerator — QGen 第 2 阶段：LLM 生成候选题目

使用 prompts/templates/generation/free_generation.txt 或 variant_generation.txt。
每次生成 n 个候选，温度依次递增（0.7 → 0.75 → 0.8）避免重复。

LLM 输出字段兼容新老 schema：
- `content_latex`（旧） → 存为 Question.content
- `content`（新） → 直接存
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import structlog

from sciforge.agents.qgen.prefilter import _tf_vector, cosine_similarity
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

# 同批候选两两 cosine 相似度去重阈值（> 此值视为近重复，丢弃后者）
BATCH_DEDUP_THRESHOLD = 0.92


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

    def validate_request(self, request: GenerationRequest) -> None:
        """拒绝与当前生成器学科契约不一致的直接调用。"""
        if request.discipline != self.discipline:
            raise ValueError(
                "generation request discipline "
                f"{request.discipline.value!r} does not match generator discipline "
                f"{self.discipline.value!r}"
            )
        if (
            request.source_question is not None
            and request.source_question.discipline != request.discipline
        ):
            raise ValueError(
                "source question discipline "
                f"{request.source_question.discipline.value!r} does not match request "
                f"discipline {request.discipline.value!r}"
            )
        if request.question_type not in self.spec.allowed_question_types:
            allowed = ", ".join(self.spec.allowed_question_types)
            raise ValueError(
                f"question_type {request.question_type!r} is not allowed for "
                f"{self.discipline.value}; allowed: {allowed}"
            )
        if request.question_form and request.question_form not in self.spec.allowed_question_forms:
            allowed = ", ".join(self.spec.allowed_question_forms)
            raise ValueError(
                f"question_form {request.question_form!r} is not allowed for "
                f"{self.discipline.value}; allowed: {allowed}"
            )

    async def generate_candidates(
        self,
        request: GenerationRequest,
        context: ContextBundle,
        n: int = 3,
    ) -> list[Question]:
        """生成 n 道候选题"""
        self.validate_request(request)
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
        return self._dedup_batch(candidates)

    def _dedup_batch(self, candidates: list[Question]) -> list[Question]:
        """同批候选两两 cosine 去重：与已保留候选相似度 > 阈值则丢弃，避免一批近重复题。"""
        kept: list[Question] = []
        kept_vecs: list[Counter[str]] = []
        for cand in candidates:
            vec = _tf_vector(cand.content)
            if any(cosine_similarity(vec, kv) > BATCH_DEDUP_THRESHOLD for kv in kept_vecs):
                logger.info("qgen_batch_dedup_drop", content_head=cand.content[:40])
                continue
            kept.append(cand)
            kept_vecs.append(vec)
        return kept

    def _build_prompt(self, req: GenerationRequest, ctx: ContextBundle) -> str:
        """构建 prompt（按 variant / free 模式 + 学科参数化）"""
        difficulty_cal = self._difficulty_cal.format(target=req.difficulty_target)
        context_block = ctx.to_prompt_block()

        # 学科上下文：用 discipline spec 的 display_name 替换 "数学"
        subject_text = req.subject or self.spec.display_name
        conversation_context = req.conversation_context.strip() or (
            "（无历史上下文；仅按当前请求生成。）"
        )

        if req.source_question is not None:
            return self._variant_template.format(
                subject=subject_text,
                source_question_latex=req.source_question.content,
                strategy_instruction=self._strategy_templates.get(req.strategy, req.strategy.value),
                target_difficulty=req.difficulty_target,
                difficulty_calibration=difficulty_cal,
                question_type=req.question_type,
                question_form=req.question_form or "subjective",
                conversation_context=conversation_context,
                context_block=context_block,
            )

        return self._free_template.format(
            subject=subject_text,
            topics=", ".join(req.topics),
            target_difficulty=req.difficulty_target,
            question_type=req.question_type,
            question_form=req.question_form or "subjective",
            conversation_context=conversation_context,
            strategy_instruction=self._strategy_templates.get(req.strategy, req.strategy.value),
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
            question_form=self.spec.question_form_or(req.question_form, ""),
            content=content,
            options=[str(option) for option in data.get("options") or []],
            correct_option=(
                str(data["correct_option"]) if data.get("correct_option") is not None else None
            ),
            blanks=(int(data["blanks"]) if data.get("blanks") is not None else None),
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
