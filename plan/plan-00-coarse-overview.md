# 修复与实施计划 · plan-00 — 粗粒度总览（主检查表）

> 本文档是**主检查表**：保留第一轮 PA/PC/IS/SUP/GN/HR/SB/FE/FB/DR ID 的落点，并补入 DP/QG/XC 重审根因。第一轮正文实际有 161 个唯一展示 ID（至少 14 个别名），约 147 个 canonical action；旧“~79/~85、100%”口径已撤销。
> 详细处方见 plan-01~04；跨系统依赖与最终验收以 [plan-05](plan-05-modernization-program.md) 为准。

---

## 1. 阶段总览

修复按**风险优先 + 依赖顺序**分 8 个阶段。阶段跨子系统；每个子系统的详细设计在对应 plan-0X 文件中按阶段组织。

| 阶段 | 主题 | 目标 | 关键项 | 阻塞关系 |
|---|---|---|---|---|
| **P0** Gates + 身份止血 | A + CI | 消除 3 Critical、建立真实入口门禁与运行身份 | DP-01, PA-01, PC-03, GN-01, fresh-install CI | 无（最先） |
| **P1** 无损摄取加固 | A | 补齐所有静默丢内容路径的兜底 + 记账 | IS-01/02/03, PC-01/02/04/05, SUP-01/05, delete 级联 | P0（CI 就位后改动才安全） |
| **P2** 治理闭环 | B | taxonomy 覆盖、评价→转正、review→处理、图谱消费 | PA-02/05/08/10, GN-02 | P1 |
| **P3** 生成与输出补齐 | B | 检索升级、讲义/组卷真实化、学科化 | GN-03/04/10/13/18 | P2（依赖治理） |
| **P4** 验证有效性 | B | 数值/Lean 独立性 + 默认可观测 | SB-01/02/04/06/07 | 可与 P3 并行 |
| **P5** 生产化硬化 | C | fencing、预算、配额原子、鉴权、分页 | HR-01/03/04, FB-01/04/05/06/07 | 可与 P2 并行 |
| **P6** 前端产品化 | — | KaTeX/契约修复/会话管理/a11y + 前端设计 | FE-01/03/09, FB-01 前端侧 | P5（契约先稳） |
| **P7** 文档/测试/打磨 | — | 文档对账、Low 项、测试补全 | DR-*, 全部 Low, 测试缺口 | 贯穿全程 |

各阶段详细设计入口：数据管线 → [plan-01](plan-01-data-pipeline.md)；harness/生成/验证 → [plan-02](plan-02-qgen-agent-harness.md)；前端 → [plan-03](plan-03-qgen-frontend-design.md)；跨领域 → [plan-04](plan-04-cross-cutting.md)；总依赖/指标/PR 序列 → [plan-05](plan-05-modernization-program.md)。

---

## 2. 全发现 → 计划映射（1:1 可溯源）

### 2.0 重审根因 ID（优先于历史严重性表）

| 范围 | ID | 计划落点 |
|---|---|---|
| Pipeline identity/config/resource | DP-01/02/08/12 | plan-05 §3；plan-01 §P0-R |
| Pipeline lossless/provenance/storage | DP-03/04/05/06/07/09/10/11 | plan-05 §4–5/§7；plan-01 §P0-R/P1 |
| Pipeline logging/threat boundary | DP-13 | plan-01 §P0-R.5；plan-04 §P0-R.3 |
| QGen artifact/evidence/config/safety | QG-01/02/03/04/05/07/08/09/10 | plan-05 §6/§8；plan-02 §P0-R/P4/P5 |
| QGen effects/billing | QG-06 | plan-05 §7；plan-02 §P5；plan-04 §P5 |
| Auth/tenant/schema/logging/deps | XC-01/02/03/04/05/06/07/08/09/10 | plan-04 §P0-R/P5/P7；plan-05 §2/§5/§7–8 |

DP/QG/XC 是对旧发现的根因级升级，不要求删掉所有历史 ID；完成一个根因 PR 时必须同时关闭其映射的历史 action 与回归测试。

### 2.1 Critical（P0）

| ID | 报告 | 修复动作（摘要） | 阶段 | 详细 |
|---|---|---|---|---|
| PA-01 | [data-pipeline](../audit/report-data-pipeline.md#3-critical-发现) | 分组键加 chapter/section/source 维度；"证明"从答案标记移除或加"题目型"判别；删除改为可逆软删 + dry-run；relink 加 `--dry-run`/确认 | P0 | plan-01 §P0.1 |
| PC-03 | [data-pipeline](../audit/report-data-pipeline.md#3-critical-发现) | block_index 改稳定标识 `(file_idx,page_start)`；producer/replay 统一空 batch 策略；watermark 用稳定键 | P0 | plan-01 §P0.2 |

### 2.2 High

| ID | 报告 | 修复动作 | 阶段 | 详细 |
|---|---|---|---|---|
| GN-01 | harness | CLI 写回改用 `update_question_solution`（一行） | P0 | plan-02 §P0 |
| IS-01 | data-pipeline | 所有去重/promote 查询 WHERE 补 `AND content_hash != ''`（实测即命中索引） | P1 | plan-01 §P1.1 |
| IS-02 | data-pipeline | vision 路径加 `stop_reason=="max_tokens"` 检测 → 截断批不缓存为完成 + 记账/重试 | P1 | plan-01 §P1.2 |
| IS-03 | data-pipeline | 文字层探测改逐页 + 事后空文本率校验回路；无 vision 降级加警告 | P1 | plan-01 §P1.3 |
| PC-01 | data-pipeline | Stage1Review 对 `status=uncertain`/force 批次豁免 near-tail 强制 null | P1 | plan-01 §P1.4 |
| PC-02 | data-pipeline | resume 重放改"集合差"（记录已完成 item_id 集合，非 max watermark） | P1 | plan-01 §P1.5 |
| PC-04 | data-pipeline | mid-doc force-consume marker-conflict 分支加 `_build_uncertain_fallback` 占位 | P1 | plan-01 §P1.6 |
| SUP-01 | data-pipeline | `trim_tail` 回退定位到 `check_math_balance` 精确未闭合 opener（非首个 opener），杜绝多环境过度截断 | P1 | plan-01 §P1.8 |
| GN-02 | harness | 生成题写 pipeline_auto 评价或引入"生成题评价→转正"通路 | P2 | plan-02 §P2.1 |
| GN-03 | harness | DocAssembly 检索先按 tag 相关性排序再截断 + 难度/质量过滤 | P3 | plan-02 §P3.1 |
| HR-01 | harness | 引入 worker lease/epoch fencing；fail_step 以持有 step 为键 | P5 | plan-02 §P5.1 |
| HR-04 | harness | API budget 缺省注入 AppConfig.qgen_run_max_* | P5 | plan-02 §P5.2 |
| FE-01 | frontend | setMode 清 templateField（+ FE-02 合并修） | P6 | plan-03 §P6.2 |
| FE-03 | frontend | setRunState("running") 时启用 cancel | P6 | plan-03 §P6.2 |
| FE-09 | frontend | 引入 KaTeX + 完整 markdown 渲染（保持 XSS 安全） | P6 | plan-03 §P6.3 |
| FB-01 | fe-be | 消息分页改 DESC + cursor；前端加载更多 | P5 | plan-04 §P5.3 |

### 2.3 Medium（按详细计划归属）

**→ plan-01（数据管线）**：PA-02（taxonomy 覆盖 + 不变量测试，P2）· PA-05（悬挂 concept 链接 + 门禁倒置，P2）· PA-06（subject 校验/canonicalize，P2）· PA-07（tag_tree 计数膨胀，P1）· PA-09（tests 边启发式改进，P2）· PA-10（review_queue 处理 CLI/API，P2）· PA-11（批量标注 count 校验 + retry，P1）· PA-12（difficulty 一致性/后验，P2）· PA-13+IS-09（delete_question 全级联，P1）· PA-14（跨学科精确匹配加 discipline 检查，P2）· IS-06（JSONL 数组记录，P1）· IS-07（XLSX 缺 r 属性回退，P1）· IS-08（tag_tree 原子写，P1）· IS-10（SaasStore 锁补全，P5）· IS-11（defusedxml + 单 entry 限制，P1）· IS-12（文档分类存证 + 度量，P2）· PC-05（failed_blocks 重试/清除，P1）· PC-06（并行路径 carry，P1）· PC-09（replay 阻塞防护，P1）· PC-11（扩窗增量重发优化，P1）· PC-12（checkpoint IO 优化，P1）· SUP-05（math_balance 转义花括号 lookbehind + `\right.` null 定界符处理，P1，与 SUP-01 同批）· SUP-02（structure.py 单行环境闭合检测，接入生产前修，P1）

**→ plan-02（harness/生成/验证）**：SB-02（默认可观测 + 无容器告警，P4）· SB-04（验证代码独立模型，P4）· SB-06（结构化退出码替代子串，P4）· SB-07（自建含 Mathlib 镜像 + 文档，P4）· SB-08（容器清理全异常出口，P4）· GN-04（free 库内查重，P3）· GN-06（按需生成候选，P3）· GN-07（难度硬门禁，P3）· GN-08（生成题 tag 校验，P3）· GN-09（behavior 回查校真，P3）· GN-10（真实组卷算法，P3）· GN-11（lecture_note 接真讲义，P3）· GN-13（学科化模板体系，P3）· GN-18（检索用 FTS + IDF，P3）· HR-02（cancel+异常先 ack，P5）· HR-03（预算预留式记账，P5）· HR-05+GN-12（token 计量全路径，P5）· HR-06（streaming/failover/成本，P5）· HR-07（checkpoint 裁剪 + 增量，P5）

**→ plan-04（跨领域）**：FB-04+IS-05（配额原子预留，P5）· FB-05（批量任务恢复 + 释放，P5）· FB-06（默认密钥拒绝启动，P5）· FB-07（register 限流 + 密码策略 + 孤儿租户，P5）· FB-08（SSE to_thread + pub/sub，P5）· FB-09（startup 恢复限流 + 水位，P5）

**→ plan-03（前端）**：FE-02（422 数组 join，P6）· FE-04（提交 in-flight 守卫，P6）· FE-05（selectConversation catch + 回滚，P6）· FE-06（会话切换状态重置，P6）· FE-10（分页 + 会话管理端点对接，P6）

### 2.4 Low（30 条，P7 集中处理）

**数据管线**：PA-15/16/17/18, IS-14/15/16/18/19/20/21, PC-08/10/13/14/15/16/17/18/19/20, SUP-03/04/06（ocr_repair 上标误改/span 漂移、headers 全局删除，均 latent 无生产调用方，接入内容路径前修）。原 IS-13 已经源码复核为误报并撤销。
**harness/生成/验证**：SB-03/05/09/10, GN-05/14/15/16/19/20/21, HR-08/10/11/12/13/15
**前端**：FE-07/08/11/12/13/14/15/16/17/18/19
**契约**：FB-11/12/13/14/15/16/17/18/19
**文档**：DR-08~15

> Low 项在 P7 集中处理，但**安全/正确性相关的 Low**（IS-19 embedding 投毒、PC-13 worker 丢失、FB-18 孤儿 run、HR-13 AttributeError）提前到对应子系统阶段一并修，详见各 plan。

### 2.5 合并项 / 同源项溯源（确保每个报告 ID 都有可搜索落点）

若干发现由多个代理独立发现同一问题、或是另一发现的不同视角，报告中登记了各自 ID 但计划中并入主 ID 修复。为使溯源无歧义，此处显式映射（用主 ID 的阶段/详细计划）：

| 报告 ID | 并入主 ID | 关系 | 修复落点 |
|---|---|---|---|
| IS-04 | PC-03 🔴 | 空 batch block_index 不对称的 Medium 视角（同根因） | plan-01 §P0.2 |
| IS-09 | PA-13 | delete_question 级联不全（两代理独立发现） | plan-01 §P1.7 |
| IS-05 | FB-04 | 单发端点配额竞态（两代理独立发现） | plan-04 §P5.1 |
| FB-03 | FE-01 🟠 | 模板/模式失配 422（前端侧同一 bug） | plan-03 §6 |
| FB-02 | FE-02 | 422→"[object Object]"（前端侧） | plan-03 §6 |
| FB-10 | FE-03 🟠 | 刷新后取消失效（前端侧） | plan-03 §6 |
| FB-16 | FE-14 | question.persisted/assistant.paused 前端不映射 | plan-03 §6（P7） |
| FB-17 | FE-10 | 会话重命名/删除（前端 + 后端端点） | plan-03 §6 + plan-04 §P5.4 |
| GN-12 | HR-05 | 非 durable 路径 token 计费恒 0（token 计量失真） | plan-02 §P5.3 |
| HR-08 | GN-05 | 温度递增声称未实现（同一代码） | plan-02 Low（P7） |
| GN-17 | DR-02 🟠 | CLAUDE.md durable/CLI 现状漂移（文档对账印证） | plan-04 §P7 |
| HR-09 | DR-02 🟠 | CLAUDE.md 落后于实现（文档对账印证） | plan-04 §P7 |
| IS-17 | DR-11/DR-07 | CLAUDE.md 模块清单 + 默认值漂移（文档对账印证） | plan-04 §P7 |

> 说明：§2.4 的 Low 清单用 `PA-15/16/17/18`、`DR-08~15` 紧凑写法枚举了每个编号（人类可 1:1 读出）；本表补齐所有"报告有 ID、计划并入他项"的映射，确保任一报告 ID 都能在计划中定位到落点。

---

## 3. 缺失功能清单（vs 简报 §1 + 官方路线图）

除修 bug 外，以下是简报要求或文档承认但**未实现/部分实现**的功能，需设计+实现（详见各 plan 的"目标态设计"）：

| 功能 | 现状 | 目标 | 阶段 | 详细 |
|---|---|---|---|---|
| 文档分类（有存证的产品能力） | 内部调参信号，无度量 | genre 入库 + 准确率度量 + 复核入口 | P2 | plan-01 §P2 |
| 知识关系图谱（prerequisite + 消费方） | 4 种边、只写不读 | prerequisite 边 + retriever/推荐消费 + 可视化 | P2 | plan-01 §P2 |
| 难度评分一致性/校准 | 无跨条目一致性 | 校准回归 + 后验修正 | P2 | plan-01 §P2 |
| review_queue 处理界面 | 只进不出 | CLI/API 处理 + 前端队列视图 | P2 | plan-01/03 |
| 真实组卷（难度分布/题型配比/topic） | 取前 N | 约束满足组卷算法 | P3 | plan-02 §P3 |
| 讲义完整版（大库检索 + PDF） | lite 头部截断 | 真检索 + Jinja2/XeLaTeX PDF | P3 | plan-02 §P3 |
| 教材级内容生成 | 未实现 | 章节大纲 + 多单元编排（Post-MVP 设计占位） | P3 | plan-02 §P3 |
| 数值/Lean 独立验证 + property-based | 自证 + 默认关 | 独立验证器 + hidden suite + Mathlib 镜像 | P4 | plan-02 §P4 |
| Extended reasoning（QGen 启用 thinking） | preset 未启 | 按难度动态 thinking budget | P4 | plan-02 §P4 |
| Context 压缩（摘要） | 截断快照 | rolling summary | P5 | plan-02 §P5 |
| Memory 系统 | 未实现 | 跨 session 用户/错题记忆（设计占位） | P5 | plan-02 §P5 |
| LLM streaming/failover/成本核算 | 无 | token 流 + 备援路由 + 价格表 | P5 | plan-02 §P5 |
| 分布式 worker lease | 无 | lease/epoch fencing + 队列 broker | P5 | plan-02 §P5 |
| Template/Skills 运行时管理 | 静态 .txt | 注册/版本化/租户扩展 | P5 | plan-02 §P5 |
| 鉴权生命周期（refresh/吊销/第三方） | 无 | refresh token + 撤销 + OAuth | P5 | plan-04 §P5 |
| 前端：KaTeX/CommonMark/artifact 中心/会话管理 | 缺 | 见前端设计 | P6 | plan-03 |
| CI/CD 门禁 | 无 | GitHub Actions（pytest/ruff/mypy/node） | P0 | plan-04 §P0 |
| hardened sandbox（gVisor/Firecracker）| OCI container | 生产级隔离 + egress policy | P4 | plan-02 §P4 |
| 向量/混合检索 | 结构过滤 + TF | embedding 默认路径 | P3 | plan-02 §P3 |

---

## 4. 覆盖性自检

- **资产覆盖**：176 个 src、159 个 tests、35 个 Docs 资产及 prompts/config/scripts 已进入 [重审覆盖矩阵](../audit/RE-AUDIT-REGISTER.md#22-文件与功能覆盖矩阵)。
- **发现覆盖**：历史 ID 保留在 §2.1–2.5，DP/QG/XC 根因在 §2.0；展示 ID 不是独立缺陷计数，合并关系必须通过 canonical action 追踪。
- **功能覆盖**：§3 的缺失功能继续有效，并新增 run identity、SourceMap、QuestionArtifact、EvidenceBinding、manifest、effect outbox、schema shape、ResourceGovernor。
- **可执行覆盖**：验收必须报告 statement/line/branch coverage、fresh-install console script、golden/crash/retry、真实 provider canary、真实 container/Lean 和 SVG render；“文档有落点”不再等同 100% 完成。
- **撤销项**：IS-13 不进入 backlog；其余历史项若在实现时证伪，应记录反证而不是保留虚假任务。

> plan-01~04 提供模块处方，plan-05 提供根因级依赖和完成定义；两者共同构成可反向核对的计划，不再声称静态清单天然 100% 覆盖未知缺陷。
