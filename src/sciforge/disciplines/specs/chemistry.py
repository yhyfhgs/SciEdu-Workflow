"""化学学科配置。"""

from sciforge.core.models import Discipline
from sciforge.disciplines.registry import DisciplineSpec

CHEMISTRY_SPEC = DisciplineSpec(
    discipline=Discipline.CHEMISTRY,
    display_name="化学",
    allowed_kp_types=(
        "definition",
        "law",
        "principle",
        "formula",
        "reaction",
        "mechanism",
        "model",
        "property",
        "experiment",
        "method",
        "example",
        "remark",
    ),
    allowed_question_types=(
        "calculation",
        "explanation",
        "mechanism_analysis",
        "experiment_design",
        "application",
        "discrimination",
        "synthesis_design",
        "exploration",
    ),
    tag_root="chemistry:化学",
    branch_keywords={
        "物理化学": "physical_chemistry",
        "有机": "organic_chemistry",
        "无机": "inorganic_chemistry",
        "分析化学": "analytical_chemistry",
        "生物化学": "biochemistry",
        "材料": "materials_chemistry",
        "环境": "environmental_chemistry",
        "电化学": "electrochemistry",
        "热化学": "thermochemistry",
        "动力学": "kinetics",
        "量子化学": "quantum_chemistry",
    },
    allowed_branches=(
        "physical_chemistry",
        "organic_chemistry",
        "inorganic_chemistry",
        "analytical_chemistry",
        "biochemistry",
        "materials_chemistry",
        "environmental_chemistry",
        "electrochemistry",
        "thermochemistry",
        "kinetics",
        "quantum_chemistry",
    ),
    prompt_vars={
        "discipline_name": "化学",
        "discipline_document_expert": "化学文档结构化提取专家",
        "discipline_vlm_expert": "高精度化学文档转写专家",
        "discipline_item_types_text": (
            "- `definition` — 定义\n- `law` — 定律\n- `principle` — 原理\n"
            "- `formula` — 公式或方程\n- `reaction` — 反应\n- `mechanism` — 机理\n"
            "- `model` — 模型\n- `property` — 性质\n- `experiment` — 实验\n"
            "- `method` — 方法\n- `example` / `exercise` / `problem` — 例题或习题"
        ),
        "discipline_question_type_hints": (
            '- `calculation` — "计算..."、"求浓度/产率..."\n'
            '- `mechanism_analysis` — "解释机理..."\n'
            '- `experiment_design` — "设计实验..."\n'
            '- `synthesis_design` — "设计合成路线..."\n'
            "- `choice` / `fill_blank` / `true_false` — 仅作为 question_form"
        ),
        "discipline_example_subject": "physical_chemistry:物理化学",
        "discipline_example_taxonomy": "chemistry.physical_chemistry.thermodynamics",
        "discipline_example_tag_root": "chemistry:化学",
        "discipline_taxonomy_root": "chemistry",
        "discipline_tag_example": (
            '["chemistry:化学", "physical_chemistry:物理化学", "thermodynamics:热力学"]'
        ),
        "discipline_example_theorem_content": (
            "在恒温恒压条件下，自发过程的吉布斯自由能变化小于零。"
        ),
        "discipline_example_question_content": "计算给定初始浓度下一级反应的半衰期。",
        "discipline_block_format_hint": (
            "**所有条目都按统一粗体编号转写，不要遗漏**：定义、定律、原理、公式、"
            "反应、机理、模型、性质、实验、方法、例题和习题分别保留原编号与方程式。"
        ),
        "discipline_reasoning_style": "化学机理、守恒关系或实验论证过程",
        "discipline_figure_caption": "图 3.1：反应能量剖面图",
    },
)
