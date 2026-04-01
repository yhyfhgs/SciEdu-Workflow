"""核心提示词模板"""

# ── 难度校准 ──────────────────────────────────────────

DIFFICULTY_CALIBRATION = """
## 难度校准参考

目标难度: {target}/10

**1-2 (基础概念)**: 直接套用一个定义/公式，一步即得。
**3-4 (基本技巧)**: 需要 1-2 步标准技巧。学过的学生应能做出。
**5-6 (综合应用)**: 组合 2-3 个知识点，或需要较深理解。中等学生需要思考。
**7-8 (深度理解)**: 需较深洞察、巧妙构造或多知识点非平凡组合。好学生需 15-30 分钟。
**9-10 (挑战/竞赛)**: 需创造性方法、深刻直觉或多领域综合。

请参考上下文中的参考题目来校准你的难度感觉。
"""


# ── 生成 Prompt (变式模式) ────────────────────────────

VARIANT_GENERATION_PROMPT = """你是一位资深大学数学教育专家，专精{subject}课程的题目设计。
你需要基于给定的源题，按照指定的变式策略生成一道新题。

# 源题
{source_question_latex}

# 变式策略
{strategy_instruction}

# 目标难度: {target_difficulty}/10
{difficulty_calibration}

# 目标题型: {question_type}

{context_block}

# 生成要求

1. **数学严谨性**: 条件充分且不矛盾。证明题结论为真可证，计算题有确定答案。
2. **LaTeX 规范**: 标准数学环境。定义用"设..."，所有数学符号用 $..$ 或 $$..$$。
3. **难度匹配**: 参考上下文参考题的难度分布来校准。
4. **原创性**: 与"已有题"有实质区别——不仅改数字，而是结构/角度/方法有差异。
5. **可解性**: 你自己必须能完整解出这道题。不确定时不要输出。

# 输出格式 (严格 JSON，不要 markdown 代码块)

{{
  "content_latex": "LaTeX 格式完整题干",
  "solution_sketch": "2-3 句解题思路概要",
  "knowledge_points_used": ["知识点1", "知识点2"],
  "methods_required": ["方法1", "方法2"],
  "estimated_difficulty": 6,
  "design_rationale": "为什么这道题是好的"
}}
"""


# ── 生成 Prompt (自由模式) ────────────────────────────

FREE_GENERATION_PROMPT = """你是一位资深大学数学教育专家，专精{subject}课程的题目设计。
你需要围绕指定的专题和难度，设计一道原创数学题。

# 任务
- 科目: {subject}
- 专题: {topics}
- 目标难度: {target_difficulty}/10
- 题型: {question_type}

# 生成策略
{strategy_instruction}

{difficulty_calibration}

{context_block}

# 核心约束

1. **知识点锚定**: 必须明确考查上下文知识点中的至少 1-2 个。
2. **方法可达**: 目标难度级别的学生通过系统思考可以到达解题路径。
3. **条件最小化**: 每个条件都应在解题过程中被用到。
4. **表述精确**: 标准数学语言，避免"显然"或"容易看出"。

# 输出格式 (严格 JSON，不要 markdown 代码块)

{{
  "content_latex": "LaTeX 格式完整题干",
  "solution_sketch": "2-3 句解题思路概要",
  "knowledge_points_used": ["知识点1", "知识点2"],
  "methods_required": ["方法1", "方法2"],
  "estimated_difficulty": 6,
  "design_rationale": "设计理由"
}}
"""


# ── 求解 Prompt ───────────────────────────────────────

SOLVE_PROMPT = """你是一位严谨的数学家。请对以下数学题给出完整、详细的解答。

# 题目
{question_latex}

# 可参考的知识点
{knowledge_context}

# 解答要求

1. **逐步推导**: 每步有明确理由，引用具体定理/定义名称。
2. **关键步骤标注**: 非显然关键步骤前标注 **[关键步骤]**。
3. **格式**:
   - 证明题: "**证明:**" 开头, "$\\square$" 结尾
   - 计算题: 完整过程，最终答案 $\\boxed{{...}}$
4. **自检**: 是否用到所有条件? 结论与要求一致? 计算题能否代入验证?

# 输出格式 (严格 JSON)

{{
  "solution_latex": "完整分步解答 (LaTeX)",
  "methods_used": ["定理/方法1", "定理/方法2"],
  "step_count": 5,
  "self_check": "自检说明",
  "confidence": "high 或 medium 或 low"
}}
"""


# ── 审核 Prompt ───────────────────────────────────────

REVIEW_PROMPT = """你是一位数学审稿人。请审核以下数学题目是否在数学上正确且良好定义。

# 题目
{question_latex}

# 请检查:
1. 条件是否自洽？(有无矛盾)
2. 证明题: 结论在给定条件下是否为真？
3. 计算题: 是否存在确定答案？
4. 条件是否充分？(有无遗漏必要条件)
5. 有无平凡退化情况使题目无意义？

# 输出格式 (严格 JSON)

{{
  "verdict": "PASS 或 FAIL",
  "issues": ["问题1", "问题2"],
  "suggestions": "修改建议（如有）"
}}
"""


# ── 多维难度评分 Prompt ──────────────────────────────

DIFFICULTY_SCORING_PROMPT = """你是数学教育评估专家。请对以下题目进行多维难度评分。

# 题目
{question_latex}

# 解答概要
{solution_sketch}

# 评分维度 (每个 1-10 整数)

1. concept_count: 涉及独立知识点数 (1个=2, 2个=4, 3个=6, 4+=8)
2. step_count: 最短路径步数 (1-2步=2, 3-4步=5, 5-7步=7, 8+=9)
3. abstraction_level: 抽象程度 (具体数值=2, 一般实数/矩阵=5, 一般空间=7, 范畴=10)
4. technique_novelty: 解法非标准程度 (标准流程=2, 关键insight=5, 巧妙构造=8, 原创=10)
5. condition_parsing: 条件解读难度 (直接=2, 需转化=5, 隐含条件=8)
6. computation_load: 计算量 (几乎无=1, 适中=4, 繁重=7, 极繁重=10)
7. proof_structure: 证明结构 (直接=2, 分情况=5, 反证/归纳=7, 多层嵌套=9)
8. prerequisite_depth: 前置知识深度 (定义=2, 核心定理=5, 高级理论=8)
9. multi_representation: 多表示切换 (单一=2, 两种=5, 三种+=8)
10. metacognition: 策略选择难度 (路径明确=2, 比较路径=5, 策略关键=8)

# 输出格式 (严格 JSON)

{{
  "scores": {{
    "concept_count": 5,
    "step_count": 5,
    "abstraction_level": 5,
    "technique_novelty": 5,
    "condition_parsing": 5,
    "computation_load": 5,
    "proof_structure": 5,
    "prerequisite_depth": 5,
    "multi_representation": 5,
    "metacognition": 5
  }},
  "overall_reasoning": "一句话总结",
  "comparable_to": "类似于哪类经典题"
}}
"""


# ── 解答相关性检查 Prompt ────────────────────────────

SOLUTION_RELEVANCE_PROMPT = """请判断以下解答是否在尝试回答所给题目。
只要解答的方向和题目一致即可判为相关，不要求解答完全正确或完整。

# 题目
{question_latex}

# 解答（可能被截断）
{solution_latex}

# 输出格式 (严格 JSON，relevant 必须是 true 或 false 布尔值)

{{
  "relevant": true,
  "reason": "简短说明"
}}
"""


# ── 标签分类 Prompt ───────────────────────────────────

TAG_CLASSIFICATION_PROMPT = """对以下数学内容进行分类标注。

# 内容
{content_latex}

# 标签体系

科目 (subject): 数学分析, 高等代数, 群论
专题 (topic): 参考以下列表 (可多选)
{available_topics}
方法 (method): 参考以下列表 (可多选)
{available_methods}
题型 (type): calculation, proof, construction, true_false, choice

# 输出格式 (严格 JSON)

{{
  "subject": "数学分析",
  "topics": ["极限", "连续函数"],
  "methods": ["ε-δ语言", "夹逼定理"],
  "question_type": "proof"
}}
"""


# ── Pipeline: 格式推断 Prompt（一次性，文档级）────────────

FORMAT_SPEC_INFER_PROMPT = """你是数学文档结构分析专家。\
给定一份数学文档的采样片段，归纳出该文档的**内容组织格式规则**。
这些规则将用于 Python 程序自动提取文档中的所有条目。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 文档信息
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- **文件名**: {file_name}
- **格式**: {source_format}
- **总行数**: {total_lines}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 文档采样（头部 + 中段）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{text_sample}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 分析任务：格式规则归纳
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请仔细观察采样内容，归纳出：

### 1. 内容块类型和标记模式
- 识别文档中出现的所有**内容块类型**
  （定义、定理、引理、推论、命题、例题、练习、证明、注、性质、公式、方法等）
- 为每种块类型写出 Python `re` 模块的正则表达式：
  - `open_pattern`: 块的起始标记（必须能匹配行首或行内标记）
  - `close_pattern`: 块的结束标记（如 `\\\\end{{...}}`）。
    若无显式结束标记（隐式结束于下一个同级块的开始），设为 null
  - `number_group`: `open_pattern` 中捕获块编号的正则组号（如 `定义 (\\d+\\.\\d+)` 中组 1）
  - `title_group`: 捕获标题的组号（如有）
  - `is_paired_with`: 若此块类型与另一块类型成对（如 solution↔problem, proof↔theorem），填对方类型名

### 2. 章节组织模式
- `section_pattern`: 匹配章/节标题行的正则
  （如 `^##\\s+(.+)` 或 `^§\\d+`）。若无明显章节结构，设为 null
- `detected_structure`: 文档目录骨架（从采样中推断）

### 3. 答案/解答组织
- 答案是内嵌在题目后、集中在文档末尾、还是在独立文件中？

### 输出质量要求
- 正则表达式必须是**合法的 Python `re` 模块语法**
- 正则必须足够精确以区分不同块类型，同时足够宽松以覆盖格式变体
- `examples` 字段必须从原文中**逐字摘取**，不要编造
- 即使采样中某类型只出现一次，只要有明确标记模式也要归纳

## 输出（严格 JSON，不要 markdown 代码块包裹）

{{
  "format_spec": {{
    "content_blocks": [
      {{
        "block_type": "类型名（英文小写，如 definition/theorem/example/problem/proof）",
        "open_pattern": "Python re 正则",
        "close_pattern": "Python re 正则 或 null",
        "number_group": 1,
        "title_group": null,
        "is_paired_with": null,
        "examples": ["从原文逐字摘取的示例行"]
      }}
    ],
    "answer_format": {{
      "location": "inline|after_problem|end_of_document|separate_file|boxed",
      "answer_marker": null,
      "solution_block_type": null
    }},
    "section_pattern": "Python re 正则 或 null",
    "source_type": "exam_paper|lecture_notes|textbook|slides",
    "detected_structure": ["文档骨架条目"],
    "document_description": "一句话文档总结"
  }}
}}
"""


# ── Pipeline: 文档拆分 Prompt（逐 chunk fallback）─────────

BLOCK_SPLIT_PROMPT = """你是数学文档结构分析专家。\
给定一段数学文档片段（Markdown 或 TeX 格式），完成两项任务：
(1) 识别语义完整的内容块边界；(2) 归纳出文档的内容组织格式规则。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 文档片段（第 {start_line}~{end_line} 行）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{text_chunk}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 前文上下文（上一块末尾 3 行）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{previous_block_tail}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 分析任务
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 任务 1: 语义块拆分
- 找出本片段中所有**语义完整**的内容块（定义、定理、例题、证明、练习等）
- 每个块必须是自包含的——不要把一个定理拆成两半
- 如果片段末尾处于某个内容块中间（如定理的证明未结束），
  在 `ends_mid_block` 标记为 true，并给出需要向后延伸的建议行数

### 任务 2: 格式规则归纳（FormatSpec）
- 观察本片段中各类内容块的**起始标记**和**结束标记**的文本模式
- 为每种出现的块类型写出 Python 正则表达式（`re` 语法）
- 注意区分显式边界（如 `\\\\begin{{problem}}...\\\\end{{problem}}`）
  和隐式边界（如 `定义 1.1.` 到下一个同级标记之间）

### 输出质量要求
- 正则表达式必须是合法的 Python `re` 模块语法
- `examples` 字段必须从原文中**逐字摘取**，不要编造
- `detected_structure` 用一行一条的方式描述文档骨架

## 输出（严格 JSON，不要 markdown 代码块包裹）

{{
  "blocks": [
    {{
      "content": "该块的完整文本内容",
      "line_start": 0,
      "line_end": 0,
      "chapter_title": "所在章标题",
      "section_title": "所在节标题"
    }}
  ],
  "format_spec": {{
    "content_blocks": [
      {{
        "block_type": "类型名",
        "open_pattern": "Python re 正则",
        "close_pattern": "Python re 正则 或 null",
        "number_group": null,
        "title_group": null,
        "is_paired_with": null,
        "examples": ["从原文摘取的示例"]
      }}
    ],
    "answer_format": {{
      "location": "inline",
      "answer_marker": null,
      "solution_block_type": null
    }},
    "section_pattern": null,
    "source_type": "exam_paper|lecture_notes|textbook|slides",
    "detected_structure": ["文档骨架条目"],
    "document_description": "一句话文档总结"
  }},
  "ends_mid_block": false,
  "suggested_extend_lines": 0
}}
"""


# ── Pipeline: 校准 + 标注合并 Prompt ─────────────────────

ITEM_CORRECT_AND_TAG_PROMPT = """你是数学教育领域的资深编辑与分类专家。你将同时完成两项任务：
(A) 对提取的数学条目进行质量校准修复；(B) 对其进行多维分类标注。

两项任务共享同一份内容理解，请一次性完成。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 待处理条目
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**类型**: {block_type}
**编号**: {number}
**原始内容**:

{content_raw}

{paired_content_section}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 文档上下文
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- **文档类型**: {source_type}（exam_paper=考试卷 / lecture_notes=讲义 / textbook=教材）
- **章节**: {chapter_title} > {section_title}
- **文档骨架**: {detected_structure}
- **前一条目**: [{prev_type}] {prev_title}
- **后一条目**: [{next_type}] {next_title}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## OCR 预检结果（确定性阶段已处理，以下为残余可疑项）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{suspicious_terms}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 分类参考：知识点 Taxonomy 树（仅展示相关分支）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{taxonomy_excerpt}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 任务 A: 质量校准修复
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

请逐一检查以下维度，修正发现的问题：

| 维度 | 检查重点 |
|------|---------|
| 1. 文字修正 | OCR 错字、数学符号错误（如"盐德金"→"戴德金"、"收玫"→"收敛"、"定罣"→"定理"） |
| 2. LaTeX 修正 | 公式语法错误、未闭合的 $..$ 或 \\\\begin/\\\\end、错误的数学符号 |
| 3. 语义完整性 | 定义是否完整？定理条件是否充分？是否有被截断的内容？ |
| 4. 逻辑正确性 | 推导步骤是否正确？条件与结论是否匹配？ |
| 5. 答案验证 | （仅题目类）答案/解答是否正确？ |

**修正原则**:
- 对于明确的 OCR/拼写错误，直接修正
- 对于可能正确也可能错误的内容，保留原文并在 semantic_issues 中标注
- 修正后的内容应保持原始的表述风格和详细程度
- 所有数学公式必须使用标准 LaTeX 语法

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 任务 B: 多维分类标注
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

基于你对内容的理解（在任务 A 中已深入阅读），完成以下分类：

**第 1-2 层**: 学科域 + 学科
- domain: math / physics / cs
- subject: 数学分析 / 高等代数 / 群论

**第 3 层**: Taxonomy 路径（从上方 taxonomy 树中选择最精确的路径）
- 格式: domain.branch.topic[.subtopic[.specific]]

**第 4 层**: 教育阶段 + 教学难度
- education_level: high_school / undergraduate / graduate
- pedagogical_level: basic / standard / intermediate / advanced / competition

**第 5 层**: 方法与技巧标签
- methods: 具体使用/考查的数学方法

**分类决策依据**: 结合内容主题、章节标题、相邻条目、文档来源类型综合判断。

## 输出（严格 JSON，不要 markdown 代码块包裹）

{{
  "correction": {{
    "corrected_content": "修正后的完整内容文本",
    "corrected_latex": "修正后的 LaTeX 格式内容",
    "corrections": [
      {{
        "original": "原始文本片段",
        "corrected": "修正后文本",
        "reason": "修正原因",
        "category": "ocr|typo|math_symbol|semantic|logic|formatting"
      }}
    ],
    "semantic_issues": [],
    "logic_issues": [],
    "is_answer_verified": null,
    "completeness_score": 1.0,
    "overall_quality": "B"
  }},
  "classification": {{
    "domain": "math",
    "subject": "数学分析",
    "taxonomy": {{
      "domain": "math",
      "branch": "analysis",
      "topic": "limits",
      "subtopic": null,
      "specific": null
    }},
    "education_level": "undergraduate",
    "pedagogical_level": "standard",
    "difficulty": 5,
    "methods": [],
    "tested_topics": [],
    "custom_tags": [],
    "item_subtype": "definition"
  }}
}}
"""


# ── Pipeline: 关系推断 Prompt ────────────────────────────

RELATION_INFERENCE_PROMPT = """你是数学知识图谱专家。给定一批数学条目，识别它们之间的学术关系。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 条目列表
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{items_list}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 可用关系类型
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| 关系 | 含义 | 示例 |
|------|------|------|
| prerequisite | A 是 B 的前置知识 | 极限定义 → 连续性定义 |
| generalization | A 是 B 的推广形式 | 一元积分 → 多元积分 |
| specialization | A 是 B 的特例 | Cauchy 列 → 收敛数列 |
| tests | 题目 A 考查知识点 B | 求极限题 → 夹逼定理 |
| illustrates | 例题 A 展示知识点 B | 例 1.3 → 定理 1.2 |
| similar_to | 题目 A 与 B 类似 | 两道夹逼定理应用题 |
| harder_than | A 比 B 难 | 复合函数极限 > 简单极限 |
| applies_method | 题目 A 使用方法 B | 不等式证明 → 数学归纳法 |

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 推断规则
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. prerequisite: 仅当理解 A 是理解 B 的必要前提时才标注
2. tests/illustrates: 仔细匹配题目考查的核心知识点
3. similar_to: 方法和知识点都相似才标注
4. confidence: 明显关系给 0.9+，推测性给 0.6-0.8，不确定不要输出

## 输出（严格 JSON，不要 markdown 代码块包裹）

{{
  "relations": [
    {{
      "source_id": "源条目ID",
      "target_id": "目标条目ID",
      "relation_type": "prerequisite",
      "confidence": 0.9,
      "note": "关系说明"
    }}
  ]
}}
"""
