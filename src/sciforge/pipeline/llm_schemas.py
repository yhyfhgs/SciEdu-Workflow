"""LLM 输出 Pydantic schema — 5 阶段 pipeline 强制校验

Stage1 (批量提取+纠错)：
- SingleItemExtractLLM: 单条条目
- BatchItemExtractPayloadLLM: LLM 一次窗口调用的完整输出
  {items: list[SingleItemExtractLLM], stop_reason, last_consumed_line, issues}
- SingleItemExtractPayloadLLM: 旧版单条输出 schema，保留给历史兼容路径

Stage2 (单条标注)：
- SingleAnnotationLLM: 单条标注（对应一条 item）

设计要点：
- model_validator 做跨字段一致性校验（题-答绑定、exercise near-tail 禁止 no_solution、tag 格式等）
- ValidationError 由 retry.py 的 with_feedback 接口捕获 → 触发 LLM 重新生成并把错误反馈入 prompt
- schema 只校验 LLM 输出的**原始 dict**；进一步的业务规则校验放在各 Stage 自己的辅助方法中
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

# ── Stage1 schema ─────────────────────────────────────


class StructuralContextLLM(BaseModel):
    """提取条目的结构上下文（章节/小节标题）"""

    chapter_title: str = ""
    chapter_number: str = ""
    section_title: str = ""
    section_number: str = ""
    subsection_title: str = ""
    subsection_number: str = ""

    model_config = ConfigDict(extra="ignore")


# v11 III.2（M2）：跨窗尾部条目的四态判别。区分"无答案"的四种真相，
# 取代一律误判 no_solution 的被动行为：
# - truly_no_answer    真无答案（习题集只有题、定义无证明）→ 收尾 no_solution_in_source
# - answer_deferred    答案延后（题在前、答案集中卷末/书末）→ 收尾 solution_deferred
# - content_truncated  内容被窗口物理截断（正文/解答中途断）→ carry 下窗续接
# - stem_unfinished    题干本身没说完 → carry 下窗续题干
TailState = Literal["truly_no_answer", "answer_deferred", "content_truncated", "stem_unfinished"]


class IncompleteItemCarry(BaseModel):
    """v11 III.2（M2）：跨窗结构化交接——上一窗末尾未闭合条目的上下文。

    是 ``stop_reason=incomplete_at_tail`` 被动防御的结构化升级：滑窗仍只推进到
    最后一条完整条目（尾部下窗重扫，v10 语义不变），carry 额外携带"尾部有什么、
    处于哪一态"注入下窗 prompt，指导 LLM 优先定案而非再次推迟。
    不持久化到 checkpoint state：中断恢复后丢失 carry = 退回 v10 被动重扫（安全降级）。
    """

    item_type: str = ""
    number: str = ""
    category: Literal["knowledge", "question"] = "question"
    head_anchor_start: str = ""  # 条目开头逐字短串（下窗匹配用）
    head_content: str = Field(default="", max_length=2000)  # 已见正文（截断省 token）
    tail_state: TailState = "content_truncated"
    original_start_line: int = Field(ge=1, default=1)  # 全局行号
    carry_window_count: int = Field(ge=1, default=1)

    model_config = ConfigDict(extra="forbid")


class ItemAnchorsLLM(BaseModel):
    """v11 II.4（M1，extract_char_span_enabled 时 LLM 输出）：条目边界锚点短串。

    LLM 只抄"条目真正开头/结尾（含解答）的逐字短串"，精确 offset 由
    ``pipeline/char_span.resolve_span`` 确定性定位（不让 LLM 数字符）。
    """

    start_anchor: str = Field(min_length=2, max_length=40)
    end_anchor: str = Field(min_length=2, max_length=40)

    model_config = ConfigDict(extra="ignore")


class SingleItemExtractLLM(BaseModel):
    """Stage1 输出的单条提取条目（纯内容+题答配对+位置信息）

    solution_status 三态：
    - complete: 有 proof/solution（两者至少一个非空）
    - no_solution_in_source: 确认原文此处及全文都没有（两者都 null）
    - solution_deferred: 原文**明示**答案在他处（如"答案见附录/章末习题解答"），
      此处无解答正文（两者都 null），但**不是无解**——留待 answer_linker 跨章节回填。
      与 no_solution_in_source 的区别：deferred 表示"答案存在但不在此"，
      因此**豁免** exercise near-tail 拒绝（这是有意为之，非截断）。

    prompt 契约：若本窗口内无法确认条目是否有解答，LLM 应返回 payload 不含该条
    （留给下一窗口），而不是在此字段乱填。
    """

    category: Literal["knowledge", "question"]
    item_type: str = Field(min_length=1)
    number: str = ""
    # 题目形式（题面可见即填，与认知类别正交）：choice/multiple_choice/fill_blank/
    # true_false/short_answer/subjective；knowledge 或无法判定时留 ""。Stage1 识别。
    question_form: str = ""
    # 结构化题型字段（REC-19，题面可见即填；非选择/填空留空）。choice 题填 options +
    # correct_option，fill_blank 题填 blanks。供 Stage3 落库到 questions.options/... 列。
    options: list[str] = Field(default_factory=list)
    correct_option: str | None = None
    blanks: int | None = None
    corrected_content: str = Field(min_length=1)
    corrected_proof: str | None = None
    corrected_solution: str | None = None
    solution_status: Literal["complete", "no_solution_in_source", "solution_deferred"]
    structural_context: StructuralContextLLM = Field(default_factory=StructuralContextLLM)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    # v11 II.4（M1，可选）：LLM 输出的边界锚点；flag 关时 prompt 不提及 → 恒 None
    anchors: ItemAnchorsLLM | None = None
    # 内部字段（非 LLM 输出）：resolve_span 定位后回填，Stage3 落 start_char/end_char 列
    resolved_start_char: int | None = None
    resolved_end_char: int | None = None

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="after")
    def _check_line_range(self) -> SingleItemExtractLLM:
        if self.start_line > self.end_line:
            raise ValueError(f"start_line ({self.start_line}) > end_line ({self.end_line})")
        return self

    @model_validator(mode="after")
    def _check_solution_status_consistency(self) -> SingleItemExtractLLM:
        """题-答绑定的强制约束

        complete                              → proof 或 solution 必须非空
        no_solution_in_source / solution_deferred → proof/solution 都必须为 null
        """
        status = self.solution_status
        has_payload = bool(self.corrected_proof) or bool(self.corrected_solution)

        if status == "complete" and not has_payload:
            raise ValueError(
                f"solution_status=complete 要求 corrected_proof 或 "
                f"corrected_solution 至少一个非 null (number={self.number!r})"
            )
        if status in ("no_solution_in_source", "solution_deferred") and has_payload:
            raise ValueError(
                f"solution_status={status} 要求 corrected_proof 和 corrected_solution "
                f"均为 null（此处无解答正文）(number={self.number!r})"
            )
        return self


_NEAR_TAIL_MARGIN = 10  # 条目 end_line 距 window 末行 < 此值时视为靠近末尾
_EXERCISE_LIKE_TYPES = {"exercise", "example", "problem"}


class BatchItemExtractPayloadLLM(BaseModel):
    """Stage1Extract 一次 LLM 调用的**批量**输出（v3）

    一次窗口提取所有可识别且完整的条目（按行号严格升序，不跳过、不重叠）。
    截断、答案未闭合、不确定是否有解的条目**不进入 items**，留给下一窗口扩窗后再提取。

    字段：
    - items: 完整条目数组，按 start_line 升序，互不重叠
    - stop_reason: 停止原因
        - "all_complete"          窗口剩余只是非条目内容（章节标题/过渡段落）
        - "incomplete_at_tail"    末尾有未闭合/不确定条目，需要扩窗
        - "no_items_found"        窗口内未找到任何完整条目
    - last_consumed_line: 上游滑动窗口的依据；非空 items 时必须等于 items[-1].end_line；
      空 items 时为 0
    - issues: LLM 自述问题（不影响下游处理）

    Validator：
    - items 严格递增、不重叠
    - exercise/example/problem 类条目若 end_line 距 window_last_line < 10 → 拒绝（应留给下一窗口）
    - last_consumed_line 与 items 末行一致性
    - stop_reason 与 items 长度自洽
    """

    items: list[SingleItemExtractLLM] = Field(default_factory=list)
    stop_reason: Literal["all_complete", "incomplete_at_tail", "no_items_found"]
    last_consumed_line: int = Field(ge=0, default=0)
    issues: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="after")
    def _check_items_ordering(self) -> BatchItemExtractPayloadLLM:
        """items 必须按 start_line 严格升序、不重叠"""
        for i in range(1, len(self.items)):
            prev = self.items[i - 1]
            curr = self.items[i]
            if curr.start_line <= prev.end_line:
                raise ValueError(
                    f"items[{i}] (start_line={curr.start_line}) 与 items[{i - 1}] "
                    f"(end_line={prev.end_line}) 行号重叠或顺序错误；"
                    f"items 必须按 start_line 严格升序且互不重叠"
                )
        return self

    @model_validator(mode="after")
    def _check_last_consumed_line(self) -> BatchItemExtractPayloadLLM:
        """last_consumed_line 必须等于最后一条 item 的 end_line（非空时）"""
        if self.items:
            last_end = self.items[-1].end_line
            if self.last_consumed_line != last_end:
                raise ValueError(
                    f"last_consumed_line={self.last_consumed_line} "
                    f"!= items[-1].end_line={last_end}；必须等于最后一条 item 的 end_line，"
                    f"否则上游会静默跳过未提取内容"
                )
        elif self.last_consumed_line != 0:
            raise ValueError(
                f"items 为空但 last_consumed_line={self.last_consumed_line} 非 0；"
                f"无 items 时 last_consumed_line 必须为 0"
            )
        return self

    @model_validator(mode="after")
    def _check_stop_reason_consistency(self) -> BatchItemExtractPayloadLLM:
        """stop_reason 与 items 长度自洽"""
        if not self.items and self.stop_reason == "all_complete":
            raise ValueError(
                "items 为空时 stop_reason 不能为 'all_complete'；"
                "应为 'no_items_found' 或 'incomplete_at_tail'"
            )
        if not self.items and self.stop_reason == "incomplete_at_tail":
            # 允许：窗口起点就是不完整条目（解答跨窗口）
            pass
        return self

    @model_validator(mode="after")
    def _check_exercise_near_tail(self, info: ValidationInfo) -> BatchItemExtractPayloadLLM:
        """exercise/example/problem 距 window 末行 < 10 → 拒绝（解答可能在下一窗口）"""
        ctx = info.context if info.context else {}
        window_last_line = ctx.get("window_last_line") if isinstance(ctx, dict) else None
        if not isinstance(window_last_line, int) or window_last_line <= 0:
            return self
        for idx, item in enumerate(self.items):
            if (
                item.item_type.lower() in _EXERCISE_LIKE_TYPES
                and item.solution_status == "no_solution_in_source"
                and window_last_line - item.end_line < _NEAR_TAIL_MARGIN
            ):
                raise ValueError(
                    f"items[{idx}] item_type={item.item_type!r}（exercise 类）"
                    f"的 end_line={item.end_line} 距窗口末行 {window_last_line} "
                    f"< {_NEAR_TAIL_MARGIN}，且 solution_status=no_solution_in_source；"
                    f"解答可能在下一窗口，请把这条**移出 items**并把 stop_reason 设为 "
                    f"'incomplete_at_tail'，让上游扩窗后下一轮再处理"
                )
        return self


class BatchReviewPayloadLLM(BaseModel):
    """Stage1Review 一次批量审核的输出（v3）

    严格 1:1 对应输入 items：长度必须等于输入；每项是修正后的 SingleItemExtractLLM
    或 None（拒绝该条）。位置 i 对应输入 items[i] 的 item_id。

    反跳过审核仍然适用：若 Stage1 跳过了顶端的条目，可在 reviewed_items[0] 位置
    **改写为顶端那条**（item_type/number/corrected_content 等全部更新）。
    """

    reviewed_items: list[SingleItemExtractLLM | None] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="after")
    def _check_expected_length(self, info: ValidationInfo) -> BatchReviewPayloadLLM:
        """若调用方提供输入长度，则 reviewed_items 必须 1:1 对齐。"""
        ctx = info.context if info.context else {}
        expected = ctx.get("expected_review_count") if isinstance(ctx, dict) else None
        if isinstance(expected, int) and expected >= 0 and len(self.reviewed_items) != expected:
            raise ValueError(
                f"reviewed_items 长度必须等于输入 items 长度 {expected}，"
                f"实际为 {len(self.reviewed_items)}"
            )
        return self

    @model_validator(mode="after")
    def _check_exercise_near_tail(self, info: ValidationInfo) -> BatchReviewPayloadLLM:
        """Stage1Review 也必须拒绝靠近窗口尾部的无解 exercise 类条目。"""
        ctx = info.context if info.context else {}
        window_last_line = ctx.get("window_last_line") if isinstance(ctx, dict) else None
        if not isinstance(window_last_line, int) or window_last_line <= 0:
            return self
        for idx, item in enumerate(self.reviewed_items):
            if item is None:
                continue
            if (
                item.item_type.lower() in _EXERCISE_LIKE_TYPES
                and item.solution_status == "no_solution_in_source"
                and window_last_line - item.end_line < _NEAR_TAIL_MARGIN
            ):
                raise ValueError(
                    f"reviewed_items[{idx}] item_type={item.item_type!r}（exercise 类）"
                    f"的 end_line={item.end_line} 距窗口末行 {window_last_line} "
                    f"< {_NEAR_TAIL_MARGIN}，且 solution_status=no_solution_in_source；"
                    f"Stage1Review 应将该位置设为 null，让上游扩窗后重提"
                )
        return self


class SingleItemExtractPayloadLLM(BaseModel):
    """Stage1 一次 LLM 调用的完整输出

    - extracted=None 表示本窗口无可确认的完整条目，调用方应扩大窗口
    - extracted=SingleItemExtractLLM 表示已提取第一个完整条目
    """

    extracted: SingleItemExtractLLM | None = None
    issues: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="after")
    def _check_exercise_near_tail(self, info: ValidationInfo) -> SingleItemExtractPayloadLLM:
        """修复 exercise 漏 solution bug 的第二道防线：

        若 item_type 属于 exercise/example/problem、solution_status=no_solution_in_source、
        且条目 end_line 距窗口末行 < _NEAR_TAIL_MARGIN → 拒绝并触发 retry（with feedback）。
        场景：习题紧贴 window 末尾，solution 可能在下一窗口，LLM 不应过早断言无解。

        依赖 Pydantic validation context：调用方用
        `model_validate(data, context={"window_last_line": N})` 注入窗口末行；
        若未注入则跳过该校验。
        """
        if self.extracted is None:
            return self
        ctx = info.context if info.context else {}
        window_last_line = ctx.get("window_last_line") if isinstance(ctx, dict) else None
        if not isinstance(window_last_line, int) or window_last_line <= 0:
            return self

        ex = self.extracted
        if (
            ex.item_type.lower() in _EXERCISE_LIKE_TYPES
            and ex.solution_status == "no_solution_in_source"
            and window_last_line - ex.end_line < _NEAR_TAIL_MARGIN
        ):
            raise ValueError(
                f"item_type={ex.item_type!r}（exercise 类）的 end_line={ex.end_line} 距窗口末行 "
                f"{window_last_line} < {_NEAR_TAIL_MARGIN}，此时 solution 可能在下一窗口，"
                f"不应标 no_solution_in_source；请改为 extracted=null 等窗口扩大，"
                f"或向前回退 end_line 并把本条目留给下一窗口"
            )
        return self


# ── Stage2 schema ─────────────────────────────────────


# 注意：`:` 为 ASCII 冒号 U+003A；全角冒号 U+FF1A 会被第一段 [a-z][a-z0-9_]* 直接卡住
_TAG_SEGMENT_RE = re.compile(r"^[a-z][a-z0-9_]*:[^\s:]+$")

_FORBIDDEN_TAG_CHARS = (
    "\u200b",  # ZERO WIDTH SPACE
    "\u200c",  # ZWNJ
    "\u200d",  # ZWJ
    "\u200e",  # LRM
    "\u200f",  # RLM
    "\ufeff",  # BOM / ZWNBSP
    "\u2060",  # WORD JOINER
)


def _validate_tag_segment(seg: str) -> None:
    """校验单个 tag 段格式 + 禁用零宽字符"""
    for ch in _FORBIDDEN_TAG_CHARS:
        if ch in seg:
            raise ValueError(f"tag segment {seg!r} 含零宽/格式控制字符 U+{ord(ch):04X}")
    if not _TAG_SEGMENT_RE.match(seg):
        raise ValueError(
            f"tag segment {seg!r} 必须匹配 'english:中文' "
            f"(english=[a-z][a-z0-9_]*，中文非空无空白且必须用 ASCII 冒号 U+003A)"
        )


class SingleAnnotationLLM(BaseModel):
    """Stage2 输出的单条标注

    数量约束宽松（审核重点是 tag_tree 复用 + 一致性，不是段数）：
    - tags: 至少 1 段（避免空标注），无上限（由 prompt 引导常见 3-6 段）
    - methods: 可空，无上限

    学科根强制校验通过 validation context 注入：
    - context={"discipline_tag_root": "math:数学", "discipline_taxonomy_root": "math"}
    - 若 tags[0] 或 taxonomy_path 根段不匹配 → 触发 retry with feedback
    - 未注入 context 时跳过（向后兼容）
    """

    subject: str = Field(min_length=1)
    taxonomy_path: str = Field(min_length=1)
    tags: list[str] = Field(min_length=1)
    difficulty: int = Field(ge=1, le=10)
    question_type: str | None = None
    methods: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")

    @field_validator("tags")
    @classmethod
    def _check_tags_format(cls, v: list[str]) -> list[str]:
        for seg in v:
            _validate_tag_segment(seg)
        return v

    @field_validator("methods")
    @classmethod
    def _check_methods_format(cls, v: list[str]) -> list[str]:
        for seg in v:
            _validate_tag_segment(seg)
        return v

    @model_validator(mode="after")
    def _check_discipline_root(self, info: ValidationInfo) -> SingleAnnotationLLM:
        """校验 tags[0] / taxonomy_path 学科根，以及 tags[1] 主分支白名单。

        context 可选字段：
        - discipline_tag_root: tags[0] 必须等于此值
        - discipline_taxonomy_root: taxonomy_path 以 "<root>." 开头或等于 <root>
        - discipline_allowed_branches: tuple[str, ...]，若 tags 长度 ≥ 2 则
          tags[1] 的 english 部分必须属于此白名单（主分支层）

        context 未注入或字段缺失 → 跳过（向后兼容），由 Stage3Persist 兜底补齐。
        """
        ctx = info.context if info.context else {}
        if not isinstance(ctx, dict):
            return self
        tag_root = ctx.get("discipline_tag_root")
        tax_root = ctx.get("discipline_taxonomy_root")
        allowed_branches = ctx.get("discipline_allowed_branches")

        if isinstance(tag_root, str) and tag_root:
            if not self.tags or self.tags[0] != tag_root:
                actual = self.tags[0] if self.tags else "(empty)"
                raise ValueError(
                    f"tags[0] 必须等于学科根 {tag_root!r}（实际为 {actual!r}）；"
                    f"请将 {tag_root!r} 作为 tags 的第一段，保留其他有效段"
                )

        if isinstance(tax_root, str) and tax_root:
            path = self.taxonomy_path.strip()
            if path != tax_root and not path.startswith(tax_root + "."):
                raise ValueError(
                    f"taxonomy_path 必须以 {tax_root + '.'!r} 开头或等于 {tax_root!r}"
                    f"（实际为 {path!r}）；请在开头加上 {tax_root + '.'!r} 前缀"
                )

        if isinstance(allowed_branches, (tuple, list)) and allowed_branches and len(self.tags) >= 2:
            branch_seg = self.tags[1]
            branch_english = branch_seg.split(":", 1)[0]
            if branch_english not in allowed_branches:
                raise ValueError(
                    f"tags[1] 必须是该学科的主分支（english 段 ∈ {list(allowed_branches)}），"
                    f"实际 tags[1]={branch_seg!r}（english={branch_english!r}）。"
                    f"请在 tags[0] 后先加主分支段（例如 algebra:代数），再接二级细分概念"
                )
        return self


class BatchAnnotationLLM(BaseModel):
    """Stage2 批量标注输出（REC-04）：一次 LLM 调用给 N 条 item 打标。

    ``annotations`` 与输入 items 严格 1:1（同序、同长度）。长度一致性由调用方
    （Stage2Annotate）与输入 N 比对；不一致 → 降级逐条标注，单条失败不污染整批。
    每个元素复用 ``SingleAnnotationLLM`` 的字段校验与学科根校验——pydantic 会把
    validation context 传播到嵌套元素，故 tags[0]/taxonomy 前缀/主分支检查照常生效。
    """

    annotations: list[SingleAnnotationLLM] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


__all__ = [
    "BatchAnnotationLLM",
    "BatchItemExtractPayloadLLM",
    "BatchReviewPayloadLLM",
    "SingleAnnotationLLM",
    "SingleItemExtractLLM",
    "SingleItemExtractPayloadLLM",
    "StructuralContextLLM",
]
