"""确定性知识条目评价与质量门禁（v11 子系统 IV / M6）。

默认评价完全不调用 LLM。它把已有结构证据转成版本化 rubric：

- 提取质量：跨度、taxonomy/tag、数学配平、OCR 可疑片段、concept 治理状态；
- 题答/定理证明完整性；
- 与 QGen 共用的 10 维 ``DifficultyScore``；
- Bloom 层级、前置深度、证明复杂度、讲义适用性、跨书通用性。

低质量条目不会被删除，而是进入 ``review_queue``；上层生成池可用质量查询门禁排除。
"""

from __future__ import annotations

import math
import re
from typing import Any

from sciforge.core.models import (
    CognitiveLevel,
    DifficultyScore,
    EvaluationBatch,
    EvaluationSource,
    ItemEvaluation,
    ReviewQueueItem,
)
from sciforge.pipeline.text_tools.math_balance import check_math_balance
from sciforge.pipeline.text_tools.ocr_repair import repair_ocr_noise

_THEOREM_LIKE = frozenset({"theorem", "lemma", "corollary", "proposition", "law"})
_CREATE_TYPES = frozenset(
    {"construction", "constructive_proof", "experiment_design", "exploration"}
)
_ANALYZE_TYPES = frozenset({"proof", "existence_proof", "counterexample", "discrimination"})
_APPLY_TYPES = frozenset({"calculation", "application"})
_CONTEXT_DEPENDENCY_RE = re.compile(
    r"(?:本节|本章|上文|下文|前面|后面|如下图|上图|下图|见图|见表|见第\s*\d|"
    r"as\s+(?:above|below)|previous\s+(?:section|chapter))",
    re.IGNORECASE,
)
_LOGIC_TOKEN_RE = re.compile(
    r"(?:若|则|当且仅当|充分|必要|任意|存在|否则|因此|从而|假设|设|"
    r"\bif\b|\bthen\b|\biff\b|\\forall|\\exists|\\implies|\\iff)",
    re.IGNORECASE,
)
_STEP_RE = re.compile(r"(?:^|\n)\s*(?:第?[一二三四五六七八九十\d]+[步.)、]|\(\d+\)|[-*])")
_COMPUTE_RE = re.compile(r"[=+\-*/^∫∑∏]|\\(?:frac|int|sum|prod|sqrt|lim)\b")
_PROOF_RE = re.compile(
    r"(?:证明|证毕|由此|反证|归纳|必要性|充分性|without\s+loss|contradiction|qed)",
    re.IGNORECASE,
)
_META_RE = re.compile(r"(?:为什么|解释|比较|评价|反思|另证|推广|why|explain|compare)", re.I)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return round(min(high, max(low, value)), 4)


def _score_1_to_10(value: float) -> int:
    return max(1, min(10, int(round(value))))


def _cognitive_level(item_kind: str, item_type: str, content: str) -> CognitiveLevel:
    if item_type in _CREATE_TYPES:
        return CognitiveLevel.CREATE
    if item_type in _ANALYZE_TYPES:
        return CognitiveLevel.ANALYZE
    if item_type in _APPLY_TYPES:
        return CognitiveLevel.APPLY
    if item_type in {"definition", "formula"} and item_kind == "knowledge":
        return CognitiveLevel.UNDERSTAND
    if any(token in content for token in ("评价", "判断优劣", "证明或反驳")):
        return CognitiveLevel.EVALUATE
    return CognitiveLevel.UNDERSTAND


def _difficulty_dimensions(row: dict[str, Any], text: str, reasoning: str) -> DifficultyScore:
    stored = float(row.get("difficulty") or 5)
    concept_count = int(row.get("concept_count") or 0)
    depth = int(row.get("concept_depth") or 0)
    step_count = len(_STEP_RE.findall(reasoning)) + max(0, reasoning.count("\n") // 4)
    logic_count = len(_LOGIC_TOKEN_RE.findall(text + "\n" + reasoning))
    compute_count = len(_COMPUTE_RE.findall(text + "\n" + reasoning))
    proof_count = len(_PROOF_RE.findall(reasoning))
    latex_commands = len(set(re.findall(r"\\[A-Za-z]+", text + reasoning)))
    has_figure = int(row.get("figure_count") or 0) > 0
    has_options = bool(row.get("options"))
    has_math = bool(re.search(r"\$|\\\[|\\begin\{", text + reasoning))

    dims = DifficultyScore(
        concept_count=_score_1_to_10(1 + concept_count * 2 + depth * 0.4),
        step_count=_score_1_to_10(1 + step_count * 1.3),
        abstraction_level=_score_1_to_10(
            stored * 0.55
            + (3.0 if str(row.get("type") or "") in _THEOREM_LIKE | _ANALYZE_TYPES else 1.0)
        ),
        technique_novelty=_score_1_to_10(1 + latex_commands * 0.55 + proof_count * 0.8),
        condition_parsing=_score_1_to_10(1 + logic_count * 0.75),
        computation_load=_score_1_to_10(1 + compute_count * 0.3),
        proof_structure=_score_1_to_10(1 + proof_count * 1.4 + step_count * 0.5),
        prerequisite_depth=_score_1_to_10(1 + depth * 1.5),
        multi_representation=_score_1_to_10(
            1 + 2 * has_figure + 1.5 * has_options + 1.5 * has_math
        ),
        metacognition=_score_1_to_10(1 + len(_META_RE.findall(text + reasoning)) * 2.0),
        reasoning="pipeline_auto:v1 deterministic features",
    )
    heuristic = dims.compute_overall()
    # 既有 Stage2 difficulty 是用户可见的稳定锚点；确定性特征只做 20% 校准，
    # 避免一次 rubric 上线就让历史题目跨越多个难度档位。
    dims.overall = round(min(10.0, max(1.0, 0.8 * stored + 0.2 * heuristic)), 1)
    return dims


def _extraction_quality(row: dict[str, Any], text: str) -> tuple[float, list[str]]:
    ingestion_status = str(row.get("ingestion_status") or "accepted")
    if ingestion_status != "accepted":
        # 摄取状态是比正文启发式更强的治理证据。即使占位片段恰好很长、数学符号
        # 配平且带 taxonomy，也不能被后续确定性特征“加分”回默认生成池。
        return 0.0, [f"ingestion_status:{ingestion_status}"]
    score = 1.0
    signals: list[str] = []
    if not text.strip():
        return 0.0, ["empty_content"]
    if len(text.strip()) < 20:
        score -= 0.15
        signals.append("very_short_content")
    start_line = int(row.get("start_line") or 0)
    end_line = int(row.get("end_line") or 0)
    if start_line <= 0 or end_line < start_line:
        score -= 0.25
        signals.append("invalid_or_missing_line_span")
    if not row.get("taxonomy_path"):
        score -= 0.12
        signals.append("missing_taxonomy")
    if not row.get("tags"):
        score -= 0.1
        signals.append("missing_tags")
    if str(row.get("concept_status") or "") == "pending":
        score -= 0.25
        signals.append("pending_concept")

    balance = check_math_balance(text.splitlines() or [text])
    if not balance.balanced:
        score -= min(0.35, 0.12 + len(balance.issues) * 0.05)
        signals.append("unbalanced_math")
    ocr = repair_ocr_noise(text)
    suspicious = sum(span.kind == "char_confusion" for span in ocr.confidence_spans)
    if suspicious:
        score -= min(0.15, suspicious * 0.04)
        signals.append("ocr_char_confusion")
    return _clamp(score), signals


def _solution_completeness(row: dict[str, Any], reasoning: str) -> tuple[float, list[str]]:
    item_kind = str(row.get("kind") or "knowledge")
    item_type = str(row.get("type") or "")
    signals: list[str] = []
    if item_kind == "question":
        if reasoning.strip():
            score = 1.0
        elif row.get("correct_option"):
            score = 0.6
            signals.append("answer_without_explanation")
        else:
            score = 0.15
            signals.append("missing_solution")
    elif item_type in _THEOREM_LIKE:
        score = 1.0 if reasoning.strip() else 0.4
        if not reasoning.strip():
            signals.append("missing_proof")
    else:
        score = 1.0

    if reasoning.strip() and not check_math_balance(reasoning.splitlines()).balanced:
        score -= 0.2
        signals.append("unbalanced_solution_math")
    return _clamp(score), signals


def evaluate_item_rows(
    rows: list[dict[str, Any]],
    *,
    rubric_version: str = "v1",
    extraction_threshold: float = 0.7,
    solution_threshold: float = 0.6,
) -> EvaluationBatch:
    """评价存储投影并生成 review queue 项。"""
    evaluations: list[ItemEvaluation] = []
    review_items: list[ReviewQueueItem] = []

    for row in rows:
        item_id = str(row.get("id") or "")
        if not item_id:
            continue
        item_kind = "question" if row.get("kind") == "question" else "knowledge"
        item_type = str(row.get("type") or "")
        content = str(row.get("content") or "")
        reasoning = str(row.get("reasoning") or "")
        extraction, extraction_signals = _extraction_quality(row, content)
        solution, solution_signals = _solution_completeness(row, reasoning)
        dims = _difficulty_dimensions(row, content, reasoning)
        depth = int(row.get("concept_depth") or 0)

        proof_tokens = len(_PROOF_RE.findall(reasoning))
        proof_complexity = _clamp(
            0.08 * proof_tokens
            + 0.02 * len(_LOGIC_TOKEN_RE.findall(reasoning))
            + 0.015 * math.sqrt(max(0, len(reasoning)))
        )
        dependency_hits = len(_CONTEXT_DEPENDENCY_RE.findall(content + "\n" + reasoning))
        generality = _clamp(1.0 - min(0.75, dependency_hits * 0.18))
        self_contained = 1.0 if dependency_hits == 0 else max(0.0, 1.0 - dependency_hits * 0.25)
        lecture = _clamp(
            0.35 * extraction + 0.25 * solution + 0.2 * generality + 0.2 * self_contained
        )

        evaluation = ItemEvaluation(
            item_id=item_id,
            item_kind=item_kind,  # type: ignore[arg-type]
            rubric_version=rubric_version,
            extraction_quality=extraction,
            solution_completeness=solution,
            difficulty_overall=dims.overall,
            difficulty_dims=dims,
            cognitive_level=_cognitive_level(item_kind, item_type, content),
            prerequisite_depth=depth,
            proof_complexity=proof_complexity,
            lecture_suitability=lecture,
            cross_book_generality=generality,
            source=EvaluationSource.PIPELINE_AUTO,
            confidence=0.68 if row.get("concept_status") == "active" else 0.55,
        )
        evaluations.append(evaluation)

        reasons: list[tuple[str, int, dict[str, Any]]] = []
        ingestion_status = str(row.get("ingestion_status") or "accepted")
        if ingestion_status != "accepted":
            reasons.append(
                (
                    "non_accepted_ingestion_status",
                    5,
                    {
                        "status": ingestion_status,
                        "score": extraction,
                        "signals": extraction_signals,
                    },
                )
            )
        elif extraction < extraction_threshold:
            reasons.append(
                (
                    "low_extraction_quality",
                    5 if extraction < 0.4 else 4,
                    {"score": extraction, "signals": extraction_signals},
                )
            )
        if solution < solution_threshold:
            reasons.append(
                (
                    "incomplete_solution",
                    5 if solution < 0.3 else 4,
                    {"score": solution, "signals": solution_signals},
                )
            )
        if row.get("concept_status") == "pending":
            reasons.append(
                (
                    "pending_concept",
                    3,
                    {"concept_ids": row.get("concept_ids") or []},
                )
            )
        for reason, priority, details in reasons:
            review_items.append(
                ReviewQueueItem(
                    item_id=item_id,
                    item_kind=item_kind,  # type: ignore[arg-type]
                    tenant_id=str(row.get("tenant_id") or ""),
                    rubric_version=rubric_version,
                    reason=reason,
                    priority=priority,
                    details=details,
                )
            )

    return EvaluationBatch(evaluations=evaluations, review_items=review_items)
