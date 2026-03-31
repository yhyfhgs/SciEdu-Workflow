"""
MathForge 种子数据 — 用于 prototype 测试

包含:
- 数学分析: 10 条知识点 + 8 道种子题
- 高等代数: 5 条知识点 + 4 道种子题
- 群论: 5 条知识点 + 4 道种子题
"""
from core.models import KnowledgePoint, KPType, Question, QuestionType, Subject


# ══════════════════════════════════════════════════════
#  数学分析 — 知识点
# ══════════════════════════════════════════════════════

ANALYSIS_KPS = [
    KnowledgePoint(
        id="kp-a01",
        title="数列极限的 ε-N 定义",
        content_latex=r"""
\textbf{定义.} 设 $\{a_n\}$ 为实数列，$a \in \mathbb{R}$。
称 $\lim_{n\to\infty} a_n = a$，若对任意 $\varepsilon > 0$，
存在正整数 $N$，使得当 $n > N$ 时，$|a_n - a| < \varepsilon$。
""",
        kp_type=KPType.DEFINITION,
        difficulty=2,
        tags=["数列极限", "极限"],
        source_book="Ruerta, Mathematical Analysis",
        source_chapter="Chapter 2",
    ),
    KnowledgePoint(
        id="kp-a02",
        title="极限的唯一性",
        content_latex=r"""
\textbf{定理.} 若数列 $\{a_n\}$ 收敛，则其极限唯一。

\textit{证明.} 设 $\lim a_n = a$ 且 $\lim a_n = b$，$a \neq b$。
取 $\varepsilon = |a-b|/2 > 0$，则存在 $N_1, N_2$ 使得 $n > N_1$ 时
$|a_n - a| < \varepsilon$，$n > N_2$ 时 $|a_n - b| < \varepsilon$。
取 $n > \max(N_1, N_2)$，由三角不等式
$|a - b| \leq |a - a_n| + |a_n - b| < 2\varepsilon = |a - b|$，矛盾。$\square$
""",
        kp_type=KPType.THEOREM,
        difficulty=3,
        tags=["数列极限", "极限"],
    ),
    KnowledgePoint(
        id="kp-a03",
        title="夹逼定理 (Squeeze Theorem)",
        content_latex=r"""
\textbf{定理.} 设 $\{a_n\}, \{b_n\}, \{c_n\}$ 为实数列，
且存在 $N_0$ 使得当 $n > N_0$ 时 $a_n \leq b_n \leq c_n$。
若 $\lim a_n = \lim c_n = L$，则 $\lim b_n = L$。
""",
        kp_type=KPType.THEOREM,
        difficulty=3,
        tags=["数列极限", "极限"],
    ),
    KnowledgePoint(
        id="kp-a04",
        title="单调有界原理",
        content_latex=r"""
\textbf{定理.} 单调有界数列必收敛。
即：若 $\{a_n\}$ 单调递增且有上界，则 $\{a_n\}$ 收敛。
""",
        kp_type=KPType.THEOREM,
        difficulty=4,
        tags=["数列极限", "极限", "实数完备性"],
    ),
    KnowledgePoint(
        id="kp-a05",
        title="函数连续的 ε-δ 定义",
        content_latex=r"""
\textbf{定义.} 设 $f: D \to \mathbb{R}$，$x_0 \in D$。
称 $f$ 在 $x_0$ 处连续，若对任意 $\varepsilon > 0$，存在 $\delta > 0$，
使得对所有 $x \in D$，$|x - x_0| < \delta$ 时 $|f(x) - f(x_0)| < \varepsilon$。
""",
        kp_type=KPType.DEFINITION,
        difficulty=3,
        tags=["连续函数", "函数极限"],
    ),
    KnowledgePoint(
        id="kp-a06",
        title="一致连续的定义",
        content_latex=r"""
\textbf{定义.} 设 $f: D \to \mathbb{R}$。称 $f$ 在 $D$ 上一致连续，
若对任意 $\varepsilon > 0$，存在 $\delta > 0$，使得对所有 $x, y \in D$，
$|x - y| < \delta$ 时 $|f(x) - f(y)| < \varepsilon$。

\textbf{注.} 与点态连续的区别: $\delta$ 不依赖于具体的点，只依赖于 $\varepsilon$。
""",
        kp_type=KPType.DEFINITION,
        difficulty=5,
        tags=["一致连续", "连续函数"],
    ),
    KnowledgePoint(
        id="kp-a07",
        title="Cantor 定理 (闭区间上连续函数一致连续)",
        content_latex=r"""
\textbf{定理 (Cantor).} 若 $f$ 在闭区间 $[a,b]$ 上连续，则 $f$ 在 $[a,b]$ 上一致连续。
""",
        kp_type=KPType.THEOREM,
        difficulty=5,
        tags=["一致连续", "连续函数"],
    ),
    KnowledgePoint(
        id="kp-a08",
        title="介值定理",
        content_latex=r"""
\textbf{定理.} 若 $f$ 在 $[a,b]$ 上连续，且 $f(a) \neq f(b)$，
则对 $f(a)$ 与 $f(b)$ 之间的任意 $c$，
存在 $\xi \in (a,b)$ 使得 $f(\xi) = c$。
""",
        kp_type=KPType.THEOREM,
        difficulty=4,
        tags=["连续函数", "介值定理"],
    ),
    KnowledgePoint(
        id="kp-a09",
        title="Rolle 中值定理",
        content_latex=r"""
\textbf{定理 (Rolle).} 设 $f$ 在 $[a,b]$ 上连续，在 $(a,b)$ 上可微，
且 $f(a) = f(b)$。则存在 $c \in (a,b)$ 使得 $f'(c) = 0$。
""",
        kp_type=KPType.THEOREM,
        difficulty=4,
        tags=["微分学", "中值定理"],
    ),
    KnowledgePoint(
        id="kp-a10",
        title="Lagrange 中值定理",
        content_latex=r"""
\textbf{定理 (Lagrange).} 设 $f$ 在 $[a,b]$ 上连续，在 $(a,b)$ 上可微。
则存在 $c \in (a,b)$ 使得 $f'(c) = \frac{f(b) - f(a)}{b - a}$。
""",
        kp_type=KPType.THEOREM,
        difficulty=4,
        tags=["微分学", "中值定理"],
    ),
]


# ══════════════════════════════════════════════════════
#  数学分析 — 种子题目
# ══════════════════════════════════════════════════════

ANALYSIS_QUESTIONS = [
    Question(
        id="q-a01",
        content_latex=r"""
用 $\varepsilon$-$N$ 语言证明: $\lim_{n\to\infty} \frac{n}{n+1} = 1$。
""",
        difficulty=3,
        question_type=QuestionType.PROOF,
        subject=Subject.ANALYSIS,
        tags=["数列极限", "ε-N语言"],
        source_book="Rudin, Ch.3",
    ),
    Question(
        id="q-a02",
        content_latex=r"""
用 $\varepsilon$-$N$ 语言证明: $\lim_{n\to\infty} \frac{n^2 + 1}{2n^2 - 3} = \frac{1}{2}$。
""",
        difficulty=4,
        question_type=QuestionType.PROOF,
        subject=Subject.ANALYSIS,
        tags=["数列极限", "ε-N语言"],
    ),
    Question(
        id="q-a03",
        content_latex=r"""
设 $a_1 = \sqrt{2}$，$a_{n+1} = \sqrt{2 + a_n}$（$n \geq 1$）。
证明 $\{a_n\}$ 收敛，并求其极限。
""",
        difficulty=5,
        question_type=QuestionType.PROOF,
        subject=Subject.ANALYSIS,
        tags=["数列极限", "单调有界"],
        source_book="Rudin, Ch.3",
    ),
    Question(
        id="q-a04",
        content_latex=r"""
证明: $f(x) = x^2$ 在 $\mathbb{R}$ 上不是一致连续的。
""",
        difficulty=5,
        question_type=QuestionType.PROOF,
        subject=Subject.ANALYSIS,
        tags=["一致连续", "连续函数"],
    ),
    Question(
        id="q-a05",
        content_latex=r"""
证明: $f(x) = \sqrt{x}$ 在 $[0, +\infty)$ 上一致连续。
""",
        difficulty=6,
        question_type=QuestionType.PROOF,
        subject=Subject.ANALYSIS,
        tags=["一致连续", "连续函数"],
    ),
    Question(
        id="q-a06",
        content_latex=r"""
设 $f$ 在 $[a,b]$ 上连续，在 $(a,b)$ 上可微，且 $f(a) = f(b) = 0$。
证明: 存在 $c \in (a,b)$ 使得 $f'(c) = f(c)$。

\textit{提示: 考虑辅助函数 $g(x) = e^{-x}f(x)$。}
""",
        difficulty=6,
        question_type=QuestionType.PROOF,
        subject=Subject.ANALYSIS,
        tags=["微分学", "中值定理", "Rolle定理"],
    ),
    Question(
        id="q-a07",
        content_latex=r"""
设 $f: [0,1] \to [0,1]$ 连续。证明: $f$ 有不动点，即存在 $x_0 \in [0,1]$ 使得 $f(x_0) = x_0$。
""",
        difficulty=4,
        question_type=QuestionType.PROOF,
        subject=Subject.ANALYSIS,
        tags=["连续函数", "介值定理"],
    ),
    Question(
        id="q-a08",
        content_latex=r"""
证明: 方程 $x^3 - 3x + 1 = 0$ 在 $(0,1)$ 内有实根。
""",
        difficulty=3,
        question_type=QuestionType.PROOF,
        subject=Subject.ANALYSIS,
        tags=["连续函数", "介值定理"],
    ),
]


# ══════════════════════════════════════════════════════
#  高等代数 — 知识点 + 题目
# ══════════════════════════════════════════════════════

LA_KPS = [
    KnowledgePoint(
        id="kp-l01", title="线性空间的定义",
        content_latex=r"""
\textbf{定义.} 设 $V$ 为集合，$F$ 为域。若 $V$ 上定义了加法和数乘运算，
满足八条公理（加法交换律、结合律、零元、逆元、
数乘结合律、分配律两条、单位元），则称 $(V, F)$ 为线性空间。
""",
        kp_type=KPType.DEFINITION, difficulty=3,
        tags=["线性空间", "基本概念"],
    ),
    KnowledgePoint(
        id="kp-l02", title="维数公式",
        content_latex=r"""
\textbf{定理.} 设 $V_1, V_2$ 为有限维线性空间 $V$ 的子空间，则
$\dim(V_1 + V_2) = \dim V_1 + \dim V_2 - \dim(V_1 \cap V_2)$。
""",
        kp_type=KPType.THEOREM, difficulty=5,
        tags=["线性空间", "维数", "子空间"],
    ),
    KnowledgePoint(
        id="kp-l03", title="线性映射的核与像",
        content_latex=r"""
\textbf{定理 (秩-零度定理).} 设 $T: V \to W$ 为有限维空间之间的线性映射，则
$\dim V = \dim \ker T + \dim \operatorname{Im} T$。
""",
        kp_type=KPType.THEOREM, difficulty=5,
        tags=["线性映射", "核与像"],
    ),
    KnowledgePoint(
        id="kp-l04", title="特征值与特征向量",
        content_latex=r"""
\textbf{定义.} 设 $T: V \to V$ 为线性算子。若存在 $\lambda \in F$ 和非零向量
$v \in V$ 使得 $Tv = \lambda v$，则称 $\lambda$ 为 $T$ 的特征值，$v$ 为对应特征向量。
""",
        kp_type=KPType.DEFINITION, difficulty=3,
        tags=["特征值", "对角化"],
    ),
    KnowledgePoint(
        id="kp-l05", title="Cayley-Hamilton 定理",
        content_latex=r"""
\textbf{定理 (Cayley-Hamilton).} 设 $A$ 为 $n$ 阶矩阵，$p(\lambda) = \det(\lambda I - A)$
为其特征多项式。则 $p(A) = 0$。
""",
        kp_type=KPType.THEOREM, difficulty=6,
        tags=["特征值", "矩阵", "最小多项式"],
    ),
]

LA_QUESTIONS = [
    Question(
        id="q-l01",
        content_latex=r"""
设 $V = \mathbb{R}^4$，$V_1 = \operatorname{span}\{(1,1,0,0), (0,1,1,0)\}$，
$V_2 = \operatorname{span}\{(0,0,1,1), (1,0,0,1)\}$。
求 $\dim(V_1 + V_2)$ 和 $\dim(V_1 \cap V_2)$。
""",
        difficulty=4, question_type=QuestionType.CALCULATION,
        subject=Subject.LINEAR_ALGEBRA, tags=["线性空间", "维数", "子空间"],
    ),
    Question(
        id="q-l02",
        content_latex=r"""
设 $T: \mathbb{R}^3 \to \mathbb{R}^3$ 定义为 $T(x,y,z) = (x+y, y+z, z+x)$。
求 $\ker T$，$\operatorname{Im} T$，并验证秩-零度定理。
""",
        difficulty=4, question_type=QuestionType.CALCULATION,
        subject=Subject.LINEAR_ALGEBRA, tags=["线性映射", "核与像"],
    ),
    Question(
        id="q-l03",
        content_latex=r"""
证明: 若 $A$ 为 $n$ 阶实矩阵且 $A^2 = A$（幂等矩阵），
则 $\mathbb{R}^n = \ker A \oplus \operatorname{Im} A$。
""",
        difficulty=6, question_type=QuestionType.PROOF,
        subject=Subject.LINEAR_ALGEBRA, tags=["线性映射", "核与像", "直和分解"],
    ),
    Question(
        id="q-l04",
        content_latex=r"""
设 $A = \begin{pmatrix} 1 & 1 \\ 0 & 1 \end{pmatrix}$。
证明 $A$ 不可对角化，并求 $A^n$（$n$ 为正整数）。
""",
        difficulty=5, question_type=QuestionType.PROOF,
        subject=Subject.LINEAR_ALGEBRA, tags=["特征值", "对角化", "Jordan标准形"],
    ),
]


# ══════════════════════════════════════════════════════
#  群论 — 知识点 + 题目
# ══════════════════════════════════════════════════════

GT_KPS = [
    KnowledgePoint(
        id="kp-g01", title="群的定义",
        content_latex=r"""
\textbf{定义.} 集合 $G$ 连同二元运算 $\cdot: G \times G \to G$ 构成群 $(G, \cdot)$，
若满足: (1) 结合律; (2) 存在单位元 $e$; (3) 每个元素有逆元。
""",
        kp_type=KPType.DEFINITION, difficulty=2,
        tags=["群的基本概念"],
    ),
    KnowledgePoint(
        id="kp-g02", title="Lagrange 定理",
        content_latex=r"""
\textbf{定理 (Lagrange).} 设 $G$ 为有限群，$H \leq G$ 为子群。
则 $|H|$ 整除 $|G|$。特别地，$[G:H] = |G|/|H|$。
""",
        kp_type=KPType.THEOREM, difficulty=4,
        tags=["子群", "Lagrange定理"],
    ),
    KnowledgePoint(
        id="kp-g03", title="正规子群与商群",
        content_latex=r"""
\textbf{定义.} 子群 $N \leq G$ 称为正规子群 ($N \trianglelefteq G$)，
若对所有 $g \in G$，$gNg^{-1} = N$。此时商集 $G/N$ 关于陪集乘法构成群，称为商群。
""",
        kp_type=KPType.DEFINITION, difficulty=5,
        tags=["正规子群", "商群"],
    ),
    KnowledgePoint(
        id="kp-g04", title="第一同构定理",
        content_latex=r"""
\textbf{定理 (第一同构定理).} 设 $\varphi: G \to H$ 为群同态。
则 $\ker\varphi \trianglelefteq G$，$\operatorname{Im}\varphi \leq H$，
且 $G/\ker\varphi \cong \operatorname{Im}\varphi$。
""",
        kp_type=KPType.THEOREM, difficulty=6,
        tags=["群同态", "同构定理", "正规子群"],
    ),
    KnowledgePoint(
        id="kp-g05", title="Sylow 第一定理",
        content_latex=r"""
\textbf{定理 (Sylow I).} 设 $G$ 为有限群，$|G| = p^a m$，$\gcd(p, m) = 1$。
则 $G$ 含有 $p^a$ 阶子群 (称为 Sylow $p$-子群)。
""",
        kp_type=KPType.THEOREM, difficulty=7,
        tags=["Sylow定理", "有限群"],
    ),
]

GT_QUESTIONS = [
    Question(
        id="q-g01",
        content_latex=r"""
证明: 6 阶群要么同构于 $\mathbb{Z}/6\mathbb{Z}$，要么同构于 $S_3$。
""",
        difficulty=6, question_type=QuestionType.PROOF,
        subject=Subject.GROUP_THEORY, tags=["群的基本概念", "Lagrange定理", "同构"],
    ),
    Question(
        id="q-g02",
        content_latex=r"""
设 $G$ 为群，$|G| = p^2$（$p$ 为素数）。证明 $G$ 为 Abel 群。
""",
        difficulty=6, question_type=QuestionType.PROOF,
        subject=Subject.GROUP_THEORY, tags=["有限群", "p群", "Abel群"],
    ),
    Question(
        id="q-g03",
        content_latex=r"""
设 $\varphi: G \to H$ 为群同态，且 $G$ 为 Abel 群。证明 $\operatorname{Im}\varphi$ 也为 Abel 群。
""",
        difficulty=3, question_type=QuestionType.PROOF,
        subject=Subject.GROUP_THEORY, tags=["群同态", "Abel群"],
    ),
    Question(
        id="q-g04",
        content_latex=r"""
设 $G$ 为 35 阶群。证明 $G$ 为循环群。
\textit{提示: 利用 Sylow 定理。}
""",
        difficulty=7, question_type=QuestionType.PROOF,
        subject=Subject.GROUP_THEORY, tags=["Sylow定理", "有限群", "循环群"],
    ),
]


# ══════════════════════════════════════════════════════
#  汇总
# ══════════════════════════════════════════════════════

ALL_KNOWLEDGE_POINTS = ANALYSIS_KPS + LA_KPS + GT_KPS
ALL_QUESTIONS = ANALYSIS_QUESTIONS + LA_QUESTIONS + GT_QUESTIONS

def get_knowledge_base(subject: str = None) -> list[KnowledgePoint]:
    if subject is None:
        return ALL_KNOWLEDGE_POINTS
    return [kp for kp in ALL_KNOWLEDGE_POINTS if subject in str(kp.tags)]

def get_question_bank(subject: str = None) -> list[Question]:
    if subject is None:
        return ALL_QUESTIONS
    return [q for q in ALL_QUESTIONS if q.subject.value == subject]
