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


# 向后兼容常量：默认数学学科
# （旧调用方可以继续 `from sciforge.prompts.templates import EXTRACT_AND_CLASSIFY_PROMPT`）
EXTRACT_AND_CLASSIFY_PROMPT = get_extract_prompt_template(Discipline.MATH)
VLM_CONVERT_PROMPT = get_vlm_prompt_template(Discipline.MATH)
