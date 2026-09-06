# Web 前端设计与当前契约

> 状态：零构建 Chatbox 工作台已实现，入口为 `/app/`。
> 实现文件：`src/sciforge/api/static/index.html`、`styles.css`、`app.js`。
> 本文先描述可运行事实，再单列尚未实现的产品路线；旧版 Next.js 蓝图不代表当前代码。

## 1. 产品定位

当前前端是 FastAPI 同源托管的专业出题工作台，而不是独立的 Next.js 应用。它以聊天流为
主界面，把约束配置、知识检索、运行进度、验证证据和恢复操作放在同一任务上下文中。

```text
左栏                         中央 Chatbox                         右栏
会话历史                     七种任务模式                         运行时间线
内置模板        ←→           消息流 + 参数面板          ←→       验证证据
快捷工具                     知识引用 + 发送器                    知识检索
```

零构建的含义是浏览器直接加载原生 HTML/CSS/JavaScript，不需要 Node.js、打包器或前端构建
步骤。优点是部署简单、与 `/v1/*` 契约同版本；代价是当前富文本、组件生态和复杂状态管理
能力有限。

## 2. 已实现界面

### 2.1 三栏响应式布局

- 顶栏：当前会话、连接状态、账户/API Key/开放模式入口；
- 左栏：创建会话、最近会话、七个后端 action template、快速生成/求解/组卷/推荐入口；
- 中栏：ChatGPT 风格消息流、七模式工具条、可折叠生成约束和消息输入框；
- 右栏：运行、验证、知识三个 tab；在窄屏上变为可关闭的 overlay rail；
- 可访问性：skip link、ARIA tab/toolbar/log、键盘 `Cmd/Ctrl + Enter` 发送、焦点可达的
  dialog 与移动端 rail。

### 2.2 七种真实模式

| UI 模式 | API `mode` | 输入约束 | 后端语义 |
|---|---|---|---|
| 出新题 | `new` | `topics` 或 `content` | B1 主题生成，走 QGen 检索、候选、求解、验证、难度标注 |
| 自进化 | `evolve` | `topics` 或 `content` | 开启失败诊断、有限 evolve 回边和候选池选择 |
| 微调变式 | `variant` | `source_question_id` | 显式选择 A1/A2/A3/A6 变式策略 |
| 深入推演 | `deepen` | `source_question_id` | B2 跨主题策略并启用 deep solve |
| 调整难度 | `adjust_difficulty` | 源题 + 显式目标难度 | 目标更高走 A4、更低走 A5、相同走 A1 |
| 评估题目 | `evaluate` | `source_question_id` | 准备完整解答后运行 independent reviewer 与选定证据策略 |
| 独立求解 | `solve` | 源题 ID 或完整题面 | 只执行 SolveAgent；策略固定为 `fast`，置信度不等于独立验证 |

领域、题型和题面形式不是任意字符串。前端随领域更新题型选项，后端再次按
`DisciplineSpec.allowed_question_types/allowed_question_forms` 校验。当前注册领域为数学、物理、
化学、生物和其他领域。

### 2.3 生成约束

参数面板已支持：

- 领域、科目、知识主题；
- 领域专属题型与题面形式；
- 目标难度 1–10；
- 源题 ID 与 A1/A2/A3/A6 变式策略；
- 输出类型：题目+解答、仅题目、结构化 JSON、讲义片段；
- 内置模板和 `fast/balanced/strict/formal` 验证策略；
- 后端 run budget（当前 UI 使用固定默认上限；暂停后可通过 dialog 增量扩容）。

`formal` 只对数学证明类题型可选，且后端要求 Lean 证据；领域或题型不适用时，前端禁用
该选项，后端仍会以 422 拒绝非法组合。`solve` 不提供 independent review，前端固定选择
`fast`；要验证现有题目应使用 `evaluate`。

### 2.4 会话与持久运行

前端通过以下真实端点工作：

1. 创建/读取会话与历史消息；
2. `POST /v1/conversations/{id}/messages` 提交命令并立即取得稳定 `run_id`；
3. `GET /v1/runs/{id}/events` 以带鉴权 header 的流式 `fetch` 消费 SSE；
4. 保存最后 sequence，并在重连时发送 `Last-Event-ID` 补发遗漏事件；
5. 终态后读取 run 与消息，恢复最终产物和用量；
6. 运行中可 cooperative cancel；预算暂停后可增量增加 step/LLM/token/wall-time 上限并继续。

刷新页面不会重新执行已完成任务：会话消息保存在 `conversations.db`，运行状态、事件与
checkpoint 保存在 `qgen_runtime.db`。QGen 恢复到最近完成节点后的 cursor，详见
`Docs/22_QGen_Harness_架构与验证协议.md`。

多轮上下文不是无限拼接：服务在当前命令落盘前快照最近最多 8 条消息，每条最多 1200 字符、
总计最多 6000 字符，并把快照写进 durable `GenerationRequest`。恢复时使用同一份快照；历史
只作为不可信约束/指代上下文，不能覆盖 system、验证策略或沙箱边界。

### 2.5 运行与验证检视器

运行 tab 展示 `run.*`、`step.*` 和 `qgen.progress` 的安全摘要，包括当前图节点、轮次、
候选数、LLM 调用、token 和累计执行时间。验证 tab 展示结构化 evidence：

- `passed`：该检查产生肯定证据；
- `failed`：检查得到了明确反例或否定结论；
- `unavailable`：所需后端/编译器/容器不可用；
- `error`：验证执行或结果解析出错。

前端不把 `unavailable/error` 美化为通过，也不展示模型私有 raw chain-of-thought。聊天区只
呈现可审计的题目、结构化解题步骤、方法摘要、验证结论和修正记录。

### 2.6 知识库联动

知识 tab 调用 `GET /v1/knowledge/search`，按领域、canonical 科目、文本和知识质量门禁检索
公共知识条目。结果返回 `source_file/page/start_line/end_line/start_char/end_char`，界面展示可读
来源 locator。用户选择后保存稳定 `knowledge_ids`；显式 pin 仍由服务端检查领域、摄取状态、
概念状态、review queue 与当前 rubric，不因用户点选而绕过门禁。

QGen 成功并通过验证后，后端把题目和 `solution_content` materialize 到当前 tenant 题库，发出
`question.persisted`。前端记住稳定 question id，切换到变式、深入、调难或评估模式时自动填入
源题，不要求用户手抄 ID。知识搜索仍不是题库通用 CRUD，也不允许浏览其他租户私有题目。

## 3. 前后端状态映射

浏览器不维护第二套任务状态机；服务端 durable run 是唯一执行事实来源。

| 服务端事实 | 前端用途 |
|---|---|
| `AgentRunOut.status` | pending/running/paused/succeeded/failed/cancelled 状态徽标 |
| `usage` / `budget` | 调用、token、运行时间与恢复预算 |
| `step.started` / `step.succeeded` 等 | 时间线步骤 |
| `qgen.progress` | QGen 阶段、安全摘要、验证 evidence |
| `evaluation.solution_ready` / `evaluation.verified` | 评估模式解答与证据 |
| `assistant.final/error/cancelled/paused` | 聊天流最终交付状态 |
| `Last-Event-ID` | 断线补发游标；不触发 Agent 重跑 |

前端对 SSE sequence 去重；网络重连只补读 append-only event。取消是安全边界协作取消，
不是浏览器强杀后端进程。

## 4. 认证与安全

- 开放模式：仅用于可信本地服务，不发送凭据；
- Bearer：登录/注册后 token 保存在 `sessionStorage`；
- API Key：同样只保存在当前浏览器 session，并放入 `X-API-Key` header；
- 所有渲染使用 DOM `textContent` 和受限 Markdown 结构，不把服务端字符串直接写入
  `innerHTML`；
- hosted 模式的会话、run、消息和源题访问都由后端按 tenant 校验，前端隐藏不是授权边界。

生产环境仍应使用 HTTPS、明确 CORS origin、正式密钥管理和更完整的 token 生命周期。

## 5. 当前真实限制

以下能力尚未实现，不能从界面文案推断其存在：

- 消息渲染只支持安全的标题、列表和段落；没有 KaTeX/MathJax、代码高亮、表格或完整
  CommonMark；
- 已持久化 artifact 尚无独立浏览/下载 API 和前端 artifact 中心；
- 会话重命名、删除、搜索、分支和分享尚未开放，当前“重命名”按钮保持禁用；
- 知识库是搜索与引用入口，不是可视知识图谱、上传/审核或通用 CRUD 工作台；
- 快捷“组卷/推荐”主要提示现有 REST 能力，尚未与 Chatbox 产物形成完整拖拽式编排；
- 没有文件上传、手写输入、流式 token 文本或离线 PWA；SSE 传输的是持久化运行事件；
- 前端没有独立自动化测试工程，当前回归主要依赖 API 测试与真实浏览器 smoke test。

## 6. Next：独立前端演进路线

只有当产品复杂度超过零构建工作台承载能力时，才迁移到独立 TypeScript 前端；迁移必须
复用现有 `/v1/*` 和 event contract，不能再造运行状态机。建议顺序：

1. 增加 KaTeX/MathJax、代码块、表格、可复制结构化产物；
2. 提供 artifact 列表/详情/导出，并把当前自动延续源题扩展为可视版本谱系；
3. 增加用户可查看/裁剪的有界多轮 context inspector；
4. 完成会话管理、题库浏览、知识图谱和组卷编排；
5. 再评估 React/Next.js、组件库、端到端测试和可选 Tauri 包装。

## 7. 验收

前端相关变更至少验证：

```bash
uv run pytest tests/test_api/test_console.py tests/test_api/test_conversations.py
uv run pytest tests/test_api/test_run_recovery.py
```

并在真实浏览器检查桌面与窄屏：登录/开放连接、创建会话、七模式参数联动、SSE 重连、
取消、预算暂停/恢复、验证四态、知识检索以及刷新后的历史恢复。
