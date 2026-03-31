"""
MathForge Prompt 模板库
所有与 LLM 交互的 Prompt 集中管理于此。
"""

# ── 变式策略指令 ──────────────────────────────────────

STRATEGY_INSTRUCTIONS = {

    "A1_parameter_shift": """
## 策略: 参数替换 (Parameter Shift)
保持源题的数学结构和解题方法不变，替换其中的具体数值、函数、空间实例等。
替换后必须验证: 新参数下题目仍有意义，解法本质相同，答案可计算。
新题难度应与源题相近 (±1)。
""",

    "A2_method_shift": """
## 策略: 方法迁移 (Method Shift)
围绕源题的同一核心知识点，设计一道需要不同解法入口的新题。
例: 源题用 ε-δ 证明极限 → 新题用夹逼/单调有界; 源题用行列式 → 新题用秩.
关键: 同一知识域，不同技术路径。
""",

    "A3_type_shift": """
## 策略: 题型转换 (Type Shift)
将源题做本质题型转换:
- 计算 → 证明: "求极限值" → "证明极限存在"
- 求解 → 判断: "求特征值" → "判断是否可对角化并证明"
- 证明 → 构造: "证明存在..." → "构造一个满足...的例子"
- 正向 → 反例: "条件A则结论B" → "构造满足A但不满足B'的反例"
题型转换通常改变难度，请根据目标难度调整。
""",

    "A4_escalate": """
## 策略: 升阶 (Escalate)
在源题基础上增加数学深度:
- 特殊→一般: ℝⁿ → 赋范空间, 有限群 → 一般群
- 增加嵌套/条件层数
- 削弱假设看能得到什么
- 要求更强结论: 点态 → 一致
新题难度应比源题高 2-3 级。确保升阶后仍可解。
""",

    "A5_simplify": """
## 策略: 降阶 (Simplify)
将源题降低抽象层次或减少步骤:
- 一般→特殊: 一般空间 → ℝⁿ 具体情形
- 添加辅助条件 (连续、有界、单调等)
- 拆成引导式 (a)(b)(c) 小问
新题难度应比源题低 2-3 级。简化后仍保留核心考查点。
""",

    "A6_reverse": """
## 策略: 逆向 (Reverse)
将源题的因果/已知-未知关系反转:
- 正向计算 → 反向求参数
- 存在性 → 唯一性
- 充分条件 → 必要条件
- 给结论求条件
验证逆向后问题是否良好定义。
""",

    "B1_topic_generation": """
## 策略: 专题自由生成 (Topic Generation)
围绕指定专题自由设计一道原创题。
设计原则:
1. 锚定核心知识点 (从上下文中选取)
2. 确定解题关键方法
3. 不掌握知识点→无法下手，掌握→路径清晰
4. 避免过于技巧性——考查理解深度，非套路记忆
灵感来源: 两定理交汇处、定理边界情况、抽象定理实例化。
""",

    "B2_cross_topic": """
## 策略: 交叉综合 (Cross-Topic)
融合多个专题设计综合题。
选取 2-3 个不同专题的知识点，设计需要串联它们的问题。
解题路径应自然——数学上确实存在联系，非人为拼凑。
综合题难度通常 ≥6。确保有挑战但路径可发现。
"""
}


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
