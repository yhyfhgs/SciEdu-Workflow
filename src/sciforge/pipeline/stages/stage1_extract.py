"""Stage1Extract — 批量滑动窗口提取（v3）

职责：
- 从 q_md 取 MdBlockSource，按累计全局行号注入 line_buffer
- 滑动窗口（默认 500 行，可扩到 3000），每窗口**一次** LLM 调用提取**所有完整**条目
- 提取成功 → 连续分配 item_ids、滑动到 last_consumed_line+1、emit 一个 ExtractedBatch 到 q_raw
- 窗口为空提取 → 按 expand_step 扩大；达 max_window 后连续 N 次空 → force consume
- 每条 item 镜像到 <checkpoint>/<hash>/items/stage1/item_<id>.json（dict 格式）

输出 schema: `BatchItemExtractPayloadLLM`（items 数组 + stop_reason + last_consumed_line）。
不完整条目（截断/未闭合解答/不确定有解）**不进 items**，留给下一窗口
（stop_reason="incomplete_at_tail"）。
"""

from __future__ import annotations

import asyncio
import bisect
import json as _json_mod
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from sciforge.core.models import (
    Discipline,
    ExtractedBatch,
    FileExtractionState,
    MdBlockSource,
)
from sciforge.core.sentinel import SENTINEL, _Sentinel
from sciforge.pipeline.llm_schemas import BatchItemExtractPayloadLLM
from sciforge.pipeline.resume_state import save_state_locked
from sciforge.pipeline.retry import (
    RetryExhausted,
    call_llm_json_with_schema_retry_with_feedback,
)
from sciforge.pipeline.source_classifier import detect_genre
from sciforge.prompts.templates import (
    build_pipeline_prompt_io,
    get_extract_stage1_initial_parts,
    get_extract_stage1_initial_template,
)

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig
    from sciforge.llm.client import LLMClient

logger = structlog.get_logger()

# 条目起始标记预检（用于智能窗口跳过）
# 设计原则：宁可 false-positive（多调一次 LLM 验证，无害）也不 false-negative（漏条目）。
# 中文 OCR 教材常见格式：
#   "定义 1．2．2"（全角句号，空格分隔）
#   "定理1.1.2"（连写，半角句号）
#   "例1．2．7"（例、例题；可含或不含空格）
#   "**定义 1.1**"（带 markdown 粗体）
# 因此**不强制** `**` 前缀，只要求数字紧跟条目关键字。
_ITEM_MARKER_RE = re.compile(
    r"(?:"
    # LaTeX 环境
    r"\\begin\{(?:theorem|definition|lemma|corollary|proposition|"
    r"property|example|problem|exercise|remark|proof|solution)\}"
    # 中文关键字 + 可选空格 + 数字（允许半角或全角数字，允许可选 ** 粗体）
    # 关键字后允许 0-2 个空白字符（半角/全角），必须跟着一个数字或字母 X（如 "定义 X"）
    r"|(?:\*\*\s*)?"
    r"(?:定[理义]|引理|推论|命题|性质|公式|例题?|习题|问题|注记?|备注|方法|"
    r"结论|法则|算法)"
    r"[\s\u00a0\u3000]{0,3}[\d\uff10-\uff19X]"
    # 英文全称 + 编号（含物理 Law/Principle/Axiom）
    r"|(?:Theorem|Definition|Lemma|Corollary|Proposition|Property|"
    r"Example|Exercise|Problem|Remark|Formula|Method|Algorithm|Law|Principle|Axiom)"
    r"\s+[\d.]"
    # 英文缩写 + 编号（Thm/Def/Prop/Cor/Lem/Pf/Rmk/Eg）
    r"|(?:Thm|Def|Prop|Cor|Lem|Pf|Rmk|Eg)\.?\s*\d"
    r")",
    re.IGNORECASE,
)

# 条目**弱信号**（strict 模式：无强标记但任一弱信号命中 → 仍不跳窗，交 LLM 判定）。
# 覆盖 OCR 把关键字彻底糊掉、或裸编号/选项/解答/章节标题等不含标准关键字的情形，
# 是"绝不静默丢条目"的第二道防线（#8）。在已剥离行号前缀的窗口文本上匹配。
_WEAK_SIGNAL_RE = re.compile(
    r"(?:"
    r"\d+\s*[.．]\s*\d+"  # 编号样式 3.2 / 3．2
    r"|\\begin\{"  # 任意 LaTeX 环境
    r"|\\underline|＿{2,}|_{3,}|[（(]\s*[）)]"  # 填空（下划线/空括号）
    r"|证\s*明|证[:：.]|解\s*答|解[:：.]|答[:：.]|Proof|Solution|Sol[.:]"  # 解答/证明
    r"|(?m:^\s*[(（]?[A-Da-d][)）.、])"  # 选择项 A. / (B)
    r"|(?m:^\s*\d{1,3}\s*[.、．)）])"  # 行首裸编号习题 12. / 3、 / 5)
    r"|(?m:^\s*#{1,6}\s)|\\(?:sub)*section\{"  # markdown / LaTeX 章节标题
    r"|第\s*[0-9０-９一二三四五六七八九十]+\s*[章节]"  # 中文章节
    r")",
    re.IGNORECASE,
)


class Stage1Extract:
    """批量提取 + 滑动窗口"""

    def __init__(
        self,
        llm_client: LLMClient,
        config: AppConfig,
        checkpoint_root: Path | None = None,
        discipline: Discipline = Discipline.MATH,
        order_index_allocator: Any | None = None,
    ) -> None:
        self.llm = llm_client
        self.config = config
        self.checkpoint_root = checkpoint_root
        self.discipline = discipline
        self.order_index_allocator = order_index_allocator
        # Prompt caching（#9）：~10k 字静态提取指令放 system（每窗口命中缓存），窗口正文留 user。
        self.prompt_cache_enabled = bool(getattr(config, "pipeline_prompt_cache_enabled", True))
        self.system_prompt, self.user_template = build_pipeline_prompt_io(
            get_extract_stage1_initial_parts(discipline),
            get_extract_stage1_initial_template(discipline),
            self.prompt_cache_enabled,
        )

        # Runtime 状态
        self.line_buffer: list[str] = []
        self.line_offset = 0
        self.next_item_id = 0
        self.current_window_size: int = int(getattr(config, "extract_window_lines", 500))
        self.consecutive_empty = 0

        # Config 派生常量
        self.base_window: int = int(getattr(config, "extract_window_lines", 500))
        self.max_window: int = max(
            int(getattr(config, "extract_max_window_lines", 3000)),
            self.base_window,
        )
        self.expand_step: int = int(getattr(config, "extract_window_expand_step", 500))
        self.empty_force_limit: int = int(getattr(config, "extract_empty_force_limit", 2))
        self.max_retries: int = int(getattr(config, "stage1_extract_max_retries", 3))
        self.retry_base_delay: float = float(getattr(config, "extract_retry_base_delay", 2.0))
        # 429 rate limit 配置
        self.rate_limit_base_sleep: float = float(getattr(config, "rate_limit_base_sleep", 300.0))
        self.rate_limit_max_sleep: float = float(getattr(config, "rate_limit_max_sleep", 1800.0))
        self.rate_limit_total_budget: float = float(
            getattr(config, "rate_limit_total_budget", 21600.0)
        )

        self._prev_tail_summary: str = ""
        self._recent_item_examples: list[str] = []
        self._max_examples = 3
        self.fast_skip_enabled: bool = bool(getattr(config, "empty_window_fast_skip", True))
        # strict（默认 True，高召回）：fast-skip 不仅看强标记，还须无弱信号才跳窗。
        # 设 False 退回旧行为（仅强标记预检）。fast_skip_enabled=False 则完全不跳。
        self.fast_skip_strict: bool = bool(getattr(config, "extract_fast_skip_strict", True))
        # REC-14：文档体裁（book/exam_paper/lecture/unknown），首个 block 内容探测一次。
        # 试卷/讲义关 fast-skip（卷末密集短题、讲义无编号要点易被 regex 预检误跳）→ 提召回。
        self._genre: str = "unknown"
        self._genre_detected: bool = False
        # v11 §E.0（M1，opt-in）：format/genre profile——extended 体裁探测（含 problem_set）
        # + 窗口自适应 + halo/carry 先验。默认 False 走上面 REC-14 白名单路径，行为不变。
        self.profile_enabled: bool = bool(getattr(config, "format_profile_enabled", False))
        self.format_profile: Any | None = None
        self.genre_profile: Any | None = None
        # v11 III.1（M1，opt-in）：marker ledger 覆盖率审计（纯观测，不改提取行为）。
        self.ledger_enabled: bool = bool(getattr(config, "marker_ledger_enabled", False))
        # REC-20：当前 block 的确定性章节结构（detect_section_boundaries 产物），注入提取 prompt
        # 作为 structural_context 的权威先验，消灭"串行路径白跑 section_boundaries"的死逻辑。
        self._section_hint: str = ""
        # #1 单文档内并行提取（opt-in）：按 section 边界切段、段间并发、段内 carry-aware 滑窗。
        # 默认 False → 走原有逐块流式串行路径（本次改动不触碰默认行为）。
        self.parallel_enabled: bool = bool(getattr(config, "intra_doc_parallel_extract", False))
        self.segment_concurrency: int = max(
            1, int(getattr(config, "intra_doc_segment_concurrency", 4))
        )
        self.min_segment_lines: int = max(
            1, int(getattr(config, "intra_doc_min_segment_lines", 40))
        )

    # ── 主循环 ─────────────────────────────────────────

    async def run(
        self,
        in_queue: asyncio.Queue[Any],
        out_queue: asyncio.Queue[Any],
        state: FileExtractionState,
    ) -> None:
        """按配置分流：默认逐块流式串行；opt-in 时走单文档内并行分段提取。"""
        if self.parallel_enabled:
            await self._run_parallel(in_queue, out_queue, state)
        else:
            await self._run_streaming(in_queue, out_queue, state)

    def _apply_genre_policy(self, md_text: str) -> None:
        """首个 block 的一次性体裁策略（幂等，只在第一次调用生效）。

        - 默认（v10/REC-14）：``detect_genre`` 四值 + 试卷/讲义白名单关 fast-skip。
        - ``format_profile_enabled=True``（v11 §E.0）：extended 体裁（含 problem_set）
          + 策略表驱动 fast-skip + 密度自适应窗口 + halo/carry 先验（供后续里程碑）。
          窗口自适应只在当前窗口仍为配置初始值时改写 current_window_size，
          不干扰 resume 恢复的扩窗状态。
        """
        if self._genre_detected:
            return
        self._genre_detected = True
        if not self.profile_enabled:
            self._genre = detect_genre(md_text)
            if self._genre in ("exam_paper", "lecture"):
                self.fast_skip_enabled = False
            logger.info(
                "stage1_genre_detected",
                genre=self._genre,
                fast_skip_enabled=self.fast_skip_enabled,
            )
            return

        from sciforge.pipeline.format_profile import compute_format_profile, compute_genre_profile

        genre_profile = compute_genre_profile(md_text)
        format_profile = compute_format_profile(md_text, genre=genre_profile.genre)
        self._genre = genre_profile.genre
        self.genre_profile = genre_profile
        self.format_profile = format_profile
        # 只收紧不放宽：用户显式关掉的 fast-skip 不被画像重新打开
        self.fast_skip_enabled = self.fast_skip_enabled and genre_profile.fast_skip_recommended
        config_base = int(getattr(self.config, "extract_window_lines", 500))
        if self.current_window_size == config_base:
            self.current_window_size = format_profile.suggested_base_window
        self.base_window = format_profile.suggested_base_window
        logger.info(
            "stage1_profile_applied",
            genre=self._genre,
            density=format_profile.density,
            base_window=self.base_window,
            fast_skip_enabled=self.fast_skip_enabled,
            ocr_noisy=format_profile.ocr_noisy,
            halo_suggestion=format_profile.suggested_halo_lines,
        )

    @staticmethod
    def _strip_line_prefix(line: str) -> str:
        """剥离 ``_annotate`` 的 7 位行号前缀（``NNNNNNN | 正文``）；无前缀原样返回。"""
        head, sep, rest = line.partition(" | ")
        return rest if sep and head.strip().isdigit() else line

    def _audit_consumed_window(
        self,
        window: list[str],
        window_start: int,
        consumed: int,
        payload: Any,
        item_ids: list[int],
    ) -> dict[str, Any] | None:
        """v11 III.1（M1 观测层）：对**已消费**窗口段做 marker 覆盖率对账。

        只写 structlog 指标 + 返回 ``ExtractedBatch.ledger_audit`` 附件——不改
        items、不改滑窗、不注入 prompt（M1 使命是度量 T4 真值质量，M4 才把
        台账注入 harness prompt）。只审计已消费段：尾部延迟条目下窗重扫，
        计入会造成假 missed。审计失败静默降级（观测层永不阻断流水线）。
        """
        if not self.ledger_enabled:
            return None
        try:
            from sciforge.pipeline.boundary_patch import (
                AuditItem,
                audit_coverage,
                build_marker_ledger,
            )

            raw_lines = [self._strip_line_prefix(ln) for ln in window[:consumed]]
            ledger = build_marker_ledger(raw_lines, line_base=window_start)
            audit_items = [
                AuditItem(
                    item_ref=str(item_id),
                    category=item.category,
                    item_type=item.item_type,
                    solution_status=item.solution_status,
                    start_line=item.start_line,
                    end_line=max(item.end_line, item.start_line),
                )
                for item_id, item in zip(item_ids, payload.items, strict=True)
            ]
            report = audit_coverage(ledger, audit_items)
            logger.info(
                "stage1_ledger_audit",
                clean=report.clean,
                markers_total=report.markers_total,
                markers_covered=report.markers_covered,
                missed=[m.global_line for m in report.missed_markers],
                boundary_suspects=len(report.boundary_suspects),
                pairing_suspects=len(report.pairing_suspects),
                line_range=f"{window_start}-{window_start + consumed - 1}",
            )
            return report.model_dump()
        except Exception as exc:  # noqa: BLE001
            logger.warning("stage1_ledger_audit_failed", error=str(exc))
            return None

    async def _run_streaming(
        self,
        in_queue: asyncio.Queue[Any],
        out_queue: asyncio.Queue[Any],
        state: FileExtractionState,
    ) -> None:
        """从 q_md 消费 MdBlockSource → 滑动窗口提取 → emit 到 q_raw（默认串行路径）"""
        self._restore_state(state)
        sentinel_seen = False
        current_block: MdBlockSource | None = None

        try:
            while not sentinel_seen:
                msg = await in_queue.get()

                if isinstance(msg, _Sentinel):
                    sentinel_seen = True
                    is_last = True
                else:
                    if not isinstance(msg, MdBlockSource):
                        logger.warning("stage1_extract_unexpected_msg", type=type(msg).__name__)
                        continue
                    current_block = msg

                    # REC-14：首个 block 内容探测体裁；试卷/讲义关 fast-skip 提召回
                    # （format_profile_enabled 时升级为 §E.0 画像路径，见 _apply_genre_policy）
                    self._apply_genre_policy(current_block.md_text)

                    # REC-20：用确定性章节边界作 structural_context 先验（取本 block 的标题）
                    if current_block.section_boundaries:
                        titles = [t for _, _, t in current_block.section_boundaries if t.strip()]
                        self._section_hint = " / ".join(titles[:8])
                    else:
                        self._section_hint = ""

                    if current_block.block_index < state.current_block_idx:
                        logger.debug(
                            "stage1_skip_block_checkpoint",
                            block=current_block.block_index,
                            until=state.current_block_idx,
                        )
                        continue
                    if current_block.block_index in state.failed_blocks:
                        logger.debug("stage1_skip_failed_block", block=current_block.block_index)
                        continue

                    start_no = self.line_offset + len(self.line_buffer) + 1
                    new_lines = self._annotate(current_block.md_text, start_no)
                    self.line_buffer.extend(new_lines)
                    state.current_block_idx = current_block.block_index + 1
                    await self._sync_state(state, force=True)
                    is_last = False

                    logger.debug(
                        "stage1_block_loaded",
                        block=current_block.block_index,
                        lines=len(new_lines),
                        buffer_size=len(self.line_buffer),
                    )

                await self._process_windows(state, current_block, is_last, out_queue)

                if not isinstance(msg, _Sentinel):
                    await self._sync_state(state)

            # 循环结束，残留处理已在 is_last 路径内
            if self.line_buffer:
                logger.warning(
                    "stage1_residual_buffer",
                    residual_lines=len(self.line_buffer),
                    line_offset=self.line_offset,
                )

            state.stage1_completed = True
            await self._sync_state(state, force=True)
            logger.info(
                "stage1_extract_complete",
                extracted=state.stage1_extracted_count,
                next_item_id=self.next_item_id,
                failed_blocks=len(state.failed_blocks),
            )
        finally:
            # 无论成功/异常都把 SENTINEL 传递给下游，避免 Stage1Review 死锁
            await out_queue.put(SENTINEL)

    # ── 窗口处理 ────────────────────────────────────────

    async def _process_windows(
        self,
        state: FileExtractionState,
        current_block: MdBlockSource | None,
        is_last: bool,
        out_queue: asyncio.Queue[Any],
    ) -> None:
        while True:
            if not self.line_buffer:
                break

            if len(self.line_buffer) < self.current_window_size and not is_last:
                break

            window_len = min(self.current_window_size, len(self.line_buffer))
            if window_len == 0:
                break
            window = self.line_buffer[:window_len]
            window_last_line = self._line_no_of(window[-1])
            window_last_int = int(window_last_line) if isinstance(window_last_line, int) else 0
            window_start_int = self.line_offset + 1

            # ── 第一层：结构感知的条目预检（zero-cost 跳过真正空白区域）────
            # 在剥离行号前缀的文本上匹配，避免前缀干扰 ^ 锚点。
            clean_window = self._clean_window_text(window)
            has_markers = _ITEM_MARKER_RE.search(clean_window) is not None
            if has_markers:
                window_empty = False
            elif self.fast_skip_strict:
                # strict：无强标记时还要确认无弱信号（裸编号/选项/解答/章节标题/填空）
                window_empty = _WEAK_SIGNAL_RE.search(clean_window) is None
            else:
                window_empty = True  # 非 strict：退回旧行为（仅强标记预检）
            if self.fast_skip_enabled and window_empty and not is_last:
                # 无任何条目迹象且不是最后窗口 → 直接跳过，不调 LLM
                slide = window_len  # 跳整个窗口（比只跳 base_window 更激进）
                logger.info(
                    "stage1_window_fast_skip",
                    window_lines=window_len,
                    line_range=f"{window_start_int}-{window_last_int}",
                    slide=slide,
                )
                self.line_buffer = self.line_buffer[slide:]
                self.line_offset += slide
                self.current_window_size = self.base_window
                self.consecutive_empty = 0
                await self._sync_state(state, force=True)
                continue

            try:
                payload = await self._extract_batch(window, window_start_int, window_last_int)
            except RetryExhausted as exc:
                # 判断失败根因：429 / rate-limit 类（临时性）vs schema / content（永久性）
                from sciforge.core.exceptions import LLMRateLimitError

                is_rate_limit = isinstance(exc.last_exc, LLMRateLimitError)
                logger.error(
                    "stage1_window_failed",
                    block=current_block.block_index if current_block else -1,
                    line_range=f"{self.line_offset + 1}-{self.line_offset + window_len}",
                    attempts=exc.attempts,
                    reason="rate_limit_budget_exhausted" if is_rate_limit else "other",
                    last_error=str(exc.last_exc)[:200] if exc.last_exc else "",
                )
                if is_rate_limit:
                    # 429 预算耗尽：**不要**把 block 标记为 failed（它不是真的失败），
                    # 直接中断当前文件，保留 state.next_item_id / line_buffer 等信息，
                    # 等用户重启 pipeline 时续跑。记录为 state.errors 以便观察。
                    err_msg = (
                        f"rate_limit_budget_exhausted at block="
                        f"{current_block.block_index if current_block else -1}, "
                        f"line_range={self.line_offset + 1}-{self.line_offset + window_len}"
                    )
                    if err_msg not in state.errors:
                        state.errors.append(err_msg)
                    await self._sync_state(state, force=True)
                    # 抛出异常让 orchestrator 终止整个文件的处理
                    raise
                # 非 rate-limit：保留原行为，把 block 加入 failed_blocks
                if (
                    current_block is not None
                    and current_block.block_index not in state.failed_blocks
                ):
                    state.failed_blocks.append(current_block.block_index)
                # 跳过本窗口内容
                slide = min(self.base_window, window_len)
                self.line_buffer = self.line_buffer[slide:]
                self.line_offset += slide
                self.current_window_size = self.base_window
                self.consecutive_empty = 0
                await self._sync_state(state, force=True)
                break

            # REC-12：文末/章末尾部 + regex 检测到条目标记但 LLM 返空 → 尽力强制再提取一次，
            # 避免试卷最后几题/章末习题因 near-tail guard 反复推迟而在 EOF 永久漏失。
            if (
                not payload.items
                and not window_empty
                and is_last
                and window_len >= len(self.line_buffer)
            ):
                try:
                    forced = await self._extract_batch(
                        window, window_start_int, window_last_int, force_extract=True
                    )
                except RetryExhausted:
                    forced = None
                if forced is not None and forced.items:
                    logger.info(
                        "stage1_terminal_tail_force_extracted",
                        line_range=f"{window_start_int}-{window_last_int}",
                        count=len(forced.items),
                    )
                    payload = forced

            if not payload.items:
                # 未提到条目 → 扩窗口或 force consume
                self.consecutive_empty += 1
                at_max = self.current_window_size >= self.max_window
                # 窗口是否已覆盖整个剩余 buffer（扩窗也无法纳入更多行）
                window_covers_buffer = window_len >= len(self.line_buffer)
                # 文末尾部：最后一个 block 且窗口已覆盖全部剩余内容。此时扩窗纯属浪费——
                # min() 卡在 buffer 长度，再扩窗只会对**同一段内容**重复调 LLM N 次。
                terminal_tail = is_last and window_covers_buffer

                # ── 第三层：no_items_found + 无任何信号 → 快速跳过整个窗口 ──
                if (
                    self.fast_skip_enabled
                    and payload.stop_reason == "no_items_found"
                    and window_empty
                ):
                    slide = window_len  # LLM 确认无条目 + regex 也无信号 → 跳整个窗口
                    logger.info(
                        "stage1_no_items_fast_skip",
                        window=self.current_window_size,
                        slide=slide,
                    )
                    self.line_buffer = self.line_buffer[slide:]
                    self.line_offset += slide
                    self.current_window_size = self.base_window
                    self.consecutive_empty = 0
                    await self._sync_state(state)  # REC-17：无条目快跳（未提取）→ 节流
                    continue

                # ── 第二层：改进 force-consume ──
                # 触发：达 max 且连续空够多次，**或**文末尾部（内容不会再增长，
                # 提前 force consume 省去对同一段内容的多次无效扩窗调用）。
                if (at_max and self.consecutive_empty >= self.empty_force_limit) or terminal_tail:
                    # 判断是否有不完整尾部条目
                    if (
                        payload.stop_reason == "incomplete_at_tail"
                        and payload.last_consumed_line > 0
                    ):
                        # 只跳到已消费位置，保留不完整尾部给下一窗口
                        cut = self._find_buffer_idx(payload.last_consumed_line)
                        slide = (cut + 1) if 0 <= cut < window_len else window_len
                    elif not window_empty and not terminal_tail:
                        # **完整性优先**：regex 检测到条目标记/信号，但 LLM 反复返回空。
                        # 此时整窗丢弃（最多 3000 行）可能静默漏掉真实条目。改为只滑
                        # base_window，让被标记内容在后续窗口重新获得提取机会；升级为
                        # error 级日志以便审计（窗口后仍有内容可承接被推迟的部分）。
                        slide = min(self.base_window, window_len)
                        logger.error(
                            "stage1_force_consume_marker_conflict",
                            line_range=f"{window_start_int}-{window_last_int}",
                            window=self.current_window_size,
                            slide=slide,
                            note=(
                                "regex 检测到条目标记但 LLM 返回空；"
                                "改滑 base_window 重扫而非丢弃整窗"
                            ),
                        )
                    else:
                        # 真空白区 或 文末尾部（无处可推迟）→ 跳整窗
                        slide = min(self.current_window_size, window_len)
                        if not window_empty:
                            logger.error(
                                "stage1_force_consume_tail_markers_dropped",
                                line_range=f"{window_start_int}-{window_last_int}",
                                slide=slide,
                                note=(
                                    "文末尾部 regex 有标记但 LLM 无产出；"
                                    "EOF 无处推迟，记录以便 numbering_audit 复核"
                                ),
                            )
                    logger.warning(
                        "stage1_force_consume",
                        window=self.current_window_size,
                        consecutive_empty=self.consecutive_empty,
                        slide=slide,
                        stop_reason=payload.stop_reason,
                        terminal_tail=terminal_tail,
                        preserved_tail=payload.stop_reason == "incomplete_at_tail",
                    )
                    self.line_buffer = self.line_buffer[slide:]
                    self.line_offset += slide
                    self.current_window_size = self.base_window
                    self.consecutive_empty = 0
                    # REC-17：纯窗口管理（未提取任何条目）→ 走 3s 节流，不每窗强制刷盘。
                    # crash 后 resume 从上次节流点重扫，Stage3 content_hash 去重幂等。
                    await self._sync_state(state)
                    continue
                if not at_max:
                    next_size = min(self.current_window_size + self.expand_step, self.max_window)
                    logger.debug(
                        "stage1_window_expand",
                        old=self.current_window_size,
                        new=next_size,
                        buffer=len(self.line_buffer),
                        stop_reason=payload.stop_reason,
                    )
                    self.current_window_size = next_size
                    await self._sync_state(state)  # REC-17：扩窗（未提取）→ 节流
                    if len(self.line_buffer) >= self.current_window_size or is_last:
                        continue
                    break
                # 已达 max 但未到 force_limit，且非末尾 → 等下一 block
                await self._sync_state(state)  # REC-17：等待下一 block（未提取）→ 节流
                if is_last:
                    continue
                break

            # 提到一批 → 分配连续 id、滑动到 last_consumed_line+1、emit 一个 ExtractedBatch
            self.consecutive_empty = 0
            items = payload.items
            # 上 LLM schema 已校验 last_consumed_line == items[-1].end_line（非空时）
            last_end_line = payload.last_consumed_line

            cut_idx = self._find_buffer_idx(last_end_line)
            if 0 <= cut_idx < window_len:
                consumed = cut_idx + 1
            else:
                consumed = window_len

            # 连续分配 item_ids。目录并发模式下必须从共享 allocator 动态预留，
            # 不能在文件启动时只拿一个起点，否则不同文件会分配重叠 ID。
            item_ids = await self._allocate_item_ids(len(items))
            first_id = item_ids[0]

            window_range = (self.line_offset + 1, self.line_offset + window_len)
            window_copy = list(window)

            # v11 III.1（M1 观测层）：对已消费窗口段做 marker 覆盖率对账（0 LLM）
            ledger_audit = self._audit_consumed_window(
                window, window_start_int, consumed, payload, item_ids
            )

            batch = ExtractedBatch(
                item_ids=item_ids,
                items=items,
                window_lines=window_copy,
                window_line_range=window_range,
                window_last_line=window_last_int,
                block_index=current_block.block_index if current_block else -1,
                source_file=current_block.file_path if current_block else "",
                page_start=current_block.page_start if current_block else None,
                page_end=current_block.page_end if current_block else None,
                stop_reason=payload.stop_reason,
                ledger_audit=ledger_audit,
            )

            self._mirror_batch(batch, state, stage_dir="stage1")
            await out_queue.put(batch)
            state.stage1_extracted_count += len(items)

            tail_item = items[-1]
            self._prev_tail_summary = (
                f"批量提取 {len(items)} 条，最后一条："
                f"类型 {tail_item.category}/{tail_item.item_type}, 编号 {tail_item.number}, "
                f"行号 {tail_item.start_line}-{tail_item.end_line}, "
                f"solution_status: {tail_item.solution_status}; "
                f"stop_reason: {payload.stop_reason}"
            )
            for it in items:
                self._update_examples(it)

            logger.info(
                "stage1_extract_batch_emitted",
                count=len(items),
                first_id=first_id,
                last_id=item_ids[-1],
                last_end_line=last_end_line,
                stop_reason=payload.stop_reason,
            )

            self.line_buffer = self.line_buffer[consumed:]
            self.line_offset += consumed
            self.current_window_size = self.base_window
            await self._sync_state(state, force=True)

    # ── LLM 调用 ──────────────────────────────────────

    async def _extract_batch(
        self,
        window: list[str],
        window_start_line: int,
        window_last_line: int,
        *,
        force_extract: bool = False,
    ) -> BatchItemExtractPayloadLLM:
        """单次 LLM 调用，批量提取窗口内**所有完整**条目。

        不完整（截断/未闭合解答/不确定有解）的条目**不进 items**，留给下一窗口。

        force_extract（REC-12）：文档/章节末尾的 last-resort 模式——指示 LLM 尽力提取剩余
        条目（解答不完整就用 solution_deferred），并**关闭 near-tail guard**（不再因靠近
        窗口末尾而拒绝），避免文末条目在 EOF 永久漏失。
        """
        chunk_text = "".join(window)

        extra_sections: list[str] = []
        if self._section_hint:
            extra_sections.append(
                "## 文档章节结构（权威先验，来自确定性边界检测）\n"
                f"{self._section_hint}\n"
                "**要求**：填 `structural_context` 时优先与上述章节标题对齐"
                "（chapter_title / section_title 取最贴近当前窗口的标题），不要臆造。"
            )
        if force_extract:
            extra_sections.append(
                "## 文档末尾强制提取（last resort）\n"
                "这是文档/章节**末尾**窗口，后面没有更多内容承接。请**尽力提取剩余所有条目**，"
                "即使解答看起来不完整/被截断；对习题/例题若解答可能在他处，用 "
                "`solution_status=solution_deferred`（**不要**因靠近窗口末尾而推迟或留空）。"
            )
        extra_sections.append(
            "## 当前窗口行号范围（**反跳过校验基准**）\n"
            f"- 起点行号：{window_start_line}\n"
            f"- 末行行号：{window_last_line}\n"
            f"**要求**：`items[0].start_line` 必须接近 {window_start_line}（窗口顶端）；"
            "起点到 items[0].start_line 之间、相邻 items 之间**不得遗留**任何条目起始标记"
            "（`**定义/例/性质/引理/命题/公式/推论/注 X.Y**` / "
            "`\\begin{definition/example/property/...}` 等）"
        )
        if self._recent_item_examples:
            examples_json = ",\n    ".join(self._recent_item_examples)
            extra_sections.append(
                f"## 前序已提取的条目示例（字段格式参照，不要重复提取）\n[{examples_json}]"
            )
        if self._prev_tail_summary:
            extra_sections.append(
                "## 前一批已提取条目（**编号连续性基准**）\n"
                f"{self._prev_tail_summary}\n"
                "**要求**：本批 `items[0]` 的 number 应连续延续上一批最后一条（按教材编号规律）；"
                "若出现跳号（如前一批末 3.1，本批 items[0] 是 3.3），说明 3.2 就在当前窗口内，"
                "必须回窗口顶端重新扫描，先提取跳过的那条；"
                "items 内部相邻两条的 number 也必须连续"
            )

        def build_prompt(feedback: str) -> str:
            retry_block = ""
            if feedback:
                retry_block = f"## 上一次输出被拒绝，错误信息（请修正）\n{feedback}\n"
            base = self.user_template.format(
                current_block=chunk_text,
                retry_feedback=retry_block,
            )
            # extra_sections（窗口行号基准/前序示例/编号连续性）是动态的，归入 user
            if extra_sections:
                return "\n".join(extra_sections) + "\n\n" + base
            return base

        t0 = time.monotonic()
        result = await call_llm_json_with_schema_retry_with_feedback(
            self.llm,
            build_prompt,
            model=BatchItemExtractPayloadLLM,
            system=self.system_prompt,
            max_attempts=self.max_retries,
            base_delay=self.retry_base_delay,
            # force_extract 时省略 window_last_line → near-tail guard 失效（允许文末条目入 items）
            validation_context={} if force_extract else {"window_last_line": window_last_line},
            stage_name="stage1_extract_force" if force_extract else "stage1_extract",
            rate_limit_base_sleep=self.rate_limit_base_sleep,
            rate_limit_max_sleep=self.rate_limit_max_sleep,
            rate_limit_total_budget=self.rate_limit_total_budget,
        )
        latency = time.monotonic() - t0
        logger.info(
            "stage1_extract_done",
            window_lines=len(window),
            items_count=len(result.items),
            stop_reason=result.stop_reason,
            last_consumed_line=result.last_consumed_line,
            latency_sec=round(latency, 2),
        )
        return result

    # ── 单文档内并行提取（opt-in）─────────────────────────

    async def _run_parallel(
        self,
        in_queue: asyncio.Queue[Any],
        out_queue: asyncio.Queue[Any],
        state: FileExtractionState,
    ) -> None:
        """drain 全部 block → 按 section 边界切段 → 段间并发 carry-aware 提取
        → 按文档(段)顺序连续分配 item_id → 逐段 emit ExtractedBatch。

        完整性保证：
        - 段**只在 section 边界**切（条目天然不跨章节），段内用与串行路径同款的
          滑窗 + carry（``_extract_segment_windows``）→ 超大 section 不在 chunk 边界切断条目。
        - item_id 在文档(段)顺序上连续递增 → 维持 ``order_index = item_id`` 不变量。
        - resume：``stage1_completed`` 已为真则不重复提取（下游 replay 由 orchestrator 处理）。
        """
        from sciforge.pipeline.document_segmenter import DocSegment
        from sciforge.pipeline.parallel_extract import ParallelSegmentExtractor

        self._restore_state(state)
        try:
            if state.stage1_completed:
                await self._drain_until_sentinel(in_queue)
                logger.info("stage1_parallel_skip_completed", file_hash=state.file_hash)
                return

            all_lines, boundary_indices, block_map = await self._collect_blocks(in_queue, state)
            if not all_lines:
                logger.info("stage1_parallel_no_content")
                state.stage1_completed = True
                await self._sync_state(state, force=True)
                return

            # window_lines 设大阈值 → segment_document 仅按 section 切，不硬切条目；
            # 段内由 _extract_segment_windows 自行滑窗 + carry。
            big = max(self.max_window * 100, 10_000)
            extractor = ParallelSegmentExtractor(
                segment_extract_fn=self._extract_segment_windows,
                window_lines=big,
                concurrency=self.segment_concurrency,
            )
            grouped: list[tuple[DocSegment, list[Any]]] = await extractor.extract_grouped(
                all_lines, boundary_indices, min_segment_lines=self.min_segment_lines
            )

            total = sum(len(items) for _, items in grouped)
            logger.info(
                "stage1_parallel_extracted",
                segments=len(grouped),
                total_lines=len(all_lines),
                items=total,
                concurrency=self.segment_concurrency,
            )
            if total == 0:
                state.stage1_completed = True
                await self._sync_state(state, force=True)
                return

            # 一次性预留连续 id（文档顺序），逐段切片分配 → order_index = item_id 连续递增
            item_ids_all = await self._allocate_item_ids(total)
            assert item_ids_all == list(range(item_ids_all[0], item_ids_all[0] + total)), (
                "并行路径 item_id 必须连续（order_index=item_id 不变量）"
            )

            cursor = 0
            for seg, items in grouped:
                if not items:
                    continue
                n = len(items)
                ids = item_ids_all[cursor : cursor + n]
                cursor += n

                seg_lines = list(seg.lines)
                first_no = self._line_no_int(seg_lines[0]) if seg_lines else 0
                last_no = self._line_no_int(seg_lines[-1]) if seg_lines else 0
                src_block = self._block_for_idx(block_map, seg.start_idx)

                batch = ExtractedBatch(
                    item_ids=ids,
                    items=items,
                    window_lines=seg_lines,
                    window_line_range=(first_no, last_no),
                    window_last_line=last_no,
                    block_index=src_block.block_index if src_block else -1,
                    source_file=src_block.file_path if src_block else "",
                    page_start=src_block.page_start if src_block else None,
                    page_end=src_block.page_end if src_block else None,
                    stop_reason="all_complete",
                )
                self._mirror_batch(batch, state, stage_dir="stage1")
                await out_queue.put(batch)
                state.stage1_extracted_count += n
                await self._sync_state(state)

            assert cursor == total, f"emit 条目数 {cursor} != 提取总数 {total}"
            state.stage1_completed = True
            await self._sync_state(state, force=True)
            logger.info(
                "stage1_parallel_complete",
                extracted=state.stage1_extracted_count,
                next_item_id=self.next_item_id,
            )
        finally:
            await out_queue.put(SENTINEL)

    async def _collect_blocks(
        self, in_queue: asyncio.Queue[Any], state: FileExtractionState
    ) -> tuple[list[str], list[int], list[tuple[int, MdBlockSource]]]:
        """drain q_md，把全部 block 拼成带全局行号前缀的行列表。

        Returns:
            (all_lines, boundary_indices, block_map)
            - all_lines: 全文档带行号前缀的行（按 block_index 顺序）
            - boundary_indices: section 边界在 all_lines 中的 0-based 下标（升序去重）
            - block_map: [(buffer_start_idx, MdBlockSource), ...] 供按段反查源 block
        """
        blocks: list[MdBlockSource] = []
        while True:
            msg = await in_queue.get()
            if isinstance(msg, _Sentinel):
                break
            if not isinstance(msg, MdBlockSource):
                logger.warning("stage1_parallel_unexpected_msg", type=type(msg).__name__)
                continue
            if msg.block_index in state.failed_blocks:
                continue
            blocks.append(msg)

        blocks.sort(key=lambda b: b.block_index)
        all_lines: list[str] = []
        boundary_indices: list[int] = []
        block_map: list[tuple[int, MdBlockSource]] = []
        line_no = 1
        for b in blocks:
            base_idx = len(all_lines)
            new_lines = self._annotate(b.md_text, line_no)
            all_lines.extend(new_lines)
            block_map.append((base_idx, b))
            for lb, _level, _title in b.section_boundaries:
                bi = base_idx + int(lb)
                if base_idx <= bi < len(all_lines):
                    boundary_indices.append(bi)
            line_no += len(new_lines)
        return all_lines, sorted(set(boundary_indices)), block_map

    async def _extract_segment_windows(self, seg_lines: list[str]) -> list[Any]:
        """段内 carry-aware 滑窗提取，返回该段所有完整条目（SingleItemExtractLLM）。

        与串行 ``_process_windows`` 同款逻辑，但操作**局部 buffer**、不触碰任何实例状态、
        不分配 id、不 emit——因此可被多段并发安全调用。段在 section 边界切分，段尾即
        section 结束，不完整尾部直接收尾（下一 section 重新开始，不存在跨段条目）。
        """
        items: list[Any] = []
        buf = list(seg_lines)
        window_size = self.base_window
        consecutive_empty = 0

        while buf:
            window_len = min(window_size, len(buf))
            window = buf[:window_len]
            covers_all = window_len >= len(buf)
            window_start_int = self._line_no_int(window[0])
            window_last_int = self._line_no_int(window[-1])

            clean = self._clean_window_text(window)
            has_markers = _ITEM_MARKER_RE.search(clean) is not None
            if has_markers:
                window_empty = False
            elif self.fast_skip_strict:
                window_empty = _WEAK_SIGNAL_RE.search(clean) is None
            else:
                window_empty = True

            # 真空白窗 + 段内后面还有内容 → 跳过（不调 LLM）
            if self.fast_skip_enabled and window_empty and not covers_all:
                buf = buf[window_len:]
                window_size = self.base_window
                consecutive_empty = 0
                continue

            try:
                payload = await self._extract_batch(window, window_start_int, window_last_int)
            except RetryExhausted:
                logger.warning(
                    "stage1_parallel_segment_window_failed",
                    line_range=f"{window_start_int}-{window_last_int}",
                )
                slide = window_len if covers_all else min(self.base_window, window_len)
                buf = buf[slide:]
                window_size = self.base_window
                consecutive_empty = 0
                continue

            if not payload.items:
                consecutive_empty += 1
                at_max = window_size >= self.max_window
                if (
                    self.fast_skip_enabled
                    and payload.stop_reason == "no_items_found"
                    and window_empty
                    and not covers_all
                ):
                    buf = buf[window_len:]
                    window_size = self.base_window
                    consecutive_empty = 0
                    continue
                # force-consume：达 max 连续空够多次，或已到段尾（covers_all）
                if (at_max and consecutive_empty >= self.empty_force_limit) or covers_all:
                    if (
                        payload.stop_reason == "incomplete_at_tail"
                        and payload.last_consumed_line > 0
                        and not covers_all
                    ):
                        cut = self._find_idx_in(buf, payload.last_consumed_line)
                        slide = (cut + 1) if 0 <= cut < window_len else window_len
                    elif not window_empty and not covers_all:
                        # 完整性优先：regex 有标记但 LLM 空 → 只滑 base_window 重扫
                        slide = min(self.base_window, window_len)
                        logger.error(
                            "stage1_parallel_marker_conflict",
                            line_range=f"{window_start_int}-{window_last_int}",
                            slide=slide,
                        )
                    else:
                        slide = window_len
                    buf = buf[slide:]
                    window_size = self.base_window
                    consecutive_empty = 0
                    continue
                if not at_max:
                    window_size = min(window_size + self.expand_step, self.max_window)
                    continue
                # 达 max 但未到 force_limit 且段内仍有内容 → 推进一窗保证进展
                buf = buf[window_len:]
                window_size = self.base_window
                consecutive_empty = 0
                continue

            # 提到条目 → 收集，滑到 last_consumed_line+1（保留不完整尾部给段内下一窗）
            consecutive_empty = 0
            items.extend(payload.items)
            cut = self._find_idx_in(buf, payload.last_consumed_line)
            consumed = (cut + 1) if 0 <= cut < window_len else window_len
            buf = buf[consumed:]
            window_size = self.base_window

        return items

    @staticmethod
    async def _drain_until_sentinel(in_queue: asyncio.Queue[Any]) -> None:
        while True:
            msg = await in_queue.get()
            if isinstance(msg, _Sentinel):
                break

    @staticmethod
    def _block_for_idx(
        block_map: list[tuple[int, MdBlockSource]], start_idx: int
    ) -> MdBlockSource | None:
        """返回 buffer 下标 start_idx 所在的源 block（block_map 按 base_idx 升序）。"""
        chosen: MdBlockSource | None = None
        for base_idx, b in block_map:
            if base_idx <= start_idx:
                chosen = b
            else:
                break
        return chosen

    # ── 产出工具 ────────────────────────────────────────

    def _mirror_batch(
        self, batch: ExtractedBatch, state: FileExtractionState, *, stage_dir: str
    ) -> None:
        """把批次中每条 item 单独镜像为 item_<id>.json（兼容旧 checkpoint 浏览习惯）"""
        if self.checkpoint_root is None or not state.file_hash:
            return
        target_dir = Path(self.checkpoint_root) / state.file_hash / "items" / stage_dir
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            for item_id, item in zip(batch.item_ids, batch.items, strict=True):
                fname = f"item_{item_id:06d}.json"
                snapshot = {
                    "item_id": item_id,
                    "item": item.model_dump(),
                    # REC-11：存原 window 全文 + stop_reason，resume 重放时 Stage1Review 才能
                    # 拿到原文做反跳过补齐/题答绑定/漏解校验（否则三道纠错防线在 resume 后空跑）。
                    # checkpoint 为本地瞬态目录（--restart 清理），此处冗余可接受。
                    "window_lines": list(batch.window_lines),
                    "stop_reason": batch.stop_reason,
                    "window_line_range": list(batch.window_line_range),
                    "window_last_line": batch.window_last_line,
                    "block_index": batch.block_index,
                    "source_file": batch.source_file,
                    "page_start": batch.page_start,
                    "page_end": batch.page_end,
                }
                (target_dir / fname).write_text(
                    _json_mod.dumps(snapshot, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("stage1_mirror_failed", error=str(exc))

    def _update_examples(self, item: Any) -> None:
        compact = {
            "category": item.category,
            "item_type": item.item_type,
            "number": item.number,
            "solution_status": item.solution_status,
            "corrected_content_preview": (item.corrected_content or "")[:200] + "...",
        }
        self._recent_item_examples.append(_json_mod.dumps(compact, ensure_ascii=False))
        if len(self._recent_item_examples) > self._max_examples:
            self._recent_item_examples.pop(0)

    async def _allocate_item_ids(self, count: int) -> list[int]:
        """为一个批次预留连续 item_id。"""
        if count <= 0:
            return []
        if self.order_index_allocator is None:
            first_id = self.next_item_id
        else:
            first_id = await self.order_index_allocator.reserve(count)
        self.next_item_id = max(self.next_item_id, first_id + count)
        return list(range(first_id, first_id + count))

    # ── state 恢复 ──────────────────────────────────────

    def _restore_state(self, state: FileExtractionState) -> None:
        self.line_buffer = list(state.line_buffer_carry)
        self.line_offset = state.line_offset
        self.next_item_id = state.next_item_id
        self.current_window_size = state.current_window_size or self.base_window
        self.consecutive_empty = state.consecutive_empty

    async def _sync_state(self, state: FileExtractionState, *, force: bool = False) -> None:
        state.line_buffer_carry = list(self.line_buffer)
        state.line_offset = self.line_offset
        state.next_item_id = self.next_item_id
        state.current_window_size = self.current_window_size
        state.consecutive_empty = self.consecutive_empty
        await save_state_locked(self.checkpoint_root, state, force=force)

    @staticmethod
    def _annotate(md_text: str, start_line_no: int) -> list[str]:
        """给每行加全局行号前缀"""
        lines = md_text.splitlines(keepends=True)
        return [f"{start_line_no + i:>7d} | {ln}" for i, ln in enumerate(lines)]

    @staticmethod
    def _clean_window_text(window: list[str]) -> str:
        """剥离每行的 `全局行号 | ` 前缀，返回纯正文（供标记/信号正则匹配）。

        前缀会干扰 ^ 锚点（如选择项 `^A.`），故预检前先去掉。
        """
        parts: list[str] = []
        for ln in window:
            if "|" in ln:
                parts.append(ln.split("|", 1)[1])
            else:
                parts.append(ln)
        return "".join(parts)

    def _find_buffer_idx(self, global_line_no: int) -> int:
        return self._find_idx_in(self.line_buffer, global_line_no)

    @staticmethod
    def _find_idx_in(buf: list[str], global_line_no: int) -> int:
        """在带行号前缀的 buffer 中定位全局行号对应的下标；找不到返回 -1。

        先按 first_no 偏移直接命中，未命中再 bisect 兜底（容忍前缀缺失行）。
        无副作用、不依赖实例状态 → 并行段提取可安全复用。
        """
        if not buf:
            return -1

        def line_no_of(line: str) -> int:
            prefix = line.split("|", 1)[0].strip()
            return int(prefix) if prefix.isdigit() else -1

        first_no = line_no_of(buf[0])
        if first_no < 0:
            return -1
        idx = global_line_no - first_no
        if 0 <= idx < len(buf) and line_no_of(buf[idx]) == global_line_no:
            return idx
        line_numbers = [line_no_of(ln) for ln in buf]
        pos = bisect.bisect_left(line_numbers, global_line_no)
        if 0 <= pos < len(line_numbers) and line_numbers[pos] == global_line_no:
            return pos
        return -1

    @staticmethod
    def _line_no_of(line: str) -> int | str:
        prefix = line.split("|", 1)[0].strip()
        return int(prefix) if prefix.isdigit() else prefix

    @staticmethod
    def _line_no_int(line: str) -> int:
        prefix = line.split("|", 1)[0].strip()
        return int(prefix) if prefix.isdigit() else 0
