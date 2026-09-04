# 审计报告 · Workstream 2 — QGen Agent Harness

> 范围：LLM 编排、控制循环、记忆/会话/上下文管理、模板/技能系统、多路径验证沙箱（代码执行/数值/Lean）、生成链业务逻辑与输出格式。
> 架构图：[diagrams/qgen-agent-harness.svg](diagrams/qgen-agent-harness.svg)
> **重审优先级**：新增 QG-01~10 以 [RE-AUDIT-REGISTER §4](RE-AUDIT-REGISTER.md) 为准；历史 GN/HR/SB 条目保留作证据。

---

## 0. 2026-07-11 重审增量

本轮沿一条 choice/fill-blank 题从 generator 追到 solver、verifier、evolver、CLI、conversation artifact、paper、export 和 UI，发现现有 harness 的首要问题不是节点数量，而是**跨节点 artifact 语义不守恒**：

| ID | 等级 | 结论 | 必要改造 |
|---|---|---|---|
| QG-01 | **High / 发布阻断** | generator 保留 `options/correct_option/blanks`，但 solver/verifier 只读 `content`，evolve 改题干仍复用旧选项/答案，CLI/API/paper/export/UI 又继续丢字段 | 一个 immutable `QuestionArtifact`；所有消费者只经 `render_for_model/user/export` |
| QG-02 | **High / 发布阻断** | Lean 只要任意 theorem 编译即可记 passed，没有绑定当前题目、答案、假设与目标 | `EvidenceBinding(artifact_digest, solution_digest, verifier_spec_digest)`；未绑定只能 `compiled_unbound` |
| QG-03 | High | CLI `--llm-config` 只用于 client，QGen flags/sandbox 另建 `AppConfig()`，自定义行为静默失效 | 解析一次 immutable `ExecutionConfig`，显式注入所有节点 |
| QG-04 | High（安全） | `trusted_local` 可执行模型生成数值代码，违背“仅人工审核仓库代码”的文档边界 | provenance 枚举 + policy engine；模型代码强制 container |
| QG-05 | High | checkpoint 不固定 model/prompt/tool/schema/sandbox 版本，resume 后语义可漂移 | `RunManifest` digest，漂移需显式 fork/migrate |
| QG-06 | High（计费） | usage/交付没有 run/effect 幂等键，reconcile/retry 可重复扣费 | effect outbox + unique `(run_id,effect_type,effect_version)` |
| QG-07 | Medium/High | `/v1/solve` 硬编码数学，多学科入口与系统声明不一致 | discipline 进入 request/artifact/route/prompt/verifier 全链 |
| QG-08 | Medium | population 只在 accepted 中选择，correctness/evidence 多为常数，所谓多目标主要退化为难度/新颖度/解答长度 | 保留 rejected diagnostics，按真实 evidence/rubric 目标选择 |
| QG-09 | Medium（成本） | 长静态指令仍在 user prompt，三候选串行一次生成完；独立 full review 后又做额外 relevance 调用 | static system cache + 按需逐候选 + 合并同一独立 reviewer 的结构化判定 |
| QG-10 | Medium | REST/CLI/conversation 三条入口各自拼 config、artifact 和副作用，造成语义漂移 | thin adapters → 单一 application service → durable graph/effect layer |

目标架构见 [plan-05 §6–8](../plan/plan-05-modernization-program.md)。

## 1. 架构盘点（现状）

**QGen 已具备教育领域规则与 durable 底座，但当前不能概括为“端到端教育特化引擎”**：题型规则、难度维度和预筛是真实的；非数学模板、结构化题型守恒、证据绑定、population 目标和多入口一致性仍不成熟。

- **StateGraph 10 节点**（`agent.py`）：`retrieve→generate→next→prefilter→solve→verify→annotate→END`，失败汇 `diagnose→evolve→solve`，population 模式 `annotate→next→select→END`。三层熔断：`_MAX_GRAPH_STEPS=400` + `round<max_rounds` + `improve_round<max_improvement_rounds`。**性质：有界反馈图**（有 evolve→solve 回边、next→generate 再生边），非固定 DAG，但 diagnose 是规则路由（非 LLM 自主规划）。
- **DurableQGenHarness + SQLiteRunStore**（`qgen_runtime.db`）：Run 状态机 PENDING→RUNNING⇄PAUSED→终态；checkpoint 不可变（触发器级）；event 单调（写锁内 MAX+1）；node-boundary resume（已完成节点不重放）；协作取消 + 启动恢复 + uuid5 幂等交付。
- **LLM Client**（`client.py` 1008 行）：双层 Semaphore、能力矩阵过滤、异常归一化、schema feedback retry、429 长睡 6h；5 个 backend（Anthropic/Compatible/OpenAI/Compatible/Gemini）。
- **验证**：四态证据（passed/failed/unavailable/error）+ 容器沙箱（`container_required`，fail-closed）。
- **一次 run LLM 调用分解**（默认）：happy path = 3(generate)+1(solve)+2(verify) = **6 次**（2 个未用候选被丢弃）；最差 3 轮 = **36 次**；evolve 每候选再 +8 次。

## 2. 简报 9 大组件覆盖

| # | 组件 | 状态 | 依据 |
|---|---|---|---|
| 1 | LLM 调用 | ◑ 有缺口 | 有 retry/能力矩阵/thinking 接口；cache 命中不能泛化，且**无 streaming/无 provider failover/无完整成本核算**（QG-09/HR-06） |
| 2 | 控制流 | ● 有界反馈图 | 真循环 + 三层熔断（有测试），diagnose 为规则路由（合理取舍），非开放式规划 |
| 3 | Extended reasoning | ◑ 部分 | thinking 全链路透传 + deep solve ≤5 轮 + evolution/population，但 QGen preset 未启 thinking |
| 4 | Long-horizon | ◑ 内核扎实、语义不完整 | 状态机/不可变 checkpoint/单调事件真实存在；缺 lease、执行 manifest、effect 幂等，不能声称 exactly-once（HR-01/03/15、QG-05/06） |
| 5 | Long-context | ◑ 条数截断 | 检索窗 + 条数上限（8KP/8ref/5neg），无 token 预算、无 embedding/分块 |
| 6 | Context 压缩 | ◑ 截断式快照 | 8 条×1200 字×总 6000 字，**无摘要/rolling summary** |
| 7 | Session 管理 | ● 边界清晰 | 产品层 ConversationStore（tenant 复合键、run link 权威归属、artifact 幂等），runtime 不越界 |
| 8 | Template/Skills | ◑ 静态 | `.txt` + lru_cache，**无运行时注册/租户扩展/版本化**；32 个模板 |
| 9 | Memory 系统 | ○ 未实现 | 无跨 session agent 记忆；lineage/population 仅 provenance；RecommendEngine 是 P4 用户画像 |

**教育特化评估（部分成立）**：确定性题型预筛、10 维难度模型、按题型选择验证路径与 prompt 教学约束是真实实现；但 choice/fill-blank 字段未贯穿消费者（QG-01），非数学学科化仍偏词面替换（GN-13），population 的 correctness/evidence 近常数且 `pedagogical_value` 主要是解答长度启发（QG-08）。

## 3. 输出格式能力（简报 §1.2）

| 格式 | 状态 | 依据 |
|---|---|---|
| 新题 | ◑ 主链可用、结构契约不完整 | 检索/预筛/求解/审核/进化/durable 已有；结构化题型丢字段、Lean 未绑定、默认程序验证关闭（QG-01/02、SB-02） |
| 变式 A1-A6/B1-B2 | ● 已实现 | 8 策略模板 + 枚举 + CLI + 会话 variant/adjust/deepen |
| 讲义 | ◑ lite（大库脱钩） | CLI DocAssembly 0-LLM 拼装，GN-03 头部截断；API lecture_note 仅单题加壳（GN-11） |
| 成品试卷/组卷 | ◑ 取前 N | `list(ids)[:count]`，无 topic/难度分布/题型配比（GN-10） |
| 教材级内容 | ○ 未实现 | 全仓无对应代码，仅 Docs 规划 |
| 导出 | ◑ markdown/DOCX（无 PDF，字段不全） | `export/__init__.py` 可输出基本题干/解答，但 options/correct_option/blanks 未完整渲染（QG-01） |

---

## 4. High 发现

### 🟠 GN-01 — generate solve/solve-deep --save 解答写回静默丢失

**组件** Generation/CLI · **类别** 代码缺陷（数据丢失） · **复核 亲读✓**（`cli/generate.py:294`）

CLI 写回用 `storage.save_batch([], [q])`（`cli/generate.py:294`、`:841`），而 save_batch 走 `INSERT OR IGNORE INTO questions` + 仅晋升**非 accepted** 行的 `_Q_PROMOTE_SQL`（`WHERE ... AND ingestion_status != 'accepted'`）。已存在题 id 主键冲突 → INSERT 被忽略；库内行通常为 `accepted` → PROMOTE 0 行。结果 `q.solution_content` 更新**永不落库**，CLI 仍打印"已更新 question xxx.solution_content"（`:295`）。正确接口 `update_question_solution(qid, sol)` 已存在（`sqlite_backend.py:689`，answer_linker 就在用）但 CLI 未用。**修复**：一行改用 `update_question_solution`。

### 🟠 GN-02 — 生成题被质量门禁排除，无转正通路

**组件** Generation/治理 · **类别** 设计缺陷（能力断层） · **复核 代理**

生成题保存（`cli/generate.py:151-161`；`conversation_routes.py:1618-1665`）**不写 item_evaluations**，也不进 review_queue。而 papers/recommend/QGen 检索全带 `QualityGateFilter`；hosted 模式 `TenantScopedStorageView.search_*` 强制 `require_scored_quality_gate` → 未评分行**必然**不可见（`tenant_storage.py:103-105`；`query.py:151-163`）。**影响**：SaaS 用户生成并"已保存"的题永远不出现在自己的试卷、推荐、知识检索与后续 QGen 参考集里（按 id 直取 `load_question` 可用，variant/evolve 锚点不受影响）。可能是有意的发布治理，但缺失"生成题评价→转正"机制使其成为能力断层。

### 🟠 GN-03 — DocAssemblyAgent 检索截断，大库讲义与 topic 脱钩

**组件** Generation/讲义 · **类别** 设计缺陷 · **复核 代理**

`_retrieve_kps/_retrieve_questions` 先 `search_*({"discipline":...})` 再 `ids[:100]`（`doc_agent.py:104,114`），而 `_search` 本身 `ORDER BY order_index LIMIT 500`（`sqlite_backend.py:1684`）；tag 相关性排序在**截断之后**。库超 100 条时讲义素材 = 最早入库 100 条里碰巧 tag 重叠者，与请求 topic 基本无关，且无难度/质量过滤 → `generate lecture` 在真实语料上产出不可用。

### 🟠 HR-01 — durable run 无 worker fencing，并发 resume 误杀健康 run

**组件** Harness/runtime · **类别** 设计缺陷 · **复核 代理**

CLI `generate resume` 与 `serve` 默认共用 `qgen_runtime.db`。worker A 执行节点时 worker B 调 `prepare_resume`（`store.py:390-420`，无 lease/epoch）：A 活跃 step 被标 INTERRUPTED，B begin_run→RUNNING；A 在 on_exit `complete_step` 抛异常 → harness 通用异常处理（`durable_harness.py:367-373`）以 run 为键 `fail_step`+`fail_run` → **A 把 B 的健康 run 误判 FAILED**（terminal 不可恢复），LLM 消费作废。代码注释自认"加 lease 前禁止多 worker"，但"CLI resume 撞活跃 serve worker"这一同机组合不在告警范围且无运行时防护。根因：`fail_step/fail_run` 以 run 为键而非以"本 worker 持有的 step/epoch"为键。

### 🟠 HR-04 — Web API 默认预算无限；qgen_run_max_* 是 dead config

**组件** Harness/API · **类别** 缺失功能/一致性 · **复核 实测✓**（grep 无消费点）

`RunBudgetRequest` 四字段默认 None（`schemas.py:164-167`），客户端不传 budget 时 durable run **无任何硬顶**。`AppConfig.qgen_run_max_steps/llm_calls/tokens/wall_time_seconds`（`settings.py:269-272`）**主线 grep 确认**除定义外零消费点，而 CLAUDE.md 把它们列为"QGen 关键配置"。实际失控上限只剩图熔断（400 步），deep_solve+evolve 模式下单 run 可达数十次大 `max_tokens` 调用而无 token 顶。CLI 路径反有默认预算（200/64/500k/1800s）。**影响**：SaaS 计费风险 + DoS 面。**修复**：API 层把 AppConfig 默认值作为 budget fallback 注入。

---

## 5. Medium 发现（紧凑）

### 5.1 验证沙箱（SB）— 无 Critical 逃逸，"验证有效性"层面削弱

| ID | 类别 | 摘要 | 证据 |
|---|---|---|---|
| SB-02 | 设计 | 默认 `enable_numerical/lean=False` + 无容器时非 required 静默降级 UNAVAILABLE 照常 passed → "最后防线"退化为**LLM 互相背书**（默认零程序化 ground-truth） | `settings.py:253-256`；`verifier.py:219-229` |
| SB-04 | 设计 | 数值/Lean 验证代码与题目默认由**同一** review_llm 生成（CLI `review_llm or llm`）→ 自我印证循环 | `verifier.py:402,483`；`agent.py:150` |
| SB-06 | 代码缺陷 | 数值验证判定基于 stdout **子串** `"ALL_PASSED"` → LLM 代码逻辑 bug 或无条件打印即虚假通过 | `verifier.py:428` |
| SB-07 | 一致性 | Lean 默认镜像 `leanprover/lean4:latest` **无 Mathlib**，prompt 强制 `import Mathlib` + `network none` → required lean 恒 UNAVAILABLE/FAILED | `settings.py:260`；`generate_lean_test.txt:23` |
| SB-08 | 代码缺陷 | 容器超时清理只在 TimeoutExpired 分支，OSError/被杀路径遗留容器，deep_solver 多轮累积泄漏 | `verification_sandbox.py:606-632` |

> **SB-01（登记为 High）**：`core/sandbox.py` 是无隔离裸子进程（`subprocess.run([sys.executable,"-c",code])`，docstring 自认"不防恶意"），且 `deep_solver.py:4` docstring **谎称**走 core.sandbox（实际 `:100` 调注入的 VerificationSandbox 容器）。**主线实测确认**。当前生产零调用，运行时安全，但"危险执行器裸露在 core 供未来误接 + docstring 谎报安全边界"是重大隐患，故定 High，报告注明当前不可利用。

### 5.2 生成链与 LLM 层（GN/HR）

| ID | 类别 | 摘要 | 证据 |
|---|---|---|---|
| GN-04 | 设计 | free 模式无库内查重、跨 run 无去重 → 批量同专题高概率近重复题全入库 | `retriever.py:247-249`；`generator.py:122-133` |
| GN-06 | 性能 | 默认每轮预生成 3 候选，首候选通过即浪费 2 次生成调用；候选串行 | `agent.py:391-397` |
| GN-07 | 设计 | 难度贴合无硬门禁，annotate overall 直接写入不校验偏差；annotator 失败静默回退自估值 | `agent.py:474`；`annotator.py:41-43` |
| GN-08 | 一致性 | 生成题 tags = LLM 自由中文 + topics，无 `english:中文` 校验无 taxonomy，绕过 Stage3 校验污染 tag 空间 | `generator.py:196` |
| GN-09 | 一致性 | `/v1/behavior` 直接落库客户端提供的 difficulty/tags 不回查校真 → 画像可操纵 | `app.py:585-592`；`saas.py:475-492` |
| GN-10 | 设计 | 组卷 = 过滤 + 取前 N，无 topic/难度分布/题型配比/随机，PaperRequest 无 topics 字段 | `app.py:326`；`schemas.py:60-68` |
| GN-11 | 一致性 | 会话 lecture_note 输出只是单题加壳固定文案，与 DocAssembly 讲义能力无关 | `conversation_routes.py:2137-2142` |
| GN-13 | 教育特化 | 生成路径学科化止于词面替换，难度校准/策略/评分示例全是数学，物理支持名不副实 | `templates.py:196-260` |
| GN-18 | 检索 | QGen 检索不用 FTS5，统一 `ORDER BY order_index LIMIT 500` 后 Python 端 TF（无 IDF）排序 → 相关材料系统性缺失偏早期文件 | `sqlite_backend.py:1684`；`retriever.py:172-178` |
| HR-02 | 代码缺陷 | cancel_requested + 节点异常复合 → 错误路径自身抛 cancel 异常，run 卡死 RUNNING+flag，CLI 无兜底 | `durable_harness.py:361-373` |
| HR-03 | 设计 | 预算是节点完成后事后记账非预留，单节点内可任意超支；违规 delta 丢失致 DB tokens_used 系统性偏低 | `durable_harness.py:293-302`；`store.py:1043-1066` |
| HR-05+GN-12 | 一致性 | token 计量多路径失真：vision/tool 不写 last_usage、OpenAI/Gemini 从不写、非 durable 路径计费恒 0 → 计费/审计失真（两代理独立发现） | `anthropic.py:194,249-358`；`agent.py:318-320` |
| HR-06 | 缺失功能 | 无 streaming、无 provider failover、无成本核算（仅 token 无价格） | `llm/` 全目录 |
| HR-07 | 性能 | checkpoint O(n²)（每 transition 全量序列化 graph+trace）、事件表只增、启动 reconciliation 全量扫描 | `durable_harness.py:326-336` |

## 6. Low 发现（登记）

SB-03（真实容器路径全 mock 无逃逸回归、无"故意错解"夹具）· SB-05（数值验证无种子无反例强度不可重放）· SB-09（trusted_local macOS RLIMIT_AS 跳过）· SB-10（numerical required 门控 OR 逻辑脆弱）· GN-05/HR-08（温度递增声称但未实现）· GN-14（"数学归纳法"术语保护 no-op）· GN-15（进化 prompt 检查名与 verifier 脱节）· GN-16（3 孤儿模板 + 死代码）· GN-19（进化件跳过 prefilter + 难度锚点恒同一题）· GN-20（population 池≤3 候选）· GN-21（CLI 保存去重命中误报新 id）· HR-10（节点级 retry 与 durable 记账未打通）· HR-11（OpenAI 缺 max_completion_tokens）· HR-12（截断重抓仅 Anthropic async）· HR-13（`_extract_text` 只取首 block）· HR-15（complete_step 与 checkpoint 非原子致重放 usage 双计）。

## 7. 测试缺口

覆盖良好：verifier 五套（27 用例）、durable harness（budget 暂停/恢复不重放/漂移拒绝）、graph/harness_graph（28）、runtime store（8，含 cancel 终态竞态、幂等 step、budget 原子 pause）、population/evolver/prefilter/retriever、API papers/recommend/租户隔离。**关键缺口**：
- CLI `solve --save`/`solve-deep --save` 写回路径**零覆盖**（→ GN-01 漏网）
- solver.py/annotator.py 无独立单测；`test_qgen_agent.py:214-263` 自述"不崩就算过"、断言恒真
- 无双 worker/CLI+serve 并发 resume 测试（HR-01）；无 cancel+节点异常复合（HR-02）；无 API 默认预算=无限守护（HR-04）
- **真实容器路径从未被执行测试**（全 mock）、无"故意错解"夹具验证错误答案能否被抓（SB-03）
- 无跨 run 重复生成回归、无 physics 生成端到端

## 8. 正面结论（须并列呈现）

- **container-required 路径不会在容器缺失时自动回退本地**，且 OCI 参数包含 network/read-only/cap-drop/no-new-privileges/non-root/pids/memory/tmpfs 等多层加固；但 `trusted_local` 来源未被代码强制（QG-04），Lean passed 也未绑定当前题解（QG-02），故只能肯定执行参数，不能泛化为验证语义 fail-closed。
- **输出卫生**：deep_solver 只发布结构化 JSON，明确不回退 transcript（有测试）；`_progress_payload` 只投影有界字段，**无 raw chain-of-thought 泄漏路径**。
- **Harness durable primitives 扎实**：RunStore 状态机、触发器保护的不可变 checkpoint、单调事件、协作取消与启动 reconciliation 都有实现和测试；但 lease/manifest/effect idempotency 是生产化前置条件。
- **确定性预筛和题型/领域枚举是有价值的 0-LLM 边界**，应保留并改为对完整 `QuestionArtifact` 校验，而不是只验题干字符串。
- **Docs/22 对许多未实现项写得坦诚**，但未覆盖本轮发现的结构题型守恒、manifest、证据绑定与副作用幂等，需补充。
- LLM 层已有能力过滤、异常归一化、Retry-After 和 schema feedback retry；cache、usage 与双层 semaphore 的生产效果须按 provider/并发实测，且当前全局 semaphore 先于 model semaphore 可能造成饥饿（XC-07）。
