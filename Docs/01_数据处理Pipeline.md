# 数据处理 Pipeline

> 版本: v3.0 | 日期: 2026-03-31

> Pipeline 架构设计为**学科无关**——核心处理流程不假设特定学科，通过 ContentTagger Agent 的学科特定 prompt 实现多学科支持。MVP 阶段聚焦大学数学数据。

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
| 高中教辅资料 | .pdf / .docx | 高中数学/物理教辅（Post-MVP） | P1 |
| 物理教材 | .pdf / .tex | 大学物理教材（Post-MVP） | P1 |

---

## 2. Pipeline 总体架构

### 2.1 核心设计原则：LLM + 固定 Python Pipeline 混合架构

> **自适应能力的关键**：LLM 负责处理"不确定性"（识别任意格式、理解内容语义），输出**固定 Pydantic Schema**；Python Pipeline 负责处理"确定性"（标准化、切分、构建模型）。新格式接入时只需更新 LLM Agent 的 prompt，Python Pipeline 代码无需修改。

```
Raw Data (任意格式: PDF/TeX/MD/DOCX/PPTX/EPUB/CSV/...)
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│  [1] FormatAnalyzer Agent (LLM)                          │
│  输入: 文件路径 + 首页预览                                  │
│  输出: FormatAnalysis (Pydantic)                          │
│  职责: 识别格式、文档结构、内容类型、语言                      │
└──────────────────┬───────────────────────────────────────┘
                   │
          ┌────────┴────────┐
          │  已知格式快速通道  │  (TeX/MD → 直接用 Python 解析器)
          │  跳过 Agent 2    │
          └────────┬────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────┐
│  [2] ContentExtractor Agent (LLM, 多模态)                 │
│  输入: 原始文件 + FormatAnalysis                           │
│  输出: RawExtraction (Pydantic)                           │
│  职责: 多模态模型读取页面/图像，提取文本和数学内容               │
└──────────────────┬───────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────┐
│  [3] Python Normalizer (确定性)                           │
│  输入: RawExtraction                                      │
│  输出: list[NormalizedBlock] (Pydantic)                   │
│  职责: 将 RawPage 转为统一块结构，类型识别，LaTeX 清理         │
└──────────────────┬───────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────┐
│  [4] OcrCorrector Agent (LLM)                            │
│  输入: list[NormalizedBlock]                              │
│  输出: CorrectedContent (Pydantic)                        │
│  职责: OCR 错误修正、逻辑修复、跨页块合并                     │
└──────────────────┬───────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────┐
│  [5] Python Splitter (确定性)                             │
│  输入: CorrectedContent                                   │
│  输出: list[ContentUnit] (Pydantic)                       │
│  职责: 按语义切分为知识点/题目/练习等独立单元                   │
└──────────────────┬───────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────┐
│  [6] ContentTagger Agent (LLM)                            │
│  输入: list[ContentUnit]                                  │
│  输出: list[TaggedUnit] (Pydantic)                        │
│  职责: 自动标签(科目/专题/方法)、难度评估、类型分类             │
└──────────────────┬───────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────┐
│  [7] Python Builder (确定性)                              │
│  输入: list[TaggedUnit]                                   │
│  输出: list[KnowledgePoint | Question] (Pydantic)         │
│  职责: 构建最终 Pydantic 核心实体模型，分配 ID，写入存储       │
└──────────────────┬───────────────────────────────────────┘
                   │
                   ▼
               [Storage]
```

### 2.2 Pipeline Pydantic 模型

每个阶段的输入输出均为 Pydantic 模型，定义见 `05_数据模型与存储.md` §2.7。核心模型：

| 阶段 | 输出模型 | 关键字段 |
|------|---------|---------|
| FormatAnalyzer | `FormatAnalysis` | detected_format, structure_type, has_math_content |
| ContentExtractor | `RawExtraction` → `list[RawPage]` | text_content, math_blocks, image_descriptions |
| Normalizer | `list[NormalizedBlock]` | block_type, content_markdown, content_latex |
| OcrCorrector | `CorrectedContent` | blocks, correction_log, merged_blocks |
| Splitter | `list[ContentUnit]` | unit_type, title, content_latex |
| ContentTagger | `list[TaggedUnit]` | subject, tags, methods, difficulty |
| Builder | `list[KnowledgePoint \| Question]` | 最终核心实体 |

---

## 3. Pipeline Agent 定义

### 3.1 FormatAnalyzer Agent

**职责**: 分析原始文件，识别格式和文档结构。

- **输入**: 文件路径 + 文件前几页的文本/图像预览
- **输出**: `FormatAnalysis`
- **LLM 策略**: 对 PDF/PPTX 使用多模态 LLM 查看前 2-3 页截图；对文本格式（TeX/MD/DOCX）读取前 100 行
- **快速通道**: 若文件扩展名已明确（`.tex` → TeX, `.md` → Markdown），可跳过 LLM 直接返回

**判断逻辑**:

| 输入特征 | structure_type | 后续处理 |
|----------|---------------|---------|
| 有 `\begin{problem}` 环境 | exam_paper | TeX 快速通道 |
| 有 `定义 X.Y` / `定理 X.Y` 模式 | textbook / lecture_notes | MD 快速通道 |
| 有物理公式/定律/实验描述 | physics_material | 通用 Pipeline（物理 prompt） |
| PDF 含扫描页面 | scanned_document | 多模态提取 |
| PPTX 文件 | slides | python-pptx + 多模态 |
| 含习题/选择题/填空题 | problem_set | 题目切分优先 |
| 含编程题/代码 | cs_material | 通用 Pipeline（CS prompt） |

### 3.2 ContentExtractor Agent

**职责**: 从原始文件中提取内容，输出标准化的 `RawExtraction`。

- **多模态模式**（PDF/PPTX/扫描件）: 将每页渲染为图像，发送给多模态 LLM（Claude Vision / GPT-4V），要求输出 `RawPage` JSON
- **文本模式**（TeX/MD/DOCX）: 使用 Python 库直接提取文本，无需 LLM
- **混合模式**（含图表的 PDF）: 文本层用 pymupdf 提取，图表/公式用多模态 LLM 补充

**文本模式的 Python 提取器**:

| 格式 | 提取工具 | 说明 |
|------|---------|------|
| TeX | 内置 tex_parser.py | 正则提取 problem/solution 环境 |
| Markdown | 内置 md_parser.py | 按定义/定理/例题模式分块 |
| DOCX | python-docx / pandoc | 转 Markdown 后复用 MD 解析器 |
| CSV | 内置 csv_processor.py | 多格式自动检测 |
| PPTX | python-pptx | 提取文本框和备注 |
| EPUB | ebooklib | HTML → Markdown |
| PDF (文本层) | pymupdf4llm | 提取 Markdown + 数学公式 |
| PDF (扫描) | 多模态 LLM | 逐页图像识别 |

**批量处理策略**（大 PDF）: 每 20-30 页为一批，批次间重叠 2-3 页防止边界遗漏，支持断点续传。

### 3.3 OcrCorrector Agent

**职责**: 修正 OCR 错误、修复逻辑不一致、合并跨页块。

**三阶段修正**:

1. **确定性预处理**（Python，无 LLM）: 应用已知替换表（60+ 条 OCR 常见错误），验证数学术语白名单

   常见 OCR 替换示例:

   | 错误 | 正确 | 错误 | 正确 |
   |------|------|------|------|
   | 肴求 | 要求 | 盐德金 | 戴德金 |
   | 觔城 | 邻域 | 板限 | 极限 |
   | 硧界 | 确界 | 沿元 | 逆元 |

2. **LLM 修正**: 对仍有疑问的内容调用 LLM，提供数学上下文辅助判断
3. **跨页合并**: 使用有限状态机检测跨页的定义/定理/证明块，LLM 辅助判断合并边界

**输出**: `CorrectedContent`，包含修正后的 blocks 和完整的修正日志。

### 3.4 ContentTagger Agent

**职责**: 为每个 ContentUnit 自动分类标签、评估难度。

**标签维度**:

| 维度 | 说明 | 示例 |
|------|------|------|
| subject_domain | 学科大类 | math, physics, cs |
| subject | 学科细分 | 数学分析, 高等代数, 力学, 电磁学 |
| education_level | 教育阶段 | high_school, undergraduate, graduate |
| topic | 专题（支持层级） | 极限 > 数列极限, 牛顿定律 > 第二定律 |
| method | 方法/技巧 | ε-δ 语言, 反证法, 受力分析, 能量守恒 |
| difficulty | 难度 1-10 | 基于 10 维评分模型 |
| kp_type / question_type | 知识点/题目类型 | definition, theorem, law, proof, calculation |

**学科适配**: ContentTagger 通过学科特定的 prompt 模板识别不同学科的标签体系。新增学科只需添加对应 prompt 模板和标签配置，无需修改 Agent 代码。

**输出**: `list[TaggedUnit]`，引用 `05_数据模型与存储.md` 中的标签分类体系。

---

## 4. Python-first 快速通道

对于已知格式的文件，跳过 LLM Agent 直接使用 Python 解析器，速度更快且无 API 成本：

### 4.1 TeX 快速通道

`tex_parser.py` → `tex_cleaner.py` → `question_builder.py`

- 正则匹配 `\begin{problem}...\end{problem}` 和 `\begin{solution}...\end{solution}`
- 从文件名和 `\section{...}` 提取元数据（年份、考试类型、题号）
- 自动宏展开（`\bbR` → `\mathbb{R}` 等）和 LaTeX 标准化
- 基于考试类型和题目位置分配难度
- 处理边界情况：`boxedsolution`、`exercise[变式N]`、无 solution 的 problem

### 4.2 MD 快速通道

`md_parser.py` → `ocr_repair.py` → `kp_builder.py`

- 按关键词模式识别块类型（`定义 X.Y.Z.`、`定理`、`引理`、`例`、`证明` 等）
- 区分讲义格式和教材格式，自动检测来源类型
- 证明块附着到对应定理
- OCR 错误检测和确定性修复
- 图片 URL 编目供人工审核

### 4.3 其他格式快速通道

| 格式 | 处理器 | 策略 |
|------|--------|------|
| DOCX | `docx_processor.py` | pandoc 转 MD → 复用 MD 快速通道 |
| CSV | `csv_processor.py` | 自动检测列格式（flat/latex/structured） |
| PDF (文本层) | `pdf_processor.py` | pymupdf4llm 提取 → MD 解析，三阶段批处理 |

---

## 5. 新格式支持

### 5.1 PPT/PPTX 处理

**处理流程**:
1. python-pptx 提取每张幻灯片的文本框、标题、备注
2. 对含数学公式图片的幻灯片，调用多模态 LLM 识别
3. 合并为 `RawExtraction` 后进入通用 Pipeline

### 5.2 EPUB 处理

**处理流程**:
1. ebooklib 解析 EPUB 结构，提取各章节 HTML
2. HTML → Markdown 转换（html2text 或 markdownify）
3. 进入 MD 快速通道或通用 Pipeline

---

## 6. 质量保障

### 6.1 内容验证器

`content_validator.py` 对解析结果进行多级验证:

| 检查项 | 说明 |
|--------|------|
| LaTeX 括号匹配 | `$`, `$$`, `{}`, `\begin/\end` 配对检查 |
| 语义完整性 | 定理-证明配对、定义完整性、QED 标记 |
| 编号连续性 | 检测定义/定理编号跳跃 |
| 交叉引用 | 验证引用的定义/定理是否存在 |

### 6.2 跨页检测器

`cross_page_detector.py` 使用有限状态机追踪跨页内容:

- 状态: IDLE → IN_DEFINITION / IN_THEOREM / IN_PROOF / IN_EXAMPLE / IN_SOLUTION
- 最大跨页限制（默认 5 页）防止死锁
- 页面结束时检查块完整性

---

## 7. 存储结构

```
data/
├── questions/
│   ├── index.json
│   ├── math/                           # 按学科大类组织
│   │   ├── xinlingjun/{exam_type}/{year-month}/
│   │   │   ├── q001.json + q001.tex + q001_sol.tex
│   │   │   └── ...
│   │   └── generated/
│   ├── physics/                        # 物理（Post-MVP）
│   └── cs/                             # CS（Future）
├── knowledge/
│   ├── index.json
│   ├── math/analysis/ | math/linear_algebra/ | math/group_theory/
│   ├── physics/mechanics/ | physics/electromagnetism/
│   └── ...
├── tags/
│   ├── taxonomy.json                   # 全局标签
│   └── subject_tags/{domain}.json      # 学科专属标签
└── config/
    ├── difficulty_tiers.json
    └── subjects.json                   # 学科注册配置
```

---

## 8. 实现文件清单

| 文件 | 职责 |
|------|------|
| **Python-first 快速通道** | |
| `pipeline/tex_parser.py` | TeX 文件解析 |
| `pipeline/tex_cleaner.py` | LaTeX 标准化 |
| `pipeline/md_parser.py` | MD 文件解析 |
| `pipeline/ocr_repair.py` | OCR 确定性修复 |
| `pipeline/question_builder.py` | ParsedProblem → Question |
| `pipeline/kp_builder.py` | MdBlock → KnowledgePoint |
| `pipeline/pdf_processor.py` | PDF 批量处理 |
| `pipeline/docx_processor.py` | DOCX 处理 |
| `pipeline/csv_processor.py` | CSV 处理 |
| `pipeline/content_validator.py` | 内容验证 |
| `pipeline/cross_page_detector.py` | 跨页检测 |
| **通用 Pipeline** | |
| `pipeline/normalizer.py` | 固定 schema 标准化 |
| `pipeline/splitter.py` | 内容单元切分 |
| `pipeline/pptx_processor.py` | PPTX 处理 |
| `pipeline/epub_processor.py` | EPUB 处理 |
| **Pipeline LLM Agents** | |
| `pipeline/agents/format_analyzer.py` | 格式分析 Agent |
| `pipeline/agents/content_extractor.py` | 内容提取 Agent |
| `pipeline/agents/ocr_corrector.py` | OCR 修正 Agent |
| `pipeline/agents/content_tagger.py` | 标签分类 Agent |
