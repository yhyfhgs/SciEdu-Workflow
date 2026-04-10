"""ItemProcessor — 批量条目校正 + 分类 LLM Agent

一次 LLM 调用处理多个 ExtractedItem，返回等数量的处理结果。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog

from mathforge.core.models import (
    CorrectionResult,
    ItemProcessingResult,
    TagSet,
)
from mathforge.prompts.templates import ITEM_CORRECT_AND_TAG_PROMPT

if TYPE_CHECKING:
    from mathforge.core.models import ExtractedItem, StructuralContext
    from mathforge.llm.client import LLMClient

logger = structlog.get_logger()


class ItemProcessor:
    """批量条目校正+分类 LLM Agent"""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm = llm_client

    async def process_batch(
        self,
        items: list[ExtractedItem],
        ctx: StructuralContext,
    ) -> list[ItemProcessingResult]:
        """批量处理多个条目：一次 LLM 调用"""
        if not items:
            return []

        t0 = time.monotonic()

        # 构建条目列表文本
        items_block = self._format_items_block(items)

        def _esc(s: str) -> str:
            return s.replace("{", "{{").replace("}", "}}")

        prompt = ITEM_CORRECT_AND_TAG_PROMPT.format(
            source_type=ctx.document_type or "textbook",
            chapter_title=_esc(ctx.chapter_title or "(未知)"),
            section_title=_esc(ctx.section_title or "(未知)"),
            item_count=len(items),
            items_block=_esc(items_block),
        )

        # LLM 调用（含重试）
        result_dict = None
        for attempt in range(2):
            try:
                result_dict = await self.llm.generate_json(prompt, max_tokens=16384)
                if result_dict and result_dict.get("items"):
                    break
                if attempt == 0:
                    logger.info("item_batch_retry", count=len(items), reason="empty")
            except Exception:
                if attempt == 0:
                    logger.info("item_batch_retry", count=len(items), reason="parse_error")
                else:
                    logger.warning("item_batch_failed", count=len(items))

        latency = time.monotonic() - t0
        logger.info(
            "item_batch_processed",
            count=len(items),
            latency_sec=round(latency, 2),
        )

        # 解析结果
        if not result_dict or not result_dict.get("items"):
            return [self._fallback(ei) for ei in items]

        results: list[ItemProcessingResult] = []
        llm_items = result_dict["items"]

        for i, ei in enumerate(items):
            if i < len(llm_items):
                results.append(self._parse_one(llm_items[i], ei))
            else:
                results.append(self._fallback(ei))

        return results

    # ── 格式化 ──

    @staticmethod
    def _format_items_block(items: list[ExtractedItem]) -> str:
        """将多个 ExtractedItem 格式化为 prompt 中的条目列表"""
        parts: list[str] = []
        for i, ei in enumerate(items):
            lines = [f"### 条目 {i}"]
            lines.append(f"类型: {ei.item_type} | 编号: {ei.number or '(无)'}")
            lines.append(f"内容:\n{ei.content}")
            if ei.proof:
                lines.append(f"证明:\n{ei.proof}")
            if ei.solution:
                lines.append(f"解答:\n{ei.solution}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    # ── 解析 ──

    @staticmethod
    def _parse_one(data: dict, ei: ExtractedItem) -> ItemProcessingResult:
        """解析单个条目的 LLM 输出"""
        quality = data.get("overall_quality", "C")
        if quality not in ("A", "B", "C", "D", "F"):
            quality = "C"

        correction = CorrectionResult(
            corrected_content=data.get("corrected_content", ei.content),
            corrected_latex=data.get("corrected_content", ei.content),
            overall_quality=quality,
        )

        # 解析 tags（必须是 "english:中文" 格式）
        tags = data.get("tags", [])
        methods = data.get("methods", [])

        classification = TagSet(
            methods=methods if isinstance(methods, list) else [],
            custom_tags=tags if isinstance(tags, list) else [],
        )

        # subject
        subject_str = data.get("subject", "")
        if subject_str:
            classification.custom_tags.insert(0, f"subject:{subject_str}")

        return ItemProcessingResult(
            correction=correction,
            classification=classification,
            item_subtype=data.get("kp_type") or data.get("question_type") or ei.item_type,
            difficulty=max(1, min(10, data.get("difficulty", 5))),
            # 新增字段从 LLM 输出传递
            corrected_proof=data.get("corrected_proof"),
            corrected_solution=data.get("corrected_solution"),
            taxonomy_path=data.get("taxonomy_path", ""),
            subject=data.get("subject", ""),
            tags=tags if isinstance(tags, list) else [],
            methods=methods if isinstance(methods, list) else [],
            question_type=data.get("question_type"),
        )

    @staticmethod
    def _fallback(ei: ExtractedItem) -> ItemProcessingResult:
        """LLM 失败时的降级结果"""
        return ItemProcessingResult(
            correction=CorrectionResult(
                corrected_content=ei.content,
                overall_quality="D",
            ),
            classification=TagSet(),
            item_subtype=ei.item_type,
        )
