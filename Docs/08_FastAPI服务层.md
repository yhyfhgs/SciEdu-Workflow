# FastAPI 服务层

> 状态：Web SaaS MVP 已实现；本文只描述 `src/sciforge/api/` 当前契约。
> 启动入口：`uv run sciforge serve [--open] [--port 8000]`

## 1. 定位与边界

FastAPI 层把持久对话式 QGen/Solve/Evaluate、单次生成、推荐、组卷和用量能力暴露为
`/v1/*`，并托管零构建控制台 `/app/`。`create_app()` 使用工厂注入
agent/storage/conversation/runtime/SaaS store，便于测试和私有化组装。

当前实现是单进程可恢复 MVP，不是分布式任务平台：Chatbox run 与 QGen graph checkpoint
持久化到 runtime SQLite，进程启动会恢复 pending/running run；批量 REST 任务仍使用
`BackgroundTasks + SaasStore`。多 worker lease、Redis/Celery、秒级令牌桶、refresh token、
第三方登录和题库通用 CRUD/导入 API 尚未实现。

```text
Browser / SDK
  │  X-API-Key 或 Bearer token
  ▼
FastAPI create_app
  ├─ authenticate → scope → quota
  ├─ tenant-scoped content view
  ├─ Conversation → durable QGen / Solve / Evaluate → SSE
  ├─ legacy single-shot Generate / Solve / Recommend / Paper
  ├─ ConversationStore + SQLiteRunStore
  ├─ SaasStore (tenant, key, user, usage, task, behavior)
  └─ static console /app/
```

## 2. 组装与启动

`src/sciforge/cli/serve.py` 构造 QGen/Solve 工厂、`storage_factory`、`SaasStore` 和
`create_app()`。默认开启鉴权与计费；`--open` 只用于可信本地调试，此时没有 tenant scope。

```bash
# 默认：data/sciforge.db + data/saas.db，启用鉴权/计费
uv run sciforge serve --port 8000

# 本地开放模式
uv run sciforge serve --open --port 8000

# OpenAPI 与内置控制台
open http://localhost:8000/docs
open http://localhost:8000/app/
```

## 3. 当前 API 契约

| 方法 | 路径 | scope / 身份 | 计费 | 说明 |
|---|---|---|---:|---|
| GET | `/v1/health` | 无 | 否 | 健康检查 |
| POST | `/v1/auth/register` | 无；需启用 SaasStore | 否 | 创建用户与默认租户，返回 bearer token |
| POST | `/v1/auth/login` | 无；需启用 SaasStore | 否 | 密码认证并返回 token |
| POST | `/v1/generate` | `generation:write` | 1/成功题 | QGen 单题生成 |
| POST | `/v1/solve` | `solve:write` | 1/成功解 | 按可见 question id 或直接 content 求解 |
| GET | `/v1/usage` | `usage:read` | 否 | 当前 UTC 月用量；配额耗尽后仍可读 |
| POST | `/v1/behavior` | Bearer 用户 | 否 | 记录练习行为；API Key 没有 user_id，不能调用 |
| GET | `/v1/recommend` | Bearer 用户 | 否 | 公共+本租户题目中的无 LLM 推荐 |
| POST | `/v1/generate/batch` | `generation:write` | 原子预留 count | 创建异步批量任务 |
| GET | `/v1/tasks/{task_id}` | `tasks:read` | 否 | 仅能读取本租户任务 |
| POST | `/v1/papers` | `papers:write` | 按选中题数 | 确定性组卷预览 |
| POST | `/v1/papers/export` | `papers:write` | 按选中题数 | Markdown；安装 docx extra 后支持 DOCX |
| POST | `/v1/conversations` | `conversations:write` | 否 | 创建 tenant 作用域会话 |
| GET | `/v1/conversations` | `conversations:read` | 否 | 最近会话列表 |
| GET | `/v1/conversations/{id}` | `conversations:read` | 否 | 会话元数据 |
| GET | `/v1/conversations/{id}/messages` | `conversations:read` | 否 | 持久消息历史 |
| POST | `/v1/conversations/{id}/messages` | conversations + generation/solve write | 成功后记账 | 创建 durable qgen/solve/evaluate run，返回 202 |
| GET | `/v1/runs/{run_id}` | `runs:read` | 否 | 当前 status/budget/usage/output |
| GET | `/v1/runs/{run_id}/events` | `runs:read` | 否 | 带 `Last-Event-ID` 的 SSE 补发 |
| POST | `/v1/runs/{run_id}/cancel` | `runs:write` | 否 | cooperative cancel |
| POST | `/v1/runs/{run_id}/resume` | `runs:write` | 否 | paused run 增量预算并恢复 |
| GET | `/v1/templates` | `templates:read` | 否 | 七种 action template |
| GET | `/v1/knowledge/search` | `knowledge:read` | 否 | 带发布门禁与来源 locator 的知识检索 |

请求/响应模型在 `api/schemas.py`，FastAPI 自动生成 `/openapi.json`。当前路径统一使用
`/v1/*`，旧蓝图中的 `/api/v1/*` 不是真实契约。

## 4. 鉴权、scope 与配额顺序

鉴权层只解析 principal，不在同一步混入业务授权：

1. `X-API-Key`：`SaasStore` 只保存 key 的 SHA-256，读取其 tenant/scopes；
2. `Authorization: Bearer <token>`：`api/security.py` 校验 HMAC-SHA256、exp、user/tenant；
3. scope：把 legacy scope 映射到 canonical capability，缺 scope 返回 403；
4. quota：仅在 scope 通过后检查，耗尽返回 429；
5. usage：业务成功后记录，批量在建 task 时一次原子预留 `count`。

月配额按 UTC 月统计。`monthly_quota=0` 表示无限，不表示禁止调用。`GET /v1/usage` 不经过
quota gate，否则用户在最需要解释 429 时反而无法查看用量。

## 5. 多租户内容隔离

离线 pipeline 使用的 `SQLiteStorageBackend` 是有意 unscoped 的维护接口，认证 API 不能直接
暴露它。`api/tenant_storage.py::TenantScopedStorageView` 在请求边界执行：

```text
visible(question) := question.tenant_id == principal.tenant_id
                  OR question.tenant_id == ''  # public corpus
```

- regular SQLite 通过独立 read-only connection 把 visibility 写入 SQL，并在
  `ORDER BY/LIMIT` 之前过滤；数据库有大量外租户行也不会截断当前租户结果；
- bulk/id load 先求可见 id，foreign id 与不存在 id 都返回 not found，避免 existence probing；
- generated save 强制写当前 tenant，拒绝写入另一个 tenant；
- knowledge points 是共享公共 corpus；question reads 默认 fail closed；
- QGen、batch、Solve、recommend、papers/export 都绑定 scoped view；open mode不构造该 view。

`MultiTenantSQLiteBackend` 仍可供原生 tenant-aware storage 使用，但不能用它的存在替代 API
边界审计；工厂若既无可读 `db_path` 又无 tenant-aware search，认证请求返回配置错误。

## 6. 持久对话、恢复与事件流

提交 Chatbox 消息时，服务先在写入当前命令前快照有界历史，再创建稳定 run，并在同一产品
流程中写 user message + run link。QGen 输入因此不会在重试时读取变化后的历史。

- QGen run：每个 graph node 完成后保存 immutable checkpoint 与 `next_node` cursor；
- Solve/Evaluate run：使用同一 run/event/budget 协议，可从持久 input 重建；
- 启动恢复：pending/running 自动调度；budget paused 由用户显式增加预算后 resume；
- 交付恢复：成功 run 若缺少题目 materialization、assistant final 或 artifact，由 reconciliation
  幂等补齐；
- SSE：event sequence 单调，`Last-Event-ID` 之后补发，每 15 秒 heartbeat；网络重连不重跑；
- 取消：在节点/step 安全边界协作取消，不在数据库事务或 sandbox 子进程中间强杀。

生成成功且通过验证时，服务把 Question 与 `solution_content` 写入当前 tenant 题库并发出
`question.persisted`。前端可直接用稳定 question id 发起 variant/deepen/adjust/evaluate。
存储通过 `save_question` 返回同一 tenant 去重后的 canonical id，不能使用被忽略候选的 UUID。
runtime 中的原始生成输出保持不可变；`question.persisted` 是实际入库 ID 的权威映射，GET run、
新交付的产品 artifact 与启动时补齐的缺失交付通过同一投影同步 `question.id` 和 `solution.question_id`。
存储失败时不发出成功入库事件，已成功的原始 run 仍可在后续 reconciliation 中补交付。
已交付的历史 artifact 按不可变记录保留，不执行覆盖或迁移；重启可追加正确的入库映射事件，
使 GET run 返回当前 canonical id，但历史 artifact 的旧 ID 与内容仍可能不同于该当前投影。

## 7. 任务、推荐与导出

- legacy batch：`create_task_if_quota_available()` 在同一事务内检查/预留额度；background task 逐题
  更新 completed/failed，task 查询带 tenant_id；当前不适合多进程抢占或宕机自动续跑。
- recommend：`RecommendEngine` 不调用 LLM，以行为流水构建掌握难度、正确率、薄弱 tags，
  在 scoped candidates 内做最近发展区排序。
- papers：按 discipline/subject/difficulty/type 筛选并确定性选题；Markdown 零额外依赖，
  DOCX 延迟导入 `python-docx`，未安装返回 501。

## 8. 四类数据库职责

| 默认文件 | 所有权 | 关键表 | 不负责 |
|---|---|---|---|
| `data/sciforge.db` | 教育内容 | KP、Question、concept、evaluation、review、FTS | 消息、run event、账号 |
| `data/conversations.db` | Chatbox 产品历史 | conversations、messages、run links、artifacts | graph checkpoint、题库正文 |
| `data/qgen_runtime.db` | Agent 执行事实 | runs、steps、checkpoints、events | 用户密码、公共知识 |
| `data/saas.db` | 账号与商业元数据 | tenant、key、user、usage、task、behavior | QGen state、题目正文 |

分库使高频 SSE event 不与 FTS 查询争写锁，也允许内容 schema、产品 schema 和 runtime schema
独立迁移。跨库一致性用稳定 run id、幂等结果 id 和启动 reconciliation 实现，不依赖跨 SQLite
事务的虚假原子性。

## 9. 文件职责

| 文件 | 职责 |
|---|---|
| `api/app.py` | app factory、路由、principal/scope/quota、agent/storage 绑定 |
| `api/schemas.py` | Pydantic API 契约 |
| `api/conversation_routes.py` | 会话、run、SSE、取消/恢复、模板、知识搜索与产品交付 |
| `api/conversations.py` | ConversationStore 与幂等消息/artifact |
| `api/security.py` | token 签发/校验、密码摘要与安全比较 |
| `api/tenant_storage.py` | fail-closed tenant/public question view |
| `api/static/*` | `/app/` 零构建 Chatbox 控制台 |
| `agents/runtime/store.py` | durable run/step/checkpoint/event/budget store |
| `storage/saas.py` | tenant/key/user/usage/task/behavior SQLite |
| `cli/serve.py` | 配置解析、依赖组装、uvicorn 启动 |

## 10. 安全与产品限制

- 默认 CORS 是 `*` 且 `allow_credentials=False`，适合 header token 联调；生产必须配置明确 origins。
- HMAC access token 当前没有 refresh/吊销表；生产账号体系见 `Docs/17` 剩余项。
- 当前只有月配额，没有每秒/每 IP token bucket；应在网关或中间件补强。
- Chatbox recovery 是单实例调度；SQLite 幂等保证重启恢复，但没有多 worker lease 或分布式
  exactly-once。legacy batch 的恢复能力更弱。
- API 的 `verified` 只映射当前 `VerificationResult.passed`。required sandbox/Lean
  unavailable/error 不能被前端改写为“已验证”。
- API/日志不得返回模型私有 raw chain-of-thought；只提供结构化解题与验证证据。

## 11. 验收

```bash
uv run pytest tests/test_api
uv run pytest tests/test_api/test_run_recovery.py tests/test_agents/test_runtime_store.py
uv run ruff check src tests
uv run mypy src/sciforge
uv run pytest
```

租户相关改动至少覆盖：外租户直接 id、bulk load、前 500 行均为外租户时的分页截断、
QGen/solve/recommend/papers 的 scoped storage，以及 foreign/missing 同响应。
