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
    # 认知类别（与题目形式 question_form 正交）。含证明的细分类别。
    allowed_question_types=(
        "proof",  # 一般证明
        "constructive_proof",  # 构造性证明
        "existence_proof",  # 存在性证明
        "counterexample",  # 反例（举反例否定命题）
        "calculation",  # 计算/求值
        "construction",  # 构造（构造对象/例子）
        "discrimination",  # 判别/判断成立性
        "application",  # 应用题
        "exploration",  # 探究/开放题
        # —— 以下为兼容旧数据保留（形式类，已迁移到 question_form）——
        "true_false",
        "choice",
        "fill_blank",
    ),
    tag_root="math:数学",
    # 常见同义 english 变体 → 规范（#6 受控词表）。同中文一致性由 canonicalizer 自动兜底。
    tag_aliases={
        "groups": "group_theory",
        "group": "group_theory",
        "rings": "ring_theory",
        "ring": "ring_theory",
        "fields": "field_theory",
        "eps_delta": "epsilon_delta",
        "epsilon_delta_method": "epsilon_delta",
        "linear_alg": "linear_algebra",
        "analysis_real": "real_analysis",
        "prob": "probability",
        "combinatorial": "combinatorics",
        "diff_eq": "differential_equations",
        "ode": "differential_equations",
    },
    # 中文章节关键词 → 英文主分支（Stage2 局部 taxonomy 注入用）
    branch_keywords={
        "实分析": "real_analysis",
        "复分析": "complex_analysis",
        "复变": "complex_analysis",
        "泛函": "functional_analysis",
        "数学分析": "analysis",
        "分析": "analysis",
        "极限": "analysis",
        "连续": "analysis",
        "微分方程": "differential_equations",
        "微分": "analysis",
        "积分": "analysis",
        "级数": "analysis",
        "线性代数": "linear_algebra",
        "线性空间": "linear_algebra",
        "矩阵": "linear_algebra",
        "行列式": "linear_algebra",
        "向量": "linear_algebra",
        "抽象代数": "abstract_algebra",
        "近世代数": "abstract_algebra",
        "群": "abstract_algebra",
        "环": "abstract_algebra",
        "域": "abstract_algebra",
        "高等代数": "algebra",
        "代数": "algebra",
        "数论": "number_theory",
        "组合": "combinatorics",
        "几何": "geometry",
        "拓扑": "topology",
        "概率": "probability",
        "统计": "statistics",
        "离散": "discrete_math",
        "逻辑": "logic",
        "集合": "set_theory",
        "数值": "numerical_analysis",
    },
    allowed_branches=(
        "analysis",
        "algebra",
        "abstract_algebra",
        "linear_algebra",
        "matrix_theory",
        "complex_analysis",
        "functional_analysis",
        "real_analysis",
        "number_theory",
        "combinatorics",
        "geometry",
        "topology",
        "probability",
        "statistics",
        "discrete_math",
        "logic",
        "numerical_analysis",
        "differential_equations",
        "set_theory",
    ),
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
        "discipline_taxonomy_root": "math",
        "discipline_tag_example": (
            '["math:数学", "algebra:代数", "group_theory:群论", "normal_subgroup:正规子群"]'
        ),
        "discipline_example_theorem_content": (
            "设 $f$ 在 $[a,b]$ 上连续，则 $f$ 在 $[a,b]$ 上一致连续。"
        ),
        "discipline_example_question_content": "计算 $\\int_0^1 x^2 dx$。",
        # vlm_convert.txt 占位符
        "discipline_block_format_hint": (
            "**所有知识点都要按统一粗体编号格式转写，不要遗漏**：\n"
            "- 定义：`**定义 X.Y (名称)**` 或 `**Definition X.Y**`\n"
            "- 定理：`**定理 X.Y (名称)**` 或 `**Theorem X.Y**`\n"
            "- 引理：`**引理 X.Y**` 或 `**Lemma X.Y**`\n"
            "- 推论：`**推论 X.Y**` 或 `**Corollary X.Y**`\n"
            "- 命题：`**命题 X.Y**` 或 `**Proposition X.Y**`\n"
            "- 性质：`**性质 X.Y**` 或 `**Property X.Y**`\n"
            "- 公式：`**公式 X.Y**` 或 `**Formula X.Y**`\n"
            "- 例题：`**例 X.Y**` / `**例题 X.Y**` 或 `**Example X.Y**`\n"
            "- 习题：`**习题 X**` / `**问题 X**` 或 `**Exercise X**` / `**Problem X**`\n"
            "- 注记：`**注 X.Y**` 或 `**Remark X.Y**`"
        ),
        "discipline_reasoning_style": "证明思路或表述风格",
        "discipline_figure_caption": "图 3.1：群的 Cayley 图",
    },
)
