"""核心提示词模板 — 按学科参数化加载

两阶段注入策略（避免与 JSON 示例的 `{{` `}}` 转义冲突）：
- **阶段 1**：用 `str.replace()` 注入 `DisciplineSpec.prompt_vars` 里的学科变量。
  不经过 python `.format()`，不会消费 `{{` `}}` 转义对。
- **阶段 2**：调用方（各 Stage）用 `.format(current_block=..., retry_feedback=...)` 注入运行时内容，
  此时 JSON 示例里的 `{{` `}}` 会被正确消费为 `{` `}`。

模板文件中必须保证：
- `{discipline_*}`（学科变量）是单括号占位符
- `{current_block}` / `{window_text}` / `{item_json}` / `{annotation_json}` /
  `{tag_subtree}` / `{taxonomy_tree}` / `{retry_feedback}` / `{page_number}` 等运行时占位符是单括号
- 所有其他字面花括号都要成对转义为 `{{` `}}`
"""

from __future__ import annotations

from sciforge.core.models import Discipline
from sciforge.disciplines import DISCIPLINE_REGISTRY
from sciforge.prompts.loader import load_prompt

_VLM_TEMPLATE_RAW = load_prompt("pipeline/vlm_convert.txt")
_EXTRACT_STAGE1_INITIAL_RAW = load_prompt("pipeline/extract_stage1_initial.txt")
_EXTRACT_STAGE1_REVIEW_RAW = load_prompt("pipeline/extract_stage1_review.txt")
_ANNOTATE_STAGE2_INITIAL_RAW = load_prompt("pipeline/annotate_stage2_initial.txt")
_ANNOTATE_STAGE2_REVIEW_RAW = load_prompt("pipeline/annotate_stage2_review.txt")


def _inject_discipline_vars(template: str, discipline: Discipline) -> str:
    """阶段 1：用 str.replace 注入学科变量

    不触发 python format，所以 JSON 示例里的 `{{` `}}` 保持不动，
    等待调用方第二阶段 `.format()` 消费。

    注意：注入的 value 中若含有 `{` `}`（例如学科示例里写了 JSON 片段），
    会被阶段 2 的 `.format()` 误解为占位符，故在替换时对 value 做花括号转义。
    """
    spec = DISCIPLINE_REGISTRY.get(discipline)
    result = template
    for key, value in spec.prompt_vars.items():
        safe_value = value.replace("{", "{{").replace("}", "}}")
        result = result.replace("{" + key + "}", safe_value)
    return result


def get_vlm_prompt_template(discipline: Discipline) -> str:
    """返回已注入学科变量的 vlm_convert 模板

    运行时占位符：`{page_number}` / `{total_pages}` / `{previous_context}` / `{figure_hint}`
    """
    return _inject_discipline_vars(_VLM_TEMPLATE_RAW, discipline)


def get_extract_stage1_initial_template(discipline: Discipline) -> str:
    """Stage1Extract prompt（单条提取）

    运行时占位符：`{current_block}` / `{retry_feedback}`
    输出 JSON schema：SingleItemExtractPayloadLLM
    """
    return _inject_discipline_vars(_EXTRACT_STAGE1_INITIAL_RAW, discipline)


def get_extract_stage1_review_template(discipline: Discipline) -> str:
    """Stage1Review prompt（单条校验）

    运行时占位符：`{window_text}` / `{items_json}` / `{retry_feedback}`
    输出 JSON schema：BatchReviewPayloadLLM
    """
    return _inject_discipline_vars(_EXTRACT_STAGE1_REVIEW_RAW, discipline)


def get_annotate_stage2_initial_template(discipline: Discipline) -> str:
    """Stage2Annotate prompt（单条标注）

    运行时占位符：`{item_json}` / `{tag_subtree}` / `{taxonomy_tree}` / `{retry_feedback}`
    输出 JSON schema：SingleAnnotationLLM
    """
    return _inject_discipline_vars(_ANNOTATE_STAGE2_INITIAL_RAW, discipline)


def get_annotate_stage2_review_template(discipline: Discipline) -> str:
    """Stage2Review prompt（单条标注审核）

    运行时占位符：`{item_json}` / `{annotation_json}` /
    `{tag_subtree}` / `{taxonomy_tree}` / `{retry_feedback}`
    输出 JSON schema：SingleAnnotationLLM
    """
    return _inject_discipline_vars(_ANNOTATE_STAGE2_REVIEW_RAW, discipline)


# 生成路径通用词语替换表（用于 generation/ solving/ verification/ 下的 prompt）
_GENERATION_PROMPT_SUBSTITUTIONS: dict[Discipline, list[tuple[str, str]]] = {
    Discipline.PHYSICS: [
        ("资深大学数学教育专家", "资深大学物理教育专家"),
        ("数学教育评估专家", "物理教育评估专家"),
        ("数学教育评审专家", "物理教育评审专家"),
        ("数学家", "物理学家"),
        ("数学验证专家", "物理验证专家"),
        ("数学审稿人", "物理审稿人"),
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
    """加载生成路径的 prompt 并按学科做词语替换"""
    template = load_prompt(path)
    if discipline == Discipline.MATH:
        return template
    substitutions = _GENERATION_PROMPT_SUBSTITUTIONS.get(discipline)
    if not substitutions:
        return template
    for old, new in substitutions:
        template = template.replace(old, new)
    return template


# 向后兼容常量：默认数学学科
VLM_CONVERT_PROMPT = get_vlm_prompt_template(Discipline.MATH)
