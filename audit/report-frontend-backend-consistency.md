# 审计报告 · Workstream 4 — 前后端契约一致性 + 后端 API 质量

> 范围：前端 `app.js` ↔ 后端 `/v1/*` 契约、鉴权链路、多租户边界、SSE 协议、后端 API 自身质量。
> 时序图：[diagrams/frontend-backend-sequence.svg](diagrams/frontend-backend-sequence.svg)
> **重审补充**：历史逐端点 tenant 联合定位结论仍有价值，但不能推出 storage capability 全面 fail-closed；新增 XC-01/02、QG-06/07 与 XC-08 以 [RE-AUDIT-REGISTER](RE-AUDIT-REGISTER.md) 为准。

---

## 0. 权限、artifact 与副作用契约增量

| ID | 等级 | 结论 | 修复方向 |
|---|---|---|---|
| XC-01 | High | legacy `generate` scope 被别名扩展到 conversations/runs/templates/knowledge/papers/tasks/usage，单一旧权限获得远超命名的能力 | 移除别名扩权；逐 endpoint capability；迁移期只允许显式复合 scope |
| XC-02 | High | `TenantScopedStorageView` 通过方法名黑名单阻断少数 API，再透明 `__getattr__` 委托；新增 concept/evaluation/semantic/figure/embedding 方法时默认放行 | 改能力白名单 facade；无 tenant-safe wrapper 的方法默认不可达 |
| QG-01 | High | API/Conversation/Paper schema 没有统一结构题型字段，前后端无法保证同一题对象 | 版本化 `QuestionArtifact` 作为请求、事件、artifact、paper、export 的共同 schema |
| QG-06/XC-08 | High | usage、artifact、question persistence/reconciliation 没有统一 effect idempotency key | transactional outbox + effect unique key；SSE 只投影 effect 状态 |
| QG-07 | Medium/High | `/v1/solve` 硬编码数学，API 的多学科陈述与路由事实不一致 | request 必填/推导 discipline，并在 artifact/manifest 固化 |

这意味着权限测试必须从“已知 endpoint 的 IDOR 矩阵”升级为“所有 storage capability 的 tenant/property test”，契约测试必须从字段存在升级为 artifact round-trip。

## 1. 端点契约矩阵（现状）

后端共 **23 条路由**（`app.py` 12 + `conversation_routes.py` 11）+ 静态挂载。鉴权模式下：

| 方法 · 路径 | auth | scope | quota | 前端接入 |
|---|---|---|---|---|
| GET /v1/health | 无 | - | - | 否 |
| POST /v1/auth/register · login | 无 | - | - | ✓ |
| POST /v1/generate | Key/Bearer | generation:write | 计1 | ✗（仅弹说明） |
| POST /v1/solve | Key/Bearer | solve:write | 计1 | ✗ |
| GET /v1/usage | Key/Bearer | usage:read | 不检查 | ✓ |
| POST /v1/behavior | 仅 Bearer | 无 scope | 否 | ✗ |
| GET /v1/recommend | 仅 Bearer | 无 scope | 否 | ✗ |
| POST /v1/generate/batch | Key/Bearer | generation:write | **原子预留**（唯一） | ✗ |
| GET /v1/tasks/{id} | Key/Bearer | tasks:read | 否 | ✗ |
| POST /v1/papers · papers/export | Key/Bearer | papers:write | 计（按题数，事后） | ✗（仅弹说明） |
| POST /v1/conversations | Key/Bearer | conversations:write | 否 | ✓ |
| GET /v1/conversations[/{id}[/messages]] | Key/Bearer | conversations:read | 否 | ✓ |
| POST /v1/conversations/{id}/messages | Key/Bearer | conv:write + (solve\|gen):write | 计1 | ✓ |
| GET /v1/runs/{id} | Key/Bearer | runs:read | 否 | ✓ |
| GET /v1/runs/{id}/events (SSE) | Key/Bearer | runs:read | 否 | ✓ |
| POST /v1/runs/{id}/cancel · resume | Key/Bearer | runs:write | resume 检查 | ✓ |
| GET /v1/templates | Key/Bearer | templates:read | 否 | ✓ |
| GET /v1/knowledge/search | Key/Bearer | knowledge:read | 否 | ✓ |

**鉴权链**：authenticate（Bearer 优先，open 模式 `Principal(scopes={"*"})`）→ enforce_scope（Bearer 恒 `*`；API Key legacy scope 经别名扩展）→ enforce_quota（`has_quota`，quota≤0 无限）。会话 tenant：空 tenant 映射 `LOCAL_TENANT_ID="local"`。

**SSE 协议**：`id:<seq>\nevent:<type>\ndata:<json>\n\n` + 注释心跳 `: heartbeat`（15s）+ 轮询 runtime DB（0.2s）；`Last-Event-ID` header → `after_sequence` 重放；终态 = run 进入 {succeeded/failed/cancelled/paused} 且已见 assistant 终态事件，否则 0.25s grace 后关闭。

**前端未接的纯 API 能力**：generate/solve/generate-batch/tasks/papers/behavior/recommend — 控制台 `handleLegacy` 仅弹说明对话框。组卷/推荐是纯 API 能力。

## 2. 能力现状

| 能力 | 状态 | 依据 |
|---|---|---|
| 双通道鉴权 | ● 有缺陷 | X-API-Key sha256 + Bearer HMAC-SHA256（24h exp，无 refresh/吊销）；默认密钥仅告警（FB-06） |
| 配额/计费 | ◑ 部分 | 仅 batch 原子预留；generate/solve/papers/消息均 check-then-record 竞态（FB-04） |
| 多租户（已审 endpoint） | ◑ 显式路径较好 | conversations/messages/runs 等已审路径有 tenant 联合定位且未复现 IDOR；storage facade 是黑名单委托，不能称全面 fail-closed（XC-02） |
| SSE/长任务 | ● 已实现 | Last-Event-ID 双端配合正确、幂等交付 uuid5、startup 恢复 + reconciliation；DB 轮询（FB-08） |
| 异步批量 | ◑ 部分 | BackgroundTasks 进程内，宕机后永久 pending 且配额预留不释放（FB-05） |
| 注入面 | ● 干净 | 参数化 LIKE + `_escape_like`；FTS5 MATCH 未暴露且被 tenant view 阻断 |
| 速率限制 | ○ 未实现 | 文档已声明；register 也无限制（FB-07） |

---

## 3. High 发现

### 🟠 FB-01 — 消息历史分页契约断裂：超过 1000 条后新消息永久不可见

**类别** 一致性 · **复核 代理**

`ConversationStore.list_messages` 以 `ORDER BY created_at, message_id LIMIT ?` 返回**最旧的** N 条（`conversations.py:252-257`），API `limit` 上限 1000，无 cursor/offset；前端固定 `messages?limit=1000`（`app.js:263,738`）。会话消息超过 1000 条后，`refreshCurrentMessages` 只拿到最旧 1000 条，新产生的 assistant 结果在 UI 中**永远不出现**（SSE 结束后的刷新也失效），且**无任何报错**。根因：分页契约只做 limit 没做方向/游标，前后端都按"全量"假设实现。**修复**：改为按 created_at DESC + cursor 分页，前端加载更多。

---

## 4. Medium 发现（紧凑）

| ID | 类别 | 摘要 | 证据 |
|---|---|---|---|
| FB-04+IS-05 | 设计（并发） | 单发 generate/solve/papers 配额 check-then-record 竞态可超卖；papers 单请求可超扣（两代理独立发现） | `app.py:413-417,723`；`saas.py:332-379` 仅 batch 原子 |
| FB-05 | 代码缺陷 | 批量任务宕机后永久占用配额预留（reserved 不释放），租户批量整月 429 直到手工改库 | `saas.py:360-368`；`_run_batch` 无 startup 恢复 |
| FB-06 | 设计（安全） | 默认 `jwt_secret="dev-secret-change-me"` 仅告警不阻止启动 + token 无吊销 → 持默认密钥者可对已知 tenant_id 伪造 token | `app.py:354`；`serve.py:116-123` |
| FB-07 | 设计（安全） | register 无速率限制无密码策略 + 每次注册自动开租户送配额（无限注册=无限免费额度）+ 失败注册残留孤儿租户 | `schemas.py:93-95`；`saas.py:218-228` |
| FB-08 | 性能 | SSE 每客户端 0.2s 新建 SQLite 连接 + 路由层多处同步 DB 调用未 `to_thread`，阻塞事件循环 | `conversation_routes.py:2210,2229` |
| FB-09 | 性能 | startup 恢复无并发上限（create_task 无 semaphore）+ reconciliation 每次启动全量重放历史 run link | `conversation_routes.py:310-315,333-349` |

## 5. Low 发现（登记）

FB-11（SSE 终止 grace 0.25s 与 assistant.final 写入竞态）· FB-12（原始异常文本 `str(exc)[:500]` 进用户可见消息与 SSE）· FB-13（前端依赖后端未定义的 `template.mode` 字段，潜伏契约）· FB-14（无 auth 模式发现端点，连接徽章可误导）· FB-15（Docs/08 三处与实现不符）· FB-16（question.persisted/assistant.paused 前端不可见，同 FE-14）· FB-17（会话重命名/删除无端点，按钮永久禁用，同 FE-10）· FB-18（run 创建与链接写入间崩溃产生不可终结孤儿 pending run）· FB-19（SSE heartbeat/非法 Last-Event-ID/并发双消息未测）。

## 6. 测试缺口

`tests/test_api` 11 文件对 23 条路由均有至少一次命中（含 SSE Last-Event-ID 补发、terminal grace、租户隔离矩阵、startup 恢复/reconciliation、幂等 resume/cancel）。**未覆盖**：SSE 15s heartbeat 帧、非法 Last-Event-ID→400、open 模式 /v1/usage 404、同会话并发双消息（后端无每会话串行化，该并发语义本身未定义）、papers/export 501 分支、以及**前端 JS 全部运行时逻辑**（test_console 仅静态断言，FB-02/03/10 类缺陷无法被拦截）。

## 7. 正面结论（须并列呈现）

- **已审显式端点未复现跨租户 IDOR**：conversations/messages/runs/cancel/resume/SSE 使用 tenant 联合定位，`require_owned_run` 还做 run metadata + conversation link 双源核对，foreign id 与 missing 同响应。该结论只覆盖已列端点；`TenantScopedStorageView` 的方法黑名单 + 透明委托不是能力级 fail-closed（XC-02），必须用白名单 facade 与自动 capability 测试补强。
- **注入面干净**：API 检索用参数化 LIKE + `_escape_like`；FTS5 MATCH 未暴露且被 tenant view 阻断；无字符串拼接用户输入。
- **SSE 长任务基础设施完整**：持久 run/checkpoint/event、Last-Event-ID 双端配合正确、幂等交付（uuid5）、startup 恢复 + reconciliation 补齐缺失 assistant/artifact/question/usage、协作取消、budget pause/resume。
- **跨进程写安全**：ConversationStore/SQLiteRunStore 均每调用新连接 + WAL + BEGIN IMMEDIATE + busy_timeout；`serve.py` 结构上锁死单 worker（多 worker 风险当前不可达）。
- **Docs/08 端点表与代码逐条比对基本一致**（少量漂移见 FB-15）。
