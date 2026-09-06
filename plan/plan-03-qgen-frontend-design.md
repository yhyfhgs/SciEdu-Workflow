# 详细计划 · plan-03 — Qgen 前端目标态设计

> 消化 [report-qgen-frontend.md](../audit/report-qgen-frontend.md) 全部发现 + 前端缺失功能（KaTeX/artifact 中心/会话管理）。
> 应用前端设计方法论：**设计立场 → 组件分解 → UI 流程 → 线框图 → 设计系统 → 修复映射**。
> 可视化 artifact：[wireframes/component-decomposition.svg](wireframes/component-decomposition.svg) · [wireframes/target-workbench.svg](wireframes/target-workbench.svg) · [wireframes/key-flows.svg](wireframes/key-flows.svg)
> **接口前置条件**：本设计依赖 plan-02 P0-R 的版本化 `QuestionArtifact` 与 `EvidenceBinding`；在后端契约就位前，不以 Markdown 猜测/重建丢失的 options、blank 或验证绑定。

---

## 1. 设计立场

**主题**：数学/理工科内容的**可信生成与审阅工作台**。受众：教师与进阶学生。页面的单一职责：让人**相信并复用** AI 生成的数学内容——所以"验证证据链"是这个产品区别于通用 chatbot 的记忆点，应被强化为**签名元素**，而非埋在检视器角落。

**关键立场：保留零构建原生架构，不推倒重来。** 现有 `app.js`（1114 行 IIFE）的 XSS 防护执行彻底（全 `textContent`、0 `innerHTML`、测试禁 `innerHTML`）——这是有意且正确的安全设计。改成 React/构建栈会**破坏这个优点**且超出审计修复范围。目标态是**在原生架构内模块化 + 补能力**，把"无构建、无依赖、XSS 安全"作为约束继承下来。唯一破例：KaTeX 作为**受控**依赖引入（见 §5.1，仅渲染数学、输入经白名单）。

**取一个可辩护的风险**：现有是 light-only。目标态引入**暗色主题镜像**（数学审阅常在低光环境长时间进行，且修 a11y 对比度问题 FE-07/08 时顺带建立完整 token 体系）——这是有实际用户价值的扩展，不是装饰。

---

## 2. 组件/模块分解

**现状**：1114 行单 IIFE，`state`/`dom`/网络/渲染/SSE/表单全在一个闭包，函数不可导入测试（FE-11 零行为测试的结构性根因）。

**目标态**：拆为**原生 ES modules**（浏览器原生 `import`，仍无构建），每模块单一职责、可单测。见 [component-decomposition.svg](wireframes/component-decomposition.svg)。

| 模块 | 职责 | 消化的发现 |
|---|---|---|
| `store.js` | 单一状态源 + 订阅（替代散落的 `state` 直改）；会话切换时**集中重置**派生状态 | FE-06（跨会话残留）、FE-05（回滚） |
| `api.js` | fetch 封装 + **错误规范化**（422 数组 → 可读文案）+ auth header | FE-02/FB-02（[object Object]） |
| `sse.js` | 手写 SSE 客户端（Last-Event-ID/去重/退避/终态）——已良好，抽为独立可测模块 | FE-11（可测性）、FB-19 |
| `render.js` | DOM 构建 + **安全 markdown/数学渲染**（KaTeX 集成，白名单） | FE-09（KaTeX）、FE-12/13（死码清理） |
| `artifact.js` | 校验/渲染 discriminated `QuestionArtifact`；choice/options、fill_blank/blanks、answer key、版本/manifest | QG-01/03/05/07 |
| `evidence.js` | 展示 binding 层级；区分 compiled、bound、checked，计算是否可发布 | QG-02/04 |
| `runstate.js` | run 状态机（running/paused/终态）↔ UI 控件启用/禁用 | FE-03/FB-10（取消失效）、FE-04（并发守卫） |
| `views/` | 七模式面板 / 会话列表 / 检视器 / **artifact 中心** / **review 队列** | FE-01（模板/模式）、FE-10（会话管理） |
| `a11y.js` | 焦点管理 + aria + 键盘导航 + 对比度 token | FE-07/08/17 |

**收益**：函数可导入单测（补 FE-11）；状态单源消除 FE-05/06；错误规范化集中消除 FE-02。

---

## 3. UI 流程图

见 [key-flows.svg](wireframes/key-flows.svg)。三条关键流程的目标态：

1. **生成一道题**（修 FE-01/03/04/QG-01/02）：选模式/模板 → 参数联动校验 → 提交（统一 in-flight 守卫）→ SSE 进度 → 收到 typed artifact 后按 form 渲染 → 证据按 binding 层级呈现 → 只有 required evidence `bound_checked` 才允许“发布/加入试卷” → run 期间取消始终可用。
2. **会话管理**（修 FE-10/FB-01/FB-17）：列表分页（游标"加载更多"）→ 重命名/删除（需后端补 PATCH/DELETE 端点，见 plan-04）→ 切换时集中重置派生状态。
3. **数学渲染管线**（修 FE-09）：LLM markdown → 白名单解析（标题/列表/表格/代码/**数学定界符**）→ KaTeX 渲染 `$...$`/`$$...$$`（`throwOnError:false`）→ 其余仍 `textContent`。**XSS 不变量保持**：非数学内容永不进 innerHTML；KaTeX 输出经其自身 HTML 白名单。

---

## 4. 线框图（ASCII ideation → SVG 交付）

目标态工作台布局（桌面三栏，见 [target-workbench.svg](wireframes/target-workbench.svg) 的高保真版）：

```
┌───────────┬───────────────────────────────┬───────────────┐
│ 会话/模板  │  对话流 + 生成结果卡片          │ 检视器         │
│           │                               │               │
│ [+ 新会话] │  ┌─ 用户命令 ────────────┐    │ ▸ 验证证据链   │  ← 签名元素
│ 会话1 ⋮   │  └───────────────────────┘    │  ✓ 独立复核    │
│ 会话2 ⋮   │  ┌─ QuestionArtifact vN ─┐    │  ✓ 数值 bound │
│ 会话3 ⋮   │  │ 题干 + 选项/空位/答案键 │    │  ⚠ Lean compiled│
│ …加载更多  │  │ KaTeX · 难度 · 学科/型 │    │    but unbound │
│           │  │ [加入试卷][变式][复制] │    │ ▸ 知识引用     │
│ ── 模板 ── │  └───────────────────────┘    │ ▸ run 指标     │
│ 独立求解   │  ┌─ 运行中… (取消 ●可用) ─┐    │               │
│ 组卷 ★     │  └───────────────────────┘    │ [artifact 中心]│  ← 新增
│ 讲义 ★     │  [输入框 · ⌘Enter 守卫]        │  历史产物浏览   │
└───────────┴───────────────────────────────┴───────────────┘
★ = 新增真实功能面板（当前仅弹说明，见 GN-10/GN-11）
窄屏：左右栏收进 drawer（现有 inert+scrim 机制已良好，保留）
```

**artifact 中心**（新增，修 PROGRESS §6.6 承认缺口 + 复用现有 ~75 行 .question-artifact 死 CSS FE-13）：生成的题目/解答/试卷作为可浏览、可复制、可导出的**版本化结构对象**，展示 artifact digest、run manifest、来源与 evidence binding；禁止把当前“单题 Markdown 加壳”继续命名为 artifact 中心。

---

## 5. 设计系统对齐

### 5.1 KaTeX 引入（FE-09，受控依赖）
- 自托管 KaTeX（无 CDN，符合无外部依赖精神），仅渲染数学。
- 输入白名单：只对 `$...$`/`$$...$$`/`\(...\)`/`\[...\]` 内文本调 `katex.render(..., {throwOnError:false, trust:false})`；`trust:false` 禁 `\href`/`\htmlClass` 等注入向量。
- **XSS 不变量**：非数学 markdown 仍走 `textContent`；表格/代码用 DOM API 构建。渲染失败降级显示原 LaTeX（不静默）。

### 5.2 Token 体系（修 FE-07/08 a11y + 暗色主题）
| Token | Light | Dark | 用途 |
|---|---|---|---|
| `--paper` | #fbfcfa | #0f172a | 背景 |
| `--ink` | #1e293b | #e2e8f0 | 正文 |
| `--muted` | **#5a6b74**（修到 4.5:1，原 #6d7c85 不达标） | #94a3b8 | 次要文字 |
| `--focus` | **实心 3px + 4.5:1 对比**（修 FE-07 的 25% alpha） | 同 | 焦点环 |
| 验证四态 | green/red/amber/slate（保持，暗色加深） | 镜像 | 证据链 |

小字（8-10px 中文，FE-08）提升到最小 12px；`prefers-color-scheme` + 手动切换。

### 5.3 排版
display 保持无衬线（工具气质）+ 数学用 KaTeX 的 Computer Modern（教材气质，形成"界面 sans + 数学 serif"的自然分层）+ 题号/元数据用等宽 utility 字体。

---

## 6. 修复项 → 设计映射（全 FE 发现覆盖）

| 发现 | 严重性 | 落点 |
|---|---|---|
| FE-01 | 🟠 | §2 views + §3 流程1（前端拦非法 template/mode 组合） |
| FE-03 | 🟠 | §2 runstate.js（取消按钮状态机） |
| FE-09 | 🟠 | §5.1 KaTeX |
| FE-02 | 🟡 | §2 api.js 错误规范化 |
| FE-04 | 🟡 | §2 runstate in-flight 守卫 |
| FE-05/06 | 🟡 | §2 store.js 单源 + 切换重置 |
| FE-10 | 🟡 | §3 流程2 + §4 会话管理（依赖 plan-04 端点） |
| FE-07/08/17 | ⚪ | §2 a11y.js + §5.2 token |
| FE-11 | ⚪ | §2 模块化（可导入单测）+ 引入 Playwright E2E |
| FE-12/13/14/15/16/18/19 | ⚪ | P7 批量：死码清理、事件映射补全、路由/深链、toast 可复制、虚拟化 |
| QG-01/07 | 发布阻断 | artifact.js + typed form renderer；结构字段缺失时显示 schema error，不降级成似是而非题卡 |
| QG-02/04/05 | 发布阻断/High | evidence.js + manifest detail；compiled_unbound 用 amber，禁发布；显示执行 policy/provenance |
| QG-06/XC-08 | High | usage/effect 状态按 effect id 去重，重连/reconcile 不叠加显示 |

---

## 7. 测试（补 FE-11 零测试）
- 模块化后：`store`/`api`/`sse`/`runstate`/`render` 各单测（Node 环境导入）。
- E2E：Playwright 覆盖三条流程 + SSE 重连 + 取消/恢复 + 数学渲染 + a11y；加入 choice/options、fill_blank/blanks、evolve key 更新、compiled_unbound 禁发布、manifest drift 和 effect 重放不重复。
- CI 纳入 `node --check` + 模块单测 + Playwright（plan-04 P0）。

## 8. 阶段与验收（P6）
- **P6.1**：模块化拆分 + store 单源（不改视觉，纯重构 + 补单测）。
- **P6.2**：契约修复（FE-01/02/03/04/05/06）——高频日常路径先修。
- **P6.3**：KaTeX + typed artifact/evidence 中心 + a11y token + 暗色主题（依赖 plan-02 P0-R schema）。
- **P6.4**：会话管理（依赖 plan-04 的 PATCH/DELETE/分页端点）+ review 队列视图。

**验收**：真实浏览器验证（PROGRESS §7 精神）desktop/mobile + SSE 重连 + 取消/恢复 + 数学正确渲染 + 键盘可达 + 对比度达 WCAG AA。
