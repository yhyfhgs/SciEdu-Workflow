# MathForge 核心引擎：题目生成系统 — 深度技术规格

---

## 0. 设计哲学

一道好的数学题不是凭空捏造的——它是**知识点的载体**、**方法的试金石**、**思维的脚手架**。

本引擎的核心原则：

1. **知识驱动，而非模板拼凑**：每道题必须锚定到具体知识点和方法，而非仅做表面文字替换。
2. **生成 → 求解 → 验证 闭环**：任何生成的题目，系统自身必须能解出来并验证正确性，否则丢弃。
3. **难度是可校准的连续量**：不是拍脑袋标个 1-5，而是基于多维特征的可解释评分。
4. **多样性有结构**：变式不是随机扰动，而是沿着明确的数学维度做有意义的变换。

---

## 1. 生成引擎总体流程

```
用户请求 (科目/专题/难度/题型/数量/变式源题)
         │
         ▼
┌─────────────────────────────────────────────────────┐
│            Stage 1: 上下文检索 (Context Retrieval)    │
│                                                     │
│  ┌─────────────┐    ┌──────────────┐                │
│  │ 知识库检索    │    │ 题库检索      │                │
│  │ (语义+标签)   │    │ (语义+去重)   │                │
│  └──────┬──────┘    └──────┬───────┘                │
│         └────────┬─────────┘                        │
│                  ▼                                   │
│         上下文包 (Context Bundle)                     │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│            Stage 2: 题目生成 (Generation)             │
│                                                     │
│  Context Bundle + Generation Prompt                  │
│         │                                           │
│         ▼                                           │
│  LLM 生成候选题 (temperature=0.7, n=3)               │
│         │                                           │
│         ▼                                           │
│  候选池 (3 道候选)                                    │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│            Stage 3: 求解 (Solution)                   │
│                                                     │
│  对每道候选题独立求解                                  │
│  Solve Agent (CoT, temperature=0)                    │
│         │                                           │
│     能解出 → 保留       解不出/矛盾 → 丢弃             │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│            Stage 4: 验证 (Verification)               │
│                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │ LaTeX 语法    │  │ 数学正确性    │  │ 去重检测   │ │
│  │ 可编译?       │  │ 独立审核      │  │ cosine<0.92│ │
│  └──────┬───────┘  └──────┬───────┘  └─────┬─────┘ │
│         └────────┬────────┴────────────────┘        │
│                  ▼                                   │
│         通过 → 进入 Stage 5                           │
│         不通过 → 回到 Stage 2 重新生成 (max 3 轮)      │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│            Stage 5: 标注 (Annotation)                 │
│                                                     │
│  ┌──────────────┐  ┌──────────────┐                 │
│  │ 多维难度评分   │  │ 多标签分类    │                 │
│  │ (10维向量)     │  │ (科目/专题/   │                 │
│  │ → 综合分       │  │  方法/题型)   │                 │
│  └──────────────┘  └──────────────┘                 │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
              存入题库 + 返回用户
```

---

## 2. Stage 1: 上下文检索 — 让 LLM "有米下锅"

这是质量的根基。LLM 不是凭空造题，而是在**精确的数学语境**中创作。

### 2.1 检索策略

```python
class ContextRetriever:
    """
    为题目生成构建上下文包。
    核心思路：双路检索 (知识库 + 题库)，语义 + 结构化过滤混合。
    """

    async def build_context(self, request: GenerationRequest) -> ContextBundle:
        """
        输入: GenerationRequest
            - subject: "数学分析"
            - topics: ["一致连续", "函数极限"]
            - difficulty_target: 6
            - question_type: "proof"
            - source_question: Optional[Question]  (变式模式下非空)
            - exclusion_ids: set[UUID]             (用户已做过的题)

        输出: ContextBundle
            - knowledge_points: list[KP]   精选知识点 (5-8 条)
            - reference_questions: list[Q]  参考题目 (5-10 条)
            - negative_examples: list[Q]    已有相似题 (用于去重)
        """

        # ── 路径 A: 知识点检索 ──
        kps = await self._retrieve_knowledge(request)

        # ── 路径 B: 参考题目检索 ──
        ref_questions = await self._retrieve_questions(request)

        # ── 路径 C: 负样本 (已有相似题，用于告诉 LLM "不要生成这样的") ──
        negative = await self._retrieve_negatives(request)

        return ContextBundle(
            knowledge_points=kps,
            reference_questions=ref_questions,
            negative_examples=negative,
        )

    async def _retrieve_knowledge(self, req) -> list[KnowledgePoint]:
        """
        混合检索：结构化过滤 + 语义排序

        Step 1: 标签过滤 — 缩小范围
            WHERE subject = req.subject
            AND topic IN req.topics
            AND kp_type IN ('definition', 'theorem', 'lemma', 'formula', 'proof_technique')

        Step 2: 语义排序 — 如果有 source_question，用它的 embedding 做相似检索
                         否则，用 topics 描述文本做 embedding

        Step 3: 难度筛选 — 优先选取 difficulty 在 [target-2, target+1] 范围内的
                          (知识点本身难度略低于题目难度是合理的)

        Step 4: 多样性保证 — 从结果中确保覆盖:
                - 至少 1 条 definition (题目需要用到的定义)
                - 至少 1 条 theorem (题目可能用到的定理)
                - 至少 1 条 proof_technique 或 formula (方法提示)

        返回 5-8 条知识点。
        """
        # 实现细节
        query_embedding = await self._get_query_embedding(req)

        # SQL 示例 (pgvector)
        sql = """
        SELECT kp.*, kp.embedding <=> $1 AS distance
        FROM knowledge_points kp
        JOIN kp_tags kt ON kp.id = kt.kp_id
        JOIN tags t ON kt.tag_id = t.id
        WHERE t.name = ANY($2)                          -- 专题过滤
          AND kp.difficulty BETWEEN $3 AND $4            -- 难度窗口
        ORDER BY distance ASC
        LIMIT 20
        """
        candidates = await db.fetch(sql, query_embedding, req.topics,
                                     req.difficulty_target - 2,
                                     req.difficulty_target + 1)

        # 多样性重排
        selected = self._diversify(candidates, min_types={
            'definition': 1, 'theorem': 1, 'proof_technique': 1
        }, max_total=8)

        return selected

    async def _retrieve_questions(self, req) -> list[Question]:
        """
        检索参考题目 — 不是给 LLM 抄，而是让它理解"这个难度/类型的题长什么样"。

        策略:
        1. 如果有 source_question (变式模式):
           - 检索与源题同专题、相近难度的其他题 (排除自身)
           - 这些题展示了同一知识域的不同考法
        2. 如果是自由生成模式:
           - 按 topics + difficulty 范围检索
           - 按 question_type 过滤

        额外: 加入 1-2 道难度略高/略低的题，给 LLM 难度锚点。
        """
        # 主检索: 目标难度附近
        main_refs = await self._vector_search_questions(
            query=req.source_question or req.topic_description,
            filters={
                'difficulty': (req.difficulty_target - 1, req.difficulty_target + 1),
                'question_type': req.question_type,
                'subject': req.subject,
            },
            exclude_ids=req.exclusion_ids,
            top_k=6,
        )

        # 锚点题: 一道明显简单的 + 一道明显难的
        easier = await self._vector_search_questions(
            query=req.source_question or req.topic_description,
            filters={'difficulty': (req.difficulty_target - 3, req.difficulty_target - 2)},
            top_k=1,
        )
        harder = await self._vector_search_questions(
            query=req.source_question or req.topic_description,
            filters={'difficulty': (req.difficulty_target + 2, req.difficulty_target + 3)},
            top_k=1,
        )

        return main_refs + easier + harder  # 6+1+1 = 8 条

    async def _retrieve_negatives(self, req) -> list[Question]:
        """
        负样本: 告诉 LLM "不要和这些题太像"。
        用于变式模式下，检索与 source_question 最相似的 top-5 已有题。
        """
        if not req.source_question:
            return []
        return await self._vector_search_questions(
            query=req.source_question,
            top_k=5,
            similarity_threshold=0.80,  # 只取确实相似的
        )
```

### 2.2 Context Bundle 结构

```python
@dataclass
class ContextBundle:
    """传递给生成 Prompt 的完整上下文"""

    # 知识背景
    knowledge_points: list[KnowledgePoint]  # 5-8 条

    # 参考题目 (含难度锚点)
    reference_questions: list[Question]      # 6-8 条

    # 负样本 (避免重复)
    negative_examples: list[Question]        # 0-5 条

    def to_prompt_block(self) -> str:
        """序列化为 Prompt 中的上下文块"""
        sections = []

        # 知识点区
        sections.append("## 相关知识点\n")
        for i, kp in enumerate(self.knowledge_points, 1):
            sections.append(
                f"### 知识点 {i}: {kp.title} [{kp.kp_type}] (难度 {kp.difficulty}/10)\n"
                f"{kp.content_latex}\n"
            )

        # 参考题区
        sections.append("\n## 同领域参考题目（供参考风格和难度，不要照搬）\n")
        for i, q in enumerate(self.reference_questions, 1):
            sections.append(
                f"### 参考题 {i} (难度 {q.difficulty}/10, 类型: {q.question_type})\n"
                f"{q.content_latex}\n"
            )

        # 去重区
        if self.negative_examples:
            sections.append(
                "\n## ⚠ 以下题目已存在于题库中，你的新题必须与它们有实质性区别\n"
            )
            for i, q in enumerate(self.negative_examples, 1):
                sections.append(f"### 已有题 {i}\n{q.content_latex}\n")

        return "\n".join(sections)
```

---

## 3. Stage 2: 题目生成 — Prompt 工程核心

### 3.1 生成模式分类

系统支持两大模式、八种策略：

```
生成模式
├── A. 变式模式 (Variant Mode) — 有源题
│   ├── A1. parameter_shift   参数替换: 改数字/函数，保持结构
│   ├── A2. method_shift      方法迁移: 同一知识点，不同解法入口
│   ├── A3. type_shift        题型转换: 计算→证明、求解→判断
│   ├── A4. escalate          升阶: 增加条件复杂度或推广
│   ├── A5. simplify          降阶: 简化条件，给出特例
│   └── A6. reverse           逆向: 已知结论求条件
│
└── B. 自由模式 (Free Mode) — 无源题
    ├── B1. topic_generation  专题生成: 指定专题+难度，自由创作
    └── B2. cross_topic       交叉生成: 融合多个专题的综合题
```

### 3.2 Prompt 模板 — 变式模式

```python
VARIANT_PROMPT = """
你是一位资深大学数学教育专家，专精{subject}课程的题目设计。
你需要基于给定的源题，按照指定的变式策略生成一道新题。

# 源题
{source_question_latex}

# 变式策略: {strategy_name}
{strategy_instruction}

# 目标难度: {target_difficulty}/10
{difficulty_calibration_instruction}

# 目标题型: {question_type}

{context_bundle}

# 生成要求

1. **数学严谨性**: 题目条件必须充分且不矛盾。如果是证明题，结论必须为真且可证。
   如果是计算题，必须有确定的答案。
2. **LaTeX 规范**: 使用标准 LaTeX 数学环境。定义用 \\textbf{{定义}}，
   定理条件用 "设..." 或 "Let..."。
3. **难度匹配**: 参考上下文中的参考题难度分布，确保新题的复杂度与目标难度一致。
4. **原创性**: 新题必须与"已有题"列表中的所有题目有实质区别——
   不仅仅是改了数字，而是在结构、考查角度或所需方法上有所不同。
5. **可解性**: 你自己必须能完整解出这道题。如果你不确定题目是否正确，不要输出。

# 输出格式

请严格按以下 XML 格式输出:

<question>
<content>
[LaTeX 格式的完整题干]
</content>
<brief_solution_sketch>
[2-3 句话的解题思路概要，用于快速验证可解性]
</brief_solution_sketch>
<knowledge_points_used>
[这道题考查/用到了哪些知识点，逗号分隔]
</knowledge_points_used>
<estimated_difficulty>
[你对这道题难度的自评，1-10 的整数]
</estimated_difficulty>
</question>
"""
```

### 3.3 八种策略的具体 Instruction

```python
STRATEGY_INSTRUCTIONS = {

    "A1_parameter_shift": """
## 策略: 参数替换 (Parameter Shift)

保持源题的数学结构和解题方法不变，替换其中的:
- 具体数值 (如将 1/n 换成 1/n², 将 e^x 换成 sin x)
- 具体函数 (保持函数类别，如连续→连续，单调→单调)
- 空间维度 (如 ℝ² → ℝ³)
- 具体群/环/空间实例 (如 ℤ/nℤ → S_n)

⚠ 替换后必须验证:
- 新参数下题目仍然有意义
- 解题方法本质上相同
- 答案可以计算出来（不会导致发散或无解）

这种变式的难度应与源题相近 (±1)。
""",

    "A2_method_shift": """
## 策略: 方法迁移 (Method Shift)

围绕源题考查的**同一个核心知识点**，设计一道需要**不同解法入口**的新题。

例如:
- 源题用 ε-δ 语言证明极限 → 新题用夹逼定理/单调有界
- 源题用行列式计算 → 新题用秩的方法
- 源题用 Lagrange 定理 → 新题用 Sylow 定理

关键: 同一知识域，不同的技术路径。这要求你理解这个知识点有哪些不同的处理方式。
""",

    "A3_type_shift": """
## 策略: 题型转换 (Type Shift)

将源题的题型做本质转换:
- 计算题 → 证明题: "计算 lim f(x)" → "证明 f(x) 的极限存在"
- 求解题 → 判断题: "求特征值" → "判断矩阵是否可对角化，并证明"
- 证明题 → 构造题: "证明存在..." → "构造一个满足...的具体例子"
- 正向 → 反例题: "若条件 A 则结论 B" → "构造满足 A 但不满足 B' 的反例"

题型转换通常会改变难度。请根据目标难度调整条件的复杂度。
""",

    "A4_escalate": """
## 策略: 升阶 (Escalate)

在源题基础上**增加数学深度**:
- 从特殊到一般: ℝⁿ → 一般赋范空间, 有限群 → 一般群
- 增加条件层数: 单条件 → 多条件组合
- 增加嵌套: 单层极限 → 双层极限/重积分
- 削弱假设: 去掉一个"好"条件 (如连续性、有界性)，看还能得到什么
- 要求更强结论: 点态收敛 → 一致收敛

新题难度应比源题高 2-3 级。确保升阶后的题目仍然可解。
""",

    "A5_simplify": """
## 策略: 降阶 (Simplify)

将源题**降低抽象层次或减少步骤**:
- 从一般到特殊: 一般度量空间 → ℝⁿ 的具体情形
- 给出更多条件: 添加连续性、单调性等辅助条件
- 拆解为子步骤: 将一个大证明拆成有引导的 (a)(b)(c) 小问
- 限定范围: "对所有 f ∈ C[0,1]" → "对 f(x) = x²"

新题难度应比源题低 2-3 级。降阶的艺术在于：简化后仍然保留核心知识点的考查。
""",

    "A6_reverse": """
## 策略: 逆向 (Reverse)

将源题的**因果/已知-未知关系反转**:
- 正向计算 → 反向求参: "求 f(2)" → "已知 f(a)=0，求 a"
- 存在性 → 唯一性: "证明解存在" → "证明解唯一"
- 充分条件 → 必要条件: "A ⟹ B，求 B ⟹ ? 的最强结论"
- 给结论求条件: "在什么条件下，以下结论成立？"

逆向题通常更有挑战性。仔细验证逆向后的问题是否有良好定义。
""",

    "B1_topic_generation": """
## 策略: 专题自由生成 (Topic Generation)

围绕指定专题，自由设计一道原创题目。

设计原则:
1. 首先确定要考查的**核心知识点** (从上下文知识点中选取)
2. 确定**解题所需的关键方法/技巧**
3. 设计题目使得: 不掌握该知识点→无法下手，掌握→有清晰路径
4. 参考上下文中的参考题，了解这个难度级别的题目"长什么样"
5. 避免出过于技巧性的题——好题考查理解深度，而非解题套路的记忆

可以从以下角度寻找灵感:
- 两个定理的交汇处 (需要同时用到两个定理)
- 一个定理的边界情况 (条件刚好不满足时的反例)
- 把抽象定理实例化到具体对象上
""",

    "B2_cross_topic": """
## 策略: 交叉综合 (Cross-Topic)

设计一道**融合多个专题**的综合题。

设计原则:
1. 从上下文知识点中选取 2-3 个来自不同专题的知识点
2. 设计一个需要将这些知识点串联起来的问题
3. 解题路径应该是自然的——不是人为拼凑，而是数学上确实存在联系

经典的交叉模式:
- 分析 + 代数: 用线性代数方法处理微分方程
- 拓扑 + 代数: 用群论证明拓扑不变量
- 极限 + 级数: 先求极限再讨论级数收敛
- 线性空间 + 矩阵: 抽象理论 + 具体计算结合

综合题的难度通常较高 (≥6)。确保题目对学生来说虽然有挑战，但解题路径是可发现的。
"""
}
```

### 3.4 难度校准指令

```python
DIFFICULTY_CALIBRATION = """
## 难度校准参考

你的目标难度是 {target}/10。以下是校准锚点:

**难度 1-2 (基础概念)**:
直接套用一个定义或公式，一步即得。
例: 求 $\\lim_{{n\\to\\infty}} \\frac{{1}}{{n}} = ?$

**难度 3-4 (基本技巧)**:
需要 1-2 步标准技巧。学过的学生应该能做出来。
例: 用 $\\varepsilon$-$N$ 语言证明 $\\lim_{{n\\to\\infty}} \\frac{{n}}{{n+1}} = 1$。

**难度 5-6 (综合应用)**:
需要组合 2-3 个知识点，或对定理有较深理解。中等学生需要思考。
例: 设 $f$ 在 $[a,b]$ 上连续，在 $(a,b)$ 上可微，且 $f(a)=f(b)=0$。
证明存在 $c \\in (a,b)$ 使得 $f'(c) = f(c)$。

**难度 7-8 (深度理解)**:
需要较深的洞察、巧妙的辅助构造，或多知识点的非平凡组合。
好学生需要认真思考 15-30 分钟。
例: 设 $f: \\mathbb{{R}} \\to \\mathbb{{R}}$ 满足 $f(x+y) = f(x)f(y)$，
且 $f$ 在 $x=0$ 处连续。证明 $f$ 在 $\\mathbb{{R}}$ 上连续。

**难度 9-10 (挑战/竞赛)**:
需要创造性方法、深刻的数学直觉，或多领域综合。
研究生入学考试或数学竞赛水平。

请参考上下文中的参考题目来校准你的难度感觉。上下文中标注了每道参考题的难度，
用它们作为标尺来确保你的新题与目标难度匹配。
"""
```

### 3.5 自由模式 Prompt

```python
FREE_GENERATION_PROMPT = """
你是一位资深大学数学教育专家，专精{subject}课程的题目设计。
你需要围绕指定的专题和难度，设计一道原创数学题。

# 任务目标
- 科目: {subject}
- 专题: {topics}
- 目标难度: {target_difficulty}/10
- 题型: {question_type}

# 生成策略: {strategy_name}
{strategy_instruction}

{difficulty_calibration_instruction}

{context_bundle}

# 核心设计约束

1. **知识点锚定**: 你的题目必须明确考查上下文知识点中的至少 1-2 个。
   在输出中注明具体用了哪些。
2. **方法可达**: 对于目标难度级别的学生，解题路径应该是可发现的——
   不需要灵光一现，而是通过系统的数学思考可以到达。
3. **条件最小化**: 不要堆砌无用条件。每个条件都应在解题过程中被用到。
4. **表述精确**: 用标准数学语言表述。避免模糊的"显然"或"容易看出"。

# 输出格式

请严格按以下 XML 格式输出:

<question>
<content>
[LaTeX 格式的完整题干]
</content>
<brief_solution_sketch>
[2-3 句话的解题思路概要]
</brief_solution_sketch>
<knowledge_points_used>
[考查的知识点，逗号分隔]
</knowledge_points_used>
<methods_required>
[解题需要的方法/技巧，逗号分隔]
</methods_required>
<estimated_difficulty>
[1-10]
</estimated_difficulty>
<design_rationale>
[简述为什么这道题是好的——它考查了什么理解、避免了什么陷阱]
</design_rationale>
</question>
"""
```

---

## 4. Stage 3 & 4: 求解与验证闭环

### 4.1 解题引擎

```python
class SolveEngine:
    """
    对 AI 生成的候选题进行独立求解。
    这是质量控制的关键环节——系统自己解不出来的题不能给学生。
    """

    SOLVE_PROMPT = """
你是一位严谨的数学家。请对以下数学题给出完整、详细的解答。

# 题目
{question_latex}

# 可参考的知识点
{relevant_knowledge_points}

# 解答要求

1. **逐步推导**: 每一步都要有明确的理由。引用具体的定理/定义名称。
2. **关键步骤标注**: 在非显然的关键步骤前标注 [关键步骤]。
3. **格式规范**:
   - 证明题: 使用 "证明:" 开头，"$\\square$" 结尾
   - 计算题: 显示完整计算过程，最终答案用 $\\boxed{{}}$ 框出
4. **自检**: 解答结束后，检查:
   - 是否用到了题目所有条件？(未用到的条件可能意味着你的解法有问题)
   - 结论是否与题目要求一致？
   - 对于计算题，能否代入验证？

# 输出格式

<solution>
<steps>
[完整的分步骤解答，LaTeX 格式]
</steps>
<methods_used>
[实际用到的定理/方法，逗号分隔]
</methods_used>
<self_check>
[自检结果: 是否所有条件都用到了？结论是否正确？]
</self_check>
<confidence>
[对解答正确性的置信度: high / medium / low]
</confidence>
</solution>
"""

    async def solve(self, question: Question, context: ContextBundle) -> SolutionResult:
        # 使用 temperature=0 确保确定性
        response = await self.llm.generate(
            prompt=self.SOLVE_PROMPT.format(
                question_latex=question.content_latex,
                relevant_knowledge_points=self._format_kps(context.knowledge_points),
            ),
            temperature=0,
            model="claude-opus-4-6",  # 复杂推理用最强模型
        )

        result = self._parse_solution(response)

        # 如果置信度不是 high，用第二次独立求解交叉验证
        if result.confidence != "high":
            result2 = await self._second_solve(question, context)
            result.cross_validated = self._compare_solutions(result, result2)

        return result
```

### 4.2 验证管线

```python
class VerificationPipeline:
    """
    多层验证，确保生成题目的质量。
    """

    async def verify(
        self,
        candidate: Question,
        solution: SolutionResult,
        context: ContextBundle,
    ) -> VerificationResult:

        checks = []

        # ── Check 1: LaTeX 可编译 ──
        latex_ok = await self._check_latex_compilable(candidate.content_latex)
        checks.append(("latex_syntax", latex_ok))

        # ── Check 2: 数学正确性 (独立审核) ──
        math_ok = await self._check_math_correctness(candidate, solution)
        checks.append(("math_correctness", math_ok))

        # ── Check 3: 去重 (与已有题库的相似度) ──
        dedup_ok = await self._check_deduplication(candidate)
        checks.append(("deduplication", dedup_ok))

        # ── Check 4: 条件完备性 ──
        condition_ok = await self._check_conditions(candidate, solution)
        checks.append(("conditions", condition_ok))

        # ── Check 5: 难度一致性 ──
        difficulty_ok = await self._check_difficulty_match(candidate, solution)
        checks.append(("difficulty", difficulty_ok))

        passed = all(ok for _, ok in checks)
        return VerificationResult(passed=passed, checks=checks)

    async def _check_math_correctness(self, question, solution) -> bool:
        """
        独立数学审核。用一个独立的 LLM 实例审查。
        注意: 审核 LLM 不会看到生成 LLM 的解答过程，而是自己独立判断。
        """
        REVIEW_PROMPT = """
你是一位数学审稿人。请审核以下数学题目是否在数学上是正确的、良好定义的。

# 题目
{question_latex}

# 请检查:
1. 题目条件是否自洽？ (有无矛盾)
2. 如果是证明题：结论在给定条件下是否为真？
3. 如果是计算题：是否存在确定的答案？
4. 条件是否充分？ (有无遗漏必要条件)
5. 是否存在平凡的退化情况使得题目无意义？

# 输出

<review>
<verdict>PASS 或 FAIL</verdict>
<issues>[如果 FAIL，列出具体问题]</issues>
<suggestions>[如果有小问题，给出修改建议]</suggestions>
</review>
"""
        response = await self.review_llm.generate(
            prompt=REVIEW_PROMPT.format(question_latex=question.content_latex),
            temperature=0,
            model="claude-sonnet-4-20250514",  # 独立实例
        )
        review = self._parse_review(response)
        return review.verdict == "PASS"

    async def _check_deduplication(self, question) -> bool:
        """
        检查新题与已有题库的相似度。
        阈值: cosine similarity > 0.92 → 判定为重复。

        注意: 纯 embedding 相似度可能会误判 (如数字不同但结构相同)，
        所以当 similarity 在 [0.85, 0.92] 区间时，额外用 LLM 做精细判断。
        """
        embedding = await self.embedder.embed(question.content_plain)

        # Top-1 最相似题
        nearest = await self.vector_store.search(embedding, top_k=1)

        if not nearest:
            return True

        sim = nearest[0].similarity

        if sim > 0.92:
            return False  # 明确重复
        elif sim > 0.85:
            # 灰区: LLM 精细判断
            return await self._llm_dedup_check(question, nearest[0].question)
        else:
            return True  # 明确不同

    async def _check_conditions(self, question, solution) -> bool:
        """
        检查条件完备性:
        1. 解答中是否用到了题目的所有条件? (有未用条件 → 题目可能有多余条件)
        2. 解答中是否引入了题目未给的隐含假设?
        """
        CHECK_PROMPT = """
对比以下题目和解答:

题目: {question}
解答: {solution}

请检查:
1. 题目中的每个条件是否都在解答中被用到了？列出未使用的条件。
2. 解答中是否使用了题目未明确给出的条件？列出隐含假设。

如果所有条件都被使用且无隐含假设，输出 PASS。否则输出 WARN 并说明。
"""
        # ...
        return result == "PASS"
```

### 4.3 生成-验证循环

```python
class GenerationLoop:
    """
    完整的生成-验证循环。最多 3 轮，每轮生成 3 个候选。
    """

    MAX_ROUNDS = 3
    CANDIDATES_PER_ROUND = 3

    async def generate_verified_question(
        self,
        request: GenerationRequest,
    ) -> GeneratedQuestion | None:

        context = await self.retriever.build_context(request)

        for round_num in range(self.MAX_ROUNDS):
            # 生成候选
            candidates = await self.generator.generate(
                request=request,
                context=context,
                n=self.CANDIDATES_PER_ROUND,
                temperature=0.7 + round_num * 0.1,  # 每轮略提高温度以增加多样性
            )

            for candidate in candidates:
                # 求解
                solution = await self.solver.solve(candidate, context)
                if solution.confidence == "low":
                    continue  # 解不出来，跳过

                # 验证
                verification = await self.verifier.verify(candidate, solution, context)

                if verification.passed:
                    # 标注
                    annotated = await self.annotator.annotate(candidate, solution)
                    return GeneratedQuestion(
                        question=annotated,
                        solution=solution,
                        verification=verification,
                        generation_meta={
                            "round": round_num + 1,
                            "model": self.generator.model,
                            "strategy": request.strategy,
                            "context_kp_ids": [kp.id for kp in context.knowledge_points],
                        }
                    )

                # 未通过：如果有具体修改建议，将其加入下一轮的 context
                if verification.has_suggestions:
                    context.add_feedback(verification.suggestions)

        # 3 轮都未能生成合格题目
        return None
```

---

## 5. Stage 5: 多维难度评分系统

不使用粗暴的单一分数，而是**10 维特征向量 → 加权综合分**。

### 5.1 难度特征维度

```python
DIFFICULTY_DIMENSIONS = {
    # 维度名称              权重   描述
    "concept_count":       0.15,  # 涉及的独立知识点数量 (1→低, 4+→高)
    "step_count":          0.15,  # 解题最短路径步数 (1-2→低, 8+→高)
    "abstraction_level":   0.12,  # 抽象程度 (具体数值→低, 一般空间→高)
    "technique_novelty":   0.12,  # 技巧非标准程度 (标准流程→低, 需要巧妙构造→高)
    "condition_parsing":   0.10,  # 条件解读难度 (直接条件→低, 隐含/需转化→高)
    "computation_load":    0.08,  # 计算量 (简单→低, 繁重→高)
    "proof_structure":     0.10,  # 证明结构复杂度 (直接→低, 需分情况/反证/嵌套→高)
    "prerequisite_depth":  0.08,  # 前置知识深度 (基本定义→低, 高级定理→高)
    "multi_representation":0.05,  # 是否需要多种表示切换 (纯代数→低, 代数+几何+分析→高)
    "metacognition":       0.05,  # 元认知需求 (直接做→低, 需要策略选择→高)
}
```

### 5.2 评分 Prompt

```python
DIFFICULTY_SCORING_PROMPT = """
你是数学教育评估专家。请对以下题目进行多维难度评分。

# 题目
{question_latex}

# 解答概要 (供参考)
{solution_sketch}

# 评分维度

对每个维度给出 1-10 的整数分:

1. concept_count: 涉及多少个独立知识点?
   1-2=基础(1-3分), 3=中等(4-6分), 4+=高级(7-10分)

2. step_count: 解题最短路径需要多少个非平凡步骤?
   1-2步(1-3分), 3-4步(4-6分), 5-7步(7-8分), 8+步(9-10分)

3. abstraction_level: 抽象程度
   具体数值/函数(1-3), 一般实数/矩阵(4-6), 一般空间/群(7-9), 范畴层面(10)

4. technique_novelty: 解法非标准程度
   标准流程(1-3), 需要关键 insight(4-6), 需要巧妙构造(7-9), 原创方法(10)

5. condition_parsing: 题目条件的解读难度
   条件直接明了(1-3), 需要转化/等价改写(4-6), 隐含条件需发掘(7-9), 条件极度隐晦(10)

6. computation_load: 纯计算量
   几乎无(1-2), 适中(3-5), 繁重(6-8), 极繁重(9-10)

7. proof_structure: 证明/推理结构复杂度
   直接推导(1-3), 需分情况(4-5), 反证/归纳(6-7), 多层嵌套(8-10)

8. prerequisite_depth: 需要多深的前置知识
   基本定义(1-3), 核心定理(4-6), 高级理论(7-9), 研究前沿(10)

9. multi_representation: 是否需要多种数学语言/表示切换
   单一表示(1-3), 两种(4-6), 三种+(7-10)

10. metacognition: 解题策略选择的难度
    路径唯一明确(1-3), 需比较2-3条路径(4-6), 策略选择关键(7-10)

# 输出格式 (严格 JSON)

{{
  "scores": {{
    "concept_count": <int>,
    "step_count": <int>,
    "abstraction_level": <int>,
    "technique_novelty": <int>,
    "condition_parsing": <int>,
    "computation_load": <int>,
    "proof_structure": <int>,
    "prerequisite_depth": <int>,
    "multi_representation": <int>,
    "metacognition": <int>
  }},
  "overall_reasoning": "<一句话总结为什么是这个难度>",
  "comparable_to": "<这道题的难度类似于哪类经典题目>"
}}
"""
```

### 5.3 综合分计算

```python
def compute_difficulty_score(dimension_scores: dict[str, int]) -> float:
    """
    加权综合分。输出 [1.0, 10.0] 的浮点数。

    额外校准：与已有题库的分布做比对，避免系统性偏高/偏低。
    """
    raw_score = sum(
        dimension_scores[dim] * weight
        for dim, weight in DIFFICULTY_DIMENSIONS.items()
    )

    # 校准: 如果系统整体偏高，可以用一个线性映射修正
    # calibrated = alpha * raw_score + beta
    # alpha, beta 从已有题库的 AI 评分 vs 人工评分 的线性回归得到
    calibrated = raw_score  # 初始阶段不校准，积累数据后启用

    return round(calibrated, 1)
```

---

## 6. 批量生成与组卷策略

### 6.1 批量生成控制器

```python
class BatchGenerator:
    """
    批量生成题目，满足指定的难度分布和知识点覆盖要求。
    """

    async def generate_batch(
        self,
        subject: str,
        topics: list[str],
        total_count: int,
        difficulty_distribution: dict[int, int],  # {3: 2, 5: 4, 7: 3, 9: 1}
        question_types: list[str],
        user_exclusions: set[str] = None,  # 用户已做过的题 ID
    ) -> BatchResult:

        generated = []
        all_generated_embeddings = []  # 用于批次内去重

        for difficulty, count in sorted(difficulty_distribution.items()):
            for i in range(count):
                # 动态选择策略
                strategy = self._select_strategy(
                    difficulty=difficulty,
                    types=question_types,
                    already_generated=generated,
                )

                request = GenerationRequest(
                    subject=subject,
                    topics=topics,
                    difficulty_target=difficulty,
                    question_type=self._pick_type(question_types, generated),
                    strategy=strategy,
                    exclusion_ids=user_exclusions,
                    # 批次内去重: 已生成题目也作为负样本
                    batch_negatives=all_generated_embeddings,
                )

                result = await self.loop.generate_verified_question(request)

                if result:
                    generated.append(result)
                    all_generated_embeddings.append(result.question.embedding)

        return BatchResult(
            questions=generated,
            coverage=self._compute_coverage(generated, topics),
            difficulty_actual=self._compute_difficulty_distribution(generated),
        )

    def _select_strategy(self, difficulty, types, already_generated):
        """
        动态策略选择:
        - 低难度 (1-4): 偏向 B1_topic_generation, A1_parameter_shift
        - 中等 (5-7): 偏向 A2_method_shift, A3_type_shift, B1
        - 高难度 (8-10): 偏向 A4_escalate, B2_cross_topic, A6_reverse

        另外考虑多样性: 如果已生成了 3 道同策略的题，强制切换。
        """
        ...

    def _compute_coverage(self, generated, target_topics):
        """
        知识点覆盖率分析:
        - 哪些 topics 有题目覆盖
        - 每个 topic 覆盖了多少道
        - 有无明显遗漏
        """
        ...
```

### 6.2 智能组卷

```python
class ExamAssembler:
    """
    智能组卷: 不只是随机抽题，而是考虑多重约束的优化问题。

    约束:
    1. 难度分布匹配目标 (如: 容易30%, 中等40%, 困难30%)
    2. 知识点覆盖尽可能广
    3. 题型分布匹配目标
    4. 无重复知识点考查 (同一知识点最多出现在 2 道题中)
    5. 总分 = 指定分数
    6. 预估完成时间 ≈ 指定时间

    方法: 贪心 + 局部搜索
    """

    async def assemble(self, params: ExamParams) -> list[ScoredQuestion]:

        # Step 1: 候选池 — 从题库+新生成题中筛选
        pool = await self._build_candidate_pool(params)
        # pool 可能有 50-200 道题

        # Step 2: 贪心初始解
        selected = self._greedy_select(pool, params)

        # Step 3: 局部搜索优化 (swap-based)
        optimized = self._local_search(selected, pool, params, max_iter=100)

        # Step 4: 如果覆盖不足，动态生成补充题
        gaps = self._find_coverage_gaps(optimized, params)
        if gaps:
            fillers = await self._generate_fillers(gaps)
            optimized = self._integrate_fillers(optimized, fillers, params)

        # Step 5: 排序 (由易到难) + 分配分数
        final = self._order_and_score(optimized, params.total_score)

        return final

    def _greedy_select(self, pool, params) -> list[Question]:
        """
        贪心选择:
        1. 按难度桶分组
        2. 每个桶中，优先选择覆盖未覆盖知识点的题
        3. 同知识点的题不超过 2 道
        """
        ...

    def _local_search(self, current, pool, params, max_iter):
        """
        局部搜索:
        每轮随机选一道已选题，尝试替换为池中另一道
        如果替换后的得分更高 (覆盖+难度匹配+题型匹配)，接受替换
        """
        ...

    def _score_selection(self, selection, params) -> float:
        """
        评分函数:
        score = w1 * difficulty_match      (难度分布与目标的KL散度，越低越好)
              + w2 * coverage_ratio         (知识点覆盖率，越高越好)
              + w3 * type_match             (题型分布与目标的匹配度)
              + w4 * diversity_bonus        (题目间的多样性，embedding平均距离)
        """
        ...
```

---

## 7. 实现优先级与里程碑

### Milestone 1: 最小可行生成器 (2 周)

```
目标: 能用 CLI 生成一道变式题并得到解答

需要实现:
├── 基础数据库 (questions + knowledge_points + tags 三张表)
├── 手动导入 20-30 道种子题 (LaTeX) + 10-20 条知识点
├── Embedding 生成 + pgvector 检索
├── ContextRetriever (简化版: 只做向量检索)
├── 单策略生成 (A1_parameter_shift)
├── SolveEngine (基础版)
├── CLI: mathforge generate variant <id>

验收标准:
- 输入一道数学分析极限题，输出一道合理的变式 + 解答
- 解答数学上正确
```

### Milestone 2: 完整生成-验证循环 (2 周)

```
新增:
├── VerificationPipeline (全部 5 项检查)
├── GenerationLoop (3 轮重试)
├── 全部 8 种策略的 Prompt
├── 多维难度评分
├── CLI: mathforge generate batch

验收标准:
- 批量生成 10 道题，>80% 通过人工审核
- 难度评分与人工评分误差 < 2
```

### Milestone 3: 知识库管线 (2 周)

```
新增:
├── PDF → Markdown (Marker)
├── 语义切分器
├── KB Agent (知识点提取+标签)
├── CLI: mathforge kb import

验收标准:
- 导入一本 Rudin 的 Chapter 2
- 自动提取 20+ 条知识点，标签准确率 > 85%
```

### Milestone 4: 组卷与文档 (2 周)

```
新增:
├── LaTeX 模板系统
├── ExamAssembler (智能组卷)
├── DocAssemblyAgent
├── CLI: mathforge doc exam / handout

验收标准:
- 自动组一份 10 题的数学分析期中考试 PDF
- 难度分布合理，知识点覆盖面广
```

---

## 8. 质量度量与持续改进

### 8.1 核心指标

| 指标 | 定义 | 目标 |
|------|------|------|
| **生成成功率** | 通过验证的题目 / 总候选题目 | > 50% |
| **人工通过率** | 人工审核通过 / 生成总数 | > 80% |
| **数学正确率** | 数学上无误的 / 生成总数 | > 95% |
| **难度校准误差** | \|AI评分 - 人工评分\| 的平均值 | < 1.5 |
| **去重有效性** | 与已有题 cosine > 0.92 的比例 | < 5% |
| **解答正确率** | 解答无误 / 解答总数 | > 90% |

### 8.2 反馈循环

```
用户做题 → 标记正确/错误 → 更新题目质量分
                          → 如果多人标记错误 → 触发人工审核
                          → 审核结果反馈到 Prompt 改进

教师审核 → 直接标记题目质量 → 更新难度评分校准模型
                            → 标记典型好题/坏题 → 加入 few-shot 示例
```

---

## 附录 A: 种子标签体系

```json
{
  "数学分析": {
    "topics": [
      "实数理论与完备性",
      "数列极限", "函数极限", "极限综合",
      "连续函数", "一致连续",
      "微分学: 导数与微分", "微分学: 中值定理", "微分学: Taylor展开",
      "Riemann积分", "广义积分(反常积分)",
      "数项级数", "函数项级数", "幂级数", "Fourier级数",
      "多元微分学", "多元积分学(重积分)",
      "曲线积分与曲面积分",
      "隐函数定理与逆映射定理",
      "Lebesgue测度与积分(选)"
    ],
    "methods": [
      "ε-δ语言", "ε-N语言",
      "夹逼定理", "单调有界原理", "Cauchy收敛准则",
      "L'Hôpital法则", "Taylor展开法",
      "反证法", "数学归纳法", "构造法",
      "Weierstrass判别法", "Abel判别法", "Dirichlet判别法",
      "换元法", "分部积分", "参数积分",
      "压缩映射原理"
    ]
  },
  "高等代数": {
    "topics": [
      "行列式", "矩阵运算", "线性方程组",
      "线性空间(向量空间)", "基与维数", "子空间",
      "线性映射(线性变换)", "核与像",
      "内积空间", "正交化与投影",
      "特征值与特征向量", "对角化",
      "Jordan标准形", "有理标准形",
      "二次型", "正定性",
      "张量积(选)", "对偶空间"
    ],
    "methods": [
      "初等变换", "矩阵分块",
      "秩的方法", "行列式方法",
      "维数公式", "基的选取",
      "特征多项式", "最小多项式",
      "Cayley-Hamilton定理",
      "Gram-Schmidt正交化",
      "Schur三角化", "谱定理"
    ]
  },
  "群论": {
    "topics": [
      "群的基本概念与例子",
      "子群", "生成子群",
      "群同态与同构",
      "正规子群与商群",
      "群作用与轨道-稳定子定理",
      "Sylow定理",
      "直积与半直积",
      "交换群结构定理",
      "可解群与幂零群",
      "自由群与展示(选)",
      "群的扩张(选)"
    ],
    "methods": [
      "Lagrange定理", "Cauchy定理",
      "Sylow定理(三个)",
      "轨道计数(Burnside引理)",
      "同构定理(三个)",
      "正规化子与中心化子",
      "类方程",
      "群作用的应用",
      "半直积构造"
    ]
  }
}
```

---

*技术规格版本: v1.0 | 2026-03-30*
