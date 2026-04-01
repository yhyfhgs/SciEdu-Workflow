# 数据处理 Pipeline

> 版本: v4.0 | 日期: 2026-04-01

> Pipeline 架构设计为**学科无关**——核心处理流程不假设特定学科，通过学科特定 prompt 和 taxonomy 实现多学科支持。MVP 阶段聚焦大学数学数据。

## 1. 数据清单

### 1.1 MVP 数据（大学数学）

| 来源 | 格式 | 体量 | 质量 | 优先级 |
|------|------|------|------|--------|
| `Raw Data/新领军真题整理/` | .tex (23个) | ~22,080 行 | 高（手写 LaTeX） | P0 - MVP |
| `Raw Data/新领军讲义md/` | .md (8个) | ~6,320 行 | 中（OCR 含错误） | P1 - MVP |
| `Raw Data/数学分析课本md/` | .md (32个) | ~31,796 行 | 中（OCR 含错误） | P1 - MVP |
| `Raw Data/抽代教材PPT习题/` | .pdf + .pptx | ~20MB | 未处理 | P2 |

### 1.2 通用数据（任意学科）

| 来源 | 格式 | 说明 | 优先级 |
|------|------|------|--------|
| 任意 PDF 教材 | .pdf | 数学/物理/CS 等各学科教材 | 通用 |
| 任意讲义/笔记 | .md / .docx / .tex | 课堂讲义、学习笔记 | 通用 |
| PPT/PPTX 课件 | .pptx | 课堂幻灯片 | 通用 |
| 试卷/题目集 | .pdf / .tex / .docx | 各学科各学段试卷 | 通用 |
| 电子书 | .epub | 教材或参考书 | 通用 |

---

## 2. Pipeline 总体架构

### 2.1 核心设计原则

> **"先统一再提取，LLM 理解格式、Python 执行提取"**
>
> 1. 所有非文本格式先转为 Markdown，MD/TeX 直接透传
> 2. LLM 循环阅读全文，完成语义完整拆分的同时**学习出文档的内容组织格式（FormatSpec）**
> 3. Python MetaExtractor 按 FormatSpec 中的规则机械提取条目——新格式接入只需 LLM 生成新 spec，Python 代码无需修改
> 4. LLM 对每个条目进行校准修复后，统一转化为 Pydantic 继承体系中的标准化条目，完成标注和关系建立

### 2.2 三环节流程图

```
Raw Data (PDF/DOCX/PPTX/EPUB/CSV/...)        MD/TeX 文件 (直接透传)
    │                                              │
    ▼                                              │
┌──────────────────────────────────┐               │
│ 1a. 格式转换 (Python + OCR/API)  │               │
│     → 单个大 MD 文件              │               │
└──────────────┬───────────────────┘               │
               │                                    │
               ▼                                    │
┌──────────────────────────────────────────────────┤
│ 1b. LLM 循环拆分 + Format 学习                    │
│     逐段读取 → 语义完整拆分 → 多个 MdBlock 文件     │
│     同时输出每个 block 的 FormatSpec               │
│     图像截取 → 保存为 asset → 建立 link            │
│     (每段用独立 LLM 窗口，控制 context 长度)        │
└──────────────┬─────────────────────────────────────┘
               │
               ▼
┌───────────────────────────────────────────────────┐
│ 【环节 2】Meta Python 格式提取器                    │
│  输入: MdBlock + FormatSpec                        │
│  输出: list[RawItem] (格式化条目，格式因源而异)      │
│  按 FormatSpec 中的规则机械提取、切分、配对           │
└──────────────┬────────────────────────────────────┘
               │
               ▼
┌───────────────────────────────────────────────────┐
│ 【环节 3】LLM 校准修复 + 统一格式化 + 标注 + 关系   │
│  3a. 逐条目 LLM 校准修复                           │
│      纠正错别字、语义/逻辑错误、验证正确性            │
│  3b. 格式转换: RawItem → UnifiedItem               │
│      Pydantic 继承体系: KnowledgeItem / QuestionItem│
│  3c. 标注: Tag 系统 (从粗到细 5 层) + 分层分级       │
│  3d. 关系建立: 条目间关联 (前置/后继/变式/关联题目)   │
└──────────────┬────────────────────────────────────┘
               │
               ▼
┌───────────────────────────────────────────────────┐
│  Entity Builder (Python)                           │
│  UnifiedItem → KnowledgePoint | Question | ...     │
│  分配持久化 ID，写入存储                             │
└──────────────┬────────────────────────────────────┘
               │
               ▼
           [Storage]
```

### 2.3 Pipeline Pydantic 模型总览

| 阶段 | 输出模型 | 关键字段 |
|------|---------|---------|
| 1a 格式转换 | 大 MD 文件 + `list[ImageAsset]` | content, asset_id, linked_block_id |
| 1b LLM 拆分 | `list[MdBlock]`（各含 `FormatSpec`） | content, format_spec, chapter_title |
| 2 MetaExtractor | `list[RawItem]` | block_type, content_raw, paired_item_id |
| 3a LLM 校准 | `list[CorrectionResult]` | corrected_content, corrections, quality |
| 3b 统一格式化 | `list[UnifiedItemBase 子类]` | 继承体系: KnowledgeItem / QuestionItem / ExampleItem |
| 3c 标注 | `TagSet`（嵌入 UnifiedItem） | taxonomy, pedagogical_level, methods |
| 3d 关系建立 | `list[ItemRelation]`（嵌入 UnifiedItem） | relation_type, target_id, confidence |
| Builder | `list[KnowledgePoint \| Question \| Solution]` | 最终持久化核心实体 |

---

## 3. 三环节详细定义

### 3.1 环节 1a — 格式转换（Python，确定性）

**职责**: 将非文本格式转为单个大 MD 文件。**MD 和 TeX 文件不需要转换，直接透传进入环节 1b**。

**格式转换工具**:

| 来源格式 | 转换工具 | 策略 |
|----------|---------|------|
| PDF (文本层) | pymupdf4llm | 提取 Markdown + 数学公式 |
| PDF (扫描件) | 多模态 LLM 逐页 | 页面截图 → Markdown |
| PPTX | python-pptx | 每张幻灯片 → 一个 MD section |
| DOCX | pandoc | 直转 Markdown |
| EPUB | ebooklib + html2text | 各章节 HTML → Markdown |
| CSV | csv_processor | 行转为条目化 Markdown |
| **MD** | **直接透传** | 不转换 |
| **TeX** | **直接透传** | 不转换 |

**图像处理**:

转换过程中遇到图像时：
1. 截取图像保存到 `assets/{doc_id}/img_{seq}.png`
2. 在 MD 中插入 `![图片描述](assets/{doc_id}/img_{seq}.png)` 链接
3. 记录图像与所在段落的关联关系

```python
class ImageAsset(BaseModel):
    """文档中提取的图像资产"""
    asset_id: str
    file_path: str                      # assets/{doc_id}/img_{seq}.png
    source_file: str                    # 原始文件路径
    source_page: int | None = None      # 来源页码
    context_text: str = ""              # 图像前后的文字上下文
    description: str = ""               # 多模态 LLM 生成的图像描述
    linked_block_id: str | None = None  # 所属 MdBlock ID
    linked_item_id: str | None = None   # 最终关联的条目 ID
```

---

### 3.2 环节 1b — 一次性 FormatSpec 推断 + 确定性分块

**职责**: 对转换后的文本（或直接透传的 MD/TeX 文件），LLM 一次性推断文档格式规则（FormatSpec），然后 Python 确定性分块。

#### 两阶段拆分流程

```
文本内容（MD/TeX/转换后文本）
    │
    ▼
Phase A: 一次性 FormatSpec 推断（1 次 LLM 调用）
    1. 采样文档（头部 150 行 + 30% 位置处 60 行）
    2. LLM 分析采样，归纳出 FormatSpec（内容块类型、正则模式、章节结构）
    3. 返回 FormatSpec
    │
    ▼
Phase B: 确定性分块（Python，无 LLM）
    - 若 FormatSpec 有 section_pattern → 按章节切分
    - 否则 → 按固定行数切分 + 语义边界检测
    - 每个 block 附带推断出的 FormatSpec
    │
    ▼
Fallback: 若 Phase A 失败 → 逐 chunk LLM 拆分（保留原有循环路径）
```

**关键设计**:
- **一次推断，多次应用**: FormatSpec 只需 1 次 LLM 调用推断，之后对整个文档确定性应用，大幅节约 token
- **FormatSpec 是通用契约**: LLM 负责识别格式，Python 负责执行提取——新格式只需 LLM 生成新 spec，Python 代码无需修改
- **Fallback 保底**: Phase A 失败时退回到逐 chunk LLM 拆分（BLOCK_SPLIT_PROMPT），保证不丢失任何内容

#### FormatSpec — 内容骨架描述（Meta Template）

LLM 为每个 MdBlock 输出的格式描述，定义了文档的内容组织方式，供环节 2 的 MetaExtractor 使用。

```python
class ContentBlockSpec(BaseModel):
    """文档中一种内容块类型的提取规则"""
    block_type: str              # 语义类型: "problem", "definition", "theorem", "proof", "solution"...
    open_pattern: str            # 开始正则 (Python re 语法)
    close_pattern: str | None    # 结束正则 (None = 到下一个 open 为止)
    number_group: int | None     # open_pattern 中编号捕获组索引
    title_group: int | None      # open_pattern 中标题捕获组索引
    is_paired_with: str | None   # 配对目标 block_type (如 "solution" → "problem")
    examples: list[str]          # LLM 从原文摘取的 2-3 个实际匹配示例

class AnswerFormatSpec(BaseModel):
    """答案组织方式"""
    location: Literal["inline", "after_problem", "end_of_document",
                       "separate_file", "boxed"]
    answer_marker: str | None    # 如 r"\\boxed\{(.+?)\}"
    solution_block_type: str | None  # 指向 ContentBlockSpec 中的 block_type

class FormatSpec(BaseModel):
    """LLM 对一个 MdBlock 的格式描述 — Meta Template"""
    content_blocks: list[ContentBlockSpec]    # 所有块类型及提取规则
    answer_format: AnswerFormatSpec
    section_pattern: str | None              # 章节标记正则
    metadata_hints: dict[str, str] = {}      # 元数据提取规则
    skip_line_patterns: list[str] = []       # 需跳过的行 (图片 URL、页码等)
    # 文档骨架信息
    detected_structure: list[str]            # 骨架摘要，如:
        # ["章: 极限理论", "§1 数列极限", "定义 1.1", "定理 1.2", "例 1.3", "练习 1-5"]
    source_type: str = ""                    # "exam_paper" | "lecture_notes" | "textbook" | "slides"
    chapter_title: str = ""
    section_titles: list[str] = []
    document_description: str = ""           # LLM 对文档的一句话总结
```

#### MdBlock + SplitCursor 模型

```python
class SplitCursor(BaseModel):
    """拆分游标 — 循环间传递状态"""
    current_line: int = 0
    current_page: int | None = None
    previous_block_tail: str = ""   # 上一个 block 末尾 3 行 (供跨块上下文)
    blocks_so_far: int = 0
    is_finished: bool = False

class MdBlock(BaseModel):
    """拆分后的单个内容块"""
    block_id: str
    source_file: str
    source_format: DocumentFormat       # 原始格式 (pdf/tex/md/...)
    content: str                        # Markdown/TeX 文本
    block_index: int                    # 在原文件中的序号 (0-based)
    total_blocks: int | None = None     # 原文件总块数 (拆分完成后填入)
    line_range: tuple[int, int]         # 对应原文件行号范围
    page_range: tuple[int, int] | None = None
    # 上下文
    chapter_title: str = ""
    section_title: str = ""
    preceding_context: str = ""         # 前一个 block 的末尾上下文
    # 格式描述
    format_spec: FormatSpec             # LLM 学习出的格式骨架
    # 图像资产
    image_assets: list[ImageAsset] = []
    # 元数据 (年份、考试类型、书名等)
    raw_metadata: dict = {}
```

---

### 3.3 环节 2 — Meta Python 格式提取器

**职责**: 接收 MdBlock + FormatSpec，按 FormatSpec 中的正则规则机械提取条目。一个**通用的 spec 驱动提取引擎**。

#### 核心理念

LLM 在环节 1 中理解了"怎么切"（输出 FormatSpec），Python 在环节 2 中执行"切"。不再为每种格式写一个 parser，而是一个 MetaExtractor 驱动所有格式。

#### 两种提取模式

| 模式 | 条件 | 示例 | 实现 |
|------|------|------|------|
| 显式边界 (A) | `close_pattern` 非空 | `\begin{problem}...\end{problem}` | `re.finditer(open)` + 向后搜索 close |
| 隐式边界 (B) | `close_pattern` 为空 | `定义 1.1.1. ...`（到下一个 open 为止） | 按 open 标记切分，块 = 两个 open 之间的内容 |

同一文档可混用两种模式（如 TeX 中 `\begin{theorem}` 用模式 A，而穿插的 `注` 用模式 B）。

#### RawItem 输出模型

**关键**: 此阶段输出的 `RawItem` 是**格式因源而异**的——不同 raw data 产生不同格式的 item。统一化在环节 3 完成。

```python
class RawItem(BaseModel):
    """环节 2 输出：从 MdBlock 中提取的原始条目"""
    item_id: str                       # 临时 ID
    block_type: str                    # 来自 ContentBlockSpec.block_type (原始类型名)
    number: str | None                 # 捕获的编号
    title: str | None                  # 捕获的标题
    content_raw: str                   # 提取的原始文本 (未修正)
    content_latex: str = ""            # LaTeX 内容 (若有)
    line_start: int
    line_end: int
    # 配对关系
    paired_item_id: str | None = None  # 配对条目 ID (solution→problem 等)
    # 来源追溯
    source_block_id: str               # 来源 MdBlock ID
    source_file: str                   # 原始文件路径
    extraction_mode: Literal["explicit", "implicit"]
    # 图像关联
    linked_image_ids: list[str] = []   # 关联的 ImageAsset ID
    # 原始元数据
    raw_metadata: dict = {}
```

#### 配对逻辑

根据 `ContentBlockSpec.is_paired_with` 字段自动配对：
- `solution` → 最近的前一个 `problem`
- `proof` → 最近的前一个 `theorem` / `lemma` / `corollary`
- `answer` → 最近的前一个 `exercise`

#### 与旧 Parser 的关系

现有 `tex_parser.py` / `md_parser.py` 降级为 fallback。当 MetaExtractor 提取结果为空或验证失败时回退到旧 parser。旧 parser 中的元数据提取函数（`classify_file()`、`extract_metadata()`）可继续复用。

---

### 3.4 环节 3a — 逐条目 LLM 校准修复

**职责**: 对每个 `RawItem` 调用 LLM，进行全面的质量校准。

**校准维度**:

| 维度 | 说明 |
|------|------|
| 文字修正 | 纠正错别字、OCR 错误（结合数学上下文） |
| 语义验证 | 定义是否完整、定理条件是否充分、证明逻辑是否连贯 |
| 逻辑验证 | 推导步骤正确性、条件与结论的匹配 |
| 答案验证 | 对题目类 item，验证答案/解答的正确性 |
| 完整性检查 | 内容是否被截断、引用是否完整 |

**Prompt 上下文融入**:
- 所在章节标题（来自 `MdBlock.chapter_title` / `section_title`）
- 前后相邻 item 的摘要
- 文档来源类型（`FormatSpec.source_type`）
- 文档骨架（`FormatSpec.detected_structure`）

```python
class CorrectionResult(BaseModel):
    """单条目校准结果"""
    corrected_content: str              # 修正后的内容
    corrected_latex: str = ""           # 修正后的 LaTeX
    corrections: list[CorrectionEntry] = []
    semantic_issues: list[str] = []     # 语义问题
    logic_issues: list[str] = []        # 逻辑问题
    is_answer_verified: bool | None = None  # 答案验证结果 (仅题目类)
    completeness_score: float = 1.0     # 内容完整度 0-1
    overall_quality: Literal["A", "B", "C", "D", "F"] = "B"

class CorrectionEntry(BaseModel):
    """单处修正记录"""
    original: str
    corrected: str
    reason: str
    category: Literal["ocr", "typo", "math_symbol", "semantic", "logic", "formatting"]
```

---

### 3.5 环节 3b — 统一条目格式（Pydantic 继承体系）

**职责**: 将不同格式的 `RawItem`（经校准后）统一转化为结构化的 `UnifiedItem` 继承体系。所有不同 raw data 格式化成 item 后最终都转化为此统一体系中的一个条目。

**设计原则**: 活用 Pydantic v2 的继承、嵌套、`computed_field`、`model_validator`，构建体系化的多样数据类型。

#### 继承体系总览

```
UnifiedItemBase (抽象基类)
├── KnowledgeItem (知识点大类)
│   ├── DefinitionItem    — 定义
│   ├── TheoremItem       — 定理
│   │   ├── LemmaItem     — 引理
│   │   ├── CorollaryItem — 推论 (含 parent_theorem_id)
│   │   └── PropositionItem — 命题
│   ├── FormulaItem       — 公式 (含 variables, applicable_conditions)
│   ├── MethodItem        — 解题方法/证明技巧
│   └── RemarkItem        — 注记/备注
│
├── QuestionItem (题目大类)
│   ├── ProofQuestion        — 证明题
│   ├── CalculationQuestion  — 计算题
│   ├── ChoiceQuestion       — 选择题 (含 options, correct_option)
│   ├── FillBlankQuestion    — 填空题 (含 blanks)
│   ├── ConstructionQuestion — 构造题
│   └── TrueFalseQuestion    — 判断题 (含 counterexample)
│
└── ExampleItem (例题 — 知识点与题目的混合)
```

#### 基类定义

```python
class UnifiedItemBase(BaseModel):
    """所有统一条目的抽象基类"""
    item_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # 内容 (三种形式)
    content_latex: str = ""
    content_plain: str = ""
    content_markdown: str = ""
    title: str = ""
    number: str | None = None          # 原始编号
    # 来源追溯
    source: ItemSource
    # 标注 (环节 3c 填入)
    tags: TagSet | None = None
    # 关系 (环节 3d 填入)
    relations: list[ItemRelation] = []
    # 质量
    quality: QualityAssessment | None = None
    # 图像
    images: list[ImageAsset] = []
    # 时间
    created_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(validate_assignment=True)

    @computed_field
    @property
    def item_category(self) -> str:
        """条目大类: 由子类覆盖"""
        return "base"

    @computed_field
    @property
    def has_images(self) -> bool:
        return len(self.images) > 0

    @computed_field
    @property
    def content_length(self) -> int:
        return len(self.content_plain)

class ItemSource(BaseModel):
    """统一来源追溯"""
    file_path: str
    block_id: str                      # MdBlock ID
    line_range: tuple[int, int] | None = None
    page_range: tuple[int, int] | None = None
    source_type: str = ""              # "exam_paper" | "textbook" | "lecture_notes"
    book_title: str | None = None
    chapter: str | None = None
    section: str | None = None
    exam_type: ExamType | None = None
    year: int | None = None
    extraction_method: str = "meta_extractor"
```

#### 知识点类

```python
class KnowledgeItem(UnifiedItemBase):
    """知识点大类基类"""
    kp_type: KPType
    subject_domain: SubjectDomain = SubjectDomain.MATH
    subject: Subject = Subject.ANALYSIS
    education_level: EducationLevel = EducationLevel.UNDERGRADUATE
    difficulty: int = Field(ge=1, le=10, default=5)
    prerequisites: list[str] = []      # 前置知识点 item_id 列表

    @computed_field
    @property
    def item_category(self) -> str:
        return "knowledge"

class DefinitionItem(KnowledgeItem):
    """定义"""
    kp_type: KPType = KPType.DEFINITION
    defined_term: str = ""             # 被定义的术语
    formal_statement: str = ""         # 形式化表述

class TheoremItem(KnowledgeItem):
    """定理"""
    kp_type: KPType = KPType.THEOREM
    conditions: list[str] = []         # 条件列表
    conclusion: str = ""               # 结论
    proof: ProofContent | None = None  # 关联的证明 (嵌套)
    is_named: bool = False             # 是否具名定理 (如 "中值定理")
    named_after: str = ""

class LemmaItem(TheoremItem):
    """引理"""
    kp_type: KPType = KPType.LEMMA

class CorollaryItem(TheoremItem):
    """推论"""
    kp_type: KPType = KPType.COROLLARY
    parent_theorem_id: str | None = None  # 所属定理的 item_id

class PropositionItem(TheoremItem):
    """命题"""
    kp_type: KPType = KPType.PROPOSITION

class FormulaItem(KnowledgeItem):
    """公式"""
    kp_type: KPType = KPType.FORMULA
    formula_latex: str = ""            # 核心公式 LaTeX
    variables: dict[str, str] = {}     # 变量说明 {"x": "实数", "n": "正整数"}
    applicable_conditions: str = ""

class MethodItem(KnowledgeItem):
    """解题方法/证明技巧"""
    kp_type: KPType = KPType.METHOD
    applicable_to: list[str] = []      # 适用场景
    steps: list[str] = []              # 方法步骤
    typical_examples: list[str] = []   # 典型应用的 item_id

class RemarkItem(KnowledgeItem):
    """注记/备注"""
    kp_type: KPType = KPType.REMARK
    remark_on: str | None = None       # 所注释的条目 item_id

class ProofContent(BaseModel):
    """证明内容（嵌套在定理类中）"""
    proof_latex: str = ""
    proof_plain: str = ""
    methods_used: list[str] = []
    step_count: int = 0
    is_complete: bool = True
```

#### 题目类

```python
class QuestionItem(UnifiedItemBase):
    """题目大类基类"""
    question_type: QuestionType
    subject_domain: SubjectDomain = SubjectDomain.MATH
    subject: Subject = Subject.ANALYSIS
    education_level: EducationLevel = EducationLevel.UNDERGRADUATE
    difficulty: int = Field(ge=1, le=10, default=5)
    difficulty_score: DifficultyScore | None = None  # 10 维精细评分
    # 解答 (嵌套)
    solution: SolutionContent | None = None
    answer: str = ""                   # 简短答案 (选择题 "C"、填空 "42")
    # 考查点
    tested_knowledge_ids: list[str] = []   # 考查的知识点 item_id
    methods_required: list[str] = []

    @computed_field
    @property
    def item_category(self) -> str:
        return "question"

    @computed_field
    @property
    def has_solution(self) -> bool:
        return self.solution is not None

class ProofQuestion(QuestionItem):
    """证明题"""
    question_type: QuestionType = QuestionType.PROOF
    claim_to_prove: str = ""

class CalculationQuestion(QuestionItem):
    """计算题"""
    question_type: QuestionType = QuestionType.CALCULATION
    expected_answer_type: str = ""     # "numerical" | "expression" | "set"

class ChoiceQuestion(QuestionItem):
    """选择题"""
    question_type: QuestionType = QuestionType.CHOICE
    options: dict[str, str] = {}       # {"A": "...", "B": "..."}
    correct_option: str = ""
    is_multiple: bool = False

class FillBlankQuestion(QuestionItem):
    """填空题"""
    question_type: QuestionType = QuestionType.FILL_BLANK
    blanks: list[str] = []             # 每个空的答案
    blank_count: int = 1

class ConstructionQuestion(QuestionItem):
    """构造题"""
    question_type: QuestionType = QuestionType.CONSTRUCTION
    target_properties: list[str] = []

class TrueFalseQuestion(QuestionItem):
    """判断题"""
    question_type: QuestionType = QuestionType.TRUE_FALSE
    correct_answer: bool = True
    counterexample: str = ""           # 若为假，反例

class SolutionContent(BaseModel):
    """解答内容（嵌套在题目类中）"""
    solution_latex: str = ""
    solution_plain: str = ""
    methods_used: list[str] = []
    step_count: int = 0
    key_steps: list[str] = []          # 关键步骤摘要
    confidence: Literal["high", "medium", "low"] = "medium"
    is_verified: bool = False
```

#### 例题类

```python
class ExampleItem(UnifiedItemBase):
    """例题/示例 — 同时具有知识点和题目属性"""
    kp_type: KPType = KPType.EXAMPLE
    subject_domain: SubjectDomain = SubjectDomain.MATH
    subject: Subject = Subject.ANALYSIS
    education_level: EducationLevel = EducationLevel.UNDERGRADUATE
    difficulty: int = Field(ge=1, le=10, default=5)
    solution: SolutionContent | None = None
    illustrates_ids: list[str] = []    # 所展示的知识点 item_id
    methods_demonstrated: list[str] = []

    @computed_field
    @property
    def item_category(self) -> str:
        return "example"
```

---

### 3.6 环节 3c — Tag 系统（从粗到细 5 层分级）

**职责**: 对每个 UnifiedItem 进行多维度、从粗到细的标注。Tag 系统与条目类型配合，确保能高效区分和归档不同条目。

#### TagSet 模型

```python
class TagSet(BaseModel):
    """条目的完整标注集 — 5 层从粗到细"""
    # 第 1 层: 学科大类 (最粗)
    domain: SubjectDomain              # math | physics | cs | ...
    # 第 2 层: 学科细分
    subject: Subject                   # 数学分析 | 高等代数 | 群论 | ...
    # 第 3 层: 知识领域 (taxonomy 路径)
    taxonomy: TaxonomyPath             # math.analysis.limits.sequence_limits
    # 第 4 层: 教育阶段 + 教学难度
    education_level: EducationLevel    # high_school | undergraduate | graduate
    pedagogical_level: PedagogicalLevel
    # 第 5 层: 方法/技巧标签 (最细)
    methods: list[str] = []            # "ε-δ 语言", "反证法", "数学归纳法"
    # 题目专属
    tested_topics: list[str] = []      # 题目考查的知识点名称
    # 自由标签
    custom_tags: list[str] = []        # 不在体系内的补充标签
```

#### TaxonomyPath — 知识点层级分类路径

```python
class TaxonomyPath(BaseModel):
    """知识点层级分类路径 (dot 分隔)"""
    domain: str         # "math"
    branch: str         # "analysis" | "algebra" | "abstract_algebra"
    topic: str          # "limits" | "eigenvalues" ...
    subtopic: str | None = None  # "sequence_limits" | "function_limits" | ...
    specific: str | None = None  # "squeeze_theorem" | "epsilon_delta" | ...

    @computed_field
    @property
    def full_path(self) -> str:
        """返回 dot 分隔路径: math.analysis.limits.sequence_limits"""
        parts = [self.domain, self.branch, self.topic]
        if self.subtopic:
            parts.append(self.subtopic)
        if self.specific:
            parts.append(self.specific)
        return ".".join(parts)
```

#### PedagogicalLevel — 教学难度阶梯

```python
class PedagogicalLevel(str, Enum):
    """教学难度阶梯"""
    BASIC = "basic"                 # 直接套公式/定义
    STANDARD = "standard"           # 标准方法 1-2 步
    INTERMEDIATE = "intermediate"   # 多知识点组合
    ADVANCED = "advanced"           # 深度理解/非平凡组合
    COMPETITION = "competition"     # 竞赛/创造性方法
```

#### QualityAssessment — 内容质量评估

```python
class QualityAssessment(BaseModel):
    """内容质量评估"""
    grade: Literal["A", "B", "C", "D", "F"]
    completeness: float = 1.0       # 0-1, 内容是否完整
    correctness: float = 1.0        # 0-1, 数学是否正确
    clarity: float = 1.0            # 0-1, 表述是否清晰
    issues: list[str] = []          # 具体问题描述
```

#### 学科特定 10 维权重（精细打分）

对题目类条目，复用现有 `DifficultyScore` 10 维评分模型，但按学科使用不同权重：

```python
SUBJECT_WEIGHTS = {
    "analysis": {
        # 数学分析更重视抽象和证明
        "abstraction_level": 0.15, "proof_structure": 0.15,
        "concept_count": 0.12, "step_count": 0.12,
        "technique_novelty": 0.10, "condition_parsing": 0.10,
        "prerequisite_depth": 0.08, "computation_load": 0.08,
        "multi_representation": 0.05, "metacognition": 0.05,
    },
    "algebra": {
        # 高等代数更重视计算和多表示
        "computation_load": 0.15, "multi_representation": 0.12,
        "concept_count": 0.12, "step_count": 0.12,
        "abstraction_level": 0.10, "technique_novelty": 0.10,
        "condition_parsing": 0.10, "proof_structure": 0.08,
        "prerequisite_depth": 0.06, "metacognition": 0.05,
    },
    "abstract_algebra": {
        # 抽象代数更重视抽象和前置知识
        "abstraction_level": 0.18, "prerequisite_depth": 0.15,
        "proof_structure": 0.12, "concept_count": 0.12,
        "technique_novelty": 0.10, "step_count": 0.10,
        "condition_parsing": 0.08, "metacognition": 0.05,
        "computation_load": 0.05, "multi_representation": 0.05,
    },
}
```

#### 分级参考信息融合（Prompt 设计）

LLM 分类标注时，prompt 中融入文档结构上下文：

```
## 分级参考上下文

### 文档来源
- 来源类型: {source_type} (exam_paper / lecture_notes / textbook)
- 来源描述: {document_description}
- 文档骨架: {detected_structure}

### 目录结构定位
- 当前章: {chapter_title}
- 当前节: {section_title}

### 相邻条目
- 前一条目: [{prev_type}] {prev_title}
- 后一条目: [{next_type}] {next_title}

请基于以上上下文判断此条目的学科分类和难度级别。
```

#### Taxonomy 树（MVP）

存储在 `config/taxonomy.json`，使用 dot 分隔路径：

```
math
├── analysis (数学分析)
│   ├── limits (极限理论)
│   │   ├── sequence_limits — squeeze_theorem, monotone_convergence,
│   │   │                      cauchy_criterion, subsequence
│   │   ├── function_limits — epsilon_delta, lhopital, asymptotic
│   │   └── series_convergence — positive_series, alternating_series,
│   │                             power_series, uniform_convergence
│   ├── continuity — pointwise, uniform_continuity, intermediate_value
│   ├── differentiation — derivatives, mean_value_theorems,
│   │                      taylor_expansion, extrema
│   ├── integration — riemann_integral, techniques,
│   │                  improper_integral, applications
│   ├── multivariable — partial_derivatives, multiple_integrals,
│   │                    line_surface_integrals, implicit_function
│   ├── topology_basics — open_closed_sets, compactness, connectedness
│   └── measure_theory
│
├── algebra (高等代数)
│   ├── linear_spaces — basis_dimension, subspaces, direct_sum
│   ├── linear_equations — gaussian_elimination, solution_structure
│   ├── matrices — operations, rank, decomposition
│   ├── determinants
│   ├── eigenvalues — characteristic_polynomial, diagonalization, jordan_form
│   ├── inner_product — orthogonality, quadratic_forms
│   └── linear_maps — kernel_image, isomorphism
│
├── abstract_algebra (抽象代数)
│   ├── groups — basic_concepts, subgroups, normal_subgroups,
│   │            homomorphisms, group_actions, sylow_theorems
│   ├── rings — ideals, polynomial_rings, factorization
│   └── fields — extensions, galois_theory
│
├── combinatorics (组合数学)
│   ├── counting
│   ├── graph_theory
│   └── number_theory — divisibility, congruences, diophantine
│
└── complex_analysis (复分析)
    ├── holomorphic
    ├── residue_theory
    └── conformal_mapping
```

---

### 3.7 环节 3d — 条目间关系建立

**职责**: 建立条目间的关联关系，形成知识图谱。

#### 关系类型

```python
class RelationType(str, Enum):
    """关系类型"""
    # 知识点间
    PREREQUISITE = "prerequisite"         # A 是 B 的前置知识
    SUCCESSOR = "successor"               # A 是 B 的后继学习内容
    COROLLARY_OF = "corollary_of"         # A 是 B 的推论
    GENERALIZATION = "generalization"     # A 是 B 的推广
    SPECIALIZATION = "specialization"     # A 是 B 的特例
    EQUIVALENT = "equivalent"             # 等价表述
    RELATED = "related"                   # 一般性关联
    # 知识点 ↔ 题目
    TESTS = "tests"                       # 题目 A 考查知识点 B
    ILLUSTRATES = "illustrates"           # 例题 A 展示知识点 B
    APPLIES_METHOD = "applies_method"     # 题目 A 使用方法 B
    # 题目间
    VARIANT_OF = "variant_of"             # A 是 B 的变式
    HARDER_THAN = "harder_than"           # A 比 B 难
    SIMILAR_TO = "similar_to"             # 相似题
    # 结构关系
    PART_OF = "part_of"                   # A 是 B 的一部分 (如小问)
    PROOF_OF = "proof_of"                 # A 是 B 的证明

class ItemRelation(BaseModel):
    """条目间的一条关系"""
    relation_type: RelationType
    target_id: str                        # 目标条目 item_id
    target_title: str = ""                # 冗余，便于展示
    confidence: float = 1.0               # 关系置信度
    source: Literal["llm", "structural", "manual"] = "llm"
    note: str = ""
```

#### 关系建立方式

| 关系类型 | 建立方式 | 说明 |
|----------|---------|------|
| `COROLLARY_OF`, `PROOF_OF`, `PART_OF` | 结构化提取 | 从 MetaExtractor 的配对关系直接映射 |
| `PREREQUISITE`, `SUCCESSOR` | LLM 推断 | 基于知识点内容和 taxonomy 位置 |
| `TESTS`, `ILLUSTRATES` | LLM 推断 | 题目/例题与知识点的关联 |
| `VARIANT_OF`, `SIMILAR_TO`, `HARDER_THAN` | LLM + embedding | 题目间关系，可结合向量相似度 |
| `GENERALIZATION`, `SPECIALIZATION` | LLM 推断 | 知识点间的抽象层级关系 |

#### 跨条目校验（Pydantic validator）

- `PREREQUISITE` 关系不能成环（DAG 约束）
- `COROLLARY_OF` 的目标必须是 theorem / lemma / proposition 类
- `TESTS` 的源必须是 question 类，目标必须是 knowledge 类
- 双向关系自动生成：A `prerequisite` B → B `successor` A

---

### 3.8 Entity Builder

**职责**: 将 `UnifiedItem`（含完整标注和关系）转化为最终的持久化核心实体（`KnowledgePoint`、`Question`、`Solution`），分配持久化 UUID，写入存储。

映射关系：

| UnifiedItem 子类 | → 最终实体 |
|------------------|-----------|
| `KnowledgeItem` 及其子类 | `KnowledgePoint` |
| `QuestionItem` 及其子类 | `Question` + `Solution`（若有解答） |
| `ExampleItem` | `KnowledgePoint`（kp_type=EXAMPLE） |

---

## 4. 指纹驱动快速通道

对于已知格式的文件，通过指纹匹配跳过 LLM format 学习，直接使用预定义的 `FormatSpec`。

### 4.1 匹配流程

1. 读取文件前 80 行 + 30% 位置 60 行作为采样
2. 对每个候选指纹的 `content_blocks` 正则在采样上执行
3. 匹配率 > 80% → 直接使用该指纹的 FormatSpec
4. 无指纹命中 → 进入 LLM 循环拆分流程

### 4.2 预定义指纹

#### `tex_xinlingjun` — 新领军真题 TeX

```python
FormatSpec(
    content_blocks=[
        ContentBlockSpec(
            block_type="problem",
            open_pattern=r"\\begin\{problem\}\[(.+?)\]",
            close_pattern=r"\\end\{problem\}",
            number_group=1,
            is_paired_with=None,
            examples=["\\begin{problem}[第1题]"],
        ),
        ContentBlockSpec(
            block_type="solution",
            open_pattern=r"\\begin\{solution\}",
            close_pattern=r"\\end\{solution\}",
            is_paired_with="problem",
            examples=["\\begin{solution}"],
        ),
    ],
    answer_format=AnswerFormatSpec(
        location="after_problem",
        answer_marker=r"\\boxed\{(.+?)\}",
        solution_block_type="solution",
    ),
    section_pattern=r"\\section\{(.+?)\}",
    source_type="exam_paper",
)
```

#### `md_lecture_zh` — 中文讲义 MD

```python
FormatSpec(
    content_blocks=[
        ContentBlockSpec(
            block_type="definition",
            open_pattern=r"^定义\s*([\d．.]+)",
            close_pattern=None,
            number_group=1,
            examples=["定义 1．1．1．"],
        ),
        ContentBlockSpec(
            block_type="theorem",
            open_pattern=r"^定理\s*([\d．.]+)",
            close_pattern=None,
            number_group=1,
            examples=["定理 1．1．2．"],
        ),
        ContentBlockSpec(
            block_type="example",
            open_pattern=r"^例\s*([\d．.]+)",
            close_pattern=None,
            number_group=1,
            examples=["例 1．1．1．"],
        ),
        ContentBlockSpec(
            block_type="proof",
            open_pattern=r"^证明[．.\s]",
            close_pattern=None,
            is_paired_with="theorem",
            examples=["证明．"],
        ),
    ],
    answer_format=AnswerFormatSpec(location="inline"),
    section_pattern=r"^##\s+[\d．.]+\s*(.+)",
    source_type="lecture_notes",
)
```

---

## 5. 新格式支持

### 5.1 PPT/PPTX 处理

1. python-pptx 提取每张幻灯片的文本框、标题、备注
2. 对含数学公式图片的幻灯片，调用多模态 LLM 识别
3. 输出单个大 MD 文件 → 进入环节 1b

### 5.2 EPUB 处理

1. ebooklib 解析 EPUB 结构，提取各章节 HTML
2. HTML → Markdown 转换（html2text 或 markdownify）
3. 输出单个大 MD 文件 → 进入环节 1b

---

## 6. 质量保障

### 6.1 内容验证器

`content_validator.py` 对解析结果进行多级验证：

| 检查项 | 说明 |
|--------|------|
| LaTeX 括号匹配 | `$`, `$$`, `{}`, `\begin/\end` 配对检查 |
| 语义完整性 | 定理-证明配对、定义完整性、QED 标记 |
| 编号连续性 | 检测定义/定理编号跳跃 |
| 交叉引用 | 验证引用的定义/定理是否存在 |

### 6.2 跨条目校验

`relation_builder.py` 对关系图进行全局校验：

| 检查项 | 说明 |
|--------|------|
| DAG 约束 | `PREREQUISITE` 关系无环 |
| 类型约束 | `COROLLARY_OF` 目标为定理类，`TESTS` 源为题目类 |
| 双向一致 | 双向关系自动生成并校验 |
| 孤立条目 | 检测无任何关系的条目（警告） |

---

## 7. 存储结构

```
data/
├── questions/
│   ├── index.json
│   ├── math/
│   │   ├── xinlingjun/{exam_type}/{year-month}/
│   │   │   ├── q001.json + q001.tex + q001_sol.tex
│   │   │   └── ...
│   │   └── generated/
│   ├── physics/
│   └── cs/
├── knowledge/
│   ├── index.json
│   ├── math/analysis/ | math/linear_algebra/ | math/group_theory/
│   └── ...
├── assets/                              # 图像资产
│   ├── {doc_id}/
│   │   ├── img_001.png
│   │   └── ...
│   └── asset_index.json
├── tags/
│   ├── taxonomy.json                    # 全局标签体系
│   └── subject_tags/{domain}.json
└── config/
    ├── difficulty_tiers.json
    ├── subjects.json
    └── fingerprints/                    # 预定义 FormatSpec 指纹
        ├── tex_xinlingjun.json
        ├── md_lecture_zh.json
        └── md_textbook_zh.json
```

---

## 8. 实现文件清单

| 文件 | 职责 |
|------|------|
| **环节 1: 格式转换 + 拆分** | |
| `pipeline/md_converter.py` | 格式转换 (PDF/DOCX/PPTX/EPUB → MD) |
| `pipeline/image_extractor.py` | 图像截取 + ImageAsset 管理 |
| `pipeline/block_splitter.py` | LLM 循环拆分 + FormatSpec 学习 |
| `pipeline/fingerprints/` | 预定义 FormatSpec 指纹集 |
| **环节 2: Meta 提取** | |
| `pipeline/meta_extractor.py` | spec 驱动通用提取引擎 |
| **环节 3: 校准 + 统一 + 标注 + 关系** | |
| `pipeline/agents/item_corrector.py` | 逐条目 LLM 校准修复 |
| `pipeline/item_unifier.py` | RawItem → UnifiedItem 格式转换 |
| `pipeline/agents/classifier_tagger.py` | LLM 分类标注 + 关系推断 |
| `pipeline/relation_builder.py` | 关系建立 + 跨条目校验 |
| **通用** | |
| `pipeline/entity_builder.py` | UnifiedItem → 最终实体 |
| `pipeline/content_validator.py` | 内容验证 |
| `config/taxonomy.json` | 知识点层级分类树 |
| **降级为 fallback** | |
| `pipeline/legacy_tex_parser.py` | 原 tex_parser.py |
| `pipeline/legacy_md_parser.py` | 原 md_parser.py |
