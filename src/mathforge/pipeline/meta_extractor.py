"""MetaExtractor — 多策略条目提取引擎

三级策略链：
1. FormatSpec regex 提取（确定性，最快）
2. 启发式提取（无 spec 或 spec 失效时的 fallback）
3. 整块 fallback（最终兜底）

每次提取附带 ExtractionReport 用于质量评估和下游决策。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

from mathforge.core.models import ContentMarkers, ExtractionReport, FormatSpec, RawItem

if TYPE_CHECKING:
    from mathforge.core.models import MdBlockV2

logger = structlog.get_logger()

# ── 噪声过滤：纯标题/章节名等无效条目 ──

_TITLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^#{1,6}\s+.{0,40}$"),  # Markdown header
    re.compile(r"^第[一二三四五六七八九十百\d]+[章节部分讲]"),  # 中文章节号
    re.compile(r"^Chapter\s+\d+", re.IGNORECASE),  # English chapter
    re.compile(r"^Section\s+\d+", re.IGNORECASE),  # English section
    re.compile(r"^\d+[\.\s]+[^\n]{2,30}$"),  # "3.2 连续函数"
    re.compile(r"^[一二三四五六七八九十]+[、.]\s*.{2,20}$"),  # "三、极限"
    re.compile(r"^§\s*\d+"),  # "§3 群的同态"
    re.compile(r"^\*\*第[一二三四五六七八九十百\d]+[章节部分讲]"),  # 加粗的章节标题
]
_MIN_CONTENT_CHARS = 20

# 目录/结构性内容的特征模式
_TOC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\.{3,}"),  # 目录中的省略号连接 "......"
    re.compile(r"…{2,}"),  # Unicode 省略号
    re.compile(r"^目\s*录\s*$"),  # "目录"
    re.compile(r"^contents?\s*$", re.IGNORECASE),  # "Contents"
    re.compile(r"^(前言|序言|编者|序$)", re.IGNORECASE),  # 前言序言
    re.compile(r"^(参考文献|references|bibliography)", re.IGNORECASE),  # 参考文献
    re.compile(r"^(索引|index)\s*$", re.IGNORECASE),  # 索引
]

# 纯过渡性/说明性段落
_TRANSITION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(本[章节]|本[部分]|下面|以下|接下来)(我们|将|来|介绍|讨论|学习|研究)"),
    re.compile(r"^(在本[章节]中|在这[一]?[章节]中)"),
    re.compile(r"^(回顾|复习|总结|小结)\s*[:：]?\s*$"),
]


def _is_toc_content(content: str) -> bool:
    """检测是否为目录/参考文献/索引等结构性内容"""
    lines = content.strip().splitlines()
    if not lines:
        return False
    # 整块匹配目录关键词
    first = lines[0].strip()
    if any(p.search(first) for p in _TOC_PATTERNS):
        return True
    # 多数行含目录连接符 "......" 或 "……"
    if len(lines) >= 2:
        dot_lines = sum(1 for ln in lines if re.search(r"\.{3,}|…{2,}", ln))
        if dot_lines >= len(lines) * 0.5:
            return True
    return False


# 启发式提取的结构标记
_HEURISTIC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "definition",
        re.compile(r"^(?:定义|Definition)\s*([\d.]+)?[.\s:：]?", re.MULTILINE | re.IGNORECASE),
    ),
    (
        "theorem",
        re.compile(r"^(?:定理|Theorem)\s*([\d.]+)?[.\s:：]?", re.MULTILINE | re.IGNORECASE),
    ),
    ("lemma", re.compile(r"^(?:引理|Lemma)\s*([\d.]+)?[.\s:：]?", re.MULTILINE | re.IGNORECASE)),
    (
        "corollary",
        re.compile(r"^(?:推论|Corollary)\s*([\d.]+)?[.\s:：]?", re.MULTILINE | re.IGNORECASE),
    ),
    (
        "proposition",
        re.compile(r"^(?:命题|Proposition)\s*([\d.]+)?[.\s:：]?", re.MULTILINE | re.IGNORECASE),
    ),
    (
        "example",
        re.compile(r"^(?:例|Example|例题)\s*([\d.]+)?[.\s:：]?", re.MULTILINE | re.IGNORECASE),
    ),
    ("proof", re.compile(r"^(?:证明|Proof)[.\s:：]?", re.MULTILINE | re.IGNORECASE)),
    (
        "exercise",
        re.compile(
            r"^(?:习题|练习|Exercise|Problem)\s*([\d.]+)?[.\s:：]?", re.MULTILINE | re.IGNORECASE
        ),
    ),
    (
        "remark",
        re.compile(r"^(?:注|注记|Remark|Note)\s*([\d.]+)?[.\s:：]?", re.MULTILINE | re.IGNORECASE),
    ),
    (
        "formula",
        re.compile(r"^(?:公式|Formula)\s*([\d.]+)?[.\s:：]?", re.MULTILINE | re.IGNORECASE),
    ),
]

# LaTeX 环境名 → block_type 映射
_LATEX_ENV_MAP: dict[str, str] = {
    "definition": "definition",
    "defn": "definition",
    "theorem": "theorem",
    "thm": "theorem",
    "lemma": "lemma",
    "lem": "lemma",
    "corollary": "corollary",
    "cor": "corollary",
    "proposition": "proposition",
    "prop": "proposition",
    "example": "example",
    "exam": "example",
    "proof": "proof",
    "pf": "proof",
    "exercise": "exercise",
    "problem": "exercise",
    "remark": "remark",
    "rem": "remark",
}

_LATEX_ENV_OPEN = re.compile(r"\\begin\{(\w+)\}", re.MULTILINE)
_LATEX_ENV_CLOSE_TMPL = r"\\end\{%s\}"

# 启发式配对规则：子类型 → 可配对的父类型
_HEURISTIC_PAIR_MAP: dict[str, list[str]] = {
    "proof": ["theorem", "lemma", "corollary", "proposition"],
    "solution": ["exercise", "example", "problem"],
}


class MetaExtractor:
    """多策略条目提取引擎"""

    def extract(self, block: MdBlockV2) -> tuple[list[RawItem], ExtractionReport]:
        """从 MdBlockV2 中提取 RawItem，返回 (items, report)

        策略链：spec regex → 启发式 → fallback
        每个 RawItem 自动附加 ContentMarkers（公式/表格/图片计数）。
        """
        # 策略 1: FormatSpec regex 提取
        if block.format_spec and block.format_spec.content_blocks:
            items = self._extract_by_spec(block)
            score = self._score_extraction(items, block)
            if score >= 0.2 and items:
                self._attach_content_markers(items)
                items = self._filter_noise_items(items)
                report = ExtractionReport(
                    block_id=block.block_id,
                    strategy_used="spec",
                    items_found=len(items),
                    coverage_ratio=self._coverage_ratio(items, block),
                    quality_score=score,
                )
                logger.info(
                    "items_extracted",
                    block_id=block.block_id,
                    strategy="spec",
                    item_count=len(items),
                    quality=round(score, 2),
                )
                return items, report

        # 策略 2: 启发式提取
        items = self._extract_heuristic(block)
        score = self._score_extraction(items, block)
        if items:
            self._attach_content_markers(items)
            items = self._filter_noise_items(items)
            report = ExtractionReport(
                block_id=block.block_id,
                strategy_used="heuristic",
                items_found=len(items),
                coverage_ratio=self._coverage_ratio(items, block),
                quality_score=score,
            )
            logger.info(
                "items_extracted",
                block_id=block.block_id,
                strategy="heuristic",
                item_count=len(items),
                quality=round(score, 2),
            )
            return items, report

        # 策略 3: fallback — 整块作为单条目
        items = self._fallback_extract(block)
        self._attach_content_markers(items)
        items = self._filter_noise_items(items)
        report = ExtractionReport(
            block_id=block.block_id,
            strategy_used="fallback",
            items_found=len(items),
            coverage_ratio=1.0,
            quality_score=0.1,
            issues=["所有提取策略失效，退化为整块单条目"],
        )
        logger.warning(
            "items_extracted_fallback",
            block_id=block.block_id,
            source_file=block.source_file,
        )
        return items, report

    # ── 噪声过滤 ──

    @staticmethod
    def _filter_noise_items(items: list[RawItem]) -> list[RawItem]:
        """过滤纯标题/章节名/目录/页码/过渡段落等无效条目"""
        filtered: list[RawItem] = []
        for item in items:
            content = item.content_raw.strip()
            # 过滤纯文字和注记类型
            if item.block_type in ("text", "remark"):
                logger.debug(
                    "type_filtered",
                    block_type=item.block_type,
                    content=content[:50],
                )
                continue

            # 目录条目检测（含 "......" 的行 + 短内容）
            if _is_toc_content(content):
                logger.debug("toc_item_filtered", content=content[:60])
                continue

            # 纯章节/过渡性标题检测（不论长度）
            first_line = content.split("\n", 1)[0].strip()
            if any(p.search(first_line) for p in _TITLE_PATTERNS):
                # 如果除了标题行外没有实质内容，过滤
                rest = content.split("\n", 1)[1].strip() if "\n" in content else ""
                if len(rest) < _MIN_CONTENT_CHARS:
                    logger.debug("title_item_filtered", content=first_line[:60])
                    continue

            # 纯过渡性段落
            if any(p.match(content) for p in _TRANSITION_PATTERNS) and len(content) < 80:
                logger.debug("transition_item_filtered", content=content[:60])
                continue

            # 内容足够长的条目直接保留
            if len(content) >= _MIN_CONTENT_CHARS:
                filtered.append(item)
                continue

            # 短内容：纯数字/标点
            if re.fullmatch(r"[\d\s.,:;，。：；、\-—()（）\[\]]*", content):
                logger.debug("noise_item_filtered", content=content[:50])
                continue
            filtered.append(item)
        if len(filtered) < len(items):
            logger.info(
                "noise_items_removed",
                removed=len(items) - len(filtered),
                remaining=len(filtered),
            )
        return filtered

    # ── 策略 1: FormatSpec regex 提取 ──

    def _extract_by_spec(self, block: MdBlockV2) -> list[RawItem]:
        """用 FormatSpec 的正则规则提取（原有逻辑）"""
        spec = block.format_spec
        assert spec is not None
        content = block.content
        lines = content.splitlines(keepends=True)

        # 收集 skip patterns
        skip_patterns = [re.compile(p) for p in spec.skip_line_patterns if p]

        # 过滤 skip 行
        filtered_lines: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            if not any(sp.search(line) for sp in skip_patterns):
                filtered_lines.append((i, line))

        filtered_text = "".join(line for _, line in filtered_lines)

        all_items: list[RawItem] = []
        _seq = 0

        for block_spec in spec.content_blocks:
            try:
                if block_spec.close_pattern:
                    items = self._extract_explicit(
                        block_spec, filtered_text, filtered_lines, block, spec, _seq
                    )
                else:
                    items = self._extract_implicit(
                        block_spec, filtered_text, filtered_lines, block, spec, _seq
                    )
                _seq += len(items)
                all_items.extend(items)
            except re.error as e:
                logger.error(
                    "meta_extractor_regex_error",
                    block_type=block_spec.block_type,
                    pattern=block_spec.open_pattern,
                    error=str(e),
                )

        all_items.sort(key=lambda x: x.line_start)
        self._pair_items(all_items, spec)
        return all_items

    # ── 策略 2: 启发式提取 ──

    def _extract_heuristic(self, block: MdBlockV2) -> list[RawItem]:
        """无 FormatSpec 时的启发式提取

        扫描常见数学文档标记（定义、定理、引理等）和 LaTeX 环境。
        """
        content = block.content
        if not content.strip():
            return []

        # 收集所有匹配点: (position, block_type, number, match_obj)
        markers: list[tuple[int, str, str | None, re.Match[str]]] = []

        # 扫描文字标记
        for block_type, pattern in _HEURISTIC_PATTERNS:
            for m in pattern.finditer(content):
                number = m.group(1) if m.lastindex and m.lastindex >= 1 else None
                markers.append((m.start(), block_type, number, m))

        # 扫描 LaTeX 环境
        for m in _LATEX_ENV_OPEN.finditer(content):
            env_name = m.group(1).lower()
            block_type = _LATEX_ENV_MAP.get(env_name)
            if block_type:
                markers.append((m.start(), block_type, None, m))

        if not markers:
            return []

        # 按位置排序
        markers.sort(key=lambda x: x[0])

        # 去重：相近位置（<5字符）的同类型标记只保留第一个
        deduped: list[tuple[int, str, str | None, re.Match[str]]] = []
        for marker in markers:
            if deduped and abs(marker[0] - deduped[-1][0]) < 5:
                continue
            deduped.append(marker)
        markers = deduped

        # 构建 RawItem
        items: list[RawItem] = []
        for i, (pos, block_type, number, match) in enumerate(markers):
            # 确定结束位置
            if i + 1 < len(markers):
                end_pos = markers[i + 1][0]
            else:
                end_pos = len(content)

            # LaTeX 环境：尝试找到 \end{...}
            env_match = _LATEX_ENV_OPEN.match(content, match.start())
            if env_match:
                env_name = env_match.group(1)
                close_pattern = re.compile(_LATEX_ENV_CLOSE_TMPL % re.escape(env_name))
                close_match = close_pattern.search(content, env_match.end())
                if close_match:
                    end_pos = close_match.end()

            content_raw = content[pos:end_pos].strip()
            if not content_raw:
                continue

            line_start = content[:pos].count("\n") + block.line_start
            line_end = content[:end_pos].count("\n") + block.line_start

            items.append(
                RawItem(
                    item_id=f"raw-{block.block_id[:8]}-h{len(items):03d}",
                    block_type=block_type,
                    number=number,
                    content_raw=content_raw,
                    content_latex=content_raw,
                    line_start=line_start,
                    line_end=line_end,
                    source_block_id=block.block_id,
                    source_file=block.source_file,
                    extraction_mode="implicit",
                    raw_metadata=block.raw_metadata,
                )
            )

        # 启发式配对
        self._pair_items_heuristic(items)

        return items

    # ── 显式/隐式提取（保持原有逻辑）──

    def _extract_explicit(
        self,
        block_spec,
        text: str,
        filtered_lines: list[tuple[int, str]],
        block: MdBlockV2,
        spec: FormatSpec,
        seq_start: int = 0,
    ) -> list[RawItem]:
        """模式 A：显式边界（open + close pattern）"""
        items: list[RawItem] = []
        open_re = re.compile(block_spec.open_pattern, re.MULTILINE)
        close_re = re.compile(block_spec.close_pattern, re.MULTILINE)  # type: ignore[arg-type]

        for open_match in open_re.finditer(text):
            close_match = close_re.search(text, open_match.end())
            if not close_match:
                content_raw = text[open_match.start() :]
                end_pos = len(text)
            else:
                content_raw = text[open_match.start() : close_match.end()]
                end_pos = close_match.end()

            number = self._capture_group(open_match, block_spec.number_group)
            title = self._capture_group(open_match, block_spec.title_group)

            line_start = text[: open_match.start()].count("\n") + block.line_start
            line_end = text[:end_pos].count("\n") + block.line_start

            items.append(
                RawItem(
                    item_id=f"raw-{block.block_id[:8]}-{seq_start + len(items):03d}",
                    block_type=block_spec.block_type,
                    number=number,
                    title=title,
                    content_raw=content_raw.strip(),
                    content_latex=content_raw.strip(),
                    line_start=line_start,
                    line_end=line_end,
                    source_block_id=block.block_id,
                    source_file=block.source_file,
                    extraction_mode="explicit",
                    raw_metadata=block.raw_metadata,
                )
            )

        return items

    def _extract_implicit(
        self,
        block_spec,
        text: str,
        filtered_lines: list[tuple[int, str]],
        block: MdBlockV2,
        spec: FormatSpec,
        seq_start: int = 0,
    ) -> list[RawItem]:
        """模式 B：隐式边界（仅 open pattern，到下一个 open 为止）"""
        items: list[RawItem] = []
        open_re = re.compile(block_spec.open_pattern, re.MULTILINE)

        all_opens = list(open_re.finditer(text))
        if not all_opens:
            return items

        all_open_patterns = []
        for bs in spec.content_blocks:
            try:
                all_open_patterns.append(re.compile(bs.open_pattern, re.MULTILINE))
            except re.error:
                pass

        for i, open_match in enumerate(all_opens):
            start_pos = open_match.start()

            end_pos = len(text)
            for pattern in all_open_patterns:
                next_match = pattern.search(text, open_match.end())
                if next_match and next_match.start() < end_pos:
                    end_pos = next_match.start()

            content_raw = text[start_pos:end_pos].strip()

            number = self._capture_group(open_match, block_spec.number_group)
            title = self._capture_group(open_match, block_spec.title_group)

            line_start = text[:start_pos].count("\n") + block.line_start
            line_end = text[:end_pos].count("\n") + block.line_start

            items.append(
                RawItem(
                    item_id=f"raw-{block.block_id[:8]}-{seq_start + len(items):03d}",
                    block_type=block_spec.block_type,
                    number=number,
                    title=title,
                    content_raw=content_raw,
                    content_latex=content_raw,
                    line_start=line_start,
                    line_end=line_end,
                    source_block_id=block.block_id,
                    source_file=block.source_file,
                    extraction_mode="implicit",
                    raw_metadata=block.raw_metadata,
                )
            )

        return items

    # ── 配对 ──

    @staticmethod
    def _pair_items(items: list[RawItem], spec: FormatSpec) -> None:
        """根据 FormatSpec 的 is_paired_with 字段配对条目"""
        pair_map: dict[str, str] = {}
        for bs in spec.content_blocks:
            if bs.is_paired_with:
                pair_map[bs.block_type] = bs.is_paired_with

        last_of_type: dict[str, str] = {}
        for item in items:
            target_type = pair_map.get(item.block_type)
            if target_type and target_type in last_of_type:
                item.paired_item_id = last_of_type[target_type]
            last_of_type[item.block_type] = item.item_id

    @staticmethod
    def _pair_items_heuristic(items: list[RawItem]) -> None:
        """启发式配对：proof→最近的 theorem/lemma，solution→最近的 exercise"""
        last_of_type: dict[str, str] = {}
        for item in items:
            parent_types = _HEURISTIC_PAIR_MAP.get(item.block_type)
            if parent_types:
                for pt in parent_types:
                    if pt in last_of_type:
                        item.paired_item_id = last_of_type[pt]
                        break
            last_of_type[item.block_type] = item.item_id

    @staticmethod
    def _attach_content_markers(items: list[RawItem]) -> None:
        """为每个 RawItem 附加内容类型标记"""
        for item in items:
            item.content_markers = detect_content_markers(item.content_raw)

    # ── 质量评分 ──

    @staticmethod
    def _coverage_ratio(items: list[RawItem], block: MdBlockV2) -> float:
        """计算提取内容覆盖率"""
        block_len = len(block.content.strip())
        if block_len == 0:
            return 1.0
        items_len = sum(len(item.content_raw) for item in items)
        return min(items_len / block_len, 1.0)

    @staticmethod
    def _score_extraction(items: list[RawItem], block: MdBlockV2) -> float:
        """对提取结果评分 (0-1)

        综合考虑：覆盖率、条目数合理性、类型多样性
        """
        if not items:
            return 0.0

        block_len = len(block.content.strip())
        if block_len == 0:
            return 0.0

        # 覆盖率 (0-0.4)
        items_len = sum(len(item.content_raw) for item in items)
        coverage = min(items_len / block_len, 1.0)
        coverage_score = coverage * 0.4

        # 条目数合理性 (0-0.3)
        # 估算预期条目数：每 500 字符约 1 个条目
        expected = max(block_len / 500, 1)
        count_ratio = min(len(items) / expected, 2.0) / 2.0
        count_score = count_ratio * 0.3

        # 类型多样性 (0-0.3)
        types = {item.block_type for item in items}
        non_text_types = types - {"text"}
        if len(types) >= 3:
            diversity_score = 0.3
        elif len(non_text_types) >= 1:
            diversity_score = 0.2
        else:
            diversity_score = 0.05

        return coverage_score + count_score + diversity_score

    # ── Fallback ──

    def _fallback_extract(self, block: MdBlockV2) -> list[RawItem]:
        """最终兜底：整块作为单条目"""
        logger.warning(
            "meta_extractor_fallback",
            block_id=block.block_id,
            source_file=block.source_file,
        )
        return [
            RawItem(
                item_id=f"raw-{block.block_id[:8]}-fallback",
                block_type=(
                    block.block_type.value
                    if hasattr(block.block_type, "value")
                    else str(block.block_type)
                ),
                number=block.number,
                title=block.name,
                content_raw=block.content,
                content_latex=block.content,
                line_start=block.line_start,
                line_end=block.line_end,
                source_block_id=block.block_id,
                source_file=block.source_file,
                extraction_mode="implicit",
                raw_metadata=block.raw_metadata,
            )
        ]

    # ── 工具方法 ──

    @staticmethod
    def _capture_group(match: re.Match, group_index: int | None) -> str | None:
        """安全提取捕获组"""
        if group_index is None:
            return None
        try:
            return match.group(group_index)
        except (IndexError, AttributeError):
            return None


# ── 内容类型检测 ──

_INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$).+?(?<!\$)\$(?!\$)")
_DISPLAY_MATH_RE = re.compile(r"\$\$.+?\$\$", re.DOTALL)
_LATEX_MATH_ENV_RE = re.compile(r"\\begin\{(equation|align|gather|multline|eqnarray)\*?\}")
_TABLE_MD_RE = re.compile(r"^\|.*\|$", re.MULTILINE)
_TABLE_LATEX_RE = re.compile(r"\\begin\{(tabular|table|array)\}")
_IMAGE_MD_RE = re.compile(r"!\[.*?\]\(.*?\)")
_IMAGE_LATEX_RE = re.compile(r"\\includegraphics")
_IMAGE_URL_RE = re.compile(r"https?://\S+\.(?:png|jpg|jpeg|gif|svg|webp)", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def detect_content_markers(text: str) -> ContentMarkers:
    """纯正则检测文本中的公式/表格/图片"""
    display = len(_DISPLAY_MATH_RE.findall(text))
    env = len(_LATEX_MATH_ENV_RE.findall(text))
    inline = len(_INLINE_MATH_RE.findall(text))
    # 计算 Markdown 表格：统计连续 |...|行 的段落数
    table_md = 0
    in_table = False
    for line in text.splitlines():
        is_table_line = line.strip().startswith("|") and line.strip().endswith("|")
        if is_table_line and not in_table:
            table_md += 1
            in_table = True
        elif not is_table_line:
            in_table = False
    table_latex = len(_TABLE_LATEX_RE.findall(text))
    images = (
        len(_IMAGE_MD_RE.findall(text))
        + len(_IMAGE_LATEX_RE.findall(text))
        + len(_IMAGE_URL_RE.findall(text))
    )
    return ContentMarkers(
        formula_count=inline + display + env,
        table_count=table_md + table_latex,
        image_count=images,
        has_display_math=display > 0 or env > 0,
        has_cjk=bool(_CJK_RE.search(text)),
    )
