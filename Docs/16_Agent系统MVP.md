# 16 Agent 系统与 QGen Harness

> SciForge 多 Agent 协作系统、题目专用可恢复 Harness 的当前实现状态和扩展指南。
> 形式化协议、研究依据与发布门禁见
> [`22_QGen_Harness_架构与验证协议.md`](./22_QGen_Harness_架构与验证协议.md)。

## 1. 架构概览

SciForge 的 Agent 系统分为：

1. **基础设施**（`src/sciforge/agents/`）：BaseAgent、共享状态、可从 node boundary 恢复的手写 StateGraph
2. **持久运行时**（`src/sciforge/agents/runtime/`）：run/step/budget/checkpoint/event/cancel/resume，
   使用独立 SQLite，不污染教育内容 schema
3. **十大 Agent**（按 [`Docs/09_Agent编排系统.md`](./09_Agent编排系统.md)）：
   - 已实现：QGen Harness / Solve / Tag / DocAssembly-lite
   - Post-MVP 骨架：KB / Recommend / FormatAnalyzer / ContentExtractor / OcrCorrector / ContentTagger
4. **学科参数化**：每个 Agent 接受 `discipline: Discipline`，从 `DisciplineRegistry` 查 spec、加载按学科注入后的 prompt

## 2. 当前实现状态

| # | Agent 名 | 状态 | 文件 | 备注 |
|---|---|---|---|---|
| 1 | KB | 🔶 骨架 | `stub_agents.py::KBAgent` | 依赖 embedding，Post-MVP |
| 2 | **QGen** | ✅ Harness | `qgen/agent.py` + `qgen/*` + `runtime/*` | 状态图、进化、sandbox 验证、候选选择、可恢复运行 |
| 3 | **Solve** | ✅ 完整 | `solve_agent.py` | 复用 `qgen/solver.py` |
| 4 | **Tag** | ✅ 完整 | `tag_agent.py` | 复用 `qgen/annotator.py` |
| 5 | **DocAssembly** | ✅ Lite | `doc_agent.py` | 生成 markdown 讲义，无 LaTeX 编译 |
| 6 | Recommend | 🔶 Agent 骨架 / ✅ 算法引擎 | `stub_agents.py::RecommendAgent` / `recommend/engine.py` | API 已用无 LLM 推荐引擎；BaseAgent 封装仍未做 |
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

## 4. QGenAgent 题目专用 Harness

QGen 不再是固定五步直线，而是一个有界状态图。默认低延迟路径仍保持五个核心职责；
高难、失败修复或显式配置时才启用 deep solve、evolve 和 population selection：

```text
retrieve → generate → next → deterministic prefilter → solve/deep-solve → verify → annotate
                                │                         │          │
                                └──────── reject ─────────┴→ diagnose → evolve → solve

annotate ── first-pass mode → END
annotate ── population mode → 收集所有通过候选 → multi-objective select → END
```

### 4.1 核心职责

1. `ContextRetriever`：按 discipline/subject/difficulty 从知识库取知识点、参考题、难度锚点和
   变式负样本。认证 API 会先把 storage 换成 tenant-scoped view。
2. `QuestionGenerator`：按 `GenerationRequest.strategy` 选择自由/变式模板，生成有界候选集。
3. `prefilter_candidate`：用长度、数学分隔符/括号配平和负样本相似度做 0 LLM 淘汰。
4. `SolveEngine` 或 `DeepSolveEngine`：独立求解；deep 模式允许多轮 tool calling，但
   `execute_python` 必须经过 verification sandbox。
5. `VerificationPipeline`：独立 reviewer 同时读取完整题面和完整解答；按 policy 追加
   solution relevance、数值代码和 Lean 证据。
6. `DifficultyAnnotator`：读取 reviewer 合并的 10 维难度，或在未合并时单独标注。
7. `Evolver`：把 solver/reviewer 失败转成具体 mutation directive，保存 parent/generation/
   strategy/directive lineage，再从 solve 节点重新验证。
8. `population.select_candidate`：先以 `verification.passed` 作 correctness 硬门禁，再按
   difficulty fit、novelty、pedagogical value、verification strength 做确定性稳定排序。

### 4.2 验证不是一个布尔字段

每项 `VerificationEvidence` 的状态是 `passed / failed / unavailable / error`：

- 数值代码执行失败、沙箱未安装、Lean 编译器不可用和超时不可映射为“通过”；
- `require_numerical_verification` / `require_lean_verification` 开启后，只有 `passed` 可发布；
- Lean 编译通过只证明生成的形式化 theorem，仍需 reviewer 检查自然语言题面与形式化翻译一致；
- independent reviewer 必须看到完整题目与完整 solution，不能只看答案摘要。

`verification_sandbox.py` 提供两种本地实现边界：

- `container_required`（默认）：Docker/Podman，禁网、只读、drop all capabilities、
  `no-new-privileges`、非 root，并限制 CPU/内存/pids/tmpfs/wall time/output；
- `trusted_local`：只允许仓库内人工审核的验证代码，不能执行 LLM/用户任意代码。

生产多租户仍需在容器外增加 gVisor/Firecracker 与宿主 egress policy；当前代码没有把普通
Docker 声称为 hardened multi-tenant sandbox。

### 4.3 输出边界

用户可获得完整题目、可教学的结构化解题步骤、方法摘要、量规、验证器输入输出摘要、
Lean diagnostics 和失败修正记录。模型私有原始 chain-of-thought 不作为模型字段、日志、
checkpoint 或 API 产品输出。

### 4.4 配置入口

`QGenAgent.from_config()` 是 CLI/API 的统一构造入口，绑定：

- round/candidate/retrieval 上限；
- lightweight prefilter 与 verifier-difficulty 合并；
- evolve 轮数、population selection、deep solve/最大轮次；
- 数值与 Lean 的 enable/require policy；
- sandbox runtime、镜像、信任策略与资源上限。

直接调用构造器仍可用于测试，但产品入口不得各自漏传验证或安全开关。

## 5. StateGraph 与持久运行时

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

**StateGraph 能力边界**：

- ✅ async 节点、线性/条件边、END、max_steps、`start_at`、`completed_steps`、
  node enter/exit/transition hooks；
- ❌ 原生 fanout/fanin、通用 human-in-the-loop UI、分布式调度。

持久化由 `agents/runtime` 和 `DurableQGenHarness` 组合完成，而不是塞进通用图类：

- `SQLiteRunStore` 使用独立 `qgen_runtime.db`，WAL + 事务保护；
- run 状态机与每 run 最多一个 running step 的原子 claim；
- step、LLM calls、tokens、wall time 四类预算；
- 每个完成节点写 immutable checkpoint（graph snapshot + trace + `next_node` cursor）；
- append-only 单调 event，供 SSE/审计复用；
- cooperative cancel 与 `prepare_resume()`：中断 step 标为 interrupted，从最近 checkpoint 的
  next node 恢复，已完成节点不再次调用 LLM；
- 成功 run 重复读取直接返回持久 output，避免副作用重放。

```python
from sciforge.agents.qgen import DurableQGenHarness
from sciforge.agents.runtime import RunBudget, SQLiteRunStore

store = SQLiteRunStore("data/qgen_runtime.db")
harness = DurableQGenHarness(agent, store)
run_id, output = await harness.run(
    qgen_input,
    budget=RunBudget(max_steps=80, max_llm_calls=30, max_tokens=120_000),
    metadata={"tenant_id": tenant_id},
)
```

通用 HITL/time-travel/分布式 worker 仍是扩展项；不要仅为品牌/API 相似度把当前实现描述成
LangGraph，也不要假设可以无改动替换。

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

# A1-A6/B1-B2 变式
uv run sciforge generate variant <question_id> --strategy A1 -D math

# 有界进化：失败证据驱动改题，最多 2 次定向改进并做候选池选择
uv run sciforge generate evolve \
  -t "math:数学,analysis:数学分析" -d 7 --type proof --max-improve 2

# 深度求解：多轮 tool calling；代码执行服从 AppConfig sandbox policy
uv run sciforge generate solve-deep <question_id> -D math --db data/sciforge.db
```

上述 `free/variant/evolve/solve-deep` 是直接交互命令。需要 node-boundary pause/resume、硬预算
和持久事件时，使用：

```bash
uv run sciforge generate durable -t "math:数学,analysis:数学分析" -d 7 --evolve
uv run sciforge generate resume <run_id>
uv run sciforge generate cancel <run_id>
```

三条命令默认共享 `data/qgen_runtime.db`，也可统一传 `--runtime-db`。不要把“durable CLI 已实现”
误写成“所有 generate 子命令默认持久化”。

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
| 入口形态 | Task0 + 5 Stage + 4 个 asyncio.Queue | `BaseAgent.run()`；QGen 可由 DurableQGenHarness 驱动 |
| 典型场景 | 批量文档处理，背压 + checkpoint + 增量恢复 | 题目生成/求解/讲义；long-horizon pause/resume |
| LLM 调用特点 | 四类 stage 调用，按风险跳过/批量/并发，schema retry | 专职 generator/solver/reviewer/evolver；按 policy 调 sandbox |
| 状态管理 | `FileExtractionState`（每文档 `state.json`） | graph state；可选独立 runtime SQLite checkpoint/event |
| 基类 | 无统一基类（各 Task 独立） | `BaseAgent[TIn, TOut]`（discipline 注入） |
| 编排器 | `PipelineOrchestrator` | `StateGraph` + `DurableQGenHarness` |

**边界**：Task2（pipeline 标注） 在职责上和 Tag Agent 重叠，但 **Tag Agent 用于对已入库
题目补标签，Task2 用于新入库的流式标注**。两者底层都调 `get_annotate_stage2_initial_template`
但封装方式不同。

---

## 10. 剩余路线与明确限制

- **Agent 1 KB**: 集成 `sqlite-vec` 做 embedding 检索 + 前置依赖图
- **Agent 6 Recommend 封装**: `recommend/engine.py` 与 API 已实现无 LLM 算法；如需统一编排，
  再封装 `BaseAgent`，不要重复实现推荐逻辑
- **Agent 7-10**: 当前职责已在 pipeline 内置；只有出现独立运行/HITL 的真实需求时再拆 Agent
- **DocAssembly 完整版**: 接入 Jinja2 模板 + XeLaTeX 编译，输出 PDF
- **验证增强**: property-based hidden cases、题型专用 deterministic verifier、自然语言↔Lean
  翻译一致性审核与 hardened multi-tenant executor
- **运行时增强**: human-in-the-loop 审批、分布式 worker lease、checkpoint time-travel UI
- **发布纪律**: sandbox unavailable/error、required 检查未通过、低质量知识库 context 均不得
  被 UI 的“已验证”徽章掩盖

见 [`Docs/09_Agent编排系统.md`](./09_Agent编排系统.md) 的完整蓝图。

## 11. 变更验收

```bash
# QGen、runtime、sandbox、verifier 与 API 绑定
uv run pytest tests/test_agents tests/test_api

# 静态检查
uv run ruff check src tests
uv run mypy src/sciforge

# 全库回归
uv run pytest
```

涉及 sandbox 的测试必须断言 unavailable/error 路径；不能因为 CI 没有 Docker/Lean 就删除这些
状态断言。涉及恢复的测试必须证明已完成节点没有再次调用 LLM，而不只检查最终输出相等。
