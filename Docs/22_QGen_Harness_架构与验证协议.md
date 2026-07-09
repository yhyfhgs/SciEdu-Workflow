# QGen Harness 架构与验证协议

> 目标：为“出题”而不是通用聊天定制的可恢复 Agent Harness
> 范围：新题、变式、加深、难度调整、求解、评估、修复、数值/代码/Lean 验证
> 状态：与 `agents/qgen/`、`agents/runtime/` 和 API conversation/run 协议同步演进

## 1. 为什么需要题目专用 Harness

通用的“prompt → answer”对出题不够：题面可以语法通顺却无解，解答可以方向相关却在
边界情形失效，难度可以评分正确却与用户知识库无关。因此 Harness 必须将创造、
独立求解、环境验证、审稿和选择分成不同角色，并用可重放事件串联。

系统不对外暴露模型的私有原始 chain-of-thought。用户看到的“详细思考过程”是可审计的
结构化解题步骤、方法选择摘要、验证证据与失败修正记录；它们可以被独立检查，
而不是一段无法验证的隐式推理日志。

## 2. 外部案例与采纳决策

| 一手案例 | 可复用机制 | SciForge 决策 |
|---|---|---|
| [Anthropic: Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | 小步任务、跨 session 交接产物、每次自验证 | 每个 graph node 独立 step/checkpoint；恢复时先读 cursor 和最近证据 |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | 每个 step 保存 state snapshot，支持 fault recovery/HITL/time travel | 保留极简 `StateGraph`，实现 immutable checkpoint + next-node cursor + append-only event |
| [Magentic-One](https://arxiv.org/abs/2411.04468) | Orchestrator 跟踪进展、重新规划、调度专用 Agent | QGen 是 orchestrator；generator/solver/verifier/evolver 只拥有单一职责 |
| [Tree of Thoughts](https://arxiv.org/abs/2305.10601) / [LATS](https://arxiv.org/abs/2310.04406) | 多路径探索、value feedback、回溯 | 只在高难/高风险模式开启有界 beam/tree，普通题保持线性低延迟 |
| [AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) / [FunSearch](https://www.nature.com/articles/s41586-023-06924-6) | 多样化候选池、可执行 evaluator、保留高分与新颖候选 | self-evolve 保留 lineage/objective vector，不是对同题无限改写 |
| [LeanDojo](https://github.com/lean-dojo/LeanDojo) / [DeepSeek-Prover-V2](https://github.com/deepseek-ai/DeepSeek-Prover-V2) | Lean 交互、retrieval、subgoal decomposition | Lean 是可选强证据；编译成功只证明形式化 theorem，不自动证明自然语言题面翻译正确 |
| [gVisor](https://gvisor.dev/docs/) / [Firecracker](https://github.com/firecracker-microvm/firecracker) | 对 LLM/用户代码建立宿主隔离边界 | 本地 subprocess 仅允许 trusted code；不受信代码需要无网络只读容器，生产多租户推荐 gVisor/Firecracker |
| [Hypothesis](https://hypothesis.readthedocs.io/en/latest/) | property-based 输入生成、失败缩减与重放 | 数值/程序题不只测一个样例，保存 seed/counterexample |

## 3. 功能模式

| mode | 输入 | 输出 | 必经验证 |
|---|---|---|---|
| `generate` | 学科、主题、难度、题型、知识范围 | 新题+解答+评分 | solver + independent review |
| `variant` | 源题 + A1–A6/B1–B2 变式策略 | 保留目标能力的实质变式 | 去重/泄漏 + solver + review |
| `deepen` | 题目/解答 + 深度预算 | 多方法或更高层推广 | deep solve + tool evidence |
| `calibrate` | 题目 + 目标难度 | 条件/表示/推理深度的定向改写 | 重新求解 + 10 维难度差 |
| `solve` | 题目 | 可教学的分步解答 | relevance + review；可选数值/Lean |
| `evaluate` | 题目 + 可选解答 | 正确性、难度、认知层级、新颖度、可教学性 | reviewer + deterministic rubric |
| `repair` | 失败候选 + 证据 | 最小修复后的下一代 | 原失败检查必须翻转 |

## 4. 状态图

```text
plan
  → retrieve_quality_gated_context
  → propose_population
  → deterministic_prefilter
  → independent_solve ── high-risk → deep_solve/tools
  → verification_matrix
       ├─ structure / duplicate / leakage
       ├─ symbolic + numeric + property tests
       ├─ code sandbox
       ├─ optional Lean compile
       └─ independent full-solution review
  → score_objectives
       ├─ pass → select → annotate → finalize
       ├─ repairable → diagnose → evolve → solve
       └─ invalid → discard → next / regenerate
```

每个节点必须是可重试的幂等单元。对外部有副作用的操作（入库、用量、导出）只能在
checkpoint 确认后执行一次，并以 run/step id 作幂等键。

## 5. Long-horizon 执行协议

`agents/runtime` 使用独立 SQLite，不污染教育内容 schema。一次 run 包含：

- `run_id/kind/status/input/output/metadata`；
- steps、LLM calls、tokens、wall time 四类硬预算；
- 单 run 最多一个 running step 的原子 claim；
- immutable checkpoint：`step_number + next_node + serialized state + cursor`；
- append-only event：为 SSE 和事后调试提供单调 sequence；
- cooperative cancellation：节点边界检查 cancel flag，不在事务/子进程中间破坏状态。

恢复规则：读取 latest checkpoint 的 `next_node`，将上次中断的 running step 标记为 interrupted，
从节点边界继续。已完成节点的输出必须来自 checkpoint，不再调用 LLM。

## 6. Self-evolve 协议

self-evolve 必须是有界搜索，而不是“让模型再想一次”：

1. 每个 candidate 保存 `candidate_id/parent_id/generation/mutation/evidence` lineage。
2. objective vector 至少包含 correctness、solution completeness、difficulty distance、
   novelty、pedagogical value、verification strength、cost。
3. 选择先应用硬约束（正确、可解、无泄漏），再做 Pareto/加权排序。
4. 保留小比例 novelty champion，避免只围绕一个模板收敛；同时用 content hash、
   concept overlap 和结构签名去重。
5. 失败证据必须转换为具体 mutation directive；修复后先重跑原失败检查，
   再跑完整矩阵。
6. 每代的 candidate 数、总 LLM calls、sandbox CPU 和 wall time 都受 RunBudget 限制。

## 7. 验证矩阵

| 题型 | 必须 | 可选强证据 | 失败策略 |
|---|---|---|---|
| 数值/代数 | 重算、边界样例、容差与单位 | SymPy/NumPy + property tests | 明确 counterexample 必须拒题 |
| 证明 | 条件/结论、关键蕴含、反例搜索 | Lean 4 | Lean 失败区分翻译、依赖和证明失败 |
| 几何 | 退化配置、数值坐标特例 | CAS/坐标 property tests | 图形直觉不能代替条件 |
| 物理 | 量纲、单位、极限情况、守恒 | 数值仿真 | 量纲错误硬拒绝 |
| 化学 | 计量/电荷/原子守恒、反应条件 | cheminformatics tool | 任一守恒失败硬拒绝 |
| 程序 | sample + hidden + boundary + property | compiler/runtime sandbox | 超时/超内存/反例硬拒绝 |
| 实验设计 | 变量、对照、可观测指标、安全 | domain reviewer | 缺对照/不可观测进人工复核 |

验证结果必须是四态：`passed / failed / unavailable / error`。不得把“沙箱未安装”、
“代码生成失败”或“编译器崩溃”偷换成数学通过。当 policy 将某验证设为 required 时，
`unavailable/error` 也不能发布。

## 8. 沙箱信任边界

| 级别 | 允许输入 | 实现 | 可发布环境 |
|---|---|---|---|
| trusted-local | 仓库内人工审核的验证代码 | subprocess + temp cwd + CPU/wall/memory limit | 单机开发 |
| untrusted-container | LLM/用户生成代码 | Docker/Podman：`--network none --read-only --cap-drop ALL --security-opt no-new-privileges`，再加 pids/memory/cpu/tmpfs | 可控单租户 |
| hardened-multitenant | 互不信任租户代码 | gVisor `runsc` 或 Firecracker microVM + host egress policy | 生产 SaaS |

任何 runner 都必须：不使用 shell，参数以 argv 列表传递；截断 stdout/stderr；限制进程数、CPU、
内存、wall time 和临时盘；默认无网络、无宿主挂载、非 root。

## 9. API / UI 事件协议

Chatbox 不应从最终一段文本猜测 Agent 状态。后端按 run 输出单调事件：

- `message.delta/message.completed`；
- `run.created/run.status/run.completed/run.failed`；
- `step.started/step.completed/step.failed`；
- `candidate.created/candidate.selected/candidate.rejected`；
- `tool.started/tool.output/tool.completed`；
- `verification.started/verification.result`；
- `artifact.created`（question/solution/rubric/lean/code/report）；
- `usage.updated`。

SSE 客户端传 `Last-Event-ID`，服务端从 append-only sequence 补发；断线不会重跑 Agent。
主聊天流展示用户意图、最终题目与解答；右侧面板订阅同一 run 的 candidate、验证、
数值输出、Lean diagnostics 与难度量规，不再建第二套状态。

## 10. 发布准则

一道题只有在以下条件同时成立时才能标记 `verified=true`：

1. 题面通过结构、去重、条件完整性检查；
2. 独立 solver 给出非 low-confidence 的完整解答；
3. independent reviewer 同时看到题面与完整解答并通过；
4. policy 要求的确定性/沙箱/Lean 验证均为 passed；
5. 难度距目标在容差内，concept 只来自质量过门的知识库范围；
6. 事件、证据、lineage、模型与成本已持久化。

“调用过验证器”不等于“已验证”；只有可读、可重放的证据结果才能支撑发布标记。
