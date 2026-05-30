# Agent 编排系统

> 版本: v3.0 | 日期: 2026-03-31（2026-04-14 订正）| 阶段: Phase 5-6 (Post-MVP 蓝图)

> ⚠️ **当前状态**：本文档描述的是 Post-MVP 完整蓝图（LangGraph + 十大 Agent）。
> **MVP 实际已实现的 Agent** 见 [`Docs/16_Agent系统MVP.md`](./16_Agent系统MVP.md)。
>
> - 实际目录：**`src/sciforge/agents/`**（本文档中的 `backend/agents/` 仅作架构示意）
> - 实际 StateGraph：**手写极简版本**（`src/sciforge/agents/graph.py`），非 LangGraph
> - Pipeline Agent 7-10（FormatAnalyzer/ContentExtractor/OcrCorrector/ContentTagger）
>   当前**不是独立 Agent 类**，其职责内置于 `src/sciforge/pipeline/` 的 5 阶段流水线
>   （`recognizers.py` / `vlm_converter.py` / `stages/stage1_extract.py` /
>   `stages/stage1_review.py` / `stages/stage2_annotate.py` / `stages/stage2_review.py` /
>   `stages/stage3_persist.py`）

## 1. 定位

将 MVP 中分散的函数式调用升级为 LangGraph 驱动的有状态 Agent 编排系统，支持循环、条件分支和人工介入。v3.0 新增 Pipeline Agent 子系统和多学科路由能力，Agent 通过学科特定的 prompt 模板和验证规则适配不同学科。

---

## 2. 编排架构 (LangGraph)

```
                    ┌─────────────┐
                    │   Router    │
                    │  (入口节点)  │
                    └──────┬──────┘
                           │ 任务分类
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
   ┌───────────┐    ┌──────────┐     ┌───────────┐
   │Pipeline   │    │ QGen     │     │ Doc       │
   │Flow (新增) │    │ Flow     │     │ Flow      │
   └─────┬─────┘    └────┬─────┘     └─────┬─────┘
         │               │                 │
   ┌─────┴──────┐  ┌─────┴──────┐   ┌─────┴──────┐
   │FormatAnalyze│  │ Retrieve   │   │ Select     │
   │→ Extract    │  │ Context    │   │ Questions  │
   │→ Normalize  │  │→ Generate  │   │→ Fill      │
   │→ Correct    │  │→ Verify    │   │ Template   │
   │→ Split      │  │→ Solve     │   │→ Compile   │
   │→ Tag        │  │→ Tag       │   └────────────┘
   │→ Build      │  └────────────┘
   └─────────────┘
```

---

## 3. 十大 Agent 详解

### Agent 1: KB Agent（知识库处理）

**职责**: 协调 Pipeline Agent 完成多学科知识库构建，负责 Embedding 生成、向量存储和前置依赖链接。

v3.0 变更：不再自己执行 PDF→Chunk→Tag 全流程，改为委托给 Pipeline Agents 完成内容提取和标签分类，自己专注于：
1. 调用 Pipeline Flow 处理教材文件（自动识别学科和学段）
2. 为每个 KnowledgePoint 生成 Embedding
3. 存储到 SQLite + sqlite-vec（按学科组织）
4. 构建前置知识依赖图（支持跨学科关联，如微积分 ↔ 力学）

### Agent 2: QGen Agent（题目生成）

**职责**: 基于 MVP 的 5 阶段流水线（Context → Generate → Solve → Verify → Annotate），增加向量检索和批次内去重。

- 继承 MVP GenerationEngine 的核心逻辑
- 向量检索替代纯标签过滤（使用 sqlite-vec）
- 批次内去重：embedding cosine > 0.92 → 拒绝
- 难度分布控制的批量生成

### Agent 3: Solve Agent（解题）

**职责**: 为题目生成详细分步解答。

- 三种输出风格: detailed / concise / hint_only
- Chain-of-Verification: 解答后自我验证
- 计算题可调用 SymPy 符号验算

### Agent 4: Tag Agent（标签与分级）

**职责**: 自动多标签分类 + 10 维连续难度评分，支持多学科标签体系。

- 学科大类识别 (subject_domain)、教育阶段识别 (education_level)
- 科目分类 (subject)、专题标签 (topic, 层级)、方法标签 (method)
- 10 维难度评分（见 `03_核心生成引擎.md`），权重可按学科调整
- 使用学科专属标签配置（`config/subject_tags/{domain}.json`）

### Agent 5: DocAssembly Agent（文档组装）

**职责**: 试卷/讲义/练习册的智能组装和 LaTeX 编译（详见 `10_文档生成系统.md`）

### Agent 6: Recommend Agent（个性化推荐）

**职责**: 基于用户画像的个性化学习推荐（详见 `11_用户系统与个性化.md`）

### Agent 7: FormatAnalyzer Agent（格式分析）⭐ 新增

**职责**: 分析原始文件格式和文档结构。

- **输入**: 文件路径 + 首页预览
- **输出**: `FormatAnalysis` (Pydantic)
- **特点**: 对已知格式（.tex, .md）可跳过 LLM 直接返回，降低成本

### Agent 8: ContentExtractor Agent（内容提取）⭐ 新增

**职责**: 多模态模型读取页面/图像，提取文本和数学内容。

- **输入**: 原始文件 + FormatAnalysis
- **输出**: `RawExtraction` (Pydantic)
- **核心能力**: 使用 `LLMClient.generate_with_images()` 逐页识别 PDF/PPT 内容
- **已知格式优化**: TeX/MD/DOCX 直接用 Python 解析器提取，无需 LLM

### Agent 9: OcrCorrector Agent（OCR 修正）⭐ 新增

**职责**: 修正 OCR 错误、修复逻辑不一致、合并跨页块。

- **输入**: `list[NormalizedBlock]`
- **输出**: `CorrectedContent` (Pydantic)
- **策略**: 确定性替换（60+ 条规则）→ LLM 辅助修正 → 跨页块合并

### Agent 10: ContentTagger Agent（内容标签）⭐ 新增

**职责**: 为 ContentUnit 自动标签（科目/专题/方法）和难度评估。

- **输入**: `list[ContentUnit]`
- **输出**: `list[TaggedUnit]` (Pydantic)
- **复用**: 与 Tag Agent 共享标签分类体系和 prompt 模板

---

## 4. LangGraph 工作流定义

### 4.1 主路由图

```python
class AgentState(TypedDict):
    task_type: str           # pipeline | generate | solve | doc | recommend
    input: dict
    context: ContextBundle | None
    candidates: list[Question]
    solution: Solution | None
    verification: VerificationResult | None
    output: dict | None
    error: str | None

def create_agent_graph():
    graph = StateGraph(AgentState)

    # 节点注册
    graph.add_node("router", router_node)
    graph.add_node("pipeline_flow", pipeline_flow_node)
    graph.add_node("retrieve_context", retrieve_context_node)
    graph.add_node("generate_candidates", generate_candidates_node)
    graph.add_node("solve_candidate", solve_candidate_node)
    graph.add_node("verify", verify_node)
    graph.add_node("annotate", annotate_node)
    graph.add_node("doc_flow", doc_flow_node)
    graph.add_node("recommend_flow", recommend_flow_node)

    # 路由
    graph.set_entry_point("router")
    graph.add_conditional_edges("router", route_by_task_type, {
        "pipeline": "pipeline_flow",
        "generate": "retrieve_context",
        "solve": "solve_candidate",
        "doc": "doc_flow",
        "recommend": "recommend_flow",
    })

    # 生成循环
    graph.add_edge("retrieve_context", "generate_candidates")
    graph.add_edge("generate_candidates", "solve_candidate")
    graph.add_edge("solve_candidate", "verify")
    graph.add_conditional_edges("verify", check_verification, {
        "passed": "annotate",
        "retry": "generate_candidates",
        "exhausted": END,
    })
    graph.add_edge("annotate", END)

    # 终止
    graph.add_edge("pipeline_flow", END)
    graph.add_edge("doc_flow", END)
    graph.add_edge("recommend_flow", END)

    return graph.compile()
```

### 4.2 Pipeline 子图

```python
class PipelineAgentState(TypedDict):
    file_path: str
    format_analysis: FormatAnalysis | None
    raw_extraction: RawExtraction | None
    normalized: list[NormalizedBlock]
    corrected: CorrectedContent | None
    content_units: list[ContentUnit]
    tagged_units: list[TaggedUnit]
    final_kps: list[KnowledgePoint]
    final_questions: list[Question]
    error: str | None

def create_pipeline_subgraph():
    graph = StateGraph(PipelineAgentState)

    graph.add_node("format_analyze", format_analyze_node)
    graph.add_node("content_extract", content_extract_node)
    graph.add_node("normalize", normalize_node)          # Python，非 LLM
    graph.add_node("ocr_correct", ocr_correct_node)
    graph.add_node("split", split_node)                  # Python，非 LLM
    graph.add_node("tag_content", tag_content_node)
    graph.add_node("build_models", build_models_node)    # Python，非 LLM

    graph.set_entry_point("format_analyze")
    graph.add_edge("format_analyze", "content_extract")
    graph.add_edge("content_extract", "normalize")
    graph.add_edge("normalize", "ocr_correct")
    graph.add_edge("ocr_correct", "split")
    graph.add_edge("split", "tag_content")
    graph.add_edge("tag_content", "build_models")
    graph.add_edge("build_models", END)

    return graph.compile()
```

---

## 5. 与 MVP 的关系

| MVP 模块 | Agent 系统对应 | 变更 |
|----------|---------------|------|
| `core/retriever.py` | QGen Agent retrieve 步骤 | 增加 sqlite-vec 向量检索 |
| `core/generator.py` | QGen Agent generate 步骤 | 包装为 LangGraph 节点 |
| `core/solver.py` | Solve Agent | 增加 SymPy 验算 |
| `core/verifier.py` | QGen Agent verify 步骤 | 增加 embedding 去重检查 |
| `core/annotator.py` | Tag Agent | 增加校准模型 |
| `pipeline/*_parser.py` | Pipeline Flow (Agent 7-10) | LLM 增强 + 通用格式支持 |
| `pipeline/ocr_repair.py` | OcrCorrector Agent (Agent 9) | 从确定性扩展为 LLM 辅助 |
| `pipeline/cross_page_detector.py` | OcrCorrector Agent (Agent 9) | 纳入跨页合并 |
| `pipeline/content_validator.py` | Pipeline Flow normalize 步骤 | 纳入标准化流程 |
| 无 | DocAssembly Agent | 全新模块 |
| 无 | Recommend Agent | 全新模块 |

---

## 6. 实现文件（MVP 实际映射）

| 蓝图 Agent | MVP 实现 | 状态 |
|---|---|---|
| Router (LangGraph) | `src/sciforge/agents/graph.py`（手写极简 StateGraph）+ `qgen/agent.py` 内置编排 | ✅ |
| QGen Agent | `src/sciforge/agents/qgen/agent.py` + retriever/generator/solver/verifier/annotator | ✅ |
| Solve Agent | `src/sciforge/agents/solve_agent.py` | ✅ |
| Tag Agent | `src/sciforge/agents/tag_agent.py` | ✅ |
| DocAssembly Agent | `src/sciforge/agents/doc_agent.py`（lite 版，markdown 输出） | ✅ |
| KB Agent | `src/sciforge/agents/stub_agents.py::KBAgent` | 🔲 骨架 |
| Recommend Agent | `src/sciforge/agents/stub_agents.py::RecommendAgent` | 🔲 骨架 |
| FormatAnalyzer Agent | 职责内置于 `src/sciforge/pipeline/recognizers.py`（`VlmRecognizer` / `PyMuPdfRecognizer` / `MarkdownRecognizer`） | ✅ 内置 |
| ContentExtractor Agent | 职责内置于 `pipeline/vlm_converter.py` + `stages/stage1_extract.py`（批量窗口提取） | ✅ 内置 |
| OcrCorrector Agent | 职责内置于 `stages/stage1_extract.py` + `stages/stage1_review.py`（两个 stage LLM 调用，修复 exercise 漏 solution bug） | ✅ 内置 |
| ContentTagger Agent | 职责内置于 `stages/stage2_annotate.py` + `stages/stage2_review.py`（两个 stage LLM 调用） | ✅ 内置 |

> **设计决策**：MVP 阶段不把 pipeline 5 阶段拆成独立 Agent 类，是为了保持流水线的
> 背压和 asyncio.Queue 协作效率；Agent 抽象（`BaseAgent.run()`）适合一次请求一次结果
> 的场景（生成/求解/讲义），不适合异步流式处理。
