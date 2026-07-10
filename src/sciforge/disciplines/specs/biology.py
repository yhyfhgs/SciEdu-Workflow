"""生物学学科配置。"""

from sciforge.core.models import Discipline
from sciforge.disciplines.registry import DisciplineSpec

BIOLOGY_SPEC = DisciplineSpec(
    discipline=Discipline.BIOLOGY,
    display_name="生物",
    allowed_kp_types=(
        "definition",
        "principle",
        "process",
        "mechanism",
        "structure",
        "function",
        "model",
        "classification",
        "experiment",
        "method",
        "example",
        "remark",
    ),
    allowed_question_types=(
        "explanation",
        "data_analysis",
        "experiment_design",
        "application",
        "comparison",
        "discrimination",
        "modeling",
        "exploration",
    ),
    tag_root="biology:生物",
    branch_keywords={
        "分子生物": "molecular_biology",
        "细胞": "cell_biology",
        "遗传": "genetics",
        "进化": "evolution",
        "生态": "ecology",
        "生理": "physiology",
        "微生物": "microbiology",
        "生物化学": "biochemistry",
        "发育": "developmental_biology",
        "神经": "neuroscience",
        "生物信息": "bioinformatics",
        "免疫": "immunology",
    },
    allowed_branches=(
        "molecular_biology",
        "cell_biology",
        "genetics",
        "evolution",
        "ecology",
        "physiology",
        "microbiology",
        "biochemistry",
        "developmental_biology",
        "neuroscience",
        "bioinformatics",
        "immunology",
    ),
    prompt_vars={
        "discipline_name": "生物",
        "discipline_document_expert": "生物文档结构化提取专家",
        "discipline_vlm_expert": "高精度生物文档转写专家",
        "discipline_item_types_text": (
            "- `definition` — 定义\n- `principle` — 原理\n- `process` — 过程\n"
            "- `mechanism` — 机制\n- `structure` — 结构\n- `function` — 功能\n"
            "- `model` — 模型\n- `classification` — 分类\n- `experiment` — 实验\n"
            "- `method` — 方法\n- `example` / `exercise` / `problem` — 例题或习题"
        ),
        "discipline_question_type_hints": (
            '- `explanation` — "解释...机制"\n'
            '- `data_analysis` — "分析图表/数据..."\n'
            '- `experiment_design` — "设计实验..."\n'
            '- `comparison` — "比较...异同"\n'
            "- `choice` / `fill_blank` / `true_false` — 仅作为 question_form"
        ),
        "discipline_example_subject": "molecular_biology:分子生物学",
        "discipline_example_taxonomy": "biology.molecular_biology.gene_expression",
        "discipline_example_tag_root": "biology:生物",
        "discipline_taxonomy_root": "biology",
        "discipline_tag_example": (
            '["biology:生物", "molecular_biology:分子生物学", "gene_expression:基因表达"]'
        ),
        "discipline_example_theorem_content": (
            "中心法则描述遗传信息从 DNA 经 RNA 流向蛋白质的基本路径。"
        ),
        "discipline_example_question_content": "根据表达量数据判断调控因子对目标基因的影响。",
        "discipline_block_format_hint": (
            "**所有条目都按统一粗体编号转写，不要遗漏**：定义、原理、过程、机制、"
            "结构、功能、模型、分类、实验、方法、例题和习题保留原编号与图表引用。"
        ),
        "discipline_reasoning_style": "机制解释、证据链或实验推断过程",
        "discipline_figure_caption": "图 3.1：基因表达调控网络",
    },
)
