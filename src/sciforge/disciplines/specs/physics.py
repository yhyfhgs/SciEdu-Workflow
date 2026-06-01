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
    branch_keywords={
        "力学": "mechanics",
        "牛顿": "mechanics",
        "运动": "mechanics",
        "热力学": "thermodynamics",
        "热": "thermodynamics",
        "统计力学": "statistical_mechanics",
        "电磁": "electromagnetism",
        "电场": "electromagnetism",
        "磁场": "electromagnetism",
        "电路": "electromagnetism",
        "光学": "optics",
        "光": "optics",
        "量子": "quantum_mechanics",
        "相对论": "relativity",
        "固体": "solid_state",
        "流体": "fluid_dynamics",
        "声": "acoustics",
        "原子": "atomic_physics",
        "核": "nuclear_physics",
        "粒子": "particle_physics",
        "天体": "astrophysics",
    },
    allowed_branches=(
        "mechanics",
        "thermodynamics",
        "electromagnetism",
        "optics",
        "quantum_mechanics",
        "relativity",
        "statistical_mechanics",
        "solid_state",
        "fluid_dynamics",
        "acoustics",
        "atomic_physics",
        "nuclear_physics",
        "particle_physics",
        "astrophysics",
    ),
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
        "discipline_taxonomy_root": "physics",
        "discipline_tag_example": (
            '["physics:物理", "mechanics:力学", "newton_laws:牛顿定律", "second_law:第二定律"]'
        ),
        "discipline_example_theorem_content": (
            "质点在恒定外力作用下做匀变速直线运动时，$v^2 = v_0^2 + 2as$。"
        ),
        # _inject_discipline_vars 会自动把 value 中的 { } 转义为 {{ }}，
        # 所以 spec 写裸 LaTeX 即可，不再需要手动写 {{ }}
        "discipline_example_question_content": (
            "一质量 $m=2\\,\\mathrm{kg}$ 的物体自由下落 $h=5\\,\\mathrm{m}$，求其末速度。"
        ),
        "discipline_block_format_hint": (
            "**所有知识点都要按统一粗体编号格式转写，不要遗漏**：\n"
            "- 定义：`**定义 X.Y (名称)**` 或 `**Definition X.Y**`\n"
            "- 定律：`**定律 X.Y (名称)**` 或 `**Law X.Y**`\n"
            "- 原理：`**原理 X.Y**` 或 `**Principle X.Y**`\n"
            "- 公式：`**公式 X.Y**` 或 `**Formula X.Y**`\n"
            "- 定理：`**定理 X.Y**` 或 `**Theorem X.Y**`\n"
            "- 推论：`**推论 X.Y**` 或 `**Corollary X.Y**`\n"
            "- 实验：`**实验 X.Y**` 或 `**Experiment X.Y**`\n"
            "- 模型：`**模型 X.Y**` 或 `**Model X.Y**`\n"
            "- 性质：`**性质 X.Y**` 或 `**Property X.Y**`\n"
            "- 例题：`**例 X.Y**` / `**例题 X.Y**` 或 `**Example X.Y**`\n"
            "- 习题：`**习题 X**` / `**问题 X**` 或 `**Exercise X**` / `**Problem X**`\n"
            "- 注记：`**注 X.Y**` 或 `**Remark X.Y**`"
        ),
        "discipline_reasoning_style": "推导过程或表述风格",
        "discipline_figure_caption": "图 3.1：斜面受力分析",
    },
)
