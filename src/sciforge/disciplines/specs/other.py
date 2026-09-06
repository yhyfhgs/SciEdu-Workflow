"""未专门建模领域的保守通用配置。"""

from sciforge.core.models import Discipline
from sciforge.disciplines.registry import DisciplineSpec

OTHER_SPEC = DisciplineSpec(
    discipline=Discipline.OTHER,
    display_name="通用领域",
    allowed_kp_types=(
        "definition",
        "principle",
        "fact",
        "method",
        "model",
        "classification",
        "example",
        "remark",
    ),
    allowed_question_types=(
        "explanation",
        "analysis",
        "calculation",
        "application",
        "construction",
        "comparison",
        "discrimination",
        "exploration",
    ),
    tag_root="other:其他",
    prompt_vars={
        "discipline_name": "通用领域",
        "discipline_document_expert": "跨领域文档结构化提取专家",
        "discipline_vlm_expert": "高精度跨领域文档转写专家",
        "discipline_item_types_text": (
            "- `definition` — 定义\n- `principle` — 原理\n- `fact` — 事实\n"
            "- `method` — 方法\n- `model` — 模型\n- `classification` — 分类\n"
            "- `example` / `exercise` / `problem` — 示例或问题\n- `remark` — 注记"
        ),
        "discipline_question_type_hints": (
            "- `explanation` — 解释\n- `analysis` — 分析\n- `calculation` — 计算\n"
            "- `application` — 应用\n- `construction` — 构造\n- `exploration` — 探究"
        ),
        "discipline_example_subject": "general:通用",
        "discipline_example_taxonomy": "other.general.fundamentals",
        "discipline_example_tag_root": "other:其他",
        "discipline_taxonomy_root": "other",
        "discipline_tag_example": '["other:其他", "general:通用", "fundamentals:基础"]',
        "discipline_example_theorem_content": "该原则给出本领域对象之间的基本关系。",
        "discipline_example_question_content": "分析给定案例并说明所用原则。",
        "discipline_block_format_hint": (
            "**所有条目都按统一粗体编号转写，不要遗漏**：定义、原理、事实、方法、"
            "模型、分类、示例、问题和注记均保留原编号。"
        ),
        "discipline_reasoning_style": "证据、规则或因果推理过程",
        "discipline_figure_caption": "图 3.1：概念关系示意图",
    },
)
