# 详细计划 · plan-02 — QGen Agent Harness / 生成 / 验证

> 消化 [report-qgen-agent-harness.md](../audit/report-qgen-agent-harness.md) 的全部发现。代码片段均为 **proposed / not-yet-implemented**。
> **重审说明**：QG-01/02 是发布契约前置条件，不应排在“生成输出补齐”之后；完整目标态见 [plan-05 §6–8](plan-05-modernization-program.md)。

---

## P0-R — QuestionArtifact、证据绑定与单一应用服务（QG-01~10）

### P0-R.1 · immutable QuestionArtifact（QG-01/07/10）

定义唯一版本化对象，至少包含 `artifact_id/version/discipline/form/content/options/correct_option/blanks/solution/tags/difficulty/source_refs/provenance`。choice、fill_blank、true_false、proof 的约束由 discriminated union 表达。solver/verifier/evolver/prefilter/CLI/API/conversation/paper/export/UI 不再各取字段，而是调用同一组 `render_for_model()`、`render_for_user()`、`render_for_export()`。

关键规则：

- evolver 修改题干时必须同时返回完整新 artifact；不能复用旧 options/key。
- evolved candidate 必须重新经过 prefilter，再 solve/verify。
- `/v1/solve` 的 discipline 来自 artifact/request，不得硬编码数学。
- REST、CLI、conversation 只是 adapter，统一进入一个 application service；side effect 由 durable effect layer 执行。

### P0-R.2 · EvidenceBinding 与发布门禁（QG-02/04）

每条程序/Lean 证据绑定 `artifact_digest + solution_digest + verifier_spec_digest + toolchain_digest + execution_digest`。状态至少区分 `generated`、`compiled_unbound`、`bound_checked`、`failed`、`unavailable`；只有 bound evidence 才能满足 formal-required。Lean translator 需输出当前题目的 assumptions/goal mapping，编译任意 theorem 只能证明工具链可用。

数值代码带 provenance：`repository_reviewed` 才可 trusted-local；`model_generated`/`user_supplied` 必须 container。policy engine 在执行前验证 provenance，不能靠文档约定。

### P0-R.3 · ExecutionConfig/RunManifest/effect outbox（QG-03/05/06）

- CLI/TOML/API 只解析一次 immutable `ExecutionConfig`，显式注入 generator/solver/reviewer/sandbox/policy；禁止内部新建默认 `AppConfig()`。
- checkpoint 固化 model/provider/prompt/schema/tool/sandbox/policy/artifact versions；resume 漂移只能显式 fork 或 migration。
- usage/artifact/question/assistant delivery 统一写 effect outbox，唯一键 `(run_id,effect_type,effect_version)`；reconcile 重放 effect，不重复收费或交付。

### P0-R.4 · population 与低成本调度（QG-08/09）

population 保留 rejected diagnostics，并用真实 correctness/evidence/rubric 分量；常数目标不得参与 Pareto。候选改为一次生成一个，首个满足 gate 即停；需要多样性时再补。长稳定指令进入 provider-capable system cache block，动态 evidence 保持小；同一独立 reviewer 一次结构化返回 correctness/relevance/rubric，避免 happy-path 重复 full-context review。

## P0 — 止血（GN-01 🟠）

### GN-01 · CLI 写回改用更新接口
```python
# proposed cli/generate.py:294 / :841  —— 一行修复
# 现: storage.save_batch([], [q])   ← INSERT OR IGNORE，accepted 行不更新
# 改:
storage.update_question_solution(question_id, result.solution.solution_latex)
```
**测试**（补 GN-22 缺口）：`solve --save` 对已存在 accepted 题 → 断言库内 `solution_content` 确被更新。**验收**：写回后 `load_question` 返回新解答。

---

## P2 — 生成题治理（GN-02 🟠，与 plan-01 §P2.3 协同）

生成题保存时写 `pipeline_auto` 评价，或引入独立"生成题评价 rubric + 转正"通路，使其可进入质量门禁（否则 SaaS 用户永远看不到自己生成的题）。详见 [plan-01](plan-01-data-pipeline.md) 的「§P2.3 评价→转正 + review 处理」。这是**产品决策点**：若"生成即发布"则默认写通过评价；若"生成需复核"则进 review_queue + 前端复核视图。建议后者（与治理设计一致）。

---

## P3 — 生成与输出补齐（GN-03/04/06/07/08/09/10/11/13/18；缺失功能：真组卷/讲义PDF/教材/向量检索）

### P3.1 · 检索升级（GN-18 🟡、GN-03 🟠）
**问题**：QGen/DocAssembly 检索统一 `ORDER BY order_index LIMIT 500` 后才 Python 端 TF 排序 → 相关材料系统性缺失、偏早期文件。
**目标态**：
```python
# proposed retriever._match_knowledge
# 1) 用 FTS5 MATCH（已建 kp_fts/question_fts）做相关性初筛，而非 order_index 截断
# 2) 排序用 TF-IDF（当前只 TF）或接入 item_embeddings 语义检索（multitenant 已有线性 cosine）
ids = storage.search_fulltext(query_terms, discipline, subject, difficulty_window, limit=500)
ranked = rank_by_tfidf_or_embedding(ids, target)
```
DocAssembly 同步：先按 tag 相关性排序**再**截断 + 难度/质量过滤（修 GN-03）。

### P3.2 · 真实组卷算法（GN-10 🟡；缺失功能）
**目标态**：`PaperRequest` 增 `topics`/`difficulty_distribution`/`form_ratio`/`shuffle`；组卷改为**约束满足**：
```
输入: 题数 N、难度分布 {easy:3,mid:5,hard:2}、题型配比 {choice:4,proof:3,calc:3}、topic 集
算法: 分层抽样(按难度桶+题型桶) → topic 覆盖贪心 → 去重 → 可选随机种子(可复现)
输出: 满足约束的题集 + 答案页分离选项
```

### P3.3 · 讲义/教材（GN-11 🟡；缺失功能）
- 会话 `lecture_note` 输出接**真** DocAssembly（多题聚合）而非单题加壳。
- 讲义完整版：Jinja2 模板 + XeLaTeX → PDF（Docs/10 蓝图落地）。
- 教材级：章节大纲 Agent（先规划目录，再逐节调 DocAssembly），设计占位 + Post-MVP 实现。

### P3.4 · 生成质量（GN-04/06/07/08/09）
| ID | 目标态 |
|---|---|
| GN-04 | free 模式检索库内相似题作 negatives，novelty 对全库查重（非仅 variant） |
| GN-06 | 按需生成候选：首候选进 solve/verify，失败再补齐，省 ~33% 生成调用 |
| GN-07 | verify 清单加难度硬门禁：`abs(overall - target) > 1` → 判 fail 或 evolve 调难度 |
| GN-08 | 生成题 tags 过 Stage3 同款 taxonomy_validator/canonicalizer |
| GN-09 | `/v1/behavior` 用 question_id 回查题库难度/tags，不信客户端断言 |

### P3.5 · 向量/混合检索（缺失功能）
`item_embeddings` 已存在（multitenant 线性 cosine），设计为 QGen/推荐/知识搜索的默认路径（当前是扩展项）。

---

## P4 — 验证有效性（SB-01/02/04/06/07/08；缺失功能：property-based/hardened sandbox/thinking）

### P4.1 · 清理危险执行器 + 修 docstring（SB-01 🟠）
- `deep_solver.py:4` docstring 改为如实描述走 VerificationSandbox（非 core.sandbox）。
- `core/sandbox.py` 无隔离裸子进程：要么删除（生产零调用），要么加显式 `# UNSAFE — tests only` 守卫并禁止生产 import（如模块级 assert 调用栈）。

### P4.2 · 验证默认可观测（SB-02 🟠）
**问题**：默认 `enable_numerical/lean=False` + 无容器静默 UNAVAILABLE → "最后防线"退化为 LLM 互证。
**目标态**：
- 容器可用时默认**启用**数值验证（数值题）；容器不可用时发**显式告警**（而非静默），并在证据里标注"程序化验证不可用"。
- 产品文档诚实反映：无容器 = 仅 LLM 软信号。

### P4.3 · 验证独立性 + 判定加固（SB-04/06 🟡）
- SB-04：验证代码用**独立** LLM（不同 model 或至少不同 provider/temperature），杜绝同模型自证；CLI 也注入独立 review_llm。
- SB-06：数值验证判定改**结构化退出码 + JSON 结果**（`{"passed": bool, "cases": N}`）替代 stdout 子串 `ALL_PASSED`；加最小用例数校验。
- QG-02：独立模型仍不足以证明有效；所有 numerical/Lean result 必须验证 `EvidenceBinding`，未绑定即 `compiled_unbound`，不可满足 required gate。
- QG-04：`trusted_local` 必须验证代码 provenance；模型/用户文本绝不允许通过该模式本地执行。

### P4.4 · Lean 镜像 + property-based（SB-07 🟡；缺失功能）
- 提供自建含 Mathlib 的 Lean 镜像 + Dockerfile + 文档（当前默认镜像无 Mathlib → required lean 恒失败）。
- 题型专用 property-based hidden suite（PROGRESS §8 P1），counterexample seed 可重放（修 SB-05）。
- hardened sandbox：gVisor/Firecracker + egress policy（PROGRESS §8 P0；缺失功能）。

### P4.5 · Extended reasoning（缺失功能）
QGen generate/solve/solve_deep preset 启用 thinking（当前未启）；按难度动态调 thinking budget。

### P4.6 · 容器清理（SB-08 🟡）
`_run_command` 全异常出口（OSError/被杀）都触发容器清理，非仅 TimeoutExpired。

---

## P5 — 生产化硬化（HR-01/02/03/04/05/06/07；缺失功能：worker lease/context压缩/memory/template管理/failover）

### P5.1 · Worker fencing（HR-01 🟠）
```python
# proposed: run 增 epoch/lease 列，worker 持 lease 才能写
# begin_run 分配 lease_id + expiry；每次 step 写操作校验 lease 未被抢占
# fail_step/fail_run 以 (run_id, lease_id) 为键，非仅 run_id
def fail_step(self, step_id, error, *, lease_id):
    row = self._require_step_row(conn, step_id)
    if row["lease_id"] != lease_id:
        return  # 不是我持有的 step，不动它 ← 修 HR-01
```
或引入队列 broker（PROGRESS §8 P1 分布式 worker lease）。

### P5.2 · API 预算缺省（HR-04 🟠）
```python
# proposed conversation_routes._run_budget
budget = RunBudget(
    max_steps = req.budget.max_steps or config.qgen_run_max_steps,      # ← 注入 AppConfig 缺省
    max_llm_calls = req.budget.max_llm_calls or config.qgen_run_max_llm_calls,
    max_tokens = req.budget.max_tokens or config.qgen_run_max_tokens,
    max_wall_time = req.budget.max_wall_time or config.qgen_run_max_wall_time_seconds,
)
```
消除 dead config + 无限预算 DoS 面。

### P5.3 · 预算预留 + 计量（HR-03 🟡、HR-05+GN-12 🟡）
- HR-03：预算改**节点前预留**（估算或按历史），超支在调用**前**拦截；违规 delta 计入（不丢失）。
- HR-05：vision/tool 路径写 `last_usage`；OpenAI/Gemini backend 从 SDK 响应写 usage；非 durable 路径 NodeTrace 带 token → `/v1/generate` 计费非 0。
- QG-06：计费记录必须带 effect idempotency key；usage 由每次调用返回值携带，禁止用并发共享 `client.last_usage`。

### P5.4 · 其余（HR-02/06/07；缺失功能）
| ID/功能 | 目标态 |
|---|---|
| HR-02 | 错误清理路径先判 cancel flag → 先 `acknowledge_cancel` 再考虑 fail；CLI durable 加 finally 兜底 |
| HR-06 streaming | LLM 层加 `generate_stream`；SSE 承载 token 流（当前只事件） |
| HR-06 failover | 多 backend 备援路由（429 耗尽切 provider 而非原地睡 6h） |
| HR-06 成本 | 价格表 + trace 货币字段 |
| HR-07 | checkpoint 只存 state delta 或裁剪 trace；事件表归档/水位；reconciliation 加时间窗 |
| QG-05 manifest | resume 前比较 execution/artifact/verifier/toolchain digest；漂移显式 fork/migrate |
| QG-09 cost | static/dynamic prompt split、按需候选、同 reviewer 合并判定、per-call price ledger |
| Context 压缩 | 会话历史超阈值做 rolling summary（当前截断） |
| Memory | 跨 session 用户/错题记忆表（设计占位 + Post-MVP） |
| Template 管理 | 模板运行时注册/版本化/租户扩展（当前静态 .txt） |

---

## Low 项（P7 批量）
SB-03（真实容器集成测试 opt-in 标记 + "故意错解"夹具）· SB-05/09/10 · GN-05/14/15/16/19/20/21 · HR-08/10/11/12/13/15。其中 **HR-13**（`_extract_text` 只取首 block 致 AttributeError）与 **GN-14**（术语保护 no-op 潜伏）建议提前到 P4/P3 一并修。

## 测试与验收

| 阶段 | 关键回归 |
|---|---|
| P0-R | choice/fill_blank round-trip；evolve 改 stem/key 同步；无关 Lean theorem 不得通过；trusted-local provenance；config 单源；manifest drift；effect retry 不重复计费 |
| P0 | solve --save 写回落库断言 |
| P3 | 检索 FTS 相关性、组卷约束满足、生成题 tag 合规、physics 端到端 |
| P4 | **真实容器执行 + "故意错解"被抓**、结构化判定、独立验证器、Lean+Mathlib 编译 |
| P5 | 双 worker fencing 不误杀、API 预算生效、token 计量全路径、cancel+异常复合 |

**验收原则**：验证类改动必须在**有容器**环境跑真实执行（当前全 mock，SB-03），并用"故意给错答案"夹具确认能被数值/Lean 抓出——这是"最后防线"名副其实的唯一证据。
