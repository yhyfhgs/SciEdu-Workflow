# 详细计划 · plan-04 — 跨领域（CI / 文档 / 契约 / 鉴权 / 配额）

> 消化 [report-cross-cutting.md](../audit/report-cross-cutting.md) 与 [report-frontend-backend-consistency.md](../audit/report-frontend-backend-consistency.md) 的后端/契约发现。代码片段均为 **proposed / not-yet-implemented**。
> **重审说明**：新增 XC-01~10 与 QG-06/07 后，CI 必须验证“可安装产品 + capability/schema/effect 契约”，不能只在 `pythonpath=src` 下跑单测。

---

## P0-R — 生产边界门禁（XC-01~10）

### P0-R.1 · fresh-install 与 coverage/security gates（XC-05/06/09）

- 在干净 venv 执行 lock check → install package → `sciforge --help` → `sciforge config validate` → 最小 `parse validate` readonly smoke；禁止 pytest 的 source path 掩盖 editable/packaging 故障。
- 按子系统设置 line/branch floor，先以当前实测值为 non-regression，再逐步提高 CLI/LLM/pipeline 低覆盖模块；报告总 coverage 不能代替关键分支。
- CI 加 pip-audit（default + extras 分层）、Bandit triage allowlist、secret scan、SVG XML/geometry/render smoke。安全工具发现必须记录 reachability，不按总数制造虚假严重性。

### P0-R.2 · capability 权限与 tenant facade（XC-01/02/10）

- 删除 legacy `generate` → 多 scope 自动扩权；API key migration 显式列出新 capabilities，用户确认后再换 key。
- 每个 endpoint 声明一个或多个最小 capability；测试遍历 route registry，确保无隐式别名放行。
- `TenantScopedStorageView` 改显式白名单 facade，所有方法签名强制 tenant context；新增 storage method 默认不可暴露。
- property tests 对每个可暴露 capability 生成 foreign tenant IDs，验证同响应、无 side effect、无跨租户聚合泄漏；duplicate audit 按 tenant partition。

### P0-R.3 · schema shape、只读与日志治理（XC-03/04/07）

- 生成 schema manifest：table/column/type/notnull/default/PK/FK/index/trigger/check；打开时 diff，不只看 `user_version`。
- readonly command 使用 `mode=ro&immutable=1`（适用时）并禁止 migration/journal/DDL；对文件大小、mtime、schema digest 做 before/after 断言。
- raw prompt/response 默认结构化 redaction，敏感内容 opt-in 加密保存且有 TTL/访问审计；异常文本不直接回用户。
- LLM usage 由每次调用返回 immutable result，去掉并发共享 `last_usage`；scheduler 先按 provider/model 公平排队，再占 global slot，避免全局 semaphore 饥饿。

### P0-R.4 · 幂等副作用（XC-08/QG-06）

usage、assistant message、artifact、question persistence 和 notification 都写 transactional outbox，唯一键为 `(run_id,effect_type,effect_version)`。worker/reconciliation 只 claim effect；成功 commit 后 ack。配额 reservation/confirm/release 与同一 effect ledger 对账，避免 retry 双扣或失败漏退。

## P0 — CI 回归门禁（缺失功能，最先落地）

**问题**：仓库无任何 CI，1318 测试 + ruff + mypy strict 的绿色基线全靠人工执行。所有后续修复缺乏回归保护。

**目标态**——最小 GitHub Actions（`proposed .github/workflows/ci.yml`）：
```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --extra all
      - run: uv lock --check
      - run: uv build
      - run: uv venv .wheel-venv
      - run: uv pip install --python .wheel-venv/bin/python dist/*.whl
      - run: .wheel-venv/bin/sciforge --help
      - run: .wheel-venv/bin/sciforge config validate
      - run: uv run ruff check src tests
      - run: uv run ruff format --check src tests
      - run: uv run mypy
      - run: uv run pytest --cov=src/sciforge --cov-branch -q
      - run: node --check src/sciforge/api/static/app.js
      - run: uv run pip-audit
      - run: xmllint --noout audit/diagrams/*.svg plan/wireframes/*.svg
```
**理由**：这是**第一优先**——先建门禁，后续每个修复阶段的 PR 才有强制回归保护。可选加 `parse validate` 冒烟（需夹具 DB）。

**验收**：PR 触发 CI，任一检查失败即红。

---

## P5 — 生产化硬化（FB-04/05/06/07/08/09, IS-10；缺失功能：鉴权生命周期）

### P5.1 · 配额原子预留（FB-04+IS-05 🟡，两代理独立发现）
把单发端点从 check-then-record 改为**原子预留**（复用 batch 已有的 `create_task_if_quota_available` 的 `BEGIN IMMEDIATE` 模式）：
```python
# proposed: enforce_quota 改为预留 token，成功后 confirm、失败后 release
reservation = saas_store.reserve_quota(tenant_id, endpoint, units=1)  # 原子
try:
    result = await agent.run(...)
    saas_store.confirm_reservation(reservation)
except Exception:
    saas_store.release_reservation(reservation)
    raise
```
papers 按实际题数在预留内结算，避免单请求超扣。

reservation 必须与 P0-R.4 的 effect id 绑定；HTTP retry、worker retry 与 startup reconciliation 对同一 effect 只确认一次。

### P5.2 · 批量任务恢复 + 配额释放（FB-05 🟡）
- `_run_batch` 加 startup 恢复（与会话 run 一致）或 TTL 超时释放。
- 宕机时 pending/running 任务的 reserved 配额随恢复/超时**释放**，避免租户整月 429。

### P5.3 · 消息分页契约（FB-01 🟠）
```python
# proposed conversations.list_messages
def list_messages(self, cid, *, before: str | None = None, limit: int = 50):
    # ORDER BY created_at DESC, message_id DESC + cursor(before)
    # 返回最新 N 条 + next_cursor；前端"加载更多"向上翻
```
前端 `app.js` 改为默认拉最新 N 条 + 向上加载更多（修 FB-01 的会话 >1000 条新消息不可见）。

### P5.4 · 鉴权硬化（FB-06/07 🟡；缺失功能 refresh/吊销）
| ID/功能 | 目标态 |
|---|---|
| FB-06 | 非 open 模式 + 默认 `jwt_secret` → **拒绝启动**（不只告警） |
| FB-07 | register 加速率限制 + 密码策略（长度/复杂度）+ 注册事务把 create_tenant 与 create_user 合并（避免孤儿租户） |
| refresh/吊销 | access token 增 refresh token + 吊销列表（jti 黑名单或短 exp + refresh）；`/v1/auth/refresh` 端点 |
| 第三方登录 | OAuth（微信/Apple/Google，Docs/17 P3，需外部凭据） |
| XC-01/02 | scope alias 迁移为 capability；tenant storage 改白名单 facade，新增方法默认拒绝 |

### P5.5 · SSE 与 startup 性能（FB-08/09 🟡、IS-10 🟡）
- FB-08：`_stream_events` 的同步 DB 调用包 `asyncio.to_thread`，或改进程内 pub/sub（事件写入时通知，替代 0.2s 轮询）。
- FB-09：startup 恢复加 semaphore 限并发 + reconciliation 加时间窗/水位（不全量重放历史）。
- IS-10：SaasStore 补全所有写方法的锁（或统一走单一加锁入口），防未来多线程事务污染。

---

## P7 — 文档对账（DR-01~15）

### 权威文档修复（High）
- **DR-01**：CLAUDE.md 补 `api/conversation_routes.py`/`conversations.py`/`static/app.js` + conversations.db 分库 + conversation 端点清单。
- **DR-02**：CLAUDE.md 修正"durable 未接 CLI"表述 → 已有 `durable/resume/cancel`；补 generate 命令清单。

### Medium/Low 文档漂移
| ID | 修复 |
|---|---|
| DR-03 | CLAUDE.md/README disciplines 清单改五学科 |
| DR-04 | 补 `bench/` + `parse bench`/`cost-report` 文档 |
| DR-05/06/07 | Doc 06/01/Pipeline_v12 版本号与默认值对齐（schema v14、REC-04 批量、v11 开关默认 True、vlm_auto_text_detect=True） |
| DR-08~15 | 悬挂引用 PROGRESS §00、文件名/路径误差、防线计数、AGENTS Stage2 描述、孤儿资产清理 |

### 孤儿资产处置
`test_diag.py`（断链可删）· `Docs/diagrams/*` 等孤儿 SVG（补引用或标注归档）· `data_old/`/`tmp/smoke_*`（归档删除）· `run_pipeline.sh`（补文档或标注）· `scripts/` 前三个（核实对 v14 库适用性）。

---

## P7 — 后端 Low 项
FB-11（SSE grace 超窗兜底重试）· FB-12（异常文本脱敏，不进用户消息）· FB-13（前端 template.mode 契约固化）· FB-14（`/v1/health` 暴露 auth 模式）· FB-15（Doc 08 三处修正）· FB-18（run 创建与链接写入合并事务，消除孤儿 pending）· FB-19（补 SSE heartbeat/非法 Last-Event-ID/并发双消息测试）。其中 **FB-18** 建议提前到 P5（正确性相关）。

---

## 测试与验收

| 阶段 | 关键回归 |
|---|---|
| P0-R | fresh wheel console script、coverage branch floor、scope matrix、tenant capability property tests、malformed-v14 shape、readonly no-write、日志脱敏、effect 重放幂等、dependency/SVG gates |
| P0 | CI 在 PR 上运行全部检查 |
| P5 | 配额并发不超卖、批量宕机释放配额、消息分页游标、默认密钥拒启动、register 限流、SSE 不阻塞 loop |
| P7 | 文档对账后 CLAUDE.md 与代码一致性抽查、补测试通过 |

**验收原则**：P0 的 CI 必须**最先落地**——它是后续所有阶段修复的回归安全网。P5 的鉴权/配额改动需在并发场景（多请求同时打配额边界）下验证原子性。
