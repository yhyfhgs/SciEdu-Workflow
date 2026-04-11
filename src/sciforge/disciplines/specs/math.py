"""数学学科配置"""

from sciforge.core.models import Discipline
from sciforge.disciplines.registry import DisciplineSpec

MATH_SPEC = DisciplineSpec(
    discipline=Discipline.MATH,
    display_name="数学",
    allowed_kp_types=(
        "definition",
        "theorem",
        "lemma",
        "corollary",
        "proposition",
        "property",
        "formula",
        "example",
        "remark",
        "method",
        "proof_technique",
    ),
    allowed_question_types=(
        "proof",
        "calculation",
        "construction",
        "true_false",
        "choice",
        "fill_blank",
    ),
    tag_root="math:数学",
    prompt_vars={
        # extract_and_classify.txt 占位符
        "discipline_name": "数学",
        "discipline_document_expert": "数学文档结构化提取专家",
        "discipline_vlm_expert": "高精度数学文档转写专家",
        "discipline_item_types_text": (
            "- `definition` — 定义\n"
            "- `theorem` — 定理（含 `proof` 字段）\n"
            "- `lemma` — 引理（含 `proof` 字段）\n"
            "- `corollary` — 推论（含 `proof` 字段）\n"
            "- `proposition` — 命题（含 `proof` 字段）\n"
            "- `property` — 性质\n"
            "- `formula` — 公式\n"
            "- `example` — 例题（含 `solution` 字段）\n"
            "- `exercise` — 练习/习题（含 `solution` 字段）\n"
            "- `problem` — 题目（含 `solution` 字段）"
        ),
        "discipline_question_type_hints": (
            '- `proof` — "证明..."、"试证..."\n'
            '- `calculation` — "计算..."、"求..."、"求值..."\n'
            "- `fill_blank` — 含 `\\underline`、空格待填\n"
            "- `choice` — 有 A/B/C/D 选项\n"
            '- `true_false` — "判断正误"、"是否成立"\n'
            '- `construction` — "构造..."、"给出一个..."'
        ),
        "discipline_example_subject": "real_analysis:实分析",
        "discipline_example_taxonomy": "math.analysis.continuity",
        "discipline_example_tag_root": "math:数学",
        "discipline_tag_example": (
            '["math:数学", "algebra:代数", "group_theory:群论", "normal_subgroup:正规子群"]'
        ),
        "discipline_example_theorem_content": (
            "设 $f$ 在 $[a,b]$ 上连续，则 $f$ 在 $[a,b]$ 上一致连续。"
        ),
        "discipline_example_question_content": "计算 $\\int_0^1 x^2 dx$。",
        # vlm_convert.txt 占位符
        "discipline_block_format_hint": (
            "定理/定义/引理格式：`**定理 X.Y (名称)**` 或 `**Theorem X.Y**`"
        ),
        "discipline_reasoning_style": "证明思路或表述风格",
        # 注意 `{{FIGURE:...}}` 的双括号用于 python format 转义，保留不动
        "discipline_figure_example": (
            "![图 3.1：群的 Cayley 图]({{FIGURE:45,10,80,90}})"
        ),
    },
)
