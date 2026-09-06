# 审计报告 · 跨领域 — 文档漂移、测试/CI、跨工作流主题

> 范围：仓库文档与实现的对账（简报 §2.2）、测试与 CI 缺口、跨工作流的系统性主题、官方承认的缺口、根目录杂项盘点。
> 全局图：[diagrams/system-overview.svg](diagrams/system-overview.svg)
> **重审补充**：本报告纳入 branch coverage、真实 CLI 入口、Bandit/pip-audit、schema shape、权限 capability、日志隐私和 7 张 SVG 的动态检查；统一证据见 [RE-AUDIT-REGISTER](RE-AUDIT-REGISTER.md)。

---

## 0. 可执行基线、安全与供应链增量

| 主题 | 本轮证据 | 判断 |
|---|---|---|
| 测试触达 | 79.7% statements / 76.9% lines / **66.9% branches**；CLI 41.9% lines / 34.9% branches | “1318 passed”是健康信号，不是全路径证明；真实命令、错误分支和组合参数是最薄处 |
| 真实 CLI | `uv run sciforge config validate` 因 hidden editable `.pth` 报 `ModuleNotFoundError`，pytest 由 `pythonpath=src` 掩盖 | CI 必须 fresh-install 后运行 console script，而非只从源码路径 import |
| schema shape | v14 只检查 `user_version` 与两张核心表；`IF NOT EXISTS` 不会修残缺列/索引/trigger/check | 加 shape manifest、PRAGMA diff、fresh/upgrade/malformed/mid-failure 四类测试（XC-03） |
| 静态安全 | Bandit：0 High、33 Medium、49 Low；多数动态 SQL 为内部白名单，XML 全量建树/OOM 是已有 IS-11 | 逐项 triage，不把工具总数直接等同可利用漏洞 |
| 依赖审计 | 默认依赖有 idna、pydantic-settings 通告；all extras 另含 cryptography、urllib3、starlette 及未使用的 chromadb 通告 | 供应链 Medium；升级/约束并在 CI 扫描，reachability 与部署暴露另行判断（XC-05） |
| LLM 隐私/计量 | raw prompt/response 明文日志无统一 redaction/retention；多 backend usage 缺失，`last_usage` 是并发共享状态 | 建 structured redaction、保留期、per-call usage return；禁止把并发计量挂在 client 可变属性（XC-04/XC-07、HR-05） |
| SVG | 7/7 XML 有效；发现反向箭头、越框、文字相交和过度正面结论 | 每图均需语义 + geometry + rendered visual 三重验收 |

这些增量把“无 CI”扩展为更精确的问题：当前测试环境与可安装/可运行产品环境不是同一被测对象。

## 1. 文档对账结论

文档体系有明确的“现行/历史/蓝图”分层，这是优点；但不能再称现行契约“逐条一致”或“没有虚构能力”。除原 DR 项外，`Docs/Pipeline_全代码逐行详解_v12.md` 把 48 个 pipeline 源文件写成 25 个并把若干不安全行为表述为保证；README/Docs 01/06/21 将会写库的 validate 描述为只读；PROGRESS 对 harness closure、tenant boundary、验证和 cache 的表述也需按 DP/QG/XC 收窄。

主要风险集中在 **CLAUDE.md**（权威项目说明，却系统性滞后于代码）与 01/04/06 三份"半更新"文档的版本残留。

## 2. 文档漂移发现（DR）

### 🟠 High

| ID | 标题 | 证据 |
|---|---|---|
| DR-01 | **CLAUDE.md 缺失整个 conversation/Chatbox 层** | api/ 清单只列 app/schemas/security/tenant_storage/static，实际另有 `conversation_routes.py`（2246 行）、`conversations.py`（787 行）、`static/app.js`（1114 行）+ conversations.db 分库；Docs 08/12/README/PROGRESS 均已收录 |
| DR-02 | **CLAUDE.md 断言 durable Harness 未接 CLI，与事实相反** | CLAUDE.md 称"CLI 仍直接调 QGenAgent.run()"，实际 `cli/generate.py:558/667/766` 有 durable/resume/cancel（HR-09/GN-17 独立印证） |

### 🟡 Medium

| ID | 标题 |
|---|---|
| DR-03 | disciplines 结构清单写 math/physics，实际五学科全注册（`registry.py:148-152`） |
| DR-04 | `bench/` 子系统 + `parse bench`/`parse cost-report` 三项未入 CLAUDE.md/Doc 06 |
| DR-05 | Doc 06 头部 "schema v14" vs §3 "当前 v10"，自相矛盾 |
| DR-06 | Doc 01 新旧混杂（v10/v14、"单条标注"vs REC-04 批量、`--max-files` 默认 4 vs 8） |
| DR-07 | Pipeline_v12 逐行详解称 `vlm_auto_text_detect` 默认关，实际 `settings.py:115=True`（IS-17 印证） |

### ⚪ Low
DR-08（Doc 01/04/06 悬挂引用 PROGRESS §00，已重写为 §1-8）· DR-09（Doc 04 文件表 openai.py 实为 openai_backend.py、llm_config.yaml 实为 TOML）· DR-10（Doc 07 taxonomy.json 位置误、tests 树缺 5 目录）· DR-11（CLAUDE.md 模块清单系统性缺 core/numbering|sandbox|trace、llm/capabilities 等）· DR-12（Doc 17 引用不存在的 generation_traces 表）· DR-13（防线计数三 vs 四道口径不一）· DR-14（AGENTS.md Stage2 描述未反映 REC-04/05 默认）· DR-15（孤儿资产：test_diag.py 断链、run_pipeline.sh 零引用、多张孤儿 SVG）。

## 3. CI/CD 缺口（简报 §5.0）

**仓库无任何 CI 配置**（`.github/workflows/` 不存在）。PROGRESS §7 列出的验收命令（pytest/ruff/mypy/`node --check`/`parse validate`/`config validate`）全部依赖**人工执行**。后果：
- 无自动化门禁保护 1318 测试 + ruff + mypy strict 的绿色基线；回归只能靠开发者自觉。
- Docs/14 §CI 集成明确列为 Post-MVP，与现状一致。
- 这放大了本审计所有"测试缺口"类发现的风险——没有 CI，即使补了测试也无强制执行点。

**建议**（详见修复计划）：引入最小 GitHub Actions（pytest + ruff + mypy + `node --check`），作为所有后续修复的回归保护基线。

## 4. 跨工作流系统性主题

历史与重审发现去重后约形成 147 个 canonical action；它们可归纳为五个反复出现的系统性主题，实施优先级以根因而非 ID 数量排序：

### 主题 A — "静默丢失/损坏知识库内容"（最高优先）
无告警、无记账地丢失或写坏内容，直接违背平台核心价值（准确的知识库）。涉及：**PA-01**🔴（误绑/删题）、**PC-03**🔴（resume 跳过）、IS-02🟠（VLM 截断）、IS-03🟠（混合 PDF 空提取）、PC-01🟠（清卷尾题）、PC-02🟠（watermark 丢条目）、PC-04🟠（滑走标记）、GN-01🟠（写回丢失）、IS-06/07（adapter 丢行/列）、PC-13（worker skip）。
**共性根因**：无损兜底在多数路径生效，但若干边界路径（并发/resume/截断/删除）未回补占位/记账，且**缺乏跨组件与语料形态边界的回归测试**。

### 主题 B — "宣称能力与实际实现的落差"
简报/文档声称的能力在实现深度上打折，影响产品可信度。涉及：IS-12（文档分类无存证）、PA-08（知识图谱只写不读、无 prerequisite、无消费方）、PA-02（taxonomy 覆盖 5/26 致治理连锁）、GN-02（生成题被门禁排除）、GN-03/GN-10（讲义/组卷头部截断）、GN-13（物理学科化=词面替换）、SB-02/04/06（验证默认关+自证+子串判定）、Memory 系统未实现。
**共性根因**：MVP 优先打通主链路，"生成之外"的输出能力与治理闭环（评价→转正、review→处理、图谱→消费）未完整实现。

### 主题 C — "单机假设下被降级、多租户/计费化后升级"
当前因单机部署 + 图自熔断而降级为 High/Medium，一旦多 worker 化或接真实计费即升级。涉及：HR-01（无 fencing）、HR-04（预算无限）、HR-03（事后记账）、HR-05（token 计量失真）、FB-04/IS-05（配额竞态）、FB-05（批量占配额）、FB-06（默认密钥）、FB-01（分页断裂）、FB-08（SSE 轮询阻塞）。
**共性根因**：check-then-act 模式、进程内假设、事后记账在单实例下"够用"，但缺乏面向生产的原子性/隔离/预留设计。

### 主题 D — “身份与证据没有贯穿生命周期”

DP-01 的 checkpoint/cache/sink、QG-05 的 run manifest、QG-02 的 verifier binding、QG-06 的 side-effect key 都是同一架构问题：对象在生命周期中换了表示，却没有稳定 digest 和可验证关联。应统一使用 manifest + artifact digest + effect id，而不是各模块再发明一个 bool/status。

### 主题 E — “配置、权限与资源靠约定而非类型/能力”

DP-02 的动态 setattr、XC-01 的 scope alias、XC-02 的方法黑名单、DP-08 的并发乘法和 XC-03 的 schema version-only 都允许非法状态绕过局部检查。目标态是 typed config、capability facade、global resource governor 与 schema shape contract。

## 5. 交叉印证（多代理独立确认，可信度增强）

以下发现由**独立子代理各自发现**，交叉印证增强可信度：

| 主题 | 独立发现者 |
|---|---|
| `delete_question` 级联不全（figures/concept/eval/review/embedding 悬挂） | PA-13（标注代理）+ IS-09（存储代理） |
| 单发端点配额 check-then-record 竞态 | FB-04（契约代理）+ IS-05（存储代理） |
| `save_discipline_tag_tree` 缺陷（PA-07 计数膨胀 + IS-08 并发丢失，**同函数两 bug**） | PA-07 + IS-08 |
| 422 detail → "[object Object]" | FE-02（前端代理）+ FB-02（契约代理） |
| 刷新后取消按钮失效 | FE-03（前端代理）+ FB-10（契约代理） |
| CLAUDE.md durable/conversation 层漂移 | DR-01/02（文档代理）+ HR-09（harness 代理）+ IS-17（存储代理）+ GN-17（生成代理） |
| 空 batch block_index 不对称 | PC-03（管线代理，Critical）+ IS-04（存储代理，Medium）→ 主线仲裁 Critical |

## 6. 官方承认的缺口（来自仓库文档，将进入修复计划）

这些是文档**自己**承认的未完成项，修复计划视为"已知路线"而非新发现：

- **PROGRESS §6（8 条明确限制）**：SQLite 单实例无 worker lease/exactly-once；OCI container 非 hostile 最终边界（需 gVisor/Firecracker）；numerical verifier 无 property-based hidden suite；Lean 只 compile 不验翻译忠实；KB/Recommend wrapper 骨架；前端无 KaTeX/CommonMark/artifact 中心/会话删除分支分享；向量检索非默认；access token 无 refresh/吊销。
- **PROGRESS §8（后续优先级）**：P0 lossless/quality gate 基准 + hardened sandbox；P1 题型专用 verifier + artifact API/KaTeX + 分布式 worker lease；P2 向量/图混合检索 + refresh/第三方身份。
- **Docs/12 §5**：无 KaTeX/代码高亮/表格/完整 CommonMark；artifact 无独立浏览/下载；会话重命名/删除/搜索/分支/分享未开放；无文件上传/流式 token/PWA。
- **Docs/16 §10 / Docs/22 §2**：KB Agent embedding+依赖图、DocAssembly 完整版（Jinja2+XeLaTeX PDF）、property-based hidden cases、题型专用 verifier、Lean 翻译一致性、hardened executor、HITL 审批、beam search/Pareto。
- **Docs/17（P3 全文）**：Web 生产前端/App/小程序、统一身份（refresh/微信/Apple/短信）、生产网关（令牌桶/K8s/监控）、私有化 License；待补端点 auth/refresh、questions CRUD、/v1/render、lectures、papers/export PDF。

## 7. 根目录杂项盘点（简报 §5.0）

| 路径 | 判定 |
|---|---|
| `test_diag.py` | **断链遗留**：import 已删除的 `pipeline.task1_extractor`，不可运行（已 gitignore）；可删 |
| `run_pipeline.sh` | 可用但**零文档引用**（git 跟踪）；引用的 env 变量仍有效 |
| `scripts/`（4 文件） | 审计/运维辅助（audit_quality/audit_checkpoints/reset_for_rerun/fix-pth）；前三个写于早期阶段，对 v14 库适用性未验证 |
| `diagrams/` · `Docs/diagrams/`（4 SVG） · `Docs/pipeline_workflow_detailed.svg` | **孤儿资产**：无任何 md 引用 |
| `Docs/Ref/`（7 文件） | v1 原型参考，未被现行文档引用，可保留作蓝图佐证 |
| `data_old/` · `tmp/smoke_*` | 重构前本地备份/smoke 残留，可归档删除 |
| `dist/` · `output/playwright/` | 构建产物/浏览器 smoke 截图，已 ignore，正常 |

## 8. 小结

跨领域视角下，SciForge 有良好的类型检查、单元测试数量与若干扎实基础设施，但**可安装入口、跨模块语义、真实 provider/容器、crash/retry 与 capability 边界**没有被同等强度验证。实施不应按 161 个展示 ID 逐条打补丁，而应围绕五条根因主线建立共同契约；覆盖率、fresh-install、golden/crash matrix、依赖扫描和 SVG 渲染检查必须成为持续门禁。
