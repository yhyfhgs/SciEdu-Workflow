"""SciForge 学科库 — 按学科参数化 Pipeline / Agent 行为

导出:
- DisciplineSpec: 单个学科的配置 (allowed types, tag root, prompt vars)
- DisciplineRegistry: 注册表，用 discipline 枚举查 spec
- DISCIPLINE_REGISTRY: 模块级单例，代码中直接用它

新增学科：见 disciplines/README.md
"""

from sciforge.disciplines.registry import (
    DISCIPLINE_REGISTRY,
    DisciplineRegistry,
    DisciplineSpec,
)

__all__ = [
    "DISCIPLINE_REGISTRY",
    "DisciplineRegistry",
    "DisciplineSpec",
]
