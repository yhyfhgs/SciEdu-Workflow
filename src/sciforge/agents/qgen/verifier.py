"""VerificationPipeline — QGen 第 4 阶段：多层验证"""

from __future__ import annotations

import structlog

from sciforge.agents.qgen.verification_sandbox import (
    SandboxLimits,
    SandboxStatus,
    TrustPolicy,
    VerificationExecutionResult,
    VerificationSandbox,
)
from sciforge.core.models import (
    CodeExecutionResult,
    ContextBundle,
    DifficultyScore,
    Discipline,
    NumericalVerification,
    Question,
    Solution,
    VerificationEvidence,
    VerificationEvidenceStatus,
    VerificationResult,
)
from sciforge.llm.client import LLMClient
from sciforge.pipeline.retry import call_llm_json_with_retry
from sciforge.prompts.templates import get_generation_prompt_template

logger = structlog.get_logger()


class VerificationPipeline:
    """对候选题 + 解答做多层验证

    Check 清单:
    1. solve_confidence: solver 的置信度不为 low
    2. review_passed: 独立 LLM 审核判断题目逻辑是否成立
    3. non_trivial: 题干长度足够
    4. solution_relevant: 解答确实在解这道题（G5，套餐可配，默认开）
    5. numerical_valid: sympy/numpy 沙箱数值验证未发现矛盾（G4，opt-in）
    """

    def __init__(
        self,
        review_llm: LLMClient,
        discipline: Discipline = Discipline.MATH,
        *,
        enable_solution_relevance: bool = True,
        enable_numerical_verification: bool = False,
        require_numerical_verification: bool = False,
        enable_lean_verification: bool = False,
        require_lean_verification: bool = False,
        sandbox_timeout: int = 10,
        sandbox: VerificationSandbox | None = None,
        sandbox_trust_policy: TrustPolicy | str = TrustPolicy.CONTAINER_REQUIRED,
    ) -> None:
        if require_numerical_verification and not enable_numerical_verification:
            raise ValueError(
                "require_numerical_verification requires enable_numerical_verification"
            )
        if require_lean_verification and not enable_lean_verification:
            raise ValueError("require_lean_verification requires enable_lean_verification")
        self.review_llm = review_llm  # 独立实例避免确认偏差
        self.discipline = discipline
        self.enable_solution_relevance = enable_solution_relevance
        self.enable_numerical_verification = enable_numerical_verification
        self.require_numerical_verification = require_numerical_verification
        self.enable_lean_verification = enable_lean_verification
        self.require_lean_verification = require_lean_verification
        self.sandbox_timeout = sandbox_timeout
        self.sandbox = sandbox or VerificationSandbox(
            limits=SandboxLimits(timeout_seconds=float(sandbox_timeout))
        )
        self.sandbox_trust_policy = TrustPolicy(sandbox_trust_policy)
        self._review_template = get_generation_prompt_template(
            "verification/review.txt", discipline
        )
        self._relevance_template = (
            get_generation_prompt_template("verification/solution_relevance.txt", discipline)
            if enable_solution_relevance
            else ""
        )
        self._numerical_template = (
            get_generation_prompt_template("verification/generate_numerical_test.txt", discipline)
            if enable_numerical_verification
            else ""
        )
        self._lean_template = (
            get_generation_prompt_template("verification/generate_lean_test.txt", discipline)
            if enable_lean_verification
            else ""
        )

    async def verify(
        self,
        candidate: Question,
        solution: Solution,
        context: ContextBundle | None = None,
        *,
        review_instruction: str = "",
    ) -> VerificationResult:
        """执行全部验证检查"""
        checks: dict[str, bool] = {}
        suggestions: list[str] = []
        numerical_verification: NumericalVerification | None = None
        evidence: list[VerificationEvidence] = []
        solution_text = (solution.solution_latex or solution.solution_plain).strip()

        # Check 1: 解题置信度
        checks["solve_confidence"] = solution.confidence in ("high", "medium")
        evidence.append(
            VerificationEvidence(
                kind="solve_confidence",
                status=(
                    VerificationEvidenceStatus.PASSED
                    if checks["solve_confidence"]
                    else VerificationEvidenceStatus.FAILED
                ),
                required=True,
                backend="independent_solver",
                summary=f"solver confidence={solution.confidence}",
            )
        )
        if not checks["solve_confidence"]:
            suggestions.append("求解引擎置信度低，题目可能存在问题")

        # Check 2: 数学/物理正确性（独立审核 LLM）
        review = await self._check_with_review_llm(
            candidate,
            solution,
            review_instruction=review_instruction,
        )
        raw_review_issues = review.get("issues", [])
        review_issues = (
            [str(issue) for issue in raw_review_issues]
            if isinstance(raw_review_issues, list)
            else [str(raw_review_issues)]
            if raw_review_issues
            else []
        )
        checks["review_passed"] = bool(review.get("passed", False))
        evidence.append(
            VerificationEvidence(
                kind="independent_review",
                status=(
                    VerificationEvidenceStatus.PASSED
                    if checks["review_passed"]
                    else VerificationEvidenceStatus.FAILED
                ),
                required=True,
                backend=_llm_backend_name(self.review_llm),
                summary=str(review.get("suggestions") or "independent review completed"),
                details={"issues": review_issues},
            )
        )
        if not checks["review_passed"]:
            suggestions.extend(review_issues)
            reviewer_suggestion = str(review.get("suggestions") or "").strip()
            if reviewer_suggestion:
                suggestions.append(reviewer_suggestion)

        # Check 3: 非平凡性
        checks["non_trivial"] = len(candidate.content) > 30
        evidence.append(
            VerificationEvidence(
                kind="non_trivial",
                status=(
                    VerificationEvidenceStatus.PASSED
                    if checks["non_trivial"]
                    else VerificationEvidenceStatus.FAILED
                ),
                required=True,
                backend="deterministic",
                summary=f"question length={len(candidate.content)}",
            )
        )

        # Check 4 (G5): 解答相关性。启用后属于核心门禁；缺正文或基础设施错误
        # 形成显式 error 证据，绝不能静默跳过后让 all(checks) 误判为通过。
        if self.enable_solution_relevance:
            relevance_evidence = (
                await self._check_solution_relevance(candidate, solution)
                if solution_text
                else VerificationEvidence(
                    kind="solution_relevance",
                    status=VerificationEvidenceStatus.ERROR,
                    required=True,
                    backend="deterministic",
                    summary="solution relevance cannot run without solution text",
                )
            )
            evidence.append(relevance_evidence)
            checks["solution_relevant"] = (
                relevance_evidence.status is VerificationEvidenceStatus.PASSED
            )
            if not checks["solution_relevant"]:
                suggestions.append(f"解答相关性未通过：{relevance_evidence.summary}")

        # Check 5 (G4): 数值验证 —— 生成 sympy/numpy 代码在安全策略沙箱执行（opt-in）。
        # 数学反例=failed；执行失败/超时=error；后两者仅在 required policy 下阻止发布。
        if self.enable_numerical_verification:
            if solution_text:
                numerical_verification, numerical_evidence = await self._check_numerical(
                    candidate,
                    solution,
                    required=self.require_numerical_verification,
                )
            else:
                numerical_verification = NumericalVerification(all_passed=False)
                numerical_evidence = VerificationEvidence(
                    kind="numerical",
                    status=VerificationEvidenceStatus.ERROR,
                    required=self.require_numerical_verification,
                    backend="deterministic",
                    summary="numerical verification cannot run without solution text",
                )
            evidence.append(numerical_evidence)
            if (
                numerical_evidence.status
                in {
                    VerificationEvidenceStatus.PASSED,
                    VerificationEvidenceStatus.FAILED,
                }
                or self.require_numerical_verification
            ):
                checks["numerical_valid"] = (
                    numerical_evidence.status is VerificationEvidenceStatus.PASSED
                )
            if numerical_evidence.status is VerificationEvidenceStatus.FAILED:
                suggestions.append(
                    "数值验证发现矛盾：" + "; ".join(numerical_verification.discrepancies[:3])
                )
            elif (
                self.require_numerical_verification
                and numerical_evidence.status is not VerificationEvidenceStatus.PASSED
            ):
                suggestions.append(f"必需的数值验证未完成：{numerical_evidence.summary}")

        if self.enable_lean_verification:
            lean_evidence = (
                await self._check_lean(
                    candidate,
                    solution,
                    required=self.require_lean_verification,
                )
                if solution_text
                else VerificationEvidence(
                    kind="lean",
                    status=VerificationEvidenceStatus.ERROR,
                    required=self.require_lean_verification,
                    backend="deterministic",
                    summary="Lean verification cannot run without solution text",
                )
            )
            evidence.append(lean_evidence)
            if (
                lean_evidence.status
                in {
                    VerificationEvidenceStatus.PASSED,
                    VerificationEvidenceStatus.FAILED,
                }
                or self.require_lean_verification
            ):
                checks["lean_valid"] = lean_evidence.status is VerificationEvidenceStatus.PASSED
            if (
                self.require_lean_verification
                and lean_evidence.status is not VerificationEvidenceStatus.PASSED
            ):
                suggestions.append(f"必需的 Lean 验证未通过：{lean_evidence.summary}")

        # Phase 6c: 从 review 提取 difficulty_scores（若 prompt 按新模板输出）
        difficulty_score: DifficultyScore | None = None
        raw_scores = review.get("difficulty_scores")
        if isinstance(raw_scores, dict) and checks["review_passed"]:
            try:
                difficulty_score = DifficultyScore(
                    concept_count=int(raw_scores.get("concept_count", 5)),
                    step_count=int(raw_scores.get("step_count", 5)),
                    abstraction_level=int(raw_scores.get("abstraction_level", 5)),
                    technique_novelty=int(raw_scores.get("technique_novelty", 5)),
                    condition_parsing=int(raw_scores.get("condition_parsing", 5)),
                    computation_load=int(raw_scores.get("computation_load", 5)),
                    proof_structure=int(raw_scores.get("proof_structure", 5)),
                    prerequisite_depth=int(raw_scores.get("prerequisite_depth", 5)),
                    multi_representation=int(raw_scores.get("multi_representation", 5)),
                    metacognition=int(raw_scores.get("metacognition", 5)),
                    reasoning=str(review.get("difficulty_reasoning") or ""),
                )
                difficulty_score.compute_overall()
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "verify_difficulty_parse_failed",
                    error=str(exc)[:200],
                )
                difficulty_score = None

        passed = all(checks.values())
        return VerificationResult(
            passed=passed,
            checks=checks,
            suggestions="\n".join(suggestions) if suggestions else "",
            difficulty_score=difficulty_score,
            numerical_verification=numerical_verification,
            evidence=evidence,
        )

    async def _check_with_review_llm(
        self,
        question: Question,
        solution: Solution,
        *,
        review_instruction: str = "",
    ) -> dict[str, object]:
        """独立审核题面与完整解答，避免只看题面的确认偏差。"""
        prompt = self._review_template.format(
            question_latex=question.content,
            solution_latex=solution.solution_latex or solution.solution_plain,
        )
        instruction = review_instruction.strip()[:2000]
        if instruction:
            prompt += (
                "\n\n# 用户指定的评估关注点\n"
                f"{instruction}\n\n"
                "该关注点仅补充审查维度，不得放宽既有正确性与证据门禁。"
            )

        try:
            result = await call_llm_json_with_retry(self.review_llm, prompt, max_attempts=2)
            verdict = str(result.get("verdict", "FAIL")).upper()
            return {
                "passed": verdict == "PASS",
                "issues": list(result.get("issues") or []),
                "suggestions": str(result.get("suggestions") or ""),
                # Phase 6c: 透传难度评分字段，由调用方决定是否使用
                "difficulty_scores": result.get("difficulty_scores"),
                "difficulty_reasoning": result.get("difficulty_reasoning", ""),
            }
        except Exception as exc:
            logger.warning("review_failed", error=str(exc)[:200])
            return {"passed": False, "issues": [f"review_error: {exc}"]}

    async def _check_solution_relevance(
        self, question: Question, solution: Solution
    ) -> VerificationEvidence:
        """G5：判断解答是否在尝试解这道题（方向/主题一致即可，不要求正确或完整）"""
        prompt = self._relevance_template.format(
            question_latex=question.content,
            solution_latex=solution.solution_latex or solution.solution_plain,
        )
        try:
            result = await call_llm_json_with_retry(self.review_llm, prompt, max_attempts=2)
            relevant = result.get("relevant")
            if not isinstance(relevant, bool):
                return VerificationEvidence(
                    kind="solution_relevance",
                    status=VerificationEvidenceStatus.ERROR,
                    required=True,
                    backend=_llm_backend_name(self.review_llm),
                    summary="solution relevance verifier returned no boolean verdict",
                )
            reason = str(result.get("reason") or "").strip()
            return VerificationEvidence(
                kind="solution_relevance",
                status=(
                    VerificationEvidenceStatus.PASSED
                    if relevant
                    else VerificationEvidenceStatus.FAILED
                ),
                required=True,
                backend=_llm_backend_name(self.review_llm),
                summary=reason
                or (
                    "solution addresses the candidate question"
                    if relevant
                    else "solution does not address the candidate question"
                ),
            )
        except Exception as exc:
            logger.warning("solution_relevance_failed", error=str(exc)[:200])
            return VerificationEvidence(
                kind="solution_relevance",
                status=VerificationEvidenceStatus.ERROR,
                required=True,
                backend=_llm_backend_name(self.review_llm),
                summary=f"solution relevance verification failed: {exc}",
            )

    async def _check_numerical(
        self,
        question: Question,
        solution: Solution,
        *,
        required: bool,
    ) -> tuple[NumericalVerification, VerificationEvidence]:
        """生成数值检查并在显式信任策略的 sandbox 中执行。"""
        prompt = self._numerical_template.format(
            question_latex=question.content,
            solution_sketch=solution.solution_latex or solution.solution_plain,
        )
        try:
            data = await call_llm_json_with_retry(self.review_llm, prompt, max_attempts=2)
        except Exception as exc:
            logger.warning("numerical_codegen_failed", error=str(exc)[:200])
            return NumericalVerification(all_passed=False), VerificationEvidence(
                kind="numerical",
                status=VerificationEvidenceStatus.ERROR,
                required=required,
                backend=_llm_backend_name(self.review_llm),
                summary=f"numerical verification code generation failed: {exc}",
            )

        code = str(data.get("verification_code") or "").strip()
        if not code:
            return NumericalVerification(all_passed=False), VerificationEvidence(
                kind="numerical",
                status=VerificationEvidenceStatus.ERROR,
                required=required,
                backend=_llm_backend_name(self.review_llm),
                summary="numerical verifier returned no executable code",
            )

        sandbox_result = await self.sandbox.arun_python(
            code,
            trust_policy=self.sandbox_trust_policy,
        )
        stdout = sandbox_result.stdout or ""
        all_passed = "ALL_PASSED" in stdout
        discrepancies = [
            line.strip() for line in stdout.splitlines() if line.strip().startswith("FAIL")
        ]
        explicit_failure = bool(discrepancies) or "SOME_FAILED" in stdout
        if explicit_failure and not discrepancies:
            discrepancies.append("verification reported SOME_FAILED")
        evidence_status = _execution_evidence_status(
            sandbox_result,
            passed=all_passed,
            explicit_failure=explicit_failure,
        )
        execution = CodeExecutionResult(
            code=code,
            stdout=sandbox_result.stdout,
            stderr=sandbox_result.stderr,
            success=sandbox_result.success,
            timeout=sandbox_result.timed_out,
            execution_time_ms=sandbox_result.duration_ms,
        )
        numerical = NumericalVerification(
            all_passed=all_passed,
            discrepancies=discrepancies,
            code_used=code,
            execution=execution,
        )
        summary = sandbox_result.reason or (
            "all generated numerical checks passed"
            if evidence_status is VerificationEvidenceStatus.PASSED
            else "numerical verification found a counterexample"
            if evidence_status is VerificationEvidenceStatus.FAILED
            else "numerical verifier did not produce a conclusive result"
        )
        return numerical, _sandbox_evidence(
            "numerical",
            sandbox_result,
            evidence_status,
            required=required,
            summary=summary,
            artifact=code,
            details={"discrepancies": discrepancies},
        )

    async def _check_lean(
        self,
        question: Question,
        solution: Solution,
        *,
        required: bool,
    ) -> VerificationEvidence:
        prompt = self._lean_template.format(
            question_latex=question.content,
            solution_latex=solution.solution_latex or solution.solution_plain,
        )
        try:
            data = await call_llm_json_with_retry(self.review_llm, prompt, max_attempts=2)
        except Exception as exc:
            return VerificationEvidence(
                kind="lean",
                status=VerificationEvidenceStatus.ERROR,
                required=required,
                backend=_llm_backend_name(self.review_llm),
                summary=f"Lean formalization generation failed: {exc}",
            )
        source = str(data.get("lean_source") or "").strip()
        assumptions = list(data.get("translation_assumptions") or [])
        if not source:
            return VerificationEvidence(
                kind="lean",
                status=VerificationEvidenceStatus.ERROR,
                required=required,
                backend=_llm_backend_name(self.review_llm),
                summary="Lean formalizer returned no theorem source",
                details={"translation_assumptions": assumptions},
            )
        result = await self.sandbox.averify_lean(
            source,
            trust_policy=self.sandbox_trust_policy,
        )
        status = {
            SandboxStatus.SUCCEEDED: VerificationEvidenceStatus.PASSED,
            SandboxStatus.FAILED: VerificationEvidenceStatus.FAILED,
            SandboxStatus.UNAVAILABLE: VerificationEvidenceStatus.UNAVAILABLE,
            SandboxStatus.POLICY_DENIED: VerificationEvidenceStatus.UNAVAILABLE,
            SandboxStatus.TIMED_OUT: VerificationEvidenceStatus.ERROR,
        }[result.status]
        summary = result.reason or (
            "Lean theorem compiled without admitted proofs"
            if status is VerificationEvidenceStatus.PASSED
            else "Lean compiler rejected the formal proof"
        )
        return _sandbox_evidence(
            "lean",
            result,
            status,
            required=required,
            summary=summary,
            artifact=source,
            details={"translation_assumptions": assumptions},
        )


def _llm_backend_name(llm: LLMClient) -> str:
    config = getattr(llm, "config", None)
    provider = getattr(config, "provider", None)
    model = str(getattr(config, "model", "") or "")
    provider_name = str(getattr(provider, "value", provider) or "llm")
    return f"{provider_name}:{model}" if model else provider_name


def _execution_evidence_status(
    result: VerificationExecutionResult,
    *,
    passed: bool,
    explicit_failure: bool,
) -> VerificationEvidenceStatus:
    if result.status in {SandboxStatus.UNAVAILABLE, SandboxStatus.POLICY_DENIED}:
        return VerificationEvidenceStatus.UNAVAILABLE
    if result.status in {SandboxStatus.TIMED_OUT}:
        return VerificationEvidenceStatus.ERROR
    if result.status is SandboxStatus.FAILED:
        return VerificationEvidenceStatus.ERROR
    if explicit_failure:
        return VerificationEvidenceStatus.FAILED
    if passed:
        return VerificationEvidenceStatus.PASSED
    return VerificationEvidenceStatus.ERROR


def _sandbox_evidence(
    kind: str,
    result: VerificationExecutionResult,
    status: VerificationEvidenceStatus,
    *,
    required: bool,
    summary: str,
    artifact: str,
    details: dict[str, object],
) -> VerificationEvidence:
    return VerificationEvidence(
        kind=kind,
        status=status,
        required=required,
        backend=result.backend,
        isolation=result.isolation.value,
        summary=summary,
        stdout=result.stdout,
        stderr=result.stderr,
        diagnostics=list(result.diagnostics),
        duration_ms=result.duration_ms,
        artifact=artifact,
        details=details,
    )
