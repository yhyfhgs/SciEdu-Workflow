# 16 Agent 系统 MVP

> SciForge 多 Agent 协作系统的当前实现状态和扩展指南

## 1. 架构概览

SciForge 的 Agent 系统分为：

1. **基础设施**（`src/sciforge/agents/`）：BaseAgent 抽象、AgentState 共享状态、极简手写 StateGraph
2. **十大 Agent**（按 [`Docs/09_Agent编排系统.md`](./09_Agent编排系统.md)）：
   - MVP 完整实现：QGen / Solve / Tag / DocAssembly-lite
   - Post-MVP 骨架：KB / Recommend / FormatAnalyzer / ContentExtractor / OcrCorrector / ContentTagger
3. **学科参数化**：每个 Agent 接受 `discipline: Discipline`，从 `DisciplineRegistry` 查 spec、加载按学科注入后的 prompt

## 2. 当前实现状态

| # | Agent 名 | 状态 | 文件 | 备注 |
|---|---|---|---|---|
| 1 | KB | 🔶 骨架 | `stub_agents.py::KBAgent` | 依赖 embedding，Post-MVP |
| 2 | **QGen** | ✅ 完整 | `qgen/agent.py` | 5 阶段闭环，MVP 核心 |
| 3 | **Solve** | ✅ 完整 | `solve_agent.py` | 复用 `qgen/solver.py` |
| 4 | **Tag** | ✅ 完整 | `tag_agent.py` | 复用 `qgen/annotator.py` |
| 5 | **DocAssembly** | ✅ Lite | `doc_agent.py` | 生成 markdown 讲义，无 LaTeX 编译 |
| 6 | Recommend | 🔶 骨架 | `stub_agents.py::RecommendAgent` | 需用户系统，Post-MVP |
| 7 | FormatAnalyzer | ✅ **内置** | 职责在 `pipeline/recognizers.py` | 非独立 Agent 类；`stub_agents.py::FormatAnalyzerAgent` 仅为 Post-MVP 骨架 |
| 8 | ContentExtractor | ✅ **内置** | 职责在 `pipeline/vlm_converter.py` + `stages/stage1_extract.py` | 非独立 Agent 类 |
| 9 | OcrCorrector | ✅ **内置** | 职责在 `stages/stage1_extract.py` + `stages/stage1_review.py`（两个 stage LLM） | 非独立 Agent 类 |
| 10 | ContentTagger | ✅ **内置** | 职责在 `stages/stage2_annotate.py` + `stages/stage2_review.py`（两个 stage LLM） | 非独立 Agent 类 |

> **术语澄清**：7-10 号"职责内置"不是 🔶 骨架；它们的逻辑已经以 pipeline 模块的形式
> 完整实现，只是没有封装成 `BaseAgent` 子类。详见"Pipeline 与 Agent 系统分离原则"。

## 3. BaseAgent 抽象

所有 Agent 继承自 `BaseAgent[TIn, TOut]`（`src/sciforge/agents/base.py`）：

```python
from sciforge.agents import BaseAgent
from sciforge.core.models import Discipline
from pydantic import BaseModel

class MyInput(BaseModel):
    data: str

class MyOutput(BaseModel):
    result: str

class MyAgent(BaseAgent[MyInput, MyOutput]):
    name = "my_agent"              # 必填，日志事件名前缀
    prompt_file = "solving/solve.txt"  # 可选，BaseAgent 会自动加载

    async def run(self, input: MyInput) -> MyOutput:
        self._log_start(data=input.data)
        # 使用 self.llm / self.discipline / self.discipline_spec / self._prompt_template
        result_dict = await self._call_llm_json(prompt="...", max_attempts=3)
        self._log_end(result=result_dict)
        return MyOutput(result=str(result_dict))
```

**内置能力**：
- `self.llm` — `LLMClient` 实例
- `self.discipline` — 当前 `Discipline` 枚举
- `self.discipline_spec` — 对应的 `DisciplineSpec`（含 `prompt_vars`）
- `self._prompt_template` — 从 `prompt_file` 加载的原始模板
- `self._call_llm_json(prompt, max_attempts, ...)` — 统一重试 + JSON 解析
- `self._log_start(**kwargs)` / `self._log_end(**kwargs)` / `self._log_error(err, **kwargs)` — structlog 事件，自动附加 `discipline` 和 `agent_name` 字段

## 4. QGenAgent 5 阶段详解

QGen 是 MVP 的核心引擎，组合 5 个子组件：

```
QGenAgent.run(request)
    ↓
[Stage 1] ContextRetriever.build_context(request, discipline)
    → 从 SQLiteStorageBackend 检索：
      - 按 discipline + subject + difficulty_range 过滤知识点
      - 按 discipline + subject + 难度窗口筛选参考题 (±2)
      - 难度锚点：一道偏简单 + 一道偏难
      - 变式模式的负样本
    ← ContextBundle (knowledge_points, reference_questions, negative_examples)
    ↓
[Stage 2] QuestionGenerator.generate_candidates(n=3)
    → 按学科加载 free/variant_generation.txt（已做词语替换）
    → 温度递增调 LLM N 次
    → 兼容 content_latex / content 两种 JSON 输出 key
    ← list[Question]（is_generated=True, discipline 已设置）
    ↓
[Stage 3] SolveEngine.solve(candidate, context)
    → 加载 solving/solve.txt（按学科替换）
    → 注入前 5 个 KP 作为 context
    ← Solution (confidence: high/medium/low)
    ↓ (低置信度跳过下一个候选)
[Stage 4] VerificationPipeline.verify(candidate, solution)
    → Check 1: solve_confidence ≠ low
    → Check 2: 独立 review LLM 审核（避免确认偏差）
    → Check 3: non_trivial（题干长度 > 30）
    ← VerificationResult (passed, checks, suggestions)
    ↓ (失败时把 suggestions 写回 context.feedback_from_previous_round，继续下一轮)
[Stage 5] DifficultyAnnotator.annotate(candidate, solution)
    → 10 维评分（concept_count, step_count, abstraction_level, ...）
    → 按 DifficultyScore.WEIGHTS 加权 → overall
    ← DifficultyScore
    ↓
QGenOutput(generated=GeneratedQuestion(...), rounds_used, candidates_tried)
```

**参数**：
- `MAX_ROUNDS = 3`（类常量，可在实例化后修改）
- `CANDIDATES_PER_ROUND = 3`

## 5. 极简 StateGraph

`src/sciforge/agents/graph.py` 提供了一个**不依赖 LangGraph** 的状态图实现，API 仿 LangGraph 子集：

```python
from sciforge.agents import END, StateGraph

async def retrieve(state): return {"context": "..."}
async def generate(state): return {"question": "..."}
async def verify(state): return {"passed": True}

graph = StateGraph()
graph.add_node("retrieve", retrieve)
graph.add_node("generate", generate)
graph.add_node("verify", verify)

graph.set_entry_point("retrieve")
graph.add_edge("retrieve", "generate")
graph.add_edge("generate", "verify")
graph.add_conditional_edges(
    "verify",
    lambda s: "pass" if s["passed"] else "retry",
    {"pass": END, "retry": "generate"},
)

compiled = graph.compile()
final = await compiled.ainvoke({}, max_steps=100)
```

**功能边界**：
- ✅ Async 节点、线性边、条件边、END 终止、max_steps 保险丝
- ❌ 持久化、human-in-the-loop、fanout/fanin、重试 hooks

**升级路径**：当 MVP 阶段结束需要更复杂的编排时，把 `from sciforge.agents import StateGraph` 改为 `from langgraph.graph import StateGraph`，API 兼容绝大多数用法。

## 6. CLI 入口

```bash
# 5 阶段完整生成
uv run sciforge generate free \
  -s "real_analysis:实分析" \
  -t "math:数学,analysis:数学分析,continuity:连续性" \
  -d 6 --type proof \
  -D math \
  --db data/sciforge.db

# 对已有题目生成详细解答
uv run sciforge generate solve <question_id> -D math --db data/sciforge.db

# 批量生成
uv run sciforge generate batch -t "math:数学" -n 5

# 生成 markdown 讲义
uv run sciforge generate lecture \
  -t "math:数学,analysis:数学分析" \
  --title "连续函数专题" \
  -o data/lectures/ \
  -D math
```

## 7. 编写新 Agent

1. 定义 Pydantic Input/Output 模型（放在 agent 文件顶部，不必进 `core/models.py`）
2. 继承 `BaseAgent[MyInput, MyOutput]`
3. 设置类属性 `name`（必填）和 `prompt_file`（可选）
4. 实现 `async def run(self, input: MyInput) -> MyOutput`
5. 在 `src/sciforge/agents/__init__.py` 导出
6. 写单元测试：至少验证（a）实例化、（b）`run()` 能跑通、（c）不同 discipline 下行为正确

**参考 SolveAgent**（`src/sciforge/agents/solve_agent.py`）作为最简单的完整示例。

## 8. 学科参数化贯通

每个 Agent 都透传 `discipline` 到底层：

```
CLI --discipline math
  → QGenAgent(discipline=MATH)
    → QuestionGenerator(discipline=MATH)
      → get_generation_prompt_template("generation/free_generation.txt", MATH)
        → (math) 返回原文
    → SolveEngine(discipline=MATH)
      → get_generation_prompt_template("solving/solve.txt", MATH)
    → ...
```

新学科接入时，关键改动都在 `src/sciforge/disciplines/` 和 `_GENERATION_PROMPT_SUBSTITUTIONS`，**不需要改 Agent 代码**。参见 [`Docs/15_学科扩展指南.md`](./15_学科扩展指南.md)。

## 9. Pipeline 与 Agent 系统分离原则

`src/sciforge/pipeline/` 和 `src/sciforge/agents/` 是**两个平行子系统**，MVP 阶段有意
不互相继承：

| 维度 | Pipeline | Agent 系统 |
|---|---|---|
| 入口形态 | async Producer-Consumer 流水线 + 双 asyncio.Queue | `BaseAgent.run(input) -> output` 单次调用 |
| 典型场景 | 批量文档处理，背压 + checkpoint + 增量恢复 | 一次题目生成、一次讲义组装 |
| LLM 调用特点 | Stage1/Stage2 共四类 LLM 调用，批量/并发处理，schema 严格重试 | 每次少量调用，可混合温度/重试策略 |
| 状态管理 | `FileExtractionState`（磁盘 `state.json`） | `AgentState`（内存 dict） |
| 基类 | 无统一基类（各 Task 独立） | `BaseAgent[TIn, TOut]`（discipline 注入） |
| 编排器 | `PipelineOrchestrator` | `StateGraph`（手写）+ 直接串联 |

**边界**：Task2（pipeline 标注） 在职责上和 Tag Agent 重叠，但 **Tag Agent 用于对已入库
题目补标签，Task2 用于新入库的流式标注**。两者底层都调 `get_annotate_stage2_initial_template`
但封装方式不同。

---

## 10. Post-MVP 路线

- **Agent 1 KB**: 集成 `sqlite-vec` 做 embedding 检索 + 前置依赖图
- **Agent 6 Recommend**: 需要先有用户系统
- **Agent 7-10**: 将当前 pipeline 内置的功能拆分成独立 Agent，用真 LangGraph 编排
- **DocAssembly 完整版**: 接入 Jinja2 模板 + XeLaTeX 编译，输出 PDF
- **QGen 进化**: 变式生成 (`generate variant`) + 进化模式 (`generate evolve`)
- **Solve 深度**: 接入 sandbox + tool calling (`generate solve-deep`)

见 [`Docs/09_Agent编排系统.md`](./09_Agent编排系统.md) 的完整蓝图。
