# 详细计划 · plan-05 — Pipeline 与 Agent Harness 现代化总纲

> 本文根据第二轮全仓重审重排原 P0–P7。原 `plan-01~04` 仍保存子系统细节，
> 但凡与本总纲冲突，以这里的依赖顺序和完成定义为准。
>
> 核心原则：**先建立身份、守恒、版本和幂等，再优化吞吐与 LLM 成本。**

---

## 1. 为什么必须重排

原计划从 PA-01/PC-03 两个局部止血项开始，随后直接进入并发、治理和产品化。这会漏掉
三个更基础的问题：

- 同一 checkpoint 可对不同 DB、学科、配置和 dry-run 复用；“完成”不等于 sink 已提交。
- 同正文 first-write-wins，没有 occurrence/revision，后续更完整来源会被吞掉。
- QGen 的“验证”没有统一题面 artifact；选项/答案/空格不进入 solver/verifier/导出，
  Lean 也没有和当前题解做 digest 绑定。

因此新的关键路径是：

```text
P0-Gates
   ↓
P0-Identity/Config/Resource
   ↓
P0-Lossless/Provenance ───→ P0-Canonical Storage
   ↓                              ↓
P0-QGen Artifact/Evidence ──→ P1-Durable Side Effects
                                   ↓
                         P1-Cost/Throughput Optimization
                                   ↓
                         P2-Governance/Product/Frontend
```

## 2. P0-Gates — 先让证据可持续

### 2.1 CI 与环境一致性

- 在 fresh Linux environment 执行 `uv sync --extra all`，验证真正的 `uv run sciforge`，
  不允许 pytest 的 `pythonpath=src` 掩盖坏的 editable install。
- 必跑：ruff、format-check、mypy、pytest、`node --check`、SVG XML/geometry、Markdown links、
  `pip-audit`（default + `--extra all`）。
- lockfile 漏洞先升级：特别是 Starlette/FastAPI 兼容组；对不可达 transitive 依赖记录
  exception、owner 和到期日，不能永久 `ignore`。
- coverage 不只设全局线：`cli/llm/orchestrator/VLM/review/persist/runtime/security` 设置
  changed-lines 与关键 branch gate。

### 2.2 审计测试分层

| 层 | 目的 | 是否可 mock |
|---|---|---|
| unit/schema | 字段与纯函数不变量 | 可 |
| contract | provider、storage、API、artifact 协议 | 只 mock 网络边缘 |
| crash/replay | 任意 node 后 kill，resume sink 等价 | 不 mock DB/checkpoint |
| adversarial | prompt injection、错题、错 key、unrelated Lean、恶意 archive | 不可把判定器 mock 成 PASS |
| real-provider canary | stop reason、usage、cache、tool schema | 小预算真实调用，非每 PR |
| performance | peak RSS、tokens/page、calls/item、DB query plan | 固定夹具，可复现 |

**P0-Gates 完成定义**：fresh runner 全绿；扫描输出被存档；CLI 真入口可运行；每个 P0 修复
都有会在旧代码失败的回归测试。

## 3. P0-Identity — 运行身份、配置与资源控制

### 3.1 三层身份模型（DP-01）

不要继续扩充 `file_hash`。拆成三个显式对象：

```python
class SourceManifest(BaseModel):
    ordered_parts: list[SourcePart]  # path label + length + sha256；长度前缀消除 ab+c 碰撞
    source_digest: str

class RecognitionCacheKey(BaseModel):
    source_digest: str
    recognizer: str
    recognizer_version: str
    backend: str
    dpi: int
    pages_per_batch: int
    model_fingerprint: str | None
    prompt_fingerprint: str | None

class IngestionRunManifest(BaseModel):
    run_id: UUID
    source_digest: str
    discipline: Discipline
    pipeline_config_digest: str
    schema_version: int
    target_sink_id: str
    stage_versions: dict[str, str]
    post_pass_versions: dict[str, str]
    mode: Literal["commit", "dry_run", "recognize_only"]
```

- recognition cache 可以跨 DB 复用，但 ingestion checkpoint 和 sink commit 不能。
- `completed` 必须同时满足：source conservation、所有 required stage/pass succeeded、
  sink commit ledger 存在且 digest 匹配。
- dry-run 使用临时 run namespace；不得写 commit state，不得影响后续正式 run。
- `--restart` 明确选择：重做 run、重做 recognition、或重建 canonical revision；不能一个
  模糊 flag 同时承担三种语义。

### 3.2 Typed PipelineConfig（DP-02/12）

- 把扁平 AppConfig 中 pipeline 字段迁入嵌套 `PipelineConfig`；TOML/env/CLI 合并完成后
  **一次性 `model_validate`**，禁止后置 `setattr`。
- 字段约束：所有 batch/queue/concurrency >0；窗口单调；timeout/budget 有上界；字符串不
  隐式塞进数字字段。
- 跨字段资源公式必须校验：

```text
inflight_pixels = files × vlm_batches_per_file × pages_per_batch × pixels_per_page
inflight_bytes  = rendered images + queued markdown + mirror writes
llm_slots       = fair global scheduler ∩ provider/model limits
```

- `config validate` 增加 capability probe：provider type/URL path、env placeholder 拼写、
  model vision/tool/JSON/thinking/cache 支持、主线是否真正消费 task。

### 3.3 全局 ResourceGovernor（DP-08/XC-07）

- 渲染前获取 page/pixel/image-byte token，而非图片已进内存后才等 LLM semaphore。
- provider/model 调度用公平队列或 weighted limiter；不得持有 global permit 等 model permit。
- 每 run 限制：pages、uncompressed bytes、prompt bytes、output tokens、wall time、retry cost。
- finish reason、usage 和 truncation 是 response envelope 的必填字段；未知/截断不得进入完成缓存。

**验收**：非法配置在启动前失败；理论/实测 peak RSS 都低于预算；20 文件压力下小模型不会被
饱和模型饿死；dry-run 后正式 run 的 sink 与 clean run 完全相同。

## 4. P0-Lossless — stable-ID patch 与逐 item provenance

### 4.1 Review 从位置数组改为 patch protocol（DP-05）

```python
class ReviewOperation(BaseModel):
    item_id: str
    op: Literal["keep", "update", "reject_to_uncertain"]
    patch: ItemPatch | None
    reason_code: str

class ReviewResult(BaseModel):
    operations: list[ReviewOperation]
    insertions: list[ProposedItem]  # 显式补漏；必须有 coverage evidence
```

- 输出 ID 集合、重复、未知 ID、顺序和 span overlap 必须确定性校验。
- `reject` 不是删除：转入 quarantine/review queue，保留 source span、原提取和原因。
- LLM review 不负责“让上游重扫”这种已经不可能发生的动作；重提由 orchestrator 创建局部
  patch task，并在 ledger 中完成闭环。
- Stage2 batch 也按 item_id map，不再按数组位置 zip。

### 4.2 SourceMap 与 conservation ledger（DP-06）

- adapter locator、file part、page、line、char、block UID 在 Task0 时形成不可变 `SourceSpan`。
- carry/window/segment 只引用 span 集合；不能用“当前 block”替代窗口中所有行的来源。
- 每条 canonical revision 保存一个或多个 occurrence；每个高风险 marker 最终必须属于：
  accepted item、uncertain item、rejected-with-reason 或 pending patch。
- conservation gate 在完成前检查：marker recall、unresolved span bytes、failed page、stage count、
  sink commit；任一未知状态阻止 `completed`。

### 4.3 并行只有一种语义

- 串行与 segment parallel 共用同一个 carry/patch/ledger state machine；并行层只调度独立 span。
- halo 去重用 stable marker/item identity + char IoU/anchor，不用“任意 overlap 即同一条”。
- crash at every node 测试：恢复后的 canonical sink、occurrences 和 review queue 与无崩溃运行等价。

## 5. P0-Canonical Storage — item / occurrence / revision

### 5.1 目标数据模型（DP-03）

```text
items
  item_id · discipline · semantic_identity · active_revision_id · lifecycle

item_revisions
  revision_id · item_id · content/solution/options/key/tags/taxonomy · completeness · created_by_run

item_occurrences
  occurrence_id · item_id · revision_id · source_digest · source_span · extraction_run

item_links / evaluations / figures / embeddings
  全部 FK 到 item_id 或 revision_id，生命周期明确
```

- semantic identity 包含 discipline/tenant/domain；不能只按正文 hash 全球唯一。
- 相同正文的新来源总是新增 occurrence；更完整 revision 由确定性 completeness policy 或人工
  review 晋升，不能 `accepted` 首写永久胜出。
- delete/merge/supersede 是单一 lifecycle service 的事务；卫星表无悬挂。
- SQLite 不支持一个 FK 指向两张表，统一 `items` 主表后再做 KP/Question subtype。

### 5.2 Schema shape contract（XC-03）

- 除 `user_version` 外保存 schema fingerprint；启动时核对列、类型、NOT NULL、check、index、
  trigger、foreign key 和 FTS external-content shape。
- migrate 采用 preflight→transaction→post-verify→commit；中断可恢复，失败不伪装成新版本。
- `validate`/`db-stats` 走 `mode=ro&immutable=1`（适用时）专用 reader；绝不创建/迁移/切 WAL。

**验收**：同题两来源一缺解一有解，得到 1 item、2 occurrences、2 revisions，active revision
含完整解答；跨 discipline/tenant 不误合并；损坏 v14 trigger 的 DB 在写入前 fail closed。

## 6. P0-QGen — 统一 QuestionArtifact 与证据绑定

### 6.1 一种题面渲染，贯穿所有消费者（QG-01）

```python
class QuestionArtifact(BaseModel):
    artifact_id: str
    discipline: Discipline
    stem: str
    question_form: str
    options: list[str]
    correct_option: str | None
    blanks: int | None
    assumptions: list[str]
    digest: str
```

- `render_for_solver`、`render_for_reviewer`、`render_for_user`、`render_for_export` 从同一 artifact
  构建；每种 form 有不变量（choice 必有 options/key，true-false 有 canonical key，fill 有 blanks）。
- evolve 必须返回完整新 artifact；题干变化却继承旧 key/options 一律 schema reject。
- prefilter 对初代和 evolve 一视同仁；不能因走回边绕过。

### 6.2 EvidenceBinding（QG-02/04/05）

```text
question_digest
  └─ solution_digest
       ├─ reviewer evidence (independent model/config fingerprint)
       ├─ numerical artifact + cases + seed + execution manifest
       └─ Lean theorem + translation map + assumptions + compiler manifest
```

- Lean 通过条件不是“某 theorem 编译”，而是 theorem statement/assumptions 与 question artifact
  的受审映射通过；未绑定只能标 `compiled_unbound`，不得标 formal verified。
- numerical verifier 返回结构化 JSON/exit code、最小 case 数与 seed，不读 `ALL_PASSED` 子串。
- model/user 生成代码永不走 trusted_local；trusted_local API 只接受签名的 repository artifact
  digest + 显式 operator approval。
- run checkpoint 固化 provider/model/prompt/tool/schema/sandbox image digest；不兼容 resume 必须 fork
  新 run，不得静默续跑。

### 6.3 Agent 架构收敛

- agent 是拥有清晰输入/输出/拒绝语义的能力边界，不以“十大名字”作为完成度。
- QGen graph 节点输出 immutable artifact/evidence；路由只读诊断码，副作用交给 effect layer。
- population objectives 必须有方差与真实含义；恒 1 的 correctness/verification_strength 删除或
  改成 evidence diversity/coverage/calibration。
- REST/CLI/conversation 都由同一 discipline-aware factory 和 AppConfig snapshot 构建。

**验收**：故意给 choice 错 key、evolve stale options、unrelated Lean theorem、伪造
`ALL_PASSED`、trusted_local 模型代码，全部 fail closed；导出试卷完整显示选项。

## 7. P1-Durable — lease、幂等副作用与 post-pass DAG

### 7.1 Worker lease/fencing

- run/step 获取 `lease_id + epoch + expires_at`；所有 checkpoint/event/effect commit 校验 lease。
- 心跳续租，接管只提升 epoch；旧 worker 的晚到写入被拒绝，不得 fail 掉健康新 worker。
- cancel 是单调终态意图；异常清理先读 cancel，再决定 acknowledge/fail。

### 7.2 Effect outbox（QG-06/XC-08）

| effect | idempotency key |
|---|---|
| question/artifact persist | `run_id + artifact_digest + effect_type` |
| assistant delivery | `run_id + message_kind` |
| billing | `tenant_id + run_id + endpoint + charge_version` |
| post-pass | `sink_commit + pass_name + pass_version + input_digest` |

- effect transaction 先写 outbox，再由 worker commit；成功/失败可重放。
- quota 是 reservation→commit/release；DOCX/LLM/容器失败不产生已提交 charge。
- post-pass 是版本化 DAG：reconciliation/类型修正先于 relations/concept/evaluation；每个 pass
  可单独重跑，失败阻止对应质量级别完成，而不是全吞 warning。

## 8. P1-Cost — 在正确性约束内降本增效

### 8.1 LLM 调用最小化顺序

1. deterministic adapter/classifier/marker/shape gate；
2. cacheable static system + small dynamic evidence；
3. risk router 决定是否需要 review/repair；
4. 先生成 1 个候选，只有未达质量/多样性目标才追加；
5. 独立验证按题型启用，不做无意义的重复 relevance call；
6. 非交互且可延迟任务使用 provider batch API；
7. 局部 patch，不重发整个扩窗/整题上下文。

### 8.2 成本账本

每次 attempt（成功、schema fail、429、truncation refetch、vision、tool）记录：provider/model、
input/output/cache tokens、价格版本、latency、retry reason、artifact/run/stage ID。`last_usage` 不能是
并发共享 attribution；usage 必须随 response envelope 返回。

核心 SLO：

| 指标 | 目标/门禁 |
|---|---|
| marker recall | golden + adversarial 高风险 marker 100% 有最终状态 |
| crash/resume | sink digest 与 clean run 相同 |
| source locator | accepted/uncertain item 100% 有可回源 span |
| answer binding | 跨章/跨源错误绑定 0；ambiguous 全进 review |
| calls/item | 按文档类型建立 p50/p95，不以单一平均掩盖 |
| tokens/accepted item | 含失败 attempt 与 cache；版本间不得回归超阈值 |
| peak RSS | 在配置 resource budget 内 |
| DB ingest | 规模增长近线性；query plan gate 禁止去重全表扫 |
| evidence calibration | verified 状态与故意错解/hidden suite 的捕获率绑定 |

## 9. P2-Governance / Product

- quality gate 必须有生成题评价→review→publish 的可操作闭环；不能只进不出。
- graph/prerequisite 只有被 retriever/recommend/authoring 消费并有质量指标才算产品能力。
- 组卷从“取前 N”升级为约束求解：topic、form、difficulty、coverage、novelty、tenant policy。
- 讲义/教材基于检索与 outline artifact，多单元分别验证；PDF 是 export concern，不与内容生成耦合。
- 前端优先展示 artifact、证据绑定、unavailable/error 和来源，不把未绑定 compile 显示为绿色通过。
- 权限改为版本化 canonical scopes；TenantScopedStorageView 由显式 capability adapter 替代
  `__getattr__` 黑名单；Bearer 也使用明确 scopes，不默认 `*`。

## 10. PR 切分与依赖

| PR 序列 | 内容 | 前置 | 禁止夹带 |
|---|---|---|---|
| 00 | CI/fresh install/dependency/SVG gates | 无 | 功能重构 |
| 01 | typed PipelineConfig + capability doctor | 00 | 并行算法变化 |
| 02 | run manifest/cache namespace/sink ledger | 01 | storage schema 重构 |
| 03 | read-only inspector + schema fingerprint | 00 | ingestion 业务逻辑 |
| 04 | SourceMap + stable-ID review patch | 02 | LLM provider 切换 |
| 05 | canonical item/occurrence/revision migration | 03/04 | UI |
| 06 | QuestionArtifact all-form renderer | 00 | durable runtime |
| 07 | EvidenceBinding + adversarial verifier suite | 06 | cost optimization |
| 08 | lease/fencing + effect outbox | 02/07 | frontend redesign |
| 09 | post-pass DAG | 05/08 | 新图谱功能 |
| 10 | scheduler/cache/batch/cost ledger | 01/04/07 | 正确性语义变化 |
| 11+ | governance/retrieval/papers/lecture/frontend | 前述稳定 | P0 债务 |

每个 PR 的完成必须同时满足：旧代码失败的新测试、数据迁移/回滚方案、观测字段、文档与图件
同步、性能/成本无未解释回归。任何“先兼容旧行为”的临时分支都必须有删除条件和到期里程碑。
