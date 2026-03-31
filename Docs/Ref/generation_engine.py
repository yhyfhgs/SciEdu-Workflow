"""
MathForge 核心题目生成引擎

完整的 生成 → 求解 → 验证 → 标注 闭环。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from core.llm_client import LLMClient, LLMConfig
from core.models import (
    ContextBundle, DifficultyScore, GeneratedQuestion, GenerationRequest,
    KnowledgePoint, Question, QuestionType, Solution, Subject,
    VariantStrategy, VerificationResult,
)
from prompts.templates import (
    DIFFICULTY_CALIBRATION, DIFFICULTY_SCORING_PROMPT,
    FREE_GENERATION_PROMPT, REVIEW_PROMPT, SOLVE_PROMPT,
    STRATEGY_INSTRUCTIONS, TAG_CLASSIFICATION_PROMPT,
    VARIANT_GENERATION_PROMPT,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
#  上下文检索器
# ══════════════════════════════════════════════════════

class ContextRetriever:
    """
    从知识库和题库中检索相关上下文。
    当前版本: 基于内存列表的简单实现。
    生产版本: 替换为 pgvector 语义检索。
    """

    def __init__(
        self,
        knowledge_base: list[KnowledgePoint],
        question_bank: list[Question],
    ):
        self.kb = knowledge_base
        self.qb = question_bank

    def build_context(self, request: GenerationRequest) -> ContextBundle:
        """构建生成上下文"""

        # 知识点: 按标签匹配 + 难度窗口
        kps = self._match_knowledge(request)

        # 参考题: 同专题 + 难度附近 + 排除已有
        refs = self._match_questions(request)

        # 负样本: 如果是变式模式，找最相似的已有题
        negatives = self._find_negatives(request)

        return ContextBundle(
            knowledge_points=kps[:8],
            reference_questions=refs[:8],
            negative_examples=negatives[:5],
        )

    def _match_knowledge(self, req: GenerationRequest) -> list[KnowledgePoint]:
        """按专题标签和难度窗口筛选知识点"""
        matched = []
        for kp in self.kb:
            # 标签匹配
            tag_overlap = set(kp.tags) & set(req.topics)
            if not tag_overlap:
                continue
            # 难度窗口 [target-3, target+1]
            if kp.difficulty < req.difficulty_target - 3:
                continue
            if kp.difficulty > req.difficulty_target + 1:
                continue
            matched.append((len(tag_overlap), kp))

        # 按标签重叠度排序
        matched.sort(key=lambda x: x[0], reverse=True)
        return [kp for _, kp in matched]

    def _match_questions(self, req: GenerationRequest) -> list[Question]:
        """按专题 + 难度 + 题型筛选参考题"""
        matched = []
        for q in self.qb:
            if q.id in req.exclusion_ids:
                continue
            tag_overlap = set(q.tags) & set(req.topics)
            if not tag_overlap:
                continue
            # 难度附近 ±2
            if abs(q.difficulty - req.difficulty_target) > 2:
                continue
            matched.append(q)

        # 按难度排序 (接近目标的优先)
        matched.sort(key=lambda q: abs(q.difficulty - req.difficulty_target))

        # 保证有难度锚点: 尝试加一道简单的和一道难的
        anchors = []
        for q in self.qb:
            if q.id in req.exclusion_ids:
                continue
            if set(q.tags) & set(req.topics):
                if q.difficulty <= req.difficulty_target - 3:
                    anchors.append(q)
                    break
        for q in self.qb:
            if q.id in req.exclusion_ids:
                continue
            if set(q.tags) & set(req.topics):
                if q.difficulty >= req.difficulty_target + 3:
                    anchors.append(q)
                    break

        return matched[:6] + anchors

    def _find_negatives(self, req: GenerationRequest) -> list[Question]:
        """变式模式下，找与源题最相似的已有题作为负样本"""
        if not req.source_question:
            return []
        # 简单版: 同专题同题型的题
        return [
            q for q in self.qb
            if q.id != req.source_question.id
            and set(q.tags) & set(req.source_question.tags)
            and q.question_type == req.source_question.question_type
        ][:5]


# ══════════════════════════════════════════════════════
#  题目生成器
# ══════════════════════════════════════════════════════

class QuestionGenerator:
    """Stage 2: 调用 LLM 生成候选题目"""

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient(LLMConfig.for_task("generate"))

    def generate_candidates(
        self,
        request: GenerationRequest,
        context: ContextBundle,
        n: int = 3,
        extra_temperature: float = 0.0,
    ) -> list[Question]:
        """生成 n 道候选题"""

        prompt = self._build_prompt(request, context)
        candidates = []

        for i in range(n):
            temp = self.llm.config.temperature + extra_temperature + i * 0.05
            temp = min(temp, 1.0)

            try:
                result = self.llm.generate_json(
                    prompt=prompt,
                    temperature=temp,
                )
                q = self._parse_candidate(result, request)
                if q:
                    candidates.append(q)
                    logger.info(f"  候选 {i+1}: 生成成功 (自评难度 {result.get('estimated_difficulty', '?')})")
            except Exception as e:
                logger.warning(f"  候选 {i+1}: 生成失败 — {e}")

        return candidates

    def _build_prompt(self, req: GenerationRequest, ctx: ContextBundle) -> str:
        """根据模式构建 Prompt"""

        strategy_instruction = STRATEGY_INSTRUCTIONS.get(req.strategy.value, "")
        difficulty_cal = DIFFICULTY_CALIBRATION.format(target=req.difficulty_target)
        context_block = ctx.to_prompt_block()

        if req.source_question:
            # 变式模式
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
            # 自由模式
            return FREE_GENERATION_PROMPT.format(
                subject=req.subject.value,
                topics=", ".join(req.topics),
                target_difficulty=req.difficulty_target,
                question_type=req.question_type.value,
                strategy_instruction=strategy_instruction,
                difficulty_calibration=difficulty_cal,
                context_block=context_block,
            )

    def _parse_candidate(self, data: dict, req: GenerationRequest) -> Optional[Question]:
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
            tags=data.get("knowledge_points_used", []) + req.topics,
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
        import re
        text = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', text)
        text = re.sub(r'[\\${}^_]', '', text)
        return text.strip()


# ══════════════════════════════════════════════════════
#  求解引擎
# ══════════════════════════════════════════════════════

class SolveEngine:
    """Stage 3: 对候选题独立求解"""

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient(LLMConfig.for_task("solve"))

    def solve(self, question: Question, context: ContextBundle) -> Optional[Solution]:
        """求解一道题，返回 Solution 或 None (解不出来)"""

        kp_context = "\n".join(
            f"- {kp.title}: {kp.content_latex[:200]}"
            for kp in context.knowledge_points[:5]
        )

        prompt = SOLVE_PROMPT.format(
            question_latex=question.content_latex,
            knowledge_context=kp_context,
        )

        try:
            result = self.llm.generate_json(prompt=prompt, temperature=0.0)
        except Exception as e:
            logger.warning(f"  求解失败: {e}")
            return None

        confidence = result.get("confidence", "low")
        if confidence == "low":
            logger.info("  求解置信度 low，标记为不可靠")

        return Solution(
            question_id=question.id,
            solution_latex=result.get("solution_latex", ""),
            methods_used=result.get("methods_used", []),
            step_count=result.get("step_count", 0),
            confidence=confidence,
        )


# ══════════════════════════════════════════════════════
#  验证管线
# ══════════════════════════════════════════════════════

class VerificationPipeline:
    """Stage 4: 多层验证"""

    def __init__(self, llm: Optional[LLMClient] = None):
        # 用独立的 LLM 实例做审核，避免确认偏差
        self.review_llm = llm or LLMClient(LLMConfig.for_task("review"))

    def verify(
        self,
        candidate: Question,
        solution: Solution,
        context: ContextBundle,
    ) -> VerificationResult:
        """执行全部验证检查"""

        checks = {}
        suggestions = []

        # Check 1: 解题置信度
        checks["solve_confidence"] = solution.confidence in ("high", "medium")
        if not checks["solve_confidence"]:
            suggestions.append("解题引擎置信度低，题目可能有问题。")

        # Check 2: 数学正确性 (独立审核)
        math_review = self._check_math(candidate)
        checks["math_correctness"] = math_review["passed"]
        if not checks["math_correctness"]:
            suggestions.extend(math_review.get("issues", []))

        # Check 3: 非平凡性 (不是过于简单或无意义)
        checks["non_trivial"] = len(candidate.content_latex) > 30

        passed = all(checks.values())

        return VerificationResult(
            passed=passed,
            checks=checks,
            suggestions="\n".join(suggestions) if suggestions else "",
        )

    def _check_math(self, question: Question) -> dict:
        """独立数学审核"""
        prompt = REVIEW_PROMPT.format(question_latex=question.content_latex)

        try:
            result = self.review_llm.generate_json(prompt=prompt, temperature=0.0)
            verdict = result.get("verdict", "FAIL")
            return {
                "passed": verdict == "PASS",
                "issues": result.get("issues", []),
                "suggestions": result.get("suggestions", ""),
            }
        except Exception as e:
            logger.warning(f"  审核调用失败: {e}")
            return {"passed": False, "issues": [str(e)]}


# ══════════════════════════════════════════════════════
#  难度标注器
# ══════════════════════════════════════════════════════

class DifficultyAnnotator:
    """Stage 5: 多维难度评分"""

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient(LLMConfig.for_task("tag"))

    def annotate(self, question: Question, solution: Solution) -> DifficultyScore:
        """计算多维难度分"""

        sketch = solution.solution_latex[:300] if solution.solution_latex else "无解答"

        prompt = DIFFICULTY_SCORING_PROMPT.format(
            question_latex=question.content_latex,
            solution_sketch=sketch,
        )

        try:
            result = self.llm.generate_json(prompt=prompt, temperature=0.0)
            scores = result.get("scores", {})

            ds = DifficultyScore(
                concept_count=scores.get("concept_count", 5),
                step_count=scores.get("step_count", 5),
                abstraction_level=scores.get("abstraction_level", 5),
                technique_novelty=scores.get("technique_novelty", 5),
                condition_parsing=scores.get("condition_parsing", 5),
                computation_load=scores.get("computation_load", 5),
                proof_structure=scores.get("proof_structure", 5),
                prerequisite_depth=scores.get("prerequisite_depth", 5),
                multi_representation=scores.get("multi_representation", 5),
                metacognition=scores.get("metacognition", 5),
                reasoning=result.get("overall_reasoning", ""),
            )
            ds.compute_overall()
            return ds

        except Exception as e:
            logger.warning(f"  难度标注失败: {e}")
            ds = DifficultyScore(reasoning="标注失败，使用默认值")
            ds.overall = float(question.difficulty)
            return ds


# ══════════════════════════════════════════════════════
#  生成-验证 主循环
# ══════════════════════════════════════════════════════

class GenerationEngine:
    """
    完整的 生成 → 求解 → 验证 → 标注 闭环。
    最多 MAX_ROUNDS 轮，每轮 CANDIDATES_PER_ROUND 个候选。
    """

    MAX_ROUNDS = 3
    CANDIDATES_PER_ROUND = 3

    def __init__(
        self,
        retriever: ContextRetriever,
        generator: Optional[QuestionGenerator] = None,
        solver: Optional[SolveEngine] = None,
        verifier: Optional[VerificationPipeline] = None,
        annotator: Optional[DifficultyAnnotator] = None,
    ):
        self.retriever = retriever
        self.generator = generator or QuestionGenerator()
        self.solver = solver or SolveEngine()
        self.verifier = verifier or VerificationPipeline()
        self.annotator = annotator or DifficultyAnnotator()

    def generate(self, request: GenerationRequest) -> Optional[GeneratedQuestion]:
        """
        主入口: 尝试生成一道通过验证的高质量题目。
        返回 None 表示多轮尝试后仍未成功。
        """
        logger.info(f"═══ 开始生成 ═══")
        logger.info(f"  科目: {request.subject.value}")
        logger.info(f"  专题: {request.topics}")
        logger.info(f"  目标难度: {request.difficulty_target}")
        logger.info(f"  策略: {request.strategy.value}")
        logger.info(f"  题型: {request.question_type.value}")

        # Stage 1: 检索上下文
        logger.info(f"\n[Stage 1] 检索上下文...")
        context = self.retriever.build_context(request)
        logger.info(f"  知识点: {len(context.knowledge_points)} 条")
        logger.info(f"  参考题: {len(context.reference_questions)} 道")
        logger.info(f"  负样本: {len(context.negative_examples)} 道")

        for round_num in range(self.MAX_ROUNDS):
            logger.info(f"\n[Round {round_num + 1}/{self.MAX_ROUNDS}]")

            # Stage 2: 生成候选
            logger.info(f"  [Stage 2] 生成候选题...")
            candidates = self.generator.generate_candidates(
                request=request,
                context=context,
                n=self.CANDIDATES_PER_ROUND,
                extra_temperature=round_num * 0.1,
            )
            logger.info(f"  生成了 {len(candidates)} 道候选题")

            for idx, candidate in enumerate(candidates):
                logger.info(f"\n  --- 候选 {idx+1} ---")
                logger.info(f"  题干预览: {candidate.content_latex[:100]}...")

                # Stage 3: 求解
                logger.info(f"  [Stage 3] 求解中...")
                solution = self.solver.solve(candidate, context)
                if not solution or solution.confidence == "low":
                    logger.info(f"  ✗ 求解失败或置信度低，跳过")
                    continue
                logger.info(f"  求解完成 (置信度: {solution.confidence})")

                # Stage 4: 验证
                logger.info(f"  [Stage 4] 验证中...")
                verification = self.verifier.verify(candidate, solution, context)

                if verification.passed:
                    logger.info(f"  ✓ 验证通过!")

                    # Stage 5: 标注
                    logger.info(f"  [Stage 5] 难度标注...")
                    difficulty_score = self.annotator.annotate(candidate, solution)
                    candidate.difficulty_ai = difficulty_score.overall
                    logger.info(f"  综合难度: {difficulty_score.overall}/10")
                    logger.info(f"  理由: {difficulty_score.reasoning}")

                    return GeneratedQuestion(
                        question=candidate,
                        solution=solution,
                        difficulty_score=difficulty_score,
                        verification=verification,
                        generation_meta={
                            "round": round_num + 1,
                            "strategy": request.strategy.value,
                            "context_kps": len(context.knowledge_points),
                        },
                    )
                else:
                    failed = [k for k, v in verification.checks.items() if not v]
                    logger.info(f"  ✗ 验证未通过: {failed}")
                    if verification.has_suggestions:
                        context.feedback_from_previous_round = verification.suggestions

        logger.info(f"\n═══ {self.MAX_ROUNDS} 轮后仍未生成合格题目 ═══")
        return None

    def generate_batch(
        self,
        request: GenerationRequest,
        count: int,
    ) -> list[GeneratedQuestion]:
        """批量生成"""
        results = []
        for i in range(count):
            logger.info(f"\n{'='*60}")
            logger.info(f"批量生成 [{i+1}/{count}]")
            logger.info(f"{'='*60}")

            result = self.generate(request)
            if result:
                results.append(result)
                # 将已生成题加入排除集，避免批次内重复
                request.exclusion_ids.add(result.question.id)

        logger.info(f"\n批量生成完成: {len(results)}/{count} 成功")
        return results
