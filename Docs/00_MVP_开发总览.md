# MathForge 完整开发总览

> 版本: v3.0 | 日期: 2026-03-31

## 0. 系统愿景

**MathForge** 是一个 AI 驱动的多学科知识库与智能教育系统。核心使命：

- **记录知识**: 从教材、讲义、试卷等资料中自动提取并结构化管理各学科知识点
- **学习知识**: 提供语义检索、知识图谱、前置知识链路等辅助系统化学习
- **生成讲义**: AI 辅助生成专题讲义、复习材料、教学文档
- **生成题目**: 基于知识库生成新题目，支持多种变式策略
- **生成解答**: 为任意题目生成严格、详尽的分步解答
- **分级分类管理**: 按学科、教育阶段、专题、难度、方法等多维度组织知识和题目

**学科聚焦**: 主要面向**数学**和**物理**的高中及大学内容，同时架构设计支持向 CS、化学、生物、语文、英语等领域拓展。

---

## 1. MVP 范围定义

MVP 以 CLI 工具形式交付三项核心能力，聚焦**大学数学**场景：

| 能力 | 说明 |
|------|------|
| **通用数据处理 Pipeline** | LLM+Python 混合架构，支持 TeX/MD/PDF/DOCX/PPTX/EPUB 等任意格式，自动提取结构化题库与知识库 |
| **核心生成引擎** | 生成新数学题目、生成详细解答、验证题目正确性（5 阶段闭环） |
| **CLI 接口** | 全部操作通过命令行完成，支持 Rich 终端输出 |

> **最小 MVP 产品** = 数据梳理 Pipeline + 生成大学数学题目及配套解答

### 明确排除（不在 MVP 范围内）

- Web 前端（Next.js）
- FastAPI 服务 / REST API
- 用户认证系统
- 试卷组装 / 文档生成
- 个性化推荐系统
- 物理/其他学科支持（数据模型预留，但 MVP 不实现）
- 高中内容（数据模型预留 education_level 字段）

### 存储方案

- MVP: JSON 文件存储
- 生产过渡: SQLite + sqlite-vec（轻量级，嵌入式，零运维）

---

## 2. 开发阶段与里程碑

### Phase 0: 项目脚手架 (Day 1-2)

- 初始化 Python 项目（pyproject.toml，uv 管理）
- Pydantic v2 模型层（核心实体 + Pipeline 中间模型 + Agent 通信模型）
- 配置管理（pydantic-settings）+ 结构化日志（structlog）

**交付物**: `mathforge --version` 正常输出

### Phase 1: TeX 解析与题库构建 (Day 3-7)

- TeX 解析器：提取 problem/solution 环境
- 元数据提取：年份、考试类型、题号
- 难度分配 + 结构化 JSON/LaTeX 输出
- 数据模型预留 `subject`、`education_level` 字段（MVP 默认大学数学）
- CLI: `mathforge parse tex`

**交付物**: 23 个 TeX 文件 → ~150-200 道结构化大学数学题目

### Phase 2: MD 解析与知识库构建 (Day 8-14)

- MD 解析器：按学科结构分块（定义/定理/引理/例题/习题/公式/概念等）
- OCR 错误检测与修复（确定性 + LLM 辅助）
- 知识点自动归类（学科/专题/教育阶段）
- CLI: `mathforge parse md`, `mathforge kb list/search`

**交付物**: 100+ 讲义知识点，200+ 教材知识点（大学数学）

### Phase 3: LLM 客户端与核心引擎 (Day 15-21)

- 多 Provider LLM 客户端（含多模态和结构化输出支持）
- 5 阶段生成引擎（Context → Generate → Solve → Verify → Annotate）
- CLI: `mathforge generate free/variant/batch/solve`

**交付物**: 端到端生成流水线，可产出经验证的数学题目

### Phase 4: 通用 Pipeline 与集成 (Day 22-28)

- 通用 Pipeline: FormatAnalyzer / ContentExtractor / OcrCorrector / ContentTagger 四个 Agent
- PDF 多模态处理、DOCX/PPTX/EPUB 支持
- 异步批量处理、错误处理和重试
- CLI: `mathforge parse auto/pdf/docx/pptx`

**交付物**: 完整 MVP，支持任意格式数据导入

---

> **以下为 Post-MVP 阶段**

### Phase 5: SQLite 迁移与 FastAPI 服务 (Week 5-7)

- SQLite + sqlite-vec 数据库迁移（含多学科/多学段数据模型）
- Embedding 生成 + 向量索引
- FastAPI REST API（Questions / Knowledge / Generate / Subject）
- asyncio 内置异步任务管理
- Docker 开发环境（仅 api + frontend 两个服务）

**交付物**: API 服务可运行，CLI 和 API 共存，多学科数据模型就绪

### Phase 6: Agent 编排与文档生成 + 学科拓展 (Week 8-10)

- LangGraph 有状态图编排（10 个 Agent）
- LaTeX 模板体系 + Jinja2 + XeLaTeX 编译
- 智能组卷引擎
- 接入物理学科（高中+大学）和高中数学内容
- 学科特定的 prompt 模板和标签体系

**交付物**: 自动组装试卷/讲义并编译为 PDF；支持数学+物理两大学科

### Phase 7: Web 前端与用户系统 (Week 11-14)

- Next.js 15 + shadcn/ui + KaTeX/MathJax
- 多学科导航、学科筛选、学段切换
- 用户认证、学习记录、错题分析、能力画像
- 个性化推荐（按学科+学段维度）

**交付物**: 完整 Web 应用，支持多学科知识库浏览与题目练习

### Phase 8: 部署与质量体系 + 更多学科 (Week 15-18)

- 生产部署（Docker + Nginx）
- 监控（Prometheus + Grafana + Sentry）
- 质量仪表板、难度校准模型
- Prompt 持续优化
- 拓展 CS、化学、生物等学科（按需）

**交付物**: 生产级系统上线，可持续接入新学科

---

## 3. 依赖关系

```
Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 (MVP)
Phase 4 → Phase 5 (数据库+API)
Phase 5 → Phase 6 (Agent+文档) ─┐
Phase 5 → Phase 7 (前端+用户)  ─┤ 可部分并行
Phase 6 + 7 → Phase 8 (部署)    ┘
```

---

## 4. 成功指标

### MVP 阶段（大学数学）

| 指标 | 目标 |
|------|------|
| TeX 文件解析成功率 | 23/23 (100%) |
| 题目提取数量 | 150+ |
| 知识点提取数量 | 200+ |
| 生成成功率 | > 50% |
| 数学正确性（人工抽检） | > 90% |
| 难度评分偏差 | < 2.0 |

### 完整系统

| 指标 | 目标 |
|------|------|
| 支持学科数 | 2+（数学、物理），可拓展 |
| 支持教育阶段 | 高中 + 大学 |
| 新学科接入时间 | < 1 周（标签体系 + prompt 模板 + 可选教材导入） |
| 跨学科知识点关联 | 支持（如：数学分析 ↔ 理论力学） |

---

## 5. 完整技术栈

| 层面 | MVP | 完整项目 |
|------|-----|----------|
| 语言 | Python 3.11+ | Python + TypeScript |
| 模型定义 | Pydantic v2 | Pydantic v2 |
| CLI | Typer + Rich | Typer + Rich |
| API | — | FastAPI + uvicorn |
| Agent 编排 | — | LangGraph |
| LLM | anthropic / openai / google-genai | 同左 |
| 前端 | — | Next.js 15 + shadcn/ui + KaTeX |
| 配置 | pydantic-settings | 同左 |
| 日志 | structlog | structlog + Sentry |
| 存储 | JSON 文件 | SQLite + sqlite-vec |
| 向量检索 | — | sqlite-vec (prod) / ChromaDB (dev) |
| 异步任务 | — | asyncio (内置) |
| 文件存储 | 本地文件 | 本地文件 / S3 (可选) |
| 文档编译 | — | TeX Live (XeLaTeX) + Jinja2 |
| 测试 | pytest | pytest + Playwright |
| 部署 | — | Docker + Nginx |

---

## 6. 风险缓解

| 风险 | 缓解措施 |
|------|----------|
| MD 内容 OCR 错误 | 两阶段修复：确定性替换 → LLM 辅助；人工审核队列 |
| LLM 生成数学/物理错误 | 多项验证检查 + 独立审核 LLM 实例；学科特定验证规则 |
| TeX 解析边界情况 | 全部 23 文件快照测试；人工审核 |
| API 成本 | Sonnet 而非 Opus；缓存 LLM 响应 |
| 文件存储瓶颈 | 超过 1000 题时迁移到 SQLite |
| 多格式输入质量不一 | LLM + 固定 Schema 混合架构确保 Python Pipeline 稳定 |
| 图片链接失效 | 编目图片 URL；人工审核清单 |
| 多学科拓展复杂度 | 学科通过配置（标签+prompt）接入，核心 Pipeline/引擎代码学科无关 |
| 不同学科验证标准差异 | 每个学科维护独立的验证规则集和 prompt 模板 |

---

## 7. 文档索引

### MVP 阶段 (Phase 0-4) — 大学数学聚焦

| 文档 | 内容 |
|------|------|
| 01_数据处理Pipeline.md | 通用 LLM+Python 混合 Pipeline（学科无关架构） |
| 02_TeX解析与题库构建.md | TeX 解析、难度分级、提示词模板 |
| 03_核心生成引擎.md | 5 阶段生成-验证引擎（可适配多学科） |
| 04_LLM接口规范.md | 多 Provider LLM API（含多模态） |
| 05_数据模型与存储.md | Pydantic 模型（含多学科/多学段字段）、存储、SQLite 方案 |
| 06_CLI接口设计.md | CLI 命令结构与输出格式 |
| 07_项目结构与开发规范.md | 项目结构、编码规范、测试 |

### 完整项目 (Phase 5-8) — 多学科拓展

| 文档 | 内容 |
|------|------|
| 08_FastAPI服务层.md | REST API、异步任务、多学科路由 |
| 09_Agent编排系统.md | LangGraph 工作流、10 个 Agent |
| 10_文档生成系统.md | LaTeX 模板、智能组卷（多学科模板） |
| 11_用户系统与个性化.md | 认证、学习记录、多学科推荐 |
| 12_Web前端设计.md | Next.js 页面、学科导航、核心组件 |
| 13_部署与运维.md | Docker 部署、监控、成本 |
| 14_质量保障与持续改进.md | 质量指标、校准模型、学科拓展机制 |
