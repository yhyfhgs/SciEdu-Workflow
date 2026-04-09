"""核心提示词模板 — 从 .txt 文件加载"""

from mathforge.prompts.loader import load_prompt

# ── 难度校准 ──────────────────────────────────────────

DIFFICULTY_CALIBRATION = load_prompt("generation/difficulty_calibration.txt")

# ── 生成 Prompt (变式模式) ────────────────────────────

VARIANT_GENERATION_PROMPT = load_prompt("generation/variant_generation.txt")

# ── 生成 Prompt (自由模式) ────────────────────────────

FREE_GENERATION_PROMPT = load_prompt("generation/free_generation.txt")

# ── 求解 Prompt ───────────────────────────────────────

SOLVE_PROMPT = load_prompt("solving/solve.txt")

# ── 审核 Prompt ───────────────────────────────────────

REVIEW_PROMPT = load_prompt("verification/review.txt")

# ── 多维难度评分 Prompt ──────────────────────────────

DIFFICULTY_SCORING_PROMPT = load_prompt("verification/difficulty_scoring.txt")

# ── 解答相关性检查 Prompt ────────────────────────────

SOLUTION_RELEVANCE_PROMPT = load_prompt("verification/solution_relevance.txt")

# ── 标签分类 Prompt ───────────────────────────────────

TAG_CLASSIFICATION_PROMPT = load_prompt("classification/tag_classification.txt")

# ── Pipeline: 校准 + 标注合并 Prompt ─────────────────────

ITEM_CORRECT_AND_TAG_PROMPT = load_prompt("pipeline/item_correct_and_tag.txt")

# ── Pipeline: 语义分块 + 条目直接提取 Prompt ────────────

CHUNK_EXTRACT_PROMPT = load_prompt("pipeline/chunk_extract.txt")

# ── Pipeline: 跨分支关系推断 Prompt ─────────────────────

CROSS_BRANCH_RELATIONS_PROMPT = load_prompt("pipeline/cross_branch_relations.txt")

# ── Pipeline: VLM 页面转写 Prompt ────────────────────────

VLM_CONVERT_PROMPT = load_prompt("pipeline/vlm_convert.txt")
