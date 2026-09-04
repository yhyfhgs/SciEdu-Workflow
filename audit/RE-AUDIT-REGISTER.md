# SciForge 全仓重审总台账（2026-07-11）

> 基线：`dev @ ba29f3b`。本文件是第二轮审计的证据与纠错入口；各工作流报告保留
> 详细上下文，修复顺序以 `../plan/plan-05-modernization-program.md` 为总纲。
>
> 结论状态：**复现**＝运行最小夹具得到错误结果；**静态证实**＝控制流与数据契约
> 可以从源码直接证明；**扫描候选**＝工具报警，必须结合可达性判断，不能直接当漏洞。

---

## 1. 这次重审纠正了什么

第一版审计内容丰富，但它的“约 85 项、100% 覆盖”不可复核：五份报告中实际存在
**161 个可搜索发现 ID**（不含 `REC-*`），其中 14 个是合并项或交叉印证别名，约
147 个独立行动项。第二轮又确认了运行身份、配置、结构化题型、权限边界等此前未建账
的问题。因此本轮不再用一个容易漂移的总数证明“完整”，改用：

1. 本文件的新增/升级台账；
2. 每个工作流报告的详细证据；
3. `plan-00` 的 ID→阶段映射；
4. 下文的源码、测试、文档和图件覆盖矩阵。

同时撤销或收窄以下旧结论：

- `IS-13 page_%03d 在 >999 页后词典序错误`是**误报**。扫描、恢复和迁移路径均解析
  数字后排序；`%03d` 只是最小宽度，不会截断 1000。
- “TenantScopedStorageView 全面 fail-closed”过度乐观：问题名黑名单不是能力白名单，
  仍可透明委托 concept/evaluation/semantic/figure 等可能含题目数据的方法（XC-02）。
- “schema v14 迁移链完整”只证明版本号迁移存在，不证明现有 v14 DB 的列、索引、
  trigger、check constraint 形状正确（XC-03）。
- “prompt cache 已真实生效”只在满足 provider capability、稳定且足够长的 system block
  时成立；当前 QGen 长指令仍在 user prompt，生产兼容 provider 也未开启 cache 能力。
- “无损摄取基本成立”必须降级为“存在局部兜底，但没有 source→sink conservation
  contract”；checkpoint、review null、批量错序、locator 和 post-pass 均可破坏无损。
- “验证证据链完成度高”必须区分“执行器加固”与“证据语义有效”：Lean 目前只证明
  某段 Lean 可编译，不证明它形式化了当前题目/解答；结构化题型甚至未把选项/答案送入验证。

## 2. 可执行基线与覆盖证据

| 检查 | 当前结果 | 它能证明什么 | 它不能证明什么 |
|---|---:|---|---|
| `uv run pytest -q` | 1318 passed, 1 skipped | 现有回归夹具全绿 | 真实 LLM、容器、宕机、长文档、跨 DB checkpoint |
| pytest + branch coverage | 79.7% statements / 76.9% lines / 66.9% branches | 量化测试触达 | 未触达分支不是安全分支 |
| `ruff check src tests` | clean | 规则集内静态质量 | 业务逻辑与并发正确性 |
| `mypy` strict | 138 source files clean | 声明类型一致 | `setattr`/JSON/模型输出的运行语义 |
| `node --check app.js` | clean | JS 语法 | 浏览器行为、状态机、可访问性 |
| `uv lock --check` | clean | lockfile 可解析 | 依赖无漏洞、环境已同步 |
| `bandit -r src` | 0 High；33 Medium 候选 | 发现 XML/动态 SQL 等复核入口 | 动态 SQL 多为白名单片段，不能一律算注入 |
| `pip-audit` 默认依赖 | 30 包；2 包有公告 | 当前基础依赖快照 | optional `all` 部署面 |
| `pip-audit --extra all` | 107 包；6 包有公告 | 完整常用部署依赖面 | 公告是否在本项目可达 |

### 2.1 源码覆盖率按子系统

| 子系统 | Python 文件 | statement coverage | branch coverage | 风险解释 |
|---|---:|---:|---:|---|
| `agents/` | 26 | 90.1% | 72.5% | 语义验证与真实容器仍主要 mock |
| `api/` | 7 | 86.9% | 69.5% | 前端 JS 行为与并发计费未覆盖 |
| `bench/` | 5 | 97.9% | 90.4% | 夹具只有三种文档形态 |
| `cli/` | 7 | **41.9%** | **34.9%** | 参数组合、真实入口和只读命令风险最高 |
| `config/` | 3 | 86.3% | 53.1% | `[pipeline]` 合并绕过字段校验 |
| `core/` | 7 | 96.8% | 81.5% | 高覆盖不代表跨模块契约完整 |
| `disciplines/` | 8 | 96.6% | 85.7% | 多学科端到端仍不完整 |
| `llm/` | 14 | **66.3%** | **55.9%** | vision/tool/usage/failover 是主要空洞 |
| `pipeline/` | 48 | 78.1% | 69.3% | orchestrator/VLM/review/persist 的关键分支偏低 |
| `storage/` | 6 | 85.5% | 72.1% | schema shape、跨租户与 lifecycle 未被证明 |

最低覆盖的核心文件包括：`llm/parser.py` 6%、`cli/serve.py` 19%、
`cli/generate.py` 21%、`stage2_review.py` 38%、`cli/parse.py` 43%、
`vlm_converter.py` 43%、`stage1_review.py` 46%、`stage3_persist.py` 47%、
`recognizers.py` 59%、`orchestrator.py` 59%。这组文件与本轮高风险发现高度重合。

### 2.2 文件与功能覆盖矩阵

| 范围 | 数量/规模 | 已做的核对 | 结论落点 |
|---|---:|---|---|
| `src/sciforge` | 176 个资产；138 个 Python source files | 逐模块读码 + call-site/模型/存储交叉检索 + coverage | 五份工作流报告 + 本台账 |
| `tests` | 159 个资产；1318 tests | 全量执行；按 pipeline/agents/api/storage/CLI 分类审阅 | 各报告“测试缺口” |
| `Docs` | 35 个资产 | 当前契约、历史蓝图、逐行详解、PROGRESS 交叉对账 | `report-cross-cutting.md` |
| prompts/templates | 35 个资产 | 输入字段、输出 schema、system/user cache 边界 | pipeline/QGen 报告 |
| config/scripts/root docs | 9+ | TOML、pyproject、CLI 脚本、README/AGENTS/CLAUDE | cross-cutting 报告 |
| `audit/` + `plan/` SVG | 7 张 | XML、实际渲染、文字 bbox、越界/重叠、箭头语义逐张检查 | §6 图件台账 |

“覆盖每一行”在工程审计中不能诚实地等同于“每行已被测试或形式化证明”。本轮的可验证
含义是：每个源码文件已纳入模块盘点；每个关键入口被顺/逆向追踪；每个测试实际执行；
每份文档进入状态分类；未覆盖分支被显式量化，而不是用“未发现问题”冒充正确。

## 3. Data Pipeline 新增/升级发现

| ID | 严重性 | 状态 | 证据 | 结论 |
|---|---|---|---|---|
| DP-01 | **Critical** | **复现** | `resume_state.py:36-50`; `orchestrator.py:291-307,323`; `parse.py:149` | checkpoint 只绑定拼接后的源字节 hash，不绑定目标 DB、discipline、config、模型、prompt、schema 或运行模式；完成 state 对全新 DB 直接早退，返回 completed 但不建库。dry-run/skip-LLM/force-VLM 也共享正式 state/cache。多卷 hash 无长度边界，`ab+c == a+bc`。 |
| DP-02 | **High** | **复现** | `config/settings.py:446,482`; `parse.py:299-312` | TOML `[pipeline]` 在模型构造后用 `setattr` 覆盖，`pages_per_batch=0`、零并发、负窗口和错误字符串绕过 Pydantic；可无限循环、死锁或资源耗尽。 |
| DP-03 | **High** | **复现** | `sqlite_backend.py:235-281,501-530` | content hash 主要按正文，accepted 首写胜出；相同题目后续更完整解答、证明、标签和来源不会合并。缺 canonical item + occurrence + revision，`--restart` 也不重建既有 accepted 行。 |
| DP-04 | **High** | **静态证实** | `orchestrator.py:518-527,531-557,621-703,705-735` | post-pass 失败多为 warning，之后仍可标 completed；没有逐 pass 版本、输入指纹、失败状态和独立重跑。多卷 pass 作用域也不一致。 |
| DP-05 | **High** | **静态证实** | `stage1_review.py:80-110`; `llm_schemas.py:327-362`; `stage2_annotate.py` batch path | review 是位置 1:1 数组：不能插入漏项，`null` 在上游 span 已消费后永久删除；同长度换序可静默错绑，因为模型输出没有稳定 item ID。 |
| DP-06 | **High** | **静态证实** | `format_adapters.py:40`; `recognizers.py:397-432`; `orchestrator.py:824-833`; `stage1_extract.py:730-781,1124-1127` | adapter 的 `source_locator` 到 `BatchResult` 后未进入 `MdBlockSource`/DB；跨 block carry 常归到最新 block，多页 batch 只保存粗 page_start/page_end，provenance 不是逐 item 精确映射。 |
| DP-07 | **High** | **复现** | `cli/parse.py:752-837`; `sqlite_backend.py:397-446` | 声称只读的 `parse validate`/`db-stats` 构造普通 backend，会创建/迁移 schema、切 WAL 和写 pragma；0-byte DB 被体检扩成约 260 KiB。 |
| DP-08 | **High** | **静态证实** | `config/sciforge.toml:191-226`; `vlm_converter.py`; `llm/backends/anthropic.py:251-291` | 当前配置理论在途上限为 20 files × 10 VLM batches × 10 pages = 2000 张 400-DPI 页图；渲染在全局 LLM semaphore 前，无全局 pixel/byte budget。vision 不记录 stop reason/usage，截断仍可缓存。 |
| DP-09 | High 候选 | 静态证实 | `orchestrator.py` failed batch path | 失败批后续成功不会从 `failed_batches` 移除，文档可永久 incomplete；完成条件读取的是历史失败账而非 unresolved failure。 |
| DP-10 | High 候选 | 静态证实 | `stage3_persist.py:108-130` | Stage3 所谓“禁止 item_id 倒退”实际只 log，随后仍 append/persist；排序不变量被破坏时不会 fail closed。 |
| DP-11 | Medium | 静态证实 | `extraction_harness.py`; `extract_evaluator.py`; `cli/parse.py` client construction | harness 的 `carry_out`、`patch_log`、token usage、evaluator verdict 未形成执行闭环；`extract_validate`/`repair` 虽在 config 中，但 pipeline 主线未创建独立 client。 |
| DP-12 | Medium | 运行校验+静态证实 | `config/sciforge.toml:50-54,155-159` | DeepSeek 被声明为 OpenAI-compatible 却指向 `/anthropic`，环境变量拼成 `DEEKSEEK`；`config validate` 仍报告 OK，说明 validator 只验形状、不验 capability/endpoint/env 可用性。 |
| DP-13 | Medium（安全） | 静态证实 | `llm/client.py:945-1008`; converters/prompts | 原始教材和模型输出会写入 DEBUG/JSONL（截断但未脱敏），无 retention/权限策略；文档内容与控制指令同处 user prompt，转换工具和 prompt-injection 边界需要单独 threat model。 |

上述发现不是在旧编号旁再堆补丁：DP-01/03/04 说明当前持久化模型缺少“运行身份、提交
身份、版本身份”三层概念，必须先修基础契约，再优化并发与 LLM 调用数。

## 4. QGen / Agent Harness 新增/升级发现

| ID | 严重性 | 状态 | 证据 | 结论 |
|---|---|---|---|---|
| QG-01 | **High** | 静态证实 | `generator.py:184-196`; `solver.py:39-42`; `verifier.py:316-319`; `evolver.py:146-165`; `api/app.py:327-335`; `export/__init__.py:13-49` | 选择/判断/填空的 options/key/blanks 只在生成器落模；solve、review、CLI/API 输出、试卷导出均只看 content。evolve 改题干后还可沿用旧选项/答案并绕过 prefilter，错误 key 仍可能“验证通过”。 |
| QG-02 | **High** | 静态证实+测试夹具 | `verifier.py:471-523`; `verification_sandbox.py:358-461` | Lean evidence 只检查 LLM 生成的某段 theorem 是否无 `sorry` 且可编译，不绑定题目、解答和 assumptions；“strict/formal”不能宣称语义形式化验证。 |
| QG-03 | **High** | 静态证实 | `cli/generate.py:200-208,446-450,513-519,604-613,725-735` | `--llm-config` 用于建 LLM client，但 `QGenAgent.from_config` 多处又接 `AppConfig()`，自定义 QGen flags/budget/sandbox 配置被静默忽略。 |
| QG-04 | **High** | 静态证实 | `verifier.py:413-426`; `verification_sandbox.py:475-492` | `trusted_local` 没有 provenance gate；数值代码由模型生成后可直接在 host Python 执行。文档声称“只允许人工审核仓库代码”，实现未强制。 |
| QG-05 | **High** | 静态证实 | `runtime/models.py:131-139`; `durable_harness.py:175-320` | checkpoint 保存 state/cursor，但没有 provider/model/prompt/tool/schema/sandbox 版本 manifest；resume 可能在不同执行语义下继续旧 run，审计不可重放。 |
| QG-06 | **High** | 静态证实 | `conversation_routes.py:2001-2035`; `storage/saas.py:270-284` | usage_records 无 run/delivery 幂等键；成功回调或 reconciliation 重试可重复计费，失败只写事件而没有可重放 outbox。 |
| QG-07 | **High** | 静态证实 | `api/app.py:537-567`; `solver.py`; discipline prompt routing | legacy `/v1/solve` 硬编码 `Discipline.MATH`；多学科表面契约与 solve/system prompt 路由不一致。 |
| QG-08 | Medium | 静态证实 | `population.py:41-98` | selection 只接收 `verification.passed` 候选，因此 correctness 与 required verification_strength 对所有候选恒为 1；“五目标选择”实际主要剩难度、novelty 和解答长度启发式。 |
| QG-09 | Medium（成本） | 静态证实 | `llm/client.py:294-310`; `generator.py:100-109`; `llm/backends/anthropic.py:102-123`; `verifier.py:308-387` | QGen 长静态指令留在动态 user prompt，cache system 前缀几乎未利用；3 个候选串行全量调用；独立 review 已看完整题解后又用同 client 做 relevance。应拆 static/dynamic、按需候选，并在同一独立 reviewer schema 合并相关判定。 |
| QG-10 | Medium | 静态证实 | `cli/generate.py`; `api/app.py`; `api/conversation_routes.py`; QGen factory call-sites | REST、CLI、conversation 三条入口分别拼 config、artifact、discipline 与副作用，造成 custom config、multi-discipline、计费和输出语义漂移；应收敛为 thin adapter → 单一 application service → durable effect layer。 |

Agent harness 的目标不应是增加更多 agent 名称，而是建立：稳定 artifact schema、执行 manifest、
证据与命题绑定、幂等副作用、可恢复 lease，以及可测的拒绝语义。当前十大 Agent 状态表中的
骨架数量不是主要成熟度指标。

## 5. API、存储、LLM 与供应链新增发现

| ID | 严重性 | 状态 | 证据 | 结论 |
|---|---|---|---|---|
| XC-01 | **High** | 静态证实 | `api/app.py:91-115,128-136` | legacy `generate` scope 被扩成 conversations/runs/templates/knowledge/papers/tasks/usage 多项读写；这不是 least privilege，应做显式版本迁移而非运行时权限膨胀。 |
| XC-02 | **High** | 静态证实 | `api/tenant_storage.py:209-220` | `__getattr__` 用名字黑名单阻断 question 方法；不含 `question` 的 concept/evaluation/embedding/figure/semantic API 可委托到底层，安全边界应改为最小能力白名单。 |
| XC-03 | **High** | 静态证实 | `storage/sqlite_backend.py:414-446`; `schema.sql` | v14 只检查两张核心表和 `user_version`；`CREATE ... IF NOT EXISTS` 不修已有错误列/索引/trigger。损坏或半迁移 DB 可被当作 v14 继续写。 |
| XC-04 | Medium | 静态证实 | `llm/client.py:968-1008` | prompt/response 明文日志包含教材、用户问题与可能的秘密；轮转只有单个 `.1`，无 TTL、权限、redaction 或 tenant 分区。 |
| XC-05 | Medium（供应链） | **在线扫描** | `uv.lock`; pip-audit 2026-07-11 | `--extra all` 的 107 包中，chromadb、cryptography、idna、urllib3、pydantic-settings、starlette 有公告。多数当前代码路径不可达或受部署条件限制，不能夸大为已利用漏洞，但 lockfile 必须升级并纳入 CI。 |
| XC-06 | Medium | 实测 | coverage JSON | CLI 42%、LLM 66%、Pipeline 78%；高风险边界恰是低覆盖区。CI 需设风险分支门禁，而非只看全局百分比。 |
| XC-07 | Medium（性能） | 静态证实 | `llm/client.py:82-97` | 先拿全局 semaphore 再等 model semaphore；一个已饱和模型可占住全局许可，饿死其他模型。应先做公平调度/按 provider 队列，不能嵌套持锁等待。 |
| XC-08 | Medium（计费） | 静态证实 | papers/export endpoint + lazy `python-docx` | DOCX 渲染依赖缺失/失败发生在配额检查或 usage 记录路径之后，失败请求的结算语义不清；需 reservation→commit/release。 |
| XC-09 | Medium（运维） | **复现** | `.venv/.../_editable_impl_sciforge.pth`; `pyproject.toml` | 当前 iCloud hidden flag 使 Python 3.13 跳过 editable `.pth`，`uv run sciforge` 报 ModuleNotFound；pytest 因 `pythonpath=["src"]` 仍全绿，掩盖真实入口坏掉。 |
| XC-10 | Medium | 静态证实 | pipeline audit/storage duplicate queries | duplicate/audit 的 tenant/discipline 范围与实际唯一约束不总一致，可把合法跨 tenant/discipline 内容报成重复。 |

Bandit 的动态 SQL 报警多数来自字段/占位符白名单拼装，当前未找到用户输入直接成为 SQL
结构的路径；不能把 33 个 Medium 候选直接列成 33 个注入漏洞。XML `ElementTree` 解析
未受信 XLSX/EPUB 则是应保留的真实加固项（原 `IS-11`）。

## 6. 七张 SVG 逐图 QA 台账

| 图 | 发现并修复的语义问题 | 几何修复 | 最终状态 |
|---|---|---|---|
| `diagrams/system-overview.svg` | 撤销 tenant/cache/迁移过度正面结论；加入 DP-01、QG-01/02/04/05/06/08/09/10、XC-01/02/03 | 长文本拆短，消除 3 处越框与跨栏拥挤 | XML✓ · overflow 0 · overlap 0 · render✓ |
| `diagrams/data-pipeline.svg` | `q_rev` 原箭头向上穿入 Stage1Review；补上 `q_ann`；加入 run identity/resource/stable-ID/SourceMap/canonical storage；撤销 IS-13 | 反向箭头改为 queue→Stage2；4 处文字缩放/改写 | XML✓ · overflow 0 · overlap 0 · render✓ |
| `diagrams/qgen-agent-harness.svg` | 原失败回路画成 `verify→evolve→diagnose→annotate`；改为 `verify/solve fail→diagnose→evolve→solve`，并补 pass/next/select；加入 artifact/evidence/manifest/trusted-local/cost | 节点/标签重排，箭头不穿框，选择节点文字缩放 | XML✓ · overflow 0 · overlap 0 · render✓ |
| `diagrams/frontend-backend-sequence.svg` | 加 scope/TenantView、structured artifact、evidence、effect/usage、恢复 manifest 边界；收窄 tenant 正面结论 | create_run 相邻文字拆开 | XML✓ · overflow 0 · overlap 0 · render✓ |
| `wireframes/component-decomposition.svg` | 增加 `artifact.js`、`evidence.js` 和 typed projection；证据面板显示 binding 层级 | 数据箭头重排为 store→artifact→evidence→artifact view，消除交叉拥挤 | XML✓ · overflow 0 · overlap 0 · render✓ |
| `wireframes/key-flows.svg` | 生成流加入 QuestionArtifact 与 EvidenceBinding 发布门禁；KaTeX/XSS 约束改为普通内容 DOM API | PATCH/DELETE 字号修正 | XML✓ · overflow 0 · overlap 0 · render✓ |
| `wireframes/target-workbench.svg` | 展示 form 字段、artifact/solution digest、manifest、`compiled_unbound` 并阻断 formal 发布；usage 带 effect | 移动会话/取消注释，缩短 artifact 标签 | XML✓ · overflow 0 · overlap 0 · render✓ |

最终检查使用 `xmllint`、1800px PNG 实际渲染、逐张视觉检查、Hiragino 字体 bbox、
text/text overlap、text/rect overflow 与箭头语义复核；7/7 图均达到零越框、零文字相交、
零 viewBox 溢出。仅 XML 合法不算通过。

## 7. 重审后的优先级结论

真正的 P0 不是先补某个 agent 或 UI，而是先恢复五个可信基础：

1. **运行身份**：source/config/model/prompt/schema/sink 全指纹；dry-run 隔离；完成必须有 sink commit。
2. **无损守恒**：stable-ID patch、marker/locator ledger、任何 reject 都可追踪并可重放。
3. **数据版本**：canonical item + occurrence + revision，禁止 first-write-wins 吞掉更完整来源。
4. **证据绑定**：题面（含 options/key/blanks）→解答→程序/Lean artifact 同一 digest；未绑定不得叫 verified。
5. **副作用幂等**：lease/fencing、billing outbox、artifact persist、post-pass DAG 均以幂等键提交。

在这五项成立后，批量、并发、prompt cache、模型分级、Message Batches 才是真正的低成本优化；
否则它们只会更快地放大静默错误。
