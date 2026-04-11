"""核心提示词模板 — 按学科参数化加载

两阶段注入策略（避免与 JSON 示例的 `{{` `}}` 转义冲突）：
- **阶段 1**：用 `str.replace()` 注入 `DisciplineSpec.prompt_vars` 里的学科变量。
  不经过 python `.format()`，不会消费 `{{` `}}` 转义对。
- **阶段 2**：调用方（如 `Task1Extractor._extract_and_classify`）用 `.format(
  current_block=..., tag_subtree=...)` 注入运行时内容，此时 JSON 示例里的
  `{{` `}}` 会被正确消费为 `{` `}`。

模板文件中必须保证：
- `{discipline_*}`（学科变量）是单括号占位符
- `{current_block}` / `{tag_subtree}` / `{page_number}` 等运行时占位符是单括号
- 所有其他字面花括号都要成对转义为 `{{` `}}`

当前主线两个 prompt：
- `EXTRACT_AND_CLASSIFY_PROMPT`（数学默认）: 由 Task1Extractor 消费
- `VLM_CONVERT_PROMPT`（数学默认）: 由 VlmConverter 消费

Pipeline 运行时应使用 `get_extract_prompt_template(discipline)` /
`get_vlm_prompt_template(discipline)` 拿按学科注入后的模板。
"""

from __future__ import annotations

from sciforge.core.models import Discipline
from sciforge.disciplines import DISCIPLINE_REGISTRY
from sciforge.prompts.loader import load_prompt

_EXTRACT_TEMPLATE_RAW = load_prompt("pipeline/extract_and_classify.txt")
_VLM_TEMPLATE_RAW = load_prompt("pipeline/vlm_convert.txt")


def _inject_discipline_vars(template: str, discipline: Discipline) -> str:
    """阶段 1：用 str.replace 注入学科变量

    不触发 python format，所以 JSON 示例里的 `{{` `}}` 保持不动，
    等待调用方第二阶段 `.format()` 消费。
    """
    spec = DISCIPLINE_REGISTRY.get(discipline)
    result = template
    for key, value in spec.prompt_vars.items():
        result = result.replace("{" + key + "}", value)
    return result


def get_extract_prompt_template(discipline: Discipline) -> str:
    """返回已注入学科变量的 extract_and_classify 模板

    返回值仍含运行时占位符 `{current_block}` / `{tag_subtree}`，调用方用
    `.format()` 填入具体内容。
    """
    return _inject_discipline_vars(_EXTRACT_TEMPLATE_RAW, discipline)


def get_vlm_prompt_template(discipline: Discipline) -> str:
    """返回已注入学科变量的 vlm_convert 模板

    返回值仍含运行时占位符 `{page_number}` / `{total_pages}` / `{previous_context}`
    / `{figure_hint}`，调用方用 `.format()` 填入具体内容。
    """
    return _inject_discipline_vars(_VLM_TEMPLATE_RAW, discipline)


# 生成路径通用词语替换表（用于 generation/ solving/ verification/ 下的 prompt）
# 这些 prompt 原文全是数学特化的词语，通过加载时 str.replace 做局部适配。
# 运行时 LLM 看到的就是"物理家"、"物理专家"等——糙但够用，Post-MVP 可以为每个
# 学科维护独立的 prompt 文件。
_GENERATION_PROMPT_SUBSTITUTIONS: dict[Discipline, list[tuple[str, str]]] = {
    Discipline.PHYSICS: [
        # 专家/家称谓
        ("资深大学数学教育专家", "资深大学物理教育专家"),
        ("数学教育评估专家", "物理教育评估专家"),
        ("数学教育评审专家", "物理教育评审专家"),
        ("数学家", "物理学家"),
        ("数学验证专家", "物理验证专家"),
        ("数学审稿人", "物理审稿人"),
        # 内容描述
        ("原创数学题", "原创物理题"),
        ("数学严谨性", "物理严谨性"),
        ("数学对象", "物理对象"),
        ("数学表达式", "物理表达式"),
        ("数学语言", "物理语言"),
        ("数学环境", "物理环境"),
        ("数学洞察力", "物理洞察力"),
        ("数学直觉", "物理直觉"),
        ("数学推导", "物理推导"),
        ("数学命题", "物理命题"),
        ("数学题", "物理题"),
        ("纯数学与计算数学", "理论物理与计算物理"),
        ("数学归纳法", "数学归纳法"),  # 术语保留
        ("数学理由", "物理理由"),
        # 兜底：独立"数学"（放最后避免前面规则被误伤）
        ("数学", "物理"),
    ],
    Discipline.CHEMISTRY: [
        ("资深大学数学教育专家", "资深大学化学教育专家"),
        ("数学教育评估专家", "化学教育评估专家"),
        ("数学教育评审专家", "化学教育评审专家"),
        ("数学家", "化学家"),
        ("原创数学题", "原创化学题"),
        ("数学题", "化学题"),
        ("数学", "化学"),
    ],
    Discipline.BIOLOGY: [
        ("资深大学数学教育专家", "资深大学生物教育专家"),
        ("数学教育评估专家", "生物教育评估专家"),
        ("数学教育评审专家", "生物教育评审专家"),
        ("数学家", "生物学家"),
        ("原创数学题", "原创生物题"),
        ("数学题", "生物题"),
        ("数学", "生物"),
    ],
}


def get_generation_prompt_template(path: str, discipline: Discipline) -> str:
    """加载生成路径的 prompt 并按学科做词语替换

    Args:
        path: 相对 `prompts/templates/` 的 .txt 路径（如 "generation/free_generation.txt"）
        discipline: 目标学科
    Returns:
        经过词语替换后的模板字符串。数学学科直接返回原文；其他学科按
        _GENERATION_PROMPT_SUBSTITUTIONS 表做 str.replace 逐条替换。
    """
    template = load_prompt(path)
    if discipline == Discipline.MATH:
        return template  # 数学是 prompt 原文，无需替换
    substitutions = _GENERATION_PROMPT_SUBSTITUTIONS.get(discipline)
    if not substitutions:
        return template
    for old, new in substitutions:
        template = template.replace(old, new)
    return template


# 向后兼容常量：默认数学学科
# （旧调用方可以继续 `from sciforge.prompts.templates import EXTRACT_AND_CLASSIFY_PROMPT`）
EXTRACT_AND_CLASSIFY_PROMPT = get_extract_prompt_template(Discipline.MATH)
VLM_CONVERT_PROMPT = get_vlm_prompt_template(Discipline.MATH)
