"""Test new SingleItem* schemas + exercise near-tail guard"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sciforge.pipeline.llm_schemas import (
    BatchItemExtractPayloadLLM,
    BatchReviewPayloadLLM,
    SingleAnnotationLLM,
    SingleItemExtractLLM,
    SingleItemExtractPayloadLLM,
)


def _valid_item(**overrides):
    data = {
        "category": "knowledge",
        "item_type": "theorem",
        "number": "3.1",
        "corrected_content": "Theorem statement.",
        "corrected_proof": "Proof body.",
        "corrected_solution": None,
        "solution_status": "complete",
        "start_line": 10,
        "end_line": 30,
    }
    data.update(overrides)
    return data


class TestSingleItemExtractLLM:
    def test_complete_with_proof_valid(self):
        it = SingleItemExtractLLM.model_validate(_valid_item())
        assert it.category == "knowledge"
        assert it.solution_status == "complete"

    def test_complete_without_payload_rejected(self):
        with pytest.raises(ValidationError):
            SingleItemExtractLLM.model_validate(
                _valid_item(corrected_proof=None, corrected_solution=None)
            )

    def test_no_solution_with_payload_rejected(self):
        with pytest.raises(ValidationError):
            SingleItemExtractLLM.model_validate(
                _valid_item(
                    solution_status="no_solution_in_source",
                    corrected_proof="some proof",
                )
            )

    def test_start_greater_than_end_rejected(self):
        with pytest.raises(ValidationError):
            SingleItemExtractLLM.model_validate(_valid_item(start_line=30, end_line=10))


class TestSingleItemExtractPayloadLLM:
    def test_extracted_none_ok(self):
        p = SingleItemExtractPayloadLLM.model_validate({"extracted": None})
        assert p.extracted is None

    def test_exercise_near_tail_rejected(self):
        """exercise 且 end_line 接近窗口尾 + no_solution_in_source → 拒绝"""
        data = {
            "extracted": _valid_item(
                category="question",
                item_type="exercise",
                solution_status="no_solution_in_source",
                corrected_proof=None,
                corrected_solution=None,
                start_line=490,
                end_line=495,
            )
        }
        with pytest.raises(ValidationError):
            SingleItemExtractPayloadLLM.model_validate(data, context={"window_last_line": 500})

    def test_exercise_far_from_tail_ok(self):
        data = {
            "extracted": _valid_item(
                category="question",
                item_type="exercise",
                solution_status="no_solution_in_source",
                corrected_proof=None,
                corrected_solution=None,
                start_line=100,
                end_line=120,
            )
        }
        p = SingleItemExtractPayloadLLM.model_validate(data, context={"window_last_line": 500})
        assert p.extracted is not None

    def test_theorem_near_tail_with_no_solution_ok(self):
        """non-exercise item_type 不受 near-tail 限制"""
        data = {
            "extracted": _valid_item(
                category="knowledge",
                item_type="theorem",
                solution_status="no_solution_in_source",
                corrected_proof=None,
                corrected_solution=None,
                start_line=490,
                end_line=495,
            )
        }
        p = SingleItemExtractPayloadLLM.model_validate(data, context={"window_last_line": 500})
        assert p.extracted is not None

    def test_missing_context_skips_guard(self):
        """未提供 context → 跳过 near-tail 校验"""
        data = {
            "extracted": _valid_item(
                category="question",
                item_type="exercise",
                solution_status="no_solution_in_source",
                corrected_proof=None,
                corrected_solution=None,
                start_line=490,
                end_line=495,
            )
        }
        p = SingleItemExtractPayloadLLM.model_validate(data)
        assert p.extracted is not None


class TestBatchItemExtractPayloadLLM:
    def test_last_consumed_line_must_equal_last_item_end(self):
        data = {
            "items": [_valid_item(start_line=10, end_line=30)],
            "stop_reason": "all_complete",
            "last_consumed_line": 100,
            "issues": [],
        }
        with pytest.raises(ValidationError, match="必须等于最后一条 item"):
            BatchItemExtractPayloadLLM.model_validate(data, context={"window_last_line": 120})

    def test_last_consumed_line_equal_last_item_end_ok(self):
        data = {
            "items": [_valid_item(start_line=10, end_line=30)],
            "stop_reason": "all_complete",
            "last_consumed_line": 30,
            "issues": [],
        }
        payload = BatchItemExtractPayloadLLM.model_validate(data, context={"window_last_line": 120})
        assert payload.last_consumed_line == 30


class TestBatchReviewPayloadLLM:
    def test_exercise_near_tail_rejected(self):
        data = {
            "reviewed_items": [
                _valid_item(
                    category="question",
                    item_type="exercise",
                    solution_status="no_solution_in_source",
                    corrected_proof=None,
                    corrected_solution=None,
                    start_line=96,
                    end_line=100,
                )
            ],
            "issues": [],
        }
        with pytest.raises(ValidationError, match="Stage1Review 应将该位置设为 null"):
            BatchReviewPayloadLLM.model_validate(
                data,
                context={"window_last_line": 105, "expected_review_count": 1},
            )

    def test_expected_review_count_rejected(self):
        data = {"reviewed_items": [_valid_item()], "issues": []}
        with pytest.raises(ValidationError, match="长度必须等于输入 items 长度"):
            BatchReviewPayloadLLM.model_validate(data, context={"expected_review_count": 2})


class TestSingleAnnotationLLM:
    def test_valid_annotation(self):
        a = SingleAnnotationLLM.model_validate(
            {
                "subject": "real_analysis:实分析",
                "taxonomy_path": "math.analysis.continuity",
                "tags": ["math:数学", "analysis:分析", "continuity:连续性"],
                "difficulty": 5,
            }
        )
        assert len(a.tags) == 3

    def test_invalid_tag_format_rejected(self):
        with pytest.raises(ValidationError):
            SingleAnnotationLLM.model_validate(
                {
                    "subject": "real_analysis:实分析",
                    "taxonomy_path": "math.analysis",
                    "tags": ["Math:数学", "analysis:分析", "continuity:连续性"],  # 大写开头非法
                    "difficulty": 5,
                }
            )

    def test_empty_tags_rejected(self):
        """tags 至少 1 段，避免空标注"""
        with pytest.raises(ValidationError):
            SingleAnnotationLLM.model_validate(
                {
                    "subject": "real_analysis:实分析",
                    "taxonomy_path": "math.analysis",
                    "tags": [],
                    "difficulty": 5,
                }
            )

    def test_many_tags_accepted(self):
        """段数不再硬限制为 3-5，更多段数也允许"""
        a = SingleAnnotationLLM.model_validate(
            {
                "subject": "real_analysis:实分析",
                "taxonomy_path": "math.analysis.continuity.uniform",
                "tags": [
                    "math:数学",
                    "analysis:分析",
                    "continuity:连续性",
                    "uniform_continuity:一致连续",
                    "heine_cantor:海涅-康托尔定理",
                    "compactness:紧性",
                    "borel_covering:Borel覆盖",
                ],
                "difficulty": 7,
            }
        )
        assert len(a.tags) == 7

    def test_difficulty_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            SingleAnnotationLLM.model_validate(
                {
                    "subject": "real_analysis:实分析",
                    "taxonomy_path": "math.analysis",
                    "tags": ["math:数学", "analysis:分析", "continuity:连续性"],
                    "difficulty": 11,
                }
            )


class TestSingleAnnotationLLMDisciplineRoot:
    """Context-aware 学科根校验（新增）"""

    _BASE = {
        "subject": "real_analysis:实分析",
        "taxonomy_path": "math.analysis.continuity",
        "tags": ["math:数学", "analysis:分析"],
        "difficulty": 5,
    }
    _CTX = {"discipline_tag_root": "math:数学", "discipline_taxonomy_root": "math"}

    def test_correct_root_accepted(self):
        a = SingleAnnotationLLM.model_validate(self._BASE, context=self._CTX)
        assert a.tags[0] == "math:数学"

    def test_wrong_tag_root_rejected(self):
        data = {**self._BASE, "tags": ["analysis:分析", "continuity:连续"]}
        with pytest.raises(ValidationError, match="tags\\[0\\] 必须等于学科根"):
            SingleAnnotationLLM.model_validate(data, context=self._CTX)

    def test_wrong_taxonomy_root_rejected(self):
        data = {**self._BASE, "taxonomy_path": "analysis.continuity"}
        with pytest.raises(ValidationError, match="taxonomy_path 必须以"):
            SingleAnnotationLLM.model_validate(data, context=self._CTX)

    def test_taxonomy_equals_root_accepted(self):
        data = {**self._BASE, "taxonomy_path": "math", "tags": ["math:数学"]}
        a = SingleAnnotationLLM.model_validate(data, context=self._CTX)
        assert a.taxonomy_path == "math"

    def test_no_context_skips_check(self):
        """向后兼容：不传 context → 跳过根段校验"""
        data = {**self._BASE, "tags": ["groups:群"], "taxonomy_path": "algebra.groups"}
        a = SingleAnnotationLLM.model_validate(data)
        assert a.tags == ["groups:群"]

    def test_physics_context_accepts_physics_root(self):
        data = {
            "subject": "classical_mechanics:经典力学",
            "taxonomy_path": "physics.mechanics",
            "tags": ["physics:物理", "mechanics:力学"],
            "difficulty": 5,
        }
        ctx = {"discipline_tag_root": "physics:物理", "discipline_taxonomy_root": "physics"}
        a = SingleAnnotationLLM.model_validate(data, context=ctx)
        assert a.tags[0] == "physics:物理"


class TestSingleAnnotationLLMAllowedBranches:
    """Context-aware tags[1] 主分支白名单校验（Step 2 新增）"""

    _BASE = {
        "subject": "abstract_algebra:抽象代数",
        "taxonomy_path": "math.abstract_algebra.groups",
        "tags": ["math:数学", "abstract_algebra:抽象代数", "group_theory:群论"],
        "difficulty": 6,
    }
    _CTX = {
        "discipline_tag_root": "math:数学",
        "discipline_taxonomy_root": "math",
        "discipline_allowed_branches": (
            "analysis",
            "algebra",
            "abstract_algebra",
            "linear_algebra",
            "number_theory",
        ),
    }

    def test_allowed_branch_accepted(self):
        a = SingleAnnotationLLM.model_validate(self._BASE, context=self._CTX)
        assert a.tags[1].startswith("abstract_algebra")

    def test_branch_skip_rejected(self):
        """tags[1] 跳过主分支直写二级概念 → 拒绝"""
        data = {**self._BASE, "tags": ["math:数学", "group_theory:群论"]}
        with pytest.raises(ValidationError, match="tags\\[1\\] 必须是该学科的主分支"):
            SingleAnnotationLLM.model_validate(data, context=self._CTX)

    def test_unknown_branch_rejected(self):
        """tags[1] 是未在白名单的 english 段 → 拒绝"""
        data = {
            **self._BASE,
            "tags": ["math:数学", "made_up_branch:编造分支", "stuff:东西"],
        }
        with pytest.raises(ValidationError, match="tags\\[1\\] 必须是该学科的主分支"):
            SingleAnnotationLLM.model_validate(data, context=self._CTX)

    def test_single_tag_skips_branch_check(self):
        """tags 只有 1 段（root 本身）→ 不触发 tags[1] 校验"""
        data = {
            **self._BASE,
            "taxonomy_path": "math",
            "tags": ["math:数学"],
        }
        a = SingleAnnotationLLM.model_validate(data, context=self._CTX)
        assert a.tags == ["math:数学"]

    def test_no_allowed_branches_context_skips_check(self):
        """context 无 discipline_allowed_branches → 跳过校验"""
        ctx = {k: v for k, v in self._CTX.items() if k != "discipline_allowed_branches"}
        data = {**self._BASE, "tags": ["math:数学", "group_theory:群论"]}
        a = SingleAnnotationLLM.model_validate(data, context=ctx)
        assert a.tags[1] == "group_theory:群论"
