"""ItemProcessor — 条目校准 + 分类标注合并 Agent

单次 LLM 调用同时完成：
1. Pre-LLM: 确定性 OCR 修复 + 术语验证
2. LLM: 校准修复 + 多维分类标注
3. Post-LLM: LaTeX 验证 + taxonomy 验证
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from mathforge.core.models import (
    CorrectionEntry,
    CorrectionResult,
    EducationLevel,
    ItemProcessingResult,
    PedagogicalLevel,
    RawItem,
    Subject,
    SubjectDomain,
    TagSet,
    TaxonomyPath,
)
from mathforge.prompts.templates import ITEM_CORRECT_AND_TAG_PROMPT

if TYPE_CHECKING:
    from mathforge.core.models import FormatSpec
    from mathforge.llm.client import LLMClient

logger = structlog.get_logger()


class ProcessingContext:
    """ItemProcessor 的上下文信息"""

    __slots__ = (
        "source_type",
        "chapter_title",
        "section_title",
        "detected_structure",
        "prev_type",
        "prev_title",
        "next_type",
        "next_title",
        "format_spec",
    )

    def __init__(
        self,
        source_type: str = "",
        chapter_title: str = "",
        section_title: str = "",
        detected_structure: str = "",
        prev_type: str = "",
        prev_title: str = "",
        next_type: str = "",
        next_title: str = "",
        format_spec: FormatSpec | None = None,
    ) -> None:
        self.source_type = source_type
        self.chapter_title = chapter_title
        self.section_title = section_title
        self.detected_structure = detected_structure
        self.prev_type = prev_type
        self.prev_title = prev_title
        self.next_type = next_type
        self.next_title = next_title
        self.format_spec = format_spec


class ItemProcessor:
    """合并校准 + 标注的 LLM Agent"""

    def __init__(
        self,
        llm_client: LLMClient,
        taxonomy_path: Path | None = None,
    ) -> None:
        self.llm = llm_client
        self._taxonomy: dict | None = None
        self._taxonomy_path = taxonomy_path

    def _load_taxonomy(self) -> dict:
        """加载 taxonomy（带缓存）"""
        if self._taxonomy is not None:
            return self._taxonomy

        if self._taxonomy_path and self._taxonomy_path.exists():
            self._taxonomy = json.loads(self._taxonomy_path.read_text(encoding="utf-8"))
        else:
            # 尝试默认路径
            default = Path(__file__).parent.parent.parent / "config" / "taxonomy.json"
            if default.exists():
                self._taxonomy = json.loads(default.read_text(encoding="utf-8"))
            else:
                self._taxonomy = {}

        return self._taxonomy

    async def process(
        self,
        item: RawItem,
        context: ProcessingContext,
        paired_content: str = "",
    ) -> ItemProcessingResult:
        """处理单个 RawItem：Pre-LLM → LLM → Post-LLM"""
        t0 = time.monotonic()

        # ── Pre-LLM: 确定性 OCR 修复 ──
        pre_content, suspicious = self._pre_llm_repair(item.content_raw)

        # ── 裁剪 taxonomy ──
        taxonomy_excerpt = self._trim_taxonomy(context)

        # ── 构建 paired content section ──
        paired_section = ""
        if paired_content:
            paired_section = f"**配对内容（解答/证明）**:\n\n{paired_content}"

        # ── LLM 调用 ──
        def _esc(s: str) -> str:
            return s.replace("{", "{{").replace("}", "}}")

        prompt = ITEM_CORRECT_AND_TAG_PROMPT.format(
            block_type=item.block_type,
            number=item.number or "(无编号)",
            content_raw=_esc(pre_content),
            paired_content_section=_esc(paired_section),
            source_type=context.source_type,
            chapter_title=_esc(context.chapter_title or "(未知)"),
            section_title=_esc(context.section_title or "(未知)"),
            detected_structure=_esc(context.detected_structure or "(未提供)"),
            prev_type=context.prev_type or "(无)",
            prev_title=_esc(context.prev_title or "(无)"),
            next_type=context.next_type or "(无)",
            next_title=_esc(context.next_title or "(无)"),
            suspicious_terms=_esc(suspicious or "(无可疑项)"),
            taxonomy_excerpt=_esc(taxonomy_excerpt),
        )

        result_dict = None
        for attempt in range(2):  # 1 次尝试 + 1 次重试
            try:
                result_dict = await self.llm.generate_json(prompt)
                latency = time.monotonic() - t0
                logger.info(
                    "item_processed",
                    item_id=item.item_id,
                    block_type=item.block_type,
                    latency_sec=round(latency, 2),
                )
                break
            except Exception:
                if attempt == 0:
                    logger.warning("item_process_retry", item_id=item.item_id)
                    continue
                logger.error(
                    "item_process_failed",
                    item_id=item.item_id,
                    block_type=item.block_type,
                    exc_info=True,
                )
                return self._fallback_result(item, pre_content)

        if result_dict is None:
            return self._fallback_result(item, pre_content)

        # ── 解析 LLM 响应 ──
        return self._parse_result(result_dict, item, pre_content)

    def process_sync(
        self,
        item: RawItem,
        context: ProcessingContext,
        paired_content: str = "",
    ) -> ItemProcessingResult:
        """同步版本（用于非 async 场景）"""
        t0 = time.monotonic()

        pre_content, suspicious = self._pre_llm_repair(item.content_raw)
        taxonomy_excerpt = self._trim_taxonomy(context)

        paired_section = ""
        if paired_content:
            paired_section = f"**配对内容（解答/证明）**:\n\n{paired_content}"

        def _esc(s: str) -> str:
            return s.replace("{", "{{").replace("}", "}}")

        prompt = ITEM_CORRECT_AND_TAG_PROMPT.format(
            block_type=item.block_type,
            number=item.number or "(无编号)",
            content_raw=_esc(pre_content),
            paired_content_section=_esc(paired_section),
            source_type=context.source_type,
            chapter_title=_esc(context.chapter_title or "(未知)"),
            section_title=_esc(context.section_title or "(未知)"),
            detected_structure=_esc(context.detected_structure or "(未提供)"),
            prev_type=context.prev_type or "(无)",
            prev_title=_esc(context.prev_title or "(无)"),
            next_type=context.next_type or "(无)",
            next_title=_esc(context.next_title or "(无)"),
            suspicious_terms=_esc(suspicious or "(无可疑项)"),
            taxonomy_excerpt=_esc(taxonomy_excerpt),
        )

        try:
            result_dict = self.llm.generate_json_sync(prompt)
            latency = time.monotonic() - t0
            logger.info(
                "item_processed",
                item_id=item.item_id,
                latency_sec=round(latency, 2),
            )
        except Exception:
            logger.error("item_process_failed", item_id=item.item_id, exc_info=True)
            return self._fallback_result(item, pre_content)

        return self._parse_result(result_dict, item, pre_content)

    # ── Pre-LLM 确定性修复 ──

    def _pre_llm_repair(self, content: str) -> tuple[str, str]:
        """确定性 OCR 修复 + 术语验证，返回 (修复后内容, 可疑项描述)"""
        suspicious_parts: list[str] = []

        try:
            from mathforge.pipeline.ocr_repair import OcrRepairEngine

            engine = OcrRepairEngine()
            repaired, report = engine.repair_known_patterns(content)
            if report:
                suspicious_parts.append(
                    f"确定性修复 {len(report)} 处: "
                    + "; ".join(f"'{r['from']}'→'{r['to']}'" for r in report[:5])
                )

            flagged = engine.validate_math_terms(repaired)
            if flagged:
                suspicious_parts.append(
                    f"可疑术语: {', '.join(d['suspicious_term'] for d in flagged[:10])}"
                )

            return repaired, "\n".join(suspicious_parts)
        except ImportError:
            return content, ""
        except Exception:
            logger.warning("pre_llm_repair_failed", exc_info=True)
            return content, ""

    # ── Taxonomy 裁剪 ──

    def _trim_taxonomy(self, context: ProcessingContext) -> str:
        """根据上下文裁剪 taxonomy，只传相关子树"""
        taxonomy = self._load_taxonomy()
        if not taxonomy:
            return "(无可用 taxonomy)"

        math_tree = taxonomy.get("math", {})
        hints = (context.chapter_title + " " + context.section_title).lower()

        # 关键词 → branch 映射
        branch_keywords = {
            "analysis": ["极限", "连续", "微分", "积分", "级数", "分析", "函数"],
            "algebra": ["线性", "矩阵", "行列式", "特征", "向量", "空间", "方程组"],
            "abstract_algebra": ["群", "环", "域", "同态", "子群", "理想", "抽象代数", "近世代数"],
            "combinatorics": ["组合", "计数", "图论", "数论"],
            "complex_analysis": ["复分析", "全纯", "留数", "解析"],
        }

        matched_branches: list[str] = []
        for branch, keywords in branch_keywords.items():
            if any(kw in hints for kw in keywords):
                matched_branches.append(branch)

        # 如果没匹配到，返回所有 branch 的顶层
        if not matched_branches:
            lines = []
            for branch, subtree in math_tree.items():
                label = subtree.get("_label", branch) if isinstance(subtree, dict) else branch
                lines.append(f"math.{branch} ({label})")
                if isinstance(subtree, dict):
                    for topic in subtree:
                        if topic != "_label":
                            t = subtree[topic]
                            tlabel = t.get("_label", topic) if isinstance(t, dict) else topic
                            lines.append(f"  math.{branch}.{topic} ({tlabel})")
            return "\n".join(lines[:40])

        # 返回匹配 branch 的详细子树
        lines = []
        for branch in matched_branches:
            subtree = math_tree.get(branch, {})
            label = subtree.get("_label", branch) if isinstance(subtree, dict) else branch
            lines.append(f"math.{branch} ({label})")
            if isinstance(subtree, dict):
                for topic, topic_data in subtree.items():
                    if topic == "_label":
                        continue
                    if isinstance(topic_data, dict):
                        tlabel = topic_data.get("_label", topic)
                        lines.append(f"  math.{branch}.{topic} ({tlabel})")
                        for sub, sub_data in topic_data.items():
                            if sub == "_label":
                                continue
                            if isinstance(sub_data, list):
                                specifics = ", ".join(sub_data[:5])
                                lines.append(f"    math.{branch}.{topic}.{sub}: [{specifics}]")
                            elif isinstance(sub_data, dict):
                                lines.append(f"    math.{branch}.{topic}.{sub}")
                    elif isinstance(topic_data, list):
                        lines.append(f"  math.{branch}.{topic}: [{', '.join(topic_data[:5])}]")

        return "\n".join(lines[:60])

    # ── 结果解析 ──

    def _parse_result(
        self, data: dict[str, Any], item: RawItem, pre_content: str
    ) -> ItemProcessingResult:
        """解析 LLM 响应为 ItemProcessingResult"""
        corr_data = data.get("correction", {})
        class_data = data.get("classification", {})

        # 解析 CorrectionResult
        corrections = []
        for c in corr_data.get("corrections", []):
            try:
                corrections.append(CorrectionEntry(**c))
            except Exception:
                pass

        # 防御性处理：LLM 可能返回非法 quality grade
        raw_quality = corr_data.get("overall_quality", "B")
        if raw_quality not in ("A", "B", "C", "D", "F"):
            raw_quality = "C"

        # completeness_score 可能是 None 或非数字
        raw_comp = corr_data.get("completeness_score", 1.0)
        completeness = float(raw_comp) if isinstance(raw_comp, (int, float)) else 1.0

        correction = CorrectionResult(
            corrected_content=corr_data.get("corrected_content") or pre_content,
            corrected_latex=corr_data.get("corrected_latex") or "",
            corrections=corrections,
            semantic_issues=corr_data.get("semantic_issues") or [],
            logic_issues=corr_data.get("logic_issues") or [],
            is_answer_verified=corr_data.get("is_answer_verified"),
            completeness_score=completeness,
            overall_quality=raw_quality,
        )

        # 解析 TagSet — 防御 None 值
        tax_data = class_data.get("taxonomy") or {}
        taxonomy = TaxonomyPath(
            domain=tax_data.get("domain") or "math",
            branch=tax_data.get("branch") or "",
            topic=tax_data.get("topic") or "",
            subtopic=tax_data.get("subtopic"),
            specific=tax_data.get("specific"),
        )

        subject_str = class_data.get("subject", "数学分析")
        try:
            subject = Subject(subject_str)
        except ValueError:
            subject = _fuzzy_match_subject(subject_str)

        ped_str = class_data.get("pedagogical_level", "standard")
        try:
            ped_level = PedagogicalLevel(ped_str)
        except ValueError:
            ped_level = PedagogicalLevel.STANDARD

        edu_str = class_data.get("education_level", "undergraduate")
        try:
            edu_level = EducationLevel(edu_str)
        except ValueError:
            edu_level = EducationLevel.UNDERGRADUATE

        classification = TagSet(
            domain=SubjectDomain.MATH,
            subject=subject,
            taxonomy=taxonomy,
            education_level=edu_level,
            pedagogical_level=ped_level,
            methods=class_data.get("methods", []),
            tested_topics=class_data.get("tested_topics", []),
            custom_tags=class_data.get("custom_tags", []),
        )

        # Post-LLM 验证
        self._post_validate(correction)

        raw_diff = class_data.get("difficulty", 5)
        difficulty = max(1, min(10, int(raw_diff) if isinstance(raw_diff, (int, float)) else 5))

        return ItemProcessingResult(
            correction=correction,
            classification=classification,
            item_subtype=class_data.get("item_subtype", item.block_type),
            difficulty=difficulty,
        )

    def _post_validate(self, correction: CorrectionResult) -> None:
        """Post-LLM 确定性验证"""
        try:
            from mathforge.pipeline.content_validator import validate_latex_brackets

            content = correction.corrected_latex or correction.corrected_content
            issues = validate_latex_brackets(content)
            if issues:
                correction.semantic_issues.extend(
                    f"LaTeX 括号问题: {issue.message}" for issue in issues[:3]
                )
        except Exception:
            logger.debug("post_validate_latex_failed", exc_info=True)

    def _fallback_result(self, item: RawItem, pre_content: str) -> ItemProcessingResult:
        """LLM 调用失败时的 fallback"""
        return ItemProcessingResult(
            correction=CorrectionResult(
                corrected_content=pre_content,
                overall_quality="F",
            ),
            classification=TagSet(),
            item_subtype=item.block_type,
            difficulty=5,
        )


# Subject 关键词 → 枚举值的映射表
_SUBJECT_KEYWORDS: dict[str, Subject] = {
    "分析": Subject.ANALYSIS,
    "微积分": Subject.ANALYSIS,
    "极限": Subject.ANALYSIS,
    "代数": Subject.LINEAR_ALGEBRA,
    "矩阵": Subject.LINEAR_ALGEBRA,
    "线性": Subject.LINEAR_ALGEBRA,
    "群": Subject.GROUP_THEORY,
    "环": Subject.GROUP_THEORY,
    "域": Subject.GROUP_THEORY,
    "抽象代数": Subject.GROUP_THEORY,
    "组合": Subject.COMBINATORICS,
    "图论": Subject.COMBINATORICS,
    "计数": Subject.COMBINATORICS,
    "数论": Subject.NUMBER_THEORY,
    "素数": Subject.NUMBER_THEORY,
    "整除": Subject.NUMBER_THEORY,
    "拓扑": Subject.TOPOLOGY,
    "开集": Subject.TOPOLOGY,
    "连通": Subject.TOPOLOGY,
    "复分析": Subject.COMPLEX_ANALYSIS,
    "全纯": Subject.COMPLEX_ANALYSIS,
    "留数": Subject.COMPLEX_ANALYSIS,
    "解析函数": Subject.COMPLEX_ANALYSIS,
    "概率": Subject.PROBABILITY,
    "随机": Subject.PROBABILITY,
    "期望": Subject.PROBABILITY,
    "实分析": Subject.REAL_ANALYSIS,
    "测度": Subject.REAL_ANALYSIS,
    "Lebesgue": Subject.REAL_ANALYSIS,
    "泛函": Subject.FUNCTIONAL_ANALYSIS,
    "算子": Subject.FUNCTIONAL_ANALYSIS,
    "Banach": Subject.FUNCTIONAL_ANALYSIS,
    "微分方程": Subject.DIFFERENTIAL_EQUATIONS,
    "ODE": Subject.DIFFERENTIAL_EQUATIONS,
}


def _fuzzy_match_subject(subject_str: str) -> Subject:
    """模糊匹配 Subject 枚举值"""
    # 先尝试枚举值的子串匹配
    for member in Subject:
        if member.value in subject_str or subject_str in member.value:
            return member
    # 再尝试关键词匹配
    for keyword, subject in _SUBJECT_KEYWORDS.items():
        if keyword in subject_str:
            return subject
    logger.warning("subject_fuzzy_match_failed", subject_str=subject_str)
    return Subject.OTHER
