"""DisciplineRegistry — 按学科查 spec，集中管理学科特化参数

`DisciplineSpec` 是不可变配置，每个学科一份。`DisciplineRegistry` 提供模块级
单例 `DISCIPLINE_REGISTRY`，代码里用 `DISCIPLINE_REGISTRY.get(Discipline.MATH)`
查 spec，然后读其 `allowed_kp_types` / `prompt_vars` 等字段。

新增学科：在 `disciplines/specs/<name>.py` 定义 `<NAME>_SPEC`，然后在本文件
`DisciplineRegistry.default()` 里注册。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sciforge.core.models import Discipline


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
    allowed_question_types: tuple[str, ...]
    tag_root: str
    prompt_vars: Mapping[str, str]
    allowed_branches: tuple[str, ...] = ()

    def kp_type_or(self, value: str, fallback: str = "theorem") -> str:
        """校验 kp_type 字符串；不在白名单时返回 fallback"""
        return value if value in self.allowed_kp_types else fallback

    def question_type_or(self, value: str, fallback: str = "calculation") -> str:
        """校验 question_type 字符串；不在白名单时返回 fallback"""
        return value if value in self.allowed_question_types else fallback


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
