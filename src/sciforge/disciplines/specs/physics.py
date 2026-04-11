"""物理学科配置"""

from sciforge.core.models import Discipline
from sciforge.disciplines.registry import DisciplineSpec

PHYSICS_SPEC = DisciplineSpec(
    discipline=Discipline.PHYSICS,
    display_name="物理",
    allowed_kp_types=(
        "definition",
        "law",
        "principle",
        "formula",
        "theorem",
        "corollary",
        "experiment",
        "model",
        "property",
        "example",
        "remark",
    ),
    allowed_question_types=(
        "calculation",
        "proof",
        "experiment_design",
        "explanation",
        "true_false",
        "choice",
        "fill_blank",
    ),
    tag_root="physics:物理",
    prompt_vars={
        "discipline_name": "物理",
        "discipline_document_expert": "物理文档结构化提取专家",
        "discipline_vlm_expert": "高精度物理文档转写专家",
        "discipline_item_types_text": (
            "- `definition` — 定义（如 电势、动量）\n"
            "- `law` — 定律（如 牛顿运动定律）\n"
            "- `principle` — 原理（如 能量守恒原理）\n"
            "- `formula` — 公式（如 $E=mc^2$）\n"
            "- `theorem` — 定理（含 `proof` 字段）\n"
            "- `corollary` — 推论\n"
            "- `experiment` — 实验说明\n"
            "- `model` — 物理模型（如 理想气体、质点）\n"
            "- `example` — 例题（含 `solution` 字段）\n"
            "- `exercise` / `problem` — 练习/习题（含 `solution` 字段）"
        ),
        "discipline_question_type_hints": (
            '- `calculation` — "计算..."、"求..."\n'
            '- `proof` — "证明..."、"推导..."\n'
            '- `experiment_design` — "设计实验..."\n'
            '- `explanation` — "解释..."、"说明...为何"\n'
            "- `fill_blank` / `choice` / `true_false` — 同数学规则"
        ),
        "discipline_example_subject": "classical_mechanics:经典力学",
        "discipline_example_taxonomy": "physics.mechanics.newton_laws",
        "discipline_example_tag_root": "physics:物理",
        "discipline_tag_example": (
            '["physics:物理", "mechanics:力学", "newton_laws:牛顿定律", "second_law:第二定律"]'
        ),
        "discipline_example_theorem_content": (
            "质点在恒定外力作用下做匀变速直线运动时，$v^2 = v_0^2 + 2as$。"
        ),
        # NOTE: LaTeX `\mathrm{kg}` / `\mathrm{m}` 里的单花括号必须转义为 `{{ }}`，
        # 否则第二阶段 str.format() 会把 {kg}/{m} 当作占位符抛 KeyError
        "discipline_example_question_content": (
            "一质量 $m=2\\,\\mathrm{{kg}}$ 的物体自由下落 $h=5\\,\\mathrm{{m}}$，求其末速度。"
        ),
        "discipline_block_format_hint": (
            "定律/定义/公式/例题格式：`**定律 X.Y (名称)**` 或 `**Law X.Y**`"
        ),
        "discipline_reasoning_style": "推导过程或表述风格",
        "discipline_figure_example": (
            "![图 3.1：斜面受力分析]({{FIGURE:45,10,80,90}})"
        ),
    },
)
