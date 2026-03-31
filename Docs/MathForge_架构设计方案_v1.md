# MathForge — AI 学科知识库与智能教育系统

## 完整架构设计方案 (v3.0)

> 更新日期: 2026-03-31

---

## 0. 系统定位与核心能力

**MathForge** 是一个 AI 驱动的多学科知识库与智能教育系统，主要面向**数学和物理**的高中及大学内容（同时可拓展至 CS、化学、生物、语文、英语等领域）。系统以"知识管理 + 内容生成"为核心，覆盖从知识录入到学习辅助的完整闭环：

| 能力 | 描述 |
|------|------|
| 知识录入与管理 | 记录、组织、检索各学科知识点，支持分级（高中/大学）、分类（学科/专题/方法）的层次化管理 |
| 通用数据处理 Pipeline | LLM+Python 混合架构，从任意格式教材/讲义/试卷中自动提取结构化知识点和题目 |
| 题库管理 | LaTeX/Markdown 格式题目存储，AI 自动分类标签与难度分级，多学科多层级体系 |
| 题目生成与变式 | 基于知识库 + 已有题目，AI 生成不同类型/难度的新题（8 种策略），覆盖数学、物理等学科 |
| 解答生成 | 对任意题目生成严格、详尽的分步解答，支持多种解法展示 |
| 讲义与教学材料生成 | AI 辅助生成专题讲义、复习材料，LaTeX 模板驱动自动组卷 |
| 知识学习与检索 | 语义搜索、知识图谱可视化、前置知识链路追踪，辅助系统化学习 |
| 个性化推荐 | 基于用户学习记录与能力画像，推荐专项练习与讲义 |

### 学科覆盖范围

| 优先级 | 学科 | 细分领域（示例） | 教育阶段 |
|--------|------|-----------------|----------|
| **P0 - MVP** | 数学 | 数学分析、高等代数、群论 | 大学 |
| **P1** | 数学 | 高中数学（函数、数列、解析几何、概率统计等） | 高中 |
| **P1** | 物理 | 力学、电磁学、热学、光学、量子力学基础 | 高中+大学 |
| **P2** | 计算机科学 | 数据结构、算法、离散数学、操作系统 | 大学 |
| **P2** | 化学/生物 | 有机化学、生物化学、分子生物学 | 高中+大学 |
| **P3** | 语文/英语 | 阅读理解、写作、语法 | 高中 |

> **最小 MVP**: 数据梳理 Pipeline + 大学数学题目生成及配套解答

---

## 1. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      用户接入层 (Presentation)               │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │   CLI    │  │  Web App     │  │  Desktop App           │ │
│  │ (Typer)  │  │ (Next.js)    │  │  (Tauri)               │ │
│  └────┬─────┘  └──────┬───────┘  └───────────┬────────────┘ │
│       └───────────────┼──────────────────────┘              │
└───────────────────────┼─────────────────────────────────────┘
                        │ REST / WebSocket
┌───────────────────────┼─────────────────────────────────────┐
│                   API 网关层 (FastAPI)                       │
│  Auth | Question | Knowledge | Generate | Document | Recommend | Subject │
└───────────────────────┼─────────────────────────────────────┘
                        │
┌───────────────────────┼─────────────────────────────────────┐
│                  Agent 编排层 (LangGraph)                     │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Agent Router                             │   │
│  │  ┌────────────┐ ┌────────────┐ ┌──────────────────┐  │   │
│  │  │ KB Agent   │ │ QGen Agent │ │ DocAssembly Agent│  │   │
│  │  └────────────┘ └────────────┘ └──────────────────┘  │   │
│  │  ┌────────────┐ ┌────────────┐ ┌──────────────────┐  │   │
│  │  │ Solve Agent│ │ Tag Agent  │ │ Recommend Agent  │  │   │
│  │  └────────────┘ └────────────┘ └──────────────────┘  │   │
│  │  ┌─── Pipeline Flow ──────────────────────────────┐  │   │
│  │  │ FormatAnalyzer → ContentExtractor → Normalizer │  │   │
│  │  │ → OcrCorrector → Splitter → ContentTagger      │  │   │
│  │  │ → Builder                                      │  │   │
│  │  └────────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────┘   │
└───────────────────────┼─────────────────────────────────────┘
                        │
┌───────────────────────┼─────────────────────────────────────┐
│                    数据与工具层                                │
│  ┌──────────┐ ┌───────────┐ ┌──────────────────────────┐   │
│  │ SQLite   │ │sqlite-vec │ │  LaTeX Engine (TeX Live) │   │
│  │ 主数据库  │ │ 向量检索   │ └──────────────────────────┘   │
│  └──────────┘ └───────────┘                                 │
│  ┌──────────┐ ┌───────────────────────────────────────┐     │
│  │ LLM API  │ │  通用 Pipeline (多模态 + Python)       │     │
│  │Claude/GPT│ │  支持 PDF/TeX/MD/DOCX/PPTX/EPUB/CSV  │     │
│  └──────────┘ └───────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 技术选型

### 2.1 后端核心

| 组件 | 选型 | 理由 |
|------|------|------|
| Web 框架 | FastAPI | 异步高性能，OpenAPI schema |
| Agent 框架 | LangGraph | 有状态图编排，支持循环/条件/人工介入 |
| LLM | Claude Sonnet 4 (主力) + Opus 4 (复杂推理) | 数学/物理推理能力强，多模态支持 |
| 数据库 | SQLite + sqlite-vec | 嵌入式，零运维，结构化 + 向量一体 |
| 异步任务 | asyncio (内置) | 无需外部队列服务 |
| 文件存储 | 本地文件系统 / S3 (可选) | 简单直接 |
| 模型定义 | Pydantic v2 | 统一数据层：Pipeline + Agent + API |
| CLI | Typer + Rich | 类型注解驱动，美化输出 |
| 日志 | structlog | 结构化 JSON/console 日志 |

### 2.2 前端

| 组件 | 选型 |
|------|------|
| Web 框架 | Next.js 15 (App Router) |
| UI 库 | shadcn/ui + Tailwind CSS |
| 数学渲染 | KaTeX (行内) + MathJax (完整) |
| 状态管理 | Zustand |
| 桌面端 | Tauri v2 |

### 2.3 数据处理

| 组件 | 选型 |
|------|------|
| PDF → Markdown | 多模态 LLM (主) / Marker (备) |
| 数学 OCR 增强 | 多模态 LLM + 确定性替换表 |
| 文本切分 | 自定义语义 Chunker |
| Embedding | text-embedding-3-large / voyage-3 |
| PPTX 处理 | python-pptx + 多模态 LLM |
| EPUB 处理 | ebooklib + html2text |

### 2.4 文档生成

| 组件 | 选型 |
|------|------|
| 排版引擎 | TeX Live (XeLaTeX) |
| 模板引擎 | Jinja2 → LaTeX |

---

## 3. 数据库设计

### 3.1 ER 图

```
users ─┬─< user_question_records >── questions ──< question_tags >── tags
       │                                  │
       │                                  ├──< solutions
       │                                  │
       └─< user_preferences               ├──< question_variants (self-ref)
                                          │
knowledge_points ──< kp_tags >── tags     │
       │                                  │
       └──< kp_sources >── books          │
                                          │
templates ──────────────── generated_documents
```

### 3.2 核心表（SQLite）

| 表名 | 主要字段 | 说明 |
|------|---------|------|
| `questions` | id TEXT PK, content_latex, content_plain, difficulty, difficulty_ai, question_type, subject, education_level, tags (JSON), source (JSON), is_generated, parent_id, generation_meta (JSON), created_at | 题库（多学科多层级） |
| `knowledge_points` | id TEXT PK, title, content_latex, content_plain, kp_type, subject, education_level, difficulty, tags (JSON), prerequisites (JSON), source (JSON), created_at | 知识库（多学科多层级） |
| `solutions` | id TEXT PK, question_id FK, solution_latex, methods_used (JSON), step_count, confidence, is_verified | 解答 |
| `tags` | name TEXT PK, category, subject, parent FK | 标签体系（按学科组织） |
| `subjects` | id TEXT PK, name, parent FK, education_levels (JSON), description | 学科与子领域 |
| `users` | id TEXT PK, username, email, password_hash, role, created_at | 用户 |
| `user_preferences` | user_id FK UNIQUE, subjects (JSON), education_level, difficulty_range (JSON), latex_style | 偏好 |
| `user_question_records` | id TEXT PK, user_id FK, question_id FK, status, error_type, error_analysis, attempted_at | 做题记录 |
| `books` | id TEXT PK, title, author, subject, education_level, pdf_path, processing_status | 教材 |
| `templates` | id TEXT PK, name, type, subject, latex_source, metadata (JSON) | LaTeX 模板 |
| `generated_documents` | id TEXT PK, user_id FK, template_id FK, title, question_ids (JSON), pdf_path | 生成文档 |

向量索引通过 sqlite-vec 虚拟表（`question_embeddings`, `kp_embeddings`）实现。

---

## 4. Agent 架构

### 4.1 十大 Agent

| # | Agent | 职责 | 输入 | 输出 |
|---|-------|------|------|------|
| 1 | **KB Agent** | 协调 Pipeline 完成多学科知识库构建，Embedding + 依赖图 | 教材文件路径 | list[KnowledgePoint] |
| 2 | **QGen Agent** | 多学科题目生成（8 种策略），向量去重 | GenerationRequest | GeneratedQuestion |
| 3 | **Solve Agent** | 分步解答，CoT + 自我验证，支持数学/物理等学科 | Question | Solution |
| 4 | **Tag Agent** | 多标签分类（学科/学段/专题/方法） + 10 维难度评分 | Question / KP | tags + DifficultyScore |
| 5 | **DocAssembly Agent** | 试卷/讲义智能组装 + LaTeX 编译 | ExamParams | PDF |
| 6 | **Recommend Agent** | 个性化推荐（weakness/advance/review） | user_id | list[Question] |
| 7 | **FormatAnalyzer** ⭐ | 分析文件格式和文档结构 | 文件路径 | FormatAnalysis |
| 8 | **ContentExtractor** ⭐ | 多模态内容提取 | 文件 + FormatAnalysis | RawExtraction |
| 9 | **OcrCorrector** ⭐ | OCR 修正 + 逻辑修复 + 跨页合并 | NormalizedBlocks | CorrectedContent |
| 10 | **ContentTagger** ⭐ | 标签/难度/方法自动分类 | ContentUnits | TaggedUnits |

⭐ = Pipeline Agent（v2.0 新增）

### 4.2 LangGraph 编排

主路由根据 task_type 分发到 5 个 Flow：Pipeline Flow、QGen Flow、Solve Flow、Doc Flow、Recommend Flow。

**QGen Flow** 包含生成循环（最多 3 轮）：retrieve → generate → solve → verify → (retry | annotate)。

**Pipeline Flow** 为线性子图：format_analyze → content_extract → normalize → ocr_correct → split → tag_content → build_models。其中 normalize、split、build_models 是 Python 确定性节点（不调用 LLM）。

### 4.3 核心设计原则

> **LLM + 固定 Python Pipeline 混合架构**: LLM Agent 负责处理不确定性（识别任意格式、理解语义、修正错误），输出固定 Pydantic Schema；Python Pipeline 负责确定性处理（标准化、切分、构建模型）。新格式接入只需更新 LLM prompt，Python 代码无需修改。

> **Pydantic 统一数据层**: Pipeline 中间表示、Agent 通信状态、API 请求/响应均为 Pydantic 模型，保证类型安全和 schema 自文档化。

---

## 5. LaTeX 模板系统

```
templates/
├── base/          # preamble.tex, math_macros.tex, style_common.tex
├── exam/          # exam_standard.tex, exam_midterm.tex, exam_final.tex
├── handout/       # handout_lecture.tex, handout_review.tex, handout_topic.tex
├── worksheet/     # worksheet_drill.tex, worksheet_quiz.tex
└── solution/      # solution_sheet.tex
```

使用 Jinja2 模板引擎，自定义分隔符 `<<` `>>` 避免与 LaTeX `{}` 冲突。模板支持: 试卷头信息、按题型分组、难度递进排列、答案卷、评分标准。

---

## 6. Web 前端页面结构

```
App
├── /                  Dashboard (学习概览 + 推荐 + 快捷入口)
├── /subjects          学科导航（数学/物理/CS/... 按学段筛选）
├── /knowledge         知识库浏览 + 知识图谱可视化（多学科）
├── /questions         题库列表 + 做题模式（分学科/学段筛选）
├── /generate          AI 生成配置器 + 实时预览（选择学科+学段）
├── /documents         文档中心 (PDF 预览/下载)
├── /profile           个人中心 (学习记录 + 雷达图 + 错题本)
└── /admin             管理后台 (用户/审核/学科管理/设置)
```

---

## 7. 项目目录结构

```
MathForge/
├── src/mathforge/           # CLI MVP 主包
│   ├── core/                # 核心逻辑 (models, engine, retriever, ...)
│   ├── llm/                 # LLM 客户端 (client, config, backends/)
│   ├── pipeline/            # 数据处理 (parsers, agents/, normalizer, ...)
│   ├── storage/             # 存储 (file_backend, sqlite_backend, index)
│   ├── cli/                 # CLI (main, parse, generate, kb, questions)
│   ├── prompts/             # 提示词模板
│   └── config/              # 配置 (settings, logging)
│
├── backend/                 # Post-MVP FastAPI 服务
│   ├── api/                 # REST 路由
│   ├── agents/              # LangGraph Agent 包装
│   └── core/                # 数据库, 用户, 推荐
│
├── frontend/                # Post-MVP Next.js
│   ├── app/                 # 页面路由
│   └── components/          # 组件
│
├── templates/               # LaTeX 模板
├── Raw Data/                # 原始数据
├── Docs/                    # 设计文档
├── tests/                   # 测试
└── config/                  # 配置文件
```

---

## 8. 实施路线图

| 阶段 | 时间 | 交付物 |
|------|------|--------|
| Phase 0-1 | Week 1 | 项目骨架 + TeX 解析 → 150+ 大学数学题 |
| Phase 2 | Week 2 | MD 解析 → 300+ 知识点（数学） |
| Phase 3 | Week 3 | LLM 客户端 + 数学题目生成引擎 |
| Phase 4 | Week 4 | 通用 Pipeline + 集成测试 = **MVP**（大学数学） |
| Phase 5 | Week 5-7 | SQLite + FastAPI + 多学科数据模型 |
| Phase 6 | Week 8-10 | LangGraph Agent + 文档生成 + 物理/高中数学支持 |
| Phase 7 | Week 11-14 | Web 前端 + 用户系统 + 学科管理 |
| Phase 8 | Week 15-18 | 生产部署 + 质量体系 + 更多学科拓展 |

---

## 9. 成本估算

| 项目 | 月费 (USD) |
|------|------------|
| LLM API | $50-150 |
| Embedding API | $10-30 |
| 服务器 (VPS) | $15-30 |
| 域名 + CDN | $0-10 |
| **合计** | **$75-220/月** |

无数据库托管费（SQLite 嵌入式）。
