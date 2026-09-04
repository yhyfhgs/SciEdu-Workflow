# 审计报告 · Workstream 3 — Qgen 前端（Web Chatbox）

> 范围：内置零构建 Web Chatbox（`src/sciforge/api/static/`）架构、状态管理、XSS、SSE、七模式、可访问性、UI 实现质量。
> 前后端契约见 [report-frontend-backend-consistency.md](report-frontend-backend-consistency.md)。
> **重审补充**：前端不只是 FE 层修补；它当前接收的 artifact 本身已在上游丢失 `options/correct_option/blanks`，且验证面板无法区分“编译成功”与“已绑定当前题解”。QG-01/02 是前端目标态的接口前置条件。

---

## 0. 结构化 artifact 与证据呈现增量

| 边界 | 当前事实 | UI 风险 | 目标契约 |
|---|---|---|---|
| 题目卡 | conversation Markdown 与 legacy schema 主要只给 `content/solution/tags/difficulty` | 选择题无选项、填空题无 blank 结构，用户看到的不是模型实际生成对象 | 接收版本化 `QuestionArtifact`，按 `form` 渲染题干、选项、空位、答案键与解析 |
| 验证面板 | 四态来自 verifier，但 Lean `passed` 未绑定题目/解答 | 绿色状态可能只表示一段无关 theorem 可编译 | 展示 `bound/compiled/checked` 三层；`compiled_unbound` 必须 amber 且不可发布 |
| 恢复/历史 | run artifact 无 execution manifest 摘要 | 用户无法判断恢复前后模型/prompt/sandbox 是否变化 | artifact detail 展示 manifest digest 与 verifier spec 版本 |
| 计费 | 同一 run 副作用缺 idempotency key | retry/reconcile 后 UI usage 可能重复 | usage 展示 effect id，后端唯一约束后再汇总 |

因此“artifact 中心”不能只是现有 Markdown 加壳，而要成为 typed artifact 与 evidence binding 的查看器；详细设计已回写 [plan-03](../plan/plan-03-qgen-frontend-design.md)。

## 1. 架构盘点（现状）

前端是**零构建原生三件套**：`index.html`（305 行静态骨架）+ `app.js`（1114 行单 IIFE，`"use strict"`，无 ES module/无依赖/无构建）+ `styles.css`（2117 行）。`node --check` 通过。

- **状态模型**：单例可变对象 `state`（authMode/token/conversationId/mode/runId/lastEventId/streamController/seenEvents/selectedKnowledge…）+ `dom` 元素缓存。无 store/无订阅，纯命令式：事件 handler → `api()` → 直接改 DOM。auth 三态持久化 sessionStorage，其余刷新即失。部分状态由 DOM 承载（`dom.source.value` 被 refreshRun 回写作"源题延续"通道），造成 JS state 与 DOM state **双轨**。
- **视图更新**：全部走 DOM API（`createElement`/`textContent`/`replaceChildren`），**全文件 0 处 innerHTML/insertAdjacentHTML/eval**（rg 核实，测试亦断言禁 innerHTML）。列表全量重建，事件绑定集中 `bindEvents()` 一次挂到静态骨架，动态项 listener 随 replaceChildren 一起 GC，无重复绑定/泄漏。
- **SSE**：fetch + ReadableStream **手写 SSE 客户端**（非 EventSource，因需自定义 auth header）；外层重连循环（AbortController + `state.runId===runId` 守卫，指数退避，>4 次抛出）；真实发送 `Last-Event-ID` header；`seenEvents` Set 去重、`lastEventId=max` 容忍乱序；终态双保险（事件驱动 + 服务端关流后 refreshRun）。**无 token 级文本流**（运行期只显示静态占位）。
- **七模式**：`MODE_LABELS` + `SOURCE_REQUIRED` Set + `setMode()` 显隐/必填联动；检视器三 tab；窄屏双 drawer（matchMedia 720/980px + inert + aria-hidden + scrim）。无路由/无 URL 状态。

## 2. 能力现状

| 能力 | 状态 | 依据 |
|---|---|---|
| 七模式工作台 | ● 已实现，有缺陷 | 7 chip 与后端 ChatMode 一一对应，`QUESTION_TYPES` 与 5 个 DisciplineSpec 逐项一致；FE-01 模板/模式失同步 422 |
| SSE 流式进度 | ● 事件级（非 token 级） | Last-Event-ID/去重/退避/heartbeat/终态兜底齐备；FE-03 重连场景取消失效 |
| 会话历史 | ◑ 部分 | 列表/新建/切换/终态刷新 OK；重命名死按钮、无删除/搜索/分页/分支/分享 |
| 知识引用 locator/pin | ● 已实现（单 pin） | search→formatSourceLocator→单 pin→knowledge_ids；FE-06 跨会话残留 |
| 验证面板 | ◑ 展示层已实现 | 四态分色 + required/optional + 三事件源 + refreshRun 恢复；尚无 evidence binding/compiled_unbound 语义（QG-02） |
| 暂停/恢复 | ● 已实现 | budget.exhausted→resume→预算增量 dialog→续流 |
| 取消 | ● 有缺陷 | 语义正确，但刷新/切会话场景永久 disabled（FE-03） |
| 源题延续变式 | ● 已实现 | refreshRun 回填生成题 ID 到源题框 |
| 桌面三栏 + 窄屏 drawer | ● 已实现 | 四断点 + inert + scrim + prefers-reduced-motion；**无暗色主题**（显式 light-only 设计） |
| 数学渲染 | ○ 未实现（官方承认） | LaTeX/代码围栏纯文本原样显示（FE-09） |

---

## 3. High 发现

### 🟠 FE-01 — 选模板后切模式芯片必触发 422（叠加 FE-02 错误不可读）

**类别** 代码缺陷 · **复核 代理** · 与 FB-03 同源

`applyTemplate` 写 `#templateField.value=template.id` 并 setMode（`app.js:200-209`），但 `setMode`（`:358-387`）与模式芯片点击**不清除** templateField；提交体恒带 `template_id`（`:452`）。后端 validator 强制 `template_id==mode`（`schemas.py:210-211`）。复现：点左栏模板卡"独立求解"→点芯片"01 出新题"→发送→**422**。模板卡是左栏一级入口，属日常路径；叠加 FE-02 错误呈现为 "[object Object]"（见下）。**修复**：setMode 清 template + api() 对 detail 数组做 join。

### 🟠 FE-03 — 刷新/切会话后附着运行中 run 时"停止运行"永久不可用

**类别** 代码缺陷 · **复核 代理** · 与 FB-10 同源

`dom.cancel.disabled=false` 仅出现在 submitMessage（`:471`）与 resumeRun（`:789`）；`selectConversation` 恢复流（`:273-277`）与 `handleRunEvent` 的 `run.started`（`:730-734`）都不启用它；refreshRun 仅在终态执行 `disabled=status!=="paused"`。即 mid-run 刷新或切回会话（Docs/12 §7 明确要求验收的场景），run 徽标显示"运行中"但唯一取消控制处于 disabled，直到自然终态/暂停。对长时低速 LLM run 是实质功能缺陷。**修复**：`setRunState("running")` 时同步启用 cancel。

### 🟠 FE-09 — 无 KaTeX：LaTeX 与代码围栏以原文显示（影响面评估）

**类别** 缺失功能（官方承认） · **复核 代理**

`renderSafeMarkdown`（`:326-356`）仅支持 #-#### 标题、-/* 列表、段落，全部 textContent；`$...$`、`\int`、\`\`\`python 围栏、表格、行内加粗/代码一律原样纯文本。对以数学题目/证明为核心产物的平台，**每一条**生成结果与右栏知识摘录都受影响。PROGRESS §6.6、Docs/12 §5 明确承认，列 P1。此处标 High 是**影响面评估**（核心内容可读性），非未声明缺陷。

---

## 4. Medium 发现（紧凑）

| ID | 类别 | 摘要 | 证据 |
|---|---|---|---|
| FE-02+FB-02 | 代码缺陷/UX | FastAPI 422 detail 是对象数组，前端 `String(detail)` → toast 显示 "[object Object]"（两代理独立发现） | `app.js:112-121` |
| FE-04 | 代码缺陷 | ⌘/Ctrl+Enter 绕过发送禁用 + 无 in-flight 守卫 → 同会话并发第二个 run，**双倍扣配额**，UI 只跟踪第二个 | `app.js:1007-1009` |
| FE-05 | 代码缺陷 | selectConversation 未处理 Promise rejection + 先改 state 后请求无回滚 → 失败后静默显示旧内容但发送到未展示会话 | `app.js:236,255-264` |
| FE-06 | 一致性 | 跨会话状态残留：source ID/知识 pin/run 徽标/resume 按钮不随会话切换重置 | `app.js:257-260,709-719` |
| FE-10 | 缺失功能 | 会话管理断层：重命名死按钮 + 会话 limit=80/消息 limit=1000 双重截断无分页 | `index.html:88`；`app.js:263` |

## 5. Low 发现（登记）

FE-07（焦点 outline 25% alpha 对比 <3:1，a11y）· FE-08（muted 文字对比 <4.5:1 且 8-10px 中文小字，a11y）· FE-11（前端**零行为测试**，test_console 仅静态字符串断言）· FE-12（thinking-dots 动画死码，无 `<i>` 子元素）· FE-13（~75 行 .question-artifact 死 CSS）· FE-14（question.persisted/assistant.paused 事件前端不映射）· FE-15（Docs/12 两处超前于实现）· FE-16（401 不重开登录框、toast 不可复制 4.2s 消失、成功 toast 过度 assertive、终态刷新失败占位气泡永久滞留）· FE-17（dialog showModal 能力守卫不一致、tablist 无方向键）· FE-18（无路由深链、刷新回落最近会话、会话 meta 显示英文枚举）· FE-19（全量重渲染无虚拟化，未实测）。

## 6. 测试缺口

前端**零行为测试**（FE-11）：`test_console.py` 7 用例全是静态文件字符串存在性断言（如 `"Last-Event-ID" in script.text`、`"innerHTML" not in script.text`），`node --check` 仅语法。SSE 解析器、run 状态机、七模式校验、markdown 渲染、鉴权流转均无单元/E2E 覆盖；IIFE 结构使函数不可导入测试。新增必须覆盖：choice/options、fill_blank/blanks、evolve 后 key 更新、`compiled_unbound` 不显示绿色、manifest drift 提示和 usage effect 去重。

## 7. 正面结论（须并列呈现）

- **XSS 审计未发现可利用点**——所有 LLM 输出、SSE payload、知识内容、用户回显均经 `textContent`/`createElement` 落 DOM，**无 HTML/URL/属性注入 sink**，`<script>`/`<img onerror>` 会被显示为字面文本；属性选择器用 `CSS.escape`。这是有意的架构选择（Docs/12 §4 声明）且**执行彻底**（测试断言禁 innerHTML）。
- **凭据存 sessionStorage 而非 localStorage**，配合无 XSS sink，风险面可控。
- **SSE 客户端与后端 `_stream_events` 契约严格吻合**（Last-Event-ID 补发/去重/退避/heartbeat/终态双保险）。
- **七模式领域-题型联动表与后端 DisciplineSpec 逐项一致**，前后端双重校验的声明属实（solve 强制 fast、formal 仅数学证明类前端禁用 + 后端二次校验）。
- **无内存泄漏/重复绑定**（事件集中绑定 + 动态项随 replaceChildren GC），1114 行组织质量尚可（常量表→state/dom→网络层→渲染层→SSE→bindEvents）。

> 前端设计层面的目标态方案（组件分解、UI 流程、线框图、KaTeX/artifact 中心引入）见 [`../plan/plan-03-qgen-frontend-design.md`](../plan/plan-03-qgen-frontend-design.md)。
