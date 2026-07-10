# QGen Harness 架构与验证协议

> 当前实现：`src/sciforge/agents/qgen/` + `agents/runtime/` + conversation/run API。
> 对外模式：`new/evolve/variant/adjust_difficulty/deepen/solve/evaluate`。
> 目标：让题目、解答、评价与工具证据可恢复、可审计、可拒绝，而不是一次 prompt 的长文本。

## 1. 产品与安全边界

通用 “prompt → answer” 不能保证题目可解、答案相关、边界条件正确或形式化翻译忠实。QGen
因此把检索、创造、独立求解、确定性预筛、独立审稿、工具验证、难度标注与失败修正分成
不同职责。

系统对外提供完整题面、可教学的结构化解题步骤、方法摘要、验证证据和修正记录；不把模型
私有 raw chain-of-thought 存为模型字段、日志、checkpoint 或 API 响应。“详细过程”必须是
能够被学习者和验证器独立检查的产品内容。

## 2. 参考机制与采纳范围

| 一手来源 | 机制 | 当前采纳 |
|---|---|---|
| [Anthropic long-running agent harness](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | 小步任务、跨 session 交接、每次自验证 | graph node 作为 step；checkpoint 保存 cursor 与证据 |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | step snapshot、fault recovery、HITL/time travel | 自研轻量 StateGraph + immutable checkpoint；尚无通用 time-travel UI |
| [Magentic-One](https://arxiv.org/abs/2411.04468) | orchestrator + 专职 agents + 重新规划 | QGen 编排单责 generator/solver/verifier/evolver |
| [Tree of Thoughts](https://arxiv.org/abs/2305.10601) 与 [LATS](https://arxiv.org/abs/2310.04406) | 多候选探索、value feedback、回退 | 高风险/进化模式启用有界候选池；普通模式保持低延迟路径 |
| [FunSearch](https://www.nature.com/articles/s41586-023-06924-6) 与 [AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) | 可执行评价、lineage、候选选择 | 保存 parent/generation/directive；用确定性多目标加权选择，不宣称 Pareto search |
| [LeanDojo](https://github.com/lean-dojo/LeanDojo) 与 [DeepSeek-Prover-V2](https://github.com/deepseek-ai/DeepSeek-Prover-V2) | Lean retrieval/proving/subgoal | Lean 作为数学证明题的可选或 required 证据；编译成功不自动证明翻译忠实 |
| [gVisor](https://gvisor.dev/docs/) 与 [Firecracker](https://github.com/firecracker-microvm/firecracker) | hostile code 的宿主隔离 | 当前默认受限 OCI 容器；hardened multi-tenant executor 仍是部署增强项 |

这些来源提供设计原则，不代表 SciForge 已实现它们的全部功能。尤其当前没有通用 beam search、
Pareto archive、property-based hidden-case generator、gVisor/Firecracker 调度或 Lean 交互式 tactic
search；这些只列在后续路线中。

## 3. 对外模式与内部策略

| API mode | 输入 | 运行语义 |
|---|---|---|
| `new` | 领域、主题/指令、难度、type/form | B1 主题生成，走完整 QGen 核心路径 |
| `evolve` | 同 new | 开启失败诊断、有限 evolve 和候选池选择 |
| `variant` | 可见源题 + A1/A2/A3/A6 | 保留目标能力的实质变式并重新求解/验证 |
| `adjust_difficulty` | 源题 + 显式难度 | 目标升高→A4，降低→A5，相同→A1 |
| `deepen` | 源题 | B2 跨主题改写并启用 DeepSolveEngine |
| `solve` | 源题 id 或完整题面 | SolveAgent；API 策略固定 fast，不声称 independent verification |
| `evaluate` | 源题 + 可选评估指令 | 准备解答后运行独立 reviewer 与选定验证矩阵 |

`repair` 是失败诊断后的内部 evolve 路径，不是公开 ChatMode。公开请求在进入 LLM 前必须由
`DisciplineSpec` 校验 question type/form，并校验源题可见性与模式必填字段。

## 4. 当前 QGen 状态图

```text
retrieve → generate → next → prefilter → solve → verify → annotate
                       │         │          │        │          │
                       │         └ fail ────┴─fail → diagnose → evolve → solve
                       │                                  │
                       ├ no candidate / next round ←──────┘
                       └ population complete → select → END

annotate ──普通模式──────────────────────────────────────→ END
annotate ──population mode→ 收集已通过候选 → next/select
```

节点职责：

1. `retrieve`：质量门禁后的知识点、参考题、难度锚点与负样本；显式 `knowledge_ids` 仍需过门；
2. `generate`：按学科和 B1/B2/A1–A6 策略产生有界候选批；
3. `next`：选择下一候选或决定再生成/进入 selection/结束；
4. `prefilter`：长度、数学分隔符/括号配平、题面形式和负样本相似度的 0-LLM 拒绝；
5. `solve`：配置决定使用 SolveEngine 或 DeepSolveEngine；节点名保持稳定；
6. `verify`：solver 置信度、完整题答相关性、独立 reviewer 与 policy 工具证据；
7. `annotate`：10 维难度与目标差；可复用 reviewer 合并结果以减少一次 LLM；
8. `diagnose/evolve`：把 solver/reviewer 失败转成具体 mutation directive，再从 solve 验证；
9. `select`：只在已通过候选中按加权目标稳定排序。

图有 max-step 保险丝；round、candidate、improvement 数以及 LLM/token/wall-time 都受上限约束。

## 5. Context 与对话输入

QGen context 不是整库 dump：

- 按 discipline、canonical subject、difficulty window、type/form 和质量门禁检索；
- 上限控制 knowledge/reference/negative 数量；
- 显式 pins 使用稳定 id，不用用户显示文本替代 id；pins 不要求 subject 完全相同，但仍要求
  discipline 与质量资格；
- Chatbox 在写当前命令前快照最近历史，最多 8 条、每条 1200 字符、总计 6000 字符；
- 快照作为 durable `GenerationRequest` 的不可变输入，恢复不会读到后来新增的消息；
- 历史被标为不可信上下文，只提供约束/指代，不允许覆盖 system、验证或沙箱规则。

## 6. Long-horizon 与恢复

`SQLiteRunStore` 使用独立 `qgen_runtime.db`，一次 run 保存：

- kind/status/input/output/metadata；
- step、LLM calls、tokens、wall time 四类预算与累计用量；
- 单 run 最多一个 running step 的原子 claim；
- 每个 QGen 完成节点后的 immutable checkpoint：state、trace、`next_node` cursor；
- append-only、按 run 单调 sequence 的 event；
- cooperative cancel flag。

恢复规则：中断的 running step 标为 interrupted，从最新 checkpoint 的 `next_node` 继续；已完成
节点直接使用 checkpoint，不再次调用 LLM。成功 run 的再次读取直接返回 output。API 启动时
自动调度 pending/running run；budget paused 必须由用户增加预算后 resume。QGen 提供图节点粒度
checkpoint；solve/evaluate 同样持久化并可重建恢复，但不是同一套多节点 QGen graph 粒度。

产品交付与 Agent 成功分开：成功 run 还需幂等地 materialize 生成题、写 assistant final message、
artifact 和 usage。启动 reconciliation 会补做缺失交付；`question.persisted`、稳定 message id 与
账单事件避免重复发布/计费。

CLI 只有 `generate durable` 默认启用这套 graph checkpoint，并配套 `generate resume/cancel`；
`free/variant/evolve/solve-deep` 是直接交互命令，不应误写成全部自动持久化。

## 7. Self-evolve 与候选选择

当前 evolve 是有界、证据驱动的局部搜索：

1. `CandidateRecord` 保存 `parent_id/generation/improvement_strategy/
   improvement_directive/failure_reasons`；
2. solver 失败与 reviewer suggestions 被转换为 solver-或verification-oriented prompt；
3. 新候选先重走 solve 与完整 verify，不能只相信“已修复”说明；
4. content/negative similarity 与结构 prefilter 抑制重复和泄漏；
5. improvement 次数、候选数和总 run budget 共同终止搜索。

`population.select_candidate` 当前不是 Pareto 算法。它对已通过候选计算：

```text
overall = correctness × (
  0.35 × difficulty_fit
  + 0.25 × novelty
  + 0.20 × pedagogical_value
  + 0.20 × verification_strength
)
```

然后以 overall、verification strength、difficulty fit、candidate id 稳定 tie-break。未来若加入
成本维度、novelty champion 或 Pareto archive，必须先扩充持久化 objective contract 与测试。

## 8. 验证策略

### 8.1 API policy 映射

| policy | solution relevance | 数值验证 | Lean |
|---|---:|---|---|
| `fast` | 否 | 关闭 | 关闭 |
| `balanced` | 是 | 数值题启用、非 required | 关闭 |
| `strict` | 是 | 数值题启用且 required | 数学证明题启用且 required |
| `formal` | 是 | 关闭 | 仅数学证明类，启用且 required |

`formal` 对非数学证明题在请求边界返回 422。require 配置如果没有同时 enable，会在配置或
verifier 构造时 fail fast。

### 8.2 四态证据

每项 `VerificationEvidence` 只能是：

- `passed`：验证器得到肯定证据；
- `failed`：得到反例、编译拒绝或明确否定；
- `unavailable`：容器、解释器、Lean 或依赖不存在；
- `error`：执行、超时或结果解析失败。

required evidence 只有 `passed` 才允许发布；unavailable/error 不能被降级为通过。Lean 编译成功
只证明生成的形式化 theorem/source，independent reviewer 仍需检查自然语言题面、形式化声明和
解答之间的一致性。

### 8.3 题型检查矩阵

| 题型 | 当前核心检查 | 可扩展强证据 |
|---|---|---|
| 数值/代数 | 完整解答、相关性、生成的数值验证程序 | SymPy/NumPy、边界/property cases |
| 数学证明 | 假设/结论、完整 reviewer、可选 Lean compile | 反例搜索、交互 tactic |
| 物理计算 | 解答/相关性、数值程序 | 量纲/守恒专用 verifier |
| 几何 | 结构/reviewer | 坐标退化样例、图形约束 verifier |
| 化学 | 结构/reviewer | 计量、电荷、原子守恒工具 |
| 程序 | 题面形式与 reviewer | 编译、sample/hidden/property tests |
| 实验设计 | 变量/对照/可观测 reviewer | 领域量规和人工复核 |

表中“可扩展”不是当前已实现声明；目前通用 numerical runner 执行 verifier 生成的 Python，Lean
runner 编译 formalizer 生成的 source。

## 9. 沙箱信任边界

| 策略 | 允许输入 | 当前实现与限制 |
|---|---|---|
| `restricted_container`（默认） | LLM/用户生成 Python 或 Lean | Docker/Podman，禁网、只读 rootfs、cap-drop ALL、no-new-privileges、非 root、CPU/内存/pids/tmpfs/wall/output 限制 |
| `trusted_local` | 仅仓库内人工审核代码 | 无 shell 的 argv subprocess + 临时目录/资源限制；不是 hostile-code 边界 |
| hardened multi-tenant | 互不信任租户代码 | **未内置**；部署应增加 gVisor/Firecracker 与宿主 egress policy |

runner 不通过 shell 拼命令，不挂载宿主工作区，不把 secret 注入环境，并截断 stdout/stderr。
容器或编译器不存在时返回 unavailable，而不是自动切换到不安全本地执行。

## 10. 真实事件协议

SSE 只传已经持久化的 append-only event，当前主要事件为：

- runtime：`run.created/started/paused/succeeded/failed/cancel_requested/cancelled/
  resume_prepared/resume_requested/recovery_scheduled`；
- step/budget：`step.started/succeeded/failed/interrupted`、`usage.recorded`、
  `budget.updated/exhausted`、`checkpoint.saved`；
- QGen：`graph.transition`、`qgen.progress`；
- 产品：`solution.ready`、`evaluation.solution_ready/evaluation.verified`、
  `question.persisted`、`assistant.final/error/cancelled/paused`；
- 异常审计：`billing.record_failed`、`product.delivery_failed/postprocess_failed/
  reconciliation_failed`。

当前没有 token 级 `message.delta`、独立 `candidate.*`、`tool.*` 或通用 `artifact.created` 事件，
文档和前端不得监听虚构协议。客户端携带 `Last-Event-ID`；服务端按 sequence 补发，并每 15 秒
发送 heartbeat。断线补读不会重跑 Agent。

## 11. 发布准则

生成题只有同时满足以下条件才 materialize 并显示 verified：

1. request 的领域/type/form 与源题权限合法；
2. context 通过知识库质量门禁；
3. deterministic prefilter 通过；
4. solver 输出非 low-confidence、非空的可教学解答；
5. independent reviewer（适用模式）看到完整题面与完整解答并通过；
6. policy 的所有 required evidence 均为 passed；
7. 难度标注、验证证据、lineage、模型、用量和 run event 已持久化；
8. 题目按 tenant 作用域去重并成功写入，assistant final 与 artifact 可幂等重建。

“调用过 verifier”“产生过 Lean 文本”或“run succeeded”都不单独等于已发布。产品交付失败必须
保留运行结果和 reconciliation 事件，不能靠前端临时文本掩盖。

## 12. 验收

```bash
uv run pytest tests/test_agents tests/test_api
uv run pytest tests/test_storage/test_evaluation.py
uv run ruff check src tests
uv run mypy
uv run pytest
```

恢复测试必须证明已完成节点没有再次调用 LLM；沙箱测试必须覆盖 unavailable/error；策略测试
必须覆盖 formal 非适用拒绝和 strict/formal required fail-closed；浏览器 smoke test 必须覆盖 SSE
重连、暂停/恢复、四态证据和生成题继续变式。
