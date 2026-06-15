"""DisciplineRegistry — 按学科查 spec，集中管理学科特化参数

`DisciplineSpec` 是不可变配置，每个学科一份。`DisciplineRegistry` 提供模块级
单例 `DISCIPLINE_REGISTRY`，代码里用 `DISCIPLINE_REGISTRY.get(Discipline.MATH)`
查 spec，然后读其 `allowed_kp_types` / `prompt_vars` 等字段。

新增学科：在 `disciplines/specs/<name>.py` 定义 `<NAME>_SPEC`，然后在本文件
`DisciplineRegistry.default()` 里注册。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from sciforge.core.models import Discipline

# REC-22：infer_branch 记忆化（frozen dataclass 不能挂可变属性，用模块级 dict）。
# key=(tag_root, texts)；规模 = 学科数 × 不同章节标题组合，天然有界。
_MISS = object()
_BRANCH_CACHE: dict[tuple[str, tuple[str, ...]], str | None] = {}


@dataclass(frozen=True)
class DisciplineSpec:
    """单个学科的完整配置

    Attributes:
        discipline: 顶层学科枚举
        display_name: 中文显示名 (如 "数学" / "物理")
        allowed_kp_types: 该学科允许的 kp_type 字符串白名单
        allowed_question_types: 该学科允许的 question_type 字符串白名单
        tag_root: tag 树的根段 (如 "math:数学")
        allowed_branches: 该学科允许的主分支 english 名白名单（tags[1] 的 english 部分必须
            属于此列表），用于阻止 LLM 跳过学科主分支直接写二级概念。空 tuple 表示不校验。
        prompt_vars: 注入到 prompt 模板的占位符 → 值映射
            value 中**禁止**出现单独的 `{` 或 `}`（会与后续 .format 冲突）；
            如需字面花括号，用 `{{` `}}` 预转义
    """

    discipline: Discipline
    display_name: str
    allowed_kp_types: tuple[str, ...]
    # question_type = 认知类别（proof/calculation/construction/counterexample/...）
    allowed_question_types: tuple[str, ...]
    tag_root: str
    prompt_vars: Mapping[str, str]
    allowed_branches: tuple[str, ...] = ()
    # tag 受控词表别名（#6）：变体 english → 规范 english，用于 TagCanonicalizer 归并同义异名。
    # 仅需列出常见 LLM 变体；同中文/同英文的一致性由 canonicalizer 运行内自动处理。
    tag_aliases: Mapping[str, str] = field(default_factory=dict)
    # question_form = 题目形式（与 category 正交）：选择/填空/判断/解答 等。
    # 由 Stage1 从题面识别（A/B/C/D 选项、下划线填空、判断正误）。空 = 不约束。
    allowed_question_forms: tuple[str, ...] = (
        "subjective",  # 主观解答题/大题（默认）
        "fill_blank",  # 填空
        "choice",  # 单选
        "multiple_choice",  # 多选
        "true_false",  # 判断
        "short_answer",  # 简答
    )
    # 中文章节关键词 → 英文主分支（用于 Stage2 从 structural_context 推断 taxonomy 分支，
    # 缩小注入的 taxonomy 子树体积、提升标注聚焦）。key 子串命中即映射。空 = 不推断。
    branch_keywords: Mapping[str, str] = field(default_factory=dict)

    def infer_branch(self, *texts: str) -> str | None:
        """从中文章节文本推断英文主分支；无命中返回 None。

        逐个 (关键词, 分支) 检查，关键词作为子串出现在任一文本中即命中。
        **优先匹配更长的关键词**（更具体，如"微分方程"胜过"微分"、"数学分析"胜过"分析"），
        消解短词歧义；命中的分支须在 allowed_branches 内。

        REC-22：结果按 (tag_root, texts) 记忆化——同一章/节内成百上千条目复用同一推断，
        避免每条都跑 O(关键词数) 子串扫描。
        """
        if not self.branch_keywords:
            return None
        key = (self.tag_root, texts)
        cached = _BRANCH_CACHE.get(key, _MISS)
        if cached is not _MISS:
            return cached  # type: ignore[return-value]
        haystack = " ".join(t for t in texts if t)
        result: str | None = None
        if haystack:
            best_kw = ""
            for kw, branch in self.branch_keywords.items():
                if kw in haystack and len(kw) > len(best_kw):
                    if not self.allowed_branches or branch in self.allowed_branches:
                        best_kw, result = kw, branch
        _BRANCH_CACHE[key] = result
        return result

    def kp_type_or(self, value: str, fallback: str = "theorem") -> str:
        """校验 kp_type 字符串；不在白名单时返回 fallback"""
        return value if value in self.allowed_kp_types else fallback

    def question_type_or(self, value: str, fallback: str = "calculation") -> str:
        """校验 question_type（认知类别）字符串；不在白名单时返回 fallback"""
        return value if value in self.allowed_question_types else fallback

    def question_form_or(self, value: str, fallback: str = "") -> str:
        """校验 question_form（题目形式）字符串；不在白名单时返回 fallback（默认空）"""
        return value if value in self.allowed_question_forms else fallback


class DisciplineRegistry:
    """学科 spec 注册表，不可变"""

    def __init__(self, specs: Mapping[Discipline, DisciplineSpec]) -> None:
        self._specs: dict[Discipline, DisciplineSpec] = dict(specs)

    def get(self, d: Discipline | str) -> DisciplineSpec:
        """按 Discipline 或字符串取 spec；未知学科抛 KeyError"""
        key = Discipline(d) if isinstance(d, str) else d
        return self._specs[key]

    def list_disciplines(self) -> list[Discipline]:
        return list(self._specs.keys())

    def has(self, d: Discipline | str) -> bool:
        try:
            key = Discipline(d) if isinstance(d, str) else d
        except ValueError:
            return False
        return key in self._specs

    def detect_from_tags(self, tags: list[str]) -> Discipline:
        """从 tag 首段识别学科 (如 'math:数学' → MATH)；识别不到返回 OTHER"""
        if not tags:
            return Discipline.OTHER
        first = tags[0].split(":", 1)[0]
        try:
            return Discipline(first)
        except ValueError:
            return Discipline.OTHER

    @classmethod
    def default(cls) -> DisciplineRegistry:
        """构造默认注册表（math + physics）"""
        from sciforge.disciplines.specs.math import MATH_SPEC
        from sciforge.disciplines.specs.physics import PHYSICS_SPEC

        return cls(
            {
                Discipline.MATH: MATH_SPEC,
                Discipline.PHYSICS: PHYSICS_SPEC,
            }
        )


# 模块级单例
DISCIPLINE_REGISTRY = DisciplineRegistry.default()
