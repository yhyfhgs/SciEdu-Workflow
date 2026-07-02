# SciForge 数据 Pipeline 全面升级方案 v11（四子系统版）

> **版本**: v4.1 · **日期**: 2026-06-17 · **核实日期**: 2026-06
> **基于**: 当前 v10 pipeline 源码全量阅读 + 跨 Provider 官方 API 文档逐条核实（Anthropic / OpenAI / DeepSeek）+ Anthropic Harness Design 系列工程文章 + 第三方审计（ChatGPT 5.5）逐项代码复核
> **目标**: **通用**（多格式 / 多体裁自适应）、**准确**（不漏条目、题答/定理证明成对、边界精确到字符）、**高效**（利用缓存 + 多轮对话 + 工具调用 + 结构化 JSON 输出，节省 LLM 调用次数与 token 开销，能快速处理上千页书籍）、**可用**（标准化知识图谱 + 体系化评价，支撑讲义生成、关联查找、难度匹配）
> **架构路线**: **渐进式叠加，避免全量重写**。所有新能力默认关闭（opt-in），关闭时行为完全不变，断点恢复兼容。复用已就绪的 `generate_multiturn` / `tool_definitions` / `capabilities` / `backends` 能力，**不重复造轮子**。
> **核心组织**: 全部能力归入 **四大子系统**：ExtractionHarness（提取 harness）、TextToolLibrary（文本工具库）、BoundaryAndPatchEngine（边界与补丁引擎）、ConceptGraphAndEvaluation（知识图谱与评价）。每个子系统独立可上线、独立可回滚。
> **里程碑优先级**: 准确不漏（M1–M3）> harness 整合省 token（M4）> 知识图谱（M5）> 评价系统（M6）> 多格式完整化（M7）。其中多格式的早期画像层（profile）前移到 M1，完整适配器仍排在 M7。

---

## 目录

1. [§0 本次修订说明（v3.0 → v4.0）](#0-本次修订说明v30--v40)
2. [§1 当前 Pipeline 工作原理（v10 基线）](#1-当前-pipeline-工作原理v10-基线)
3. [§2 需求 → 任务 可追溯矩阵（12 项全覆盖）](#2-需求--任务-可追溯矩阵12-项全覆盖)
4. [§3 技术校正与跨 Provider 能力矩阵](#3-技术校正与跨-provider-能力矩阵)
5. [§4 核心设计理念：Harness 原则如何映射到 Pipeline](#4-核心设计理念harness-原则如何映射到-pipeline)
6. [**子系统 I** — ExtractionHarness（窗口内 agent harness）](#子系统-i--extractionharness窗口内-agent-harness)
7. [**子系统 II** — TextToolLibrary（确定性工具库）](#子系统-ii--texttoollibrary确定性工具库)
8. [**子系统 III** — BoundaryAndPatchEngine（边界与补丁引擎）](#子系统-iii--boundaryandpatchengine边界与补丁引擎)
9. [**子系统 IV** — ConceptGraphAndEvaluation（知识图谱与评价）](#子系统-iv--conceptgraphandevaluation知识图谱与评价)
10. [§E 多格式 FormatAdapter + 早期 Format/Genre Profile](#e-多格式-formatadapter--早期-formatgenre-profile)
11. [§F Schema 迁移 v10 → v11](#f-schema-迁移-v10--v11)
12. [§G 成本/缓存/结构化输出工程](#g-成本缓存结构化输出工程)
13. [§H 大书吞吐与速度](#h-大书吞吐与速度)
14. [§I 验收、基准与可观测](#i-验收基准与可观测)
15. [里程碑与配置开关](#里程碑与配置开关)
16. [风险与降级矩阵](#风险与降级矩阵)
17. [附录](#附录)

---

## 术语约定

本文档英文术语统一为以下中文简称（首次出现时括号注英文）：

| 英文/缩写 | 中文简称 | 说明 |
|-----------|---------|------|
| prompt caching | 提示缓存 / 缓存 | 将 LLM 请求的重复前缀缓存以省 token |
| multi-turn conversation | 多轮对话 / multiturn | 单次对话中包含多轮 LLM↔工具 交互 |
| tool call / function calling | 工具调用 / tool call | LLM 调用外部确定性函数获取结果 |
| structured output / JSON mode | 结构化输出 / JSON 输出 | LLM 输出受 Schema 约束的 JSON |
| carry / handoff artifact | 跨窗传递 / carry | 窗口间传递未闭合条目的结构化上下文 |
| evaluator / cross validator | 校验器 / evaluator | 独立于提取者的评估 Agent |
| anchor | 锚点 | 用于字符级定位的逐字短串 |
| content hash | 内容哈希 | SHA-256 归一化去重 |
| fast-skip | 快速跳过 | regex 预检发现空窗口时 0 LLM 跳过 |
| near-tail guard | 近尾防护 | 条目贴近窗口末尾时禁止标"无解"的 Pydantic 规则 |
| **harness** | **harness / 编排框架** | **把"模型容易出错"的环节用确定性脚手架固化的外壳；本文特指窗口内的提取 harness** |
| **marker ledger** | **标记台账** | **由确定性正则扫描全窗口得到的"条目起始标记的真值清单"，用来查漏对账** |
| **coverage interval** | **覆盖区间** | **已被某 item 覆盖的行/字符区间；与标记台账对账即可发现遗漏** |
| **PatchInstruction** | **补丁指令 / 局部补抽** | **只针对遗漏或错配的小段文本做 micro-extraction，不重跑整个窗口** |
| **provisional id** | **临时 id** | **窗口内先分配的占位 id，所有补丁合并后统一 remap 为连续的最终 id** |
| **per-item status** | **条目级状态** | **accepted / uncertain / needs_patch / rejected 四种状态** |
| **segment halo** | **段重叠区 / halo** | **并行切段时相邻段互相多扫的重叠行数，保证跨段条目不被切断** |
| **boundary arbiter** | **边界仲裁器** | **确定性合并相邻段 halo 区中重复或部分重复的条目** |
| **concept node** | **概念结点** | **跨书跨文件稳定的知识实体（以稳定的 concept id 作为唯一标识）** |
| **rubric** | **评分量规 / rubric** | **标准化、版本化的多维评价维度集合** |
| extra_body / thinking | 额外请求体 / 思考预算 | 跨 provider 透传非标参数的机制（如 reasoning 预算）；本项目通过 Anthropic 兼容网关的 `extra_body.thinking` 传递 |

---

## §0 本次修订说明（v3.0 → v4.0）

v3.0 已经将"字符级锚点提取（§A）、结构化 carry（§B）、Generator–Evaluator 回路（§C）、复用清单（§D）、多格式（§E）、schema 迁移（§F）、缓存工程（§G）、大书吞吐（§H）"作为**分散的增强层**叠加在 `Stage1Extract` 之上。经第三方审计逐项代码复核，确认存在以下**结构性缺口**，本版（v4.0）针对性补齐：

| # | v3.0 缺口（已用代码核实） | v4.0 补齐方式 | 实现位置 |
|---|---|---|---|
| **G1** | §A/§B/§C 是散落的增强层，**缺少"把窗口当作一次 agent 任务"的统一抽象**——需求 2「pipeline 整体升级为体系化 agent harness」未真正落地 | 引入 **ExtractionHarness**：定义 `WindowContext` 输入和 `HarnessResult` 输出契约，把 locate→extract→audit→patch→carry 合成一个窗口内的 harness | 子系统 I |
| **G2** | §C evaluator 只能"发现"遗漏，**缺少"只补局部"的机制**——发现问题后仍然是"反馈 Generator 局部重提"，没有定义 marker ledger / coverage / patch | 引入 **marker ledger + coverage intervals + PatchInstruction**：对遗漏 marker 附近做 micro-extraction，不重跑整个窗口 | 子系统 III |
| **G3** | §H 明确"并行模式不启用 carry"（v3.0 line 300/522）——与需求 6「前后并行不漏中间截断」**直接冲突** | 引入 **segment halo + 边界仲裁器 + char span 去重合并**，并行也能跨段不漏 | 子系统 III.4 |
| **G4** | **完全没有知识图谱设计**——T3/T4 被标"基线已实现"跳过，需求 7 落空。现状 tag 是纯字符串路径、无稳定 id，关系边只连接 item_id 且仅推断 3 种 | 新增 **Concept 层**：`concepts / concept_aliases / item_concepts / concept_relations`，tag 降为展示层，以稳定 concept id 作为权威标识 | 子系统 IV.1–IV.4 |
| **G5** | **完全没有评价系统设计**——需求 8 落空。现状 `DifficultyScore` 10 维只服务生成引擎，入库只存单值 `difficulty:int` | 新增 **item 评价 rubric 表**：抽取质量 / 解答完整性 / 认知层级 / 依赖深度 / 证明复杂度 / 讲义适用性 / 跨书通用性，复用 10 维难度，入库为一等数据 | 子系统 IV.5–IV.6 |
| **G6** | 工具库只有 4 个（resolve_span + 3 校验）——需求 10「完善高级工具库」不足 | 扩为 **13 类 TextToolLibrary** + 通用正则 DSL（LaTeX/中文/Unicode-aware，字符级掐头去尾） | 子系统 II |
| **G7** | 批量出错主要靠整窗 retry——需求 11「谨慎 retry、增量精准处理」不足。现状 item_id 提取后立即分配，没有 provisional/remap 机制 | **per-item 四态 + provisional id → 统一 remap**，所有 patch 合并后才分配 final id | 子系统 I.3 + III.3 |
| **G8** | 多格式完全放在 M6——需求 1「自动识别并自适应」需要更早介入 | **早期 Format/Genre Profile 层前移到 M1**：格式画像驱动窗口策略和体裁策略，完整 adapter 仍排 M7 | §E.0 |
| **G9** | 缺少端到端可证明性——审计 #12 | 新增 **§I 验收/基准/可观测**：golden benchmark / 成本仪表盘 / human review queue / provider capability tests / OCR 门禁 / prompt & tool trace 版本化 | §I |

**四大子系统总览**：

```
┌─────────────────────────────────────────────────────────────────────┐
│  外部仍是五阶段 queue 流水线（Task0→Stage1→Stage1R→Stage2→Stage2R→Stage3）  │
│  改造只发生在 Stage1 内部 + 入库后 post-pass，其余阶段零改动              │
└─────────────────────────────────────────────────────────────────────┘
        │
        ▼  每个窗口 / 每个段 进入 →
┌──────────────────────────────┐     调用     ┌──────────────────────────┐
│  子系统 I  ExtractionHarness   │ ──────────▶ │ 子系统 II  TextToolLibrary │
│  窗口内 agent：locate→extract  │  确定性工具  │ 13 类确定性工具 + 正则 DSL │
│  →audit→patch→carry           │ ◀────────── │ 输出皆可追溯 line/char/page│
│  输出 HarnessResult           │   ground     └──────────────────────────┘
└──────────────────────────────┘   truth              │ 复用
        │ accepted_items + carry_out                   ▼
        ▼  跨窗 / 跨段 交接 →                  ┌──────────────────────────┐
┌──────────────────────────────┐              │ 子系统 III BoundaryAndPatch│
│  Stage3 入库（knowledge/question）│ ◀──────── │ marker ledger / coverage  │
└──────────────────────────────┘   合并去重    │ carry 四态 / patch / halo  │
        │ 入库后 post-pass                     │ 仲裁去重合并               │
        ▼                                      └──────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│  子系统 IV  ConceptGraphAndEvaluation（入库后 / 入库时一等数据）          │
│  concept 归并 → 知识图谱边 → 多维评价 rubric → 服务讲义/关联/难度匹配     │
└─────────────────────────────────────────────────────────────────────┘
```

**12 项需求 → 子系统/章节 速查**（详见 §2 矩阵）：

| 需求 | 归属 | 需求 | 归属 |
|---|---|---|---|
| 1 多格式自适应 | §E + §E.0 早期 profile | 7 tag→知识图谱 | 子系统 IV.1–IV.4 |
| 2 体系化 agent harness | 子系统 I | 8 评价系统 | 子系统 IV.5–IV.6 |
| 3 multiturn/tool/json | 子系统 I.4 + II | 9 降调用+cache+工具库 | 子系统 I + II + §G |
| 4 窗口截断（四态） | 子系统 III.2 | 10 完善工具库 | 子系统 II |
| 5 遗漏检测+局部补全 | 子系统 III.1+III.3 | 11 批量增量修复 | 子系统 I.3 + III.3 |
| 6 并行不漏中间截断 | 子系统 III.4 | 12 额外（验收/可观测） | §I |

---

## §1 当前 Pipeline 工作原理（v10 基线）

> 任何读者读完本章应能理解 v10 基线。四子系统全部在此基线上**叠加**，默认关闭时行为不变。

### 1.1 五阶段并发 Producer-Consumer

```
LOGICAL DOCUMENT (单文件 or *_part_N_M.* 多分卷)
   │
   ▼  [Task0 Producer]  文档识别 (VlmRecognizer / PyMuPdfRecognizer / MarkdownRecognizer)
   │     · 每 N 页一批 → page_NNN-NNN.md (1-based)
   │     · detect_section_boundaries 填 MdBlockSource.section_boundaries
   │     · 顺序推入 q_md（背压 maxsize=pipeline_queue_size）
   ▼  q_md (MdBlockSource)
   │  [Stage1Extract]  批量滑动窗口提取（LLM 调用 1，**单轮 JSON**）
   ▼  q_raw (ExtractedBatch)
   │  [Stage1Review]   条件化批量校验（LLM 调用 2，REC-06 风险信号制）
   ▼  q_rev (ReviewedItem)
   │  [Stage2Annotate] 批量打标（LLM 调用 3，REC-04 + N-worker 并发）
   ▼  q_ann (AnnotatedItem)
   │  [Stage2Review]   标注审核（LLM 调用 4，REC-05 默认关）
   ▼  q_done (ReviewedAnnotatedItem)
   │  [Stage3Persist]  合并入库（无 LLM，content_hash 去重）
   ▼  SQLite (knowledge_points / questions / figures / item_relations)
```

- 6 个并发 `asyncio.Task`，按依赖倒序启动，`asyncio.gather` 等待全部完成；`SENTINEL` 哨兵通知各 stage 流结束。
- **完全 FIFO + 各 stage 单协程消费**（或 Stage2 N-worker + `ReorderBuffer` 恢复顺序）→ Stage3 按 `item_id` 严格递增入库，`order_index = item_id` 贯通到 DB。
- 关键代码：`pipeline/orchestrator.py`（队列/Task/`OrderIndexAllocator`/缓存重放/post-pass 调度）。

### 1.2 行号滑动窗口提取（Stage1Extract 现状）

- Task0 给每行加 **7 位全局行号前缀** `NNNNNNN | 正文`（`stage1_extract.py:_annotate`，约 line 1008）。
- 窗口按**行数**切：`base_window=500` 起步，0 条则 `+500` 扩到 `max_window=3000`；达 max 且连续空 `empty_force_limit=2` 次 → force-consume（主循环 `_process_windows` 约 line 281–564）。
- 输出 `BatchItemExtractPayloadLLM`（`llm_schemas.py:110–206`）：`items[]` + `stop_reason ∈ {all_complete, incomplete_at_tail, no_items_found}` + `last_consumed_line`。
- 滑窗游标：`consumed = _find_buffer_idx(last_consumed_line)+1`，`line_buffer = line_buffer[consumed:]`，`line_offset += consumed`。
- **当前是单轮无状态 JSON**：`_extract_batch`（约 line 567–661）→ `retry.call_llm_json_with_schema_retry_with_feedback` → `llm.generate_json` + `BatchItemExtractPayloadLLM.model_validate`。失败时把 Pydantic 错误注入 prompt 重试（feedback retry）。**没有 multiturn、没有 tool call、没有字符级定位、没有 per-item 局部修复**。
- **item_id 分配时机**：提取成功后**立即**经 `_allocate_item_ids(len(items))`（约 line 979）分配连续 id 并 emit；**无 provisional / remap 概念**——这是子系统 I.3 / G7 的改造点。

### 1.3 四道防漏防线（现状）

1. **regex 预检 fast-skip**：`_ITEM_MARKER_RE`（定义/定理/例/习题…）+ `_WEAK_SIGNAL_RE`（编号样式/LaTeX/填空/选项）。无任何信号且非末窗 → 0 LLM 跳窗。
2. **Pydantic near-tail guard**：`BatchItemExtractPayloadLLM` validator——`solution_status=no_solution_in_source` + `item_type ∈ {exercise,example,problem}` + 距窗口末行 `< _NEAR_TAIL_MARGIN(10)` → `ValueError` 触发 retry。
3. **REC-12 文末 force_extract**：文档/章节末窗若 regex 有标记但 LLM 返空 → `_extract_batch(force_extract=True)` 关 near-tail guard 再尽力提取一次（`solution_deferred`）。
4. **入库后 numbering_audit**：全表扫描检测跳号/重号，仅报告不改库（`numbering_audit.py:audit_numbering` 约 line 25–61）。

> **现状无**：marker ledger、coverage intervals、per-item 状态、provisional id、PatchInstruction、segment halo——均为本版新增（已用代码核实不存在冲突点）。

### 1.4 跨窗口条目的现状处理（被动）

条目（题干+解答、定理+证明）跨窗口时，靠 `stop_reason=incomplete_at_tail` + `last_consumed_line`：不完整尾部**不进 items**，滑窗只推进到最后一条完整条目，下窗口从断点重扫。这是**被动防御**——若 LLM 漏标 `incomplete_at_tail`，仍可能切断。子系统 III 将其升级为**主动结构化 carry + 四态判别**。

### 1.5 数据模型与去重

- `KnowledgePoint`（`content` + `proof_content` 同表）/ `Question`（`content` + `solution_content` + `options/correct_option/blanks` 同表）。**题答/定理证明存同一条记录**，不拆两条。
- 溯源字段仅 `source_file` + `start_line` / `end_line`（**行号，无字符 offset / 页码 / bbox**）。
- `content_hash = sha256(" ".join(content.split()))[:32]`，UNIQUE 部分索引 + `INSERT OR IGNORE` 幂等去重。跨文档题答联动靠 `parse relink`。
- 标注落库仅 `difficulty:int(1-10)` + `subject` + `taxonomy_path` + `tags:list[str]`（`SingleAnnotationLLM`）。**10 维 `DifficultyScore` 不入库**（子系统 IV.5 改造点）。
- `tags` 是 `english:中文` 段列表（纯字符串路径，**无稳定 concept id**）；`relations` 是内存字段不持久化，落库走 `item_relations` 边表（边连 item_id）。
- DB schema **v10**，`sqlite_backend._MIGRATIONS` 增量 ALTER 升级旧库。

### 1.6 断点恢复与 post-pass

- `FileExtractionState`（`file_hash`/`vlm_batches_done`/`next_item_id`/`line_buffer_carry`/`line_offset`/`tag_tree_json`/各 stage 里程碑）+ 各 stage `items/<stage>/item_<id>.json` 镜像 → 任意阶段可 resume。
- 入库后 post-pass（均 `asyncio.to_thread`，无 LLM）：`numbering_audit`（#2）/ `answer_linker`（#3）/ `relation_inference`（#6，确定性推断 `corollary_of/tests/successor` 三种边）/ `reconciliation`（#7）。

### 1.7 已支持格式

| 格式 | Recognizer | LLM | 备注 |
|---|---|---|---|
| PDF（含文字层） | PyMuPdfRecognizer | 0 | REC-01 文字层探测自动降级 fitz |
| PDF（扫描件/图密集） | VlmRecognizer | VLM | `--force-vlm` 强制 |
| DOCX / PPTX | VlmRecognizer | VLM | 经 LibreOffice→PDF→VLM |
| MD / TeX | MarkdownRecognizer | 0 | 整文件单 batch |

**CSV / Excel / HTML / EPUB / 图片**：`DocumentFormat` enum 有占位但**无 recognizer**，`recognizers.get_recognizer` 末尾 `raise RuntimeError(f"暂不支持的格式")`（`recognizers.py:536`）。§E 补齐。

---

## §2 需求 → 任务 可追溯矩阵（12 项全覆盖）

> 12 项用户需求逐条拆为可执行任务，标注子系统/章节、里程碑、实现方式、以及第三方审计判据。加粗 = 最高优先级（准确不漏·题答成对·边界精确）。

| 需求 | 拆解任务 | 子系统/章节 | 里程碑 | 第三方审计判据 → 本版处置 |
|---|---|---|---|---|
| **1** 多格式/长短自适应 | 早期 format profile + genre profile 驱动窗口/体裁策略；完整 adapter（CSV/Excel/HTML/图片/EPUB） | §E.0 + §E | **M1**(profile) / M7(adapter) | "部分满足，建议前移 profile" → profile 层前移 M1 |
| **2** 体系化 agent harness | `ExtractionHarness`：WindowContext→HarnessResult，把 locate/extract/audit/patch/carry 合一 | 子系统 I | M4 | "未满足，建议新增 ExtractionHarness" → **采纳，新增子系统 I** |
| **3** multiturn/tool/json | 复用 `generate_multiturn`；新增 extraction tools；明确 provider capability + strict JSON/tool fallback | 子系统 I.4 + II + §3.3 | M4 | "方向满足落地未完成" → 落地 + 明确 provider 约束 |
| **4** 窗口截断（含题答阶段） | 结构化 carry **四态**：真无答案 / 答案延后 / 内容截断 / 题干未完 | 子系统 III.2 | **M2** | "部分满足，要区分四态" → **采纳四态判别** |
| **5** 条目遗漏检测+局部补全不重跑 | marker ledger + coverage intervals 查漏 + PatchInstruction micro-extraction | 子系统 III.1 + III.3 | **M1**(ledger)/**M3**(patch) | "缺只补局部 contract" → **采纳 marker ledger/coverage/patch** |
| **6** 并行不漏中间截断（前后并行） | segment halo 重叠 + boundary arbiter 仲裁 + char span 去重合并 | 子系统 III.4 | M2/M5 | "未完全满足，并行禁用 carry 是漏洞" → **采纳 halo+仲裁，并行也跨段不漏** |
| **7** tag→知识图谱（标准化可复用） | concept 层（nodes/aliases/item_concepts/relations）+ 持久化别名归并；tag 降展示层 | 子系统 IV.1–IV.4 | M5 | "明显不足" → **新增 concept graph 全套** |
| **8** 体系化评价系统 | item 评价 rubric 表（7+ 维 + 复用 10 维难度）入库一等数据；服务讲义/关联/难度匹配 | 子系统 IV.5–IV.6 | M6 | "不足，建议 quality/rubric 表" → **新增评价子系统** |
| **9** 降 LLM 调用+cache+工具库 | harness 内 multiturn 共享缓存前缀；条件化触发；cache hit/miss 统计；高级工具库 | 子系统 I + II + §G | M4 | "方向正确，要加 hit/miss 统计 + 风险触发" → **采纳，非每窗默认 multiturn** |
| **10** 完善工具库（字符级掐头去尾） | 13 类确定性工具 + 通用正则 DSL（LaTeX/中文/Unicode-aware）；输出可追溯 span | 子系统 II | M3 | "Docs/20 不够" → **扩为 13 类，列明清单** |
| **11** 批量增量修复（慎用 retry） | per-item 四态（accepted/uncertain/needs_patch/rejected）+ provisional id 统一 remap | 子系统 I.3 + III.3 | M3 | "Stage1 缺 per-item 修复" → **采纳四态 + provisional id** |
| **12** 额外补充 | golden benchmark / 端到端验收集 / 成本仪表盘 / human review queue / capability tests / OCR 门禁 / prompt&tool trace 版本化 | §I | 贯穿 | "否则难证明更稳" → **新增 §I 验收/可观测** |
| 基线 | 字符级 anchor 提取 + 字符级溯源落库 | 子系统 II.4 + §F | **M1** | （v3.0 §A，保留并入工具库） |

---

## §3 技术校正与跨 Provider 能力矩阵

> v2.0 旧方案存在若干技术事实错误，下表逐条更正（带官方依据与正确参数名/数值）。**设计须遵守这些事实。** v4.0 在 C7 后补充 C8/C9 两条 harness 落地必须遵守的工程约束。

### 3.1 校正清单

| # | 旧说法 | 官方事实（核实 2026-06） | 对设计的影响 |
|---|---|---|---|
| **C1** | Anthropic 只能 tool_use + output_schema 做结构化输出 | **不准确。** Anthropic 已 GA 原生 JSON Schema 输出（`output_config.format = {"type":"json_schema","schema":{…}}`），Opus 4.8 / Sonnet 4.6 / Haiku 4.5 支持，无需 beta header。`output_config` 独立于 `strict:true`（约束 tool name/inputs），可独立或同用。 | 主力模型 `claude-opus-4-8`：`_default_json_mode` 对 Anthropic 从"丢弃 response_format"升级为下发 `output_config.format`。**核实：`capabilities.py` 当前 anthropic / anthropic_compatible 能力集均不含 `response_format`（line 22–53），故走 response_format 必被静默丢弃——结构化输出必须走 `output_config.format` 或 tool_use。当前代码尚未实现 output_config，§G.3 列为修复任务。** |
| **C2** | DeepSeek 支持 json_object + strict 结构化输出 | **部分不准确。** DeepSeek `response_format` 仅支持 `{"type":"json_object"}`，不支持 JSON Schema；要求 prompt 含 "json" 字样 + 给示例 + 设合理 `max_tokens`；偶尔返回空 content。`strict` **只用于 tool call**（base_url=`/beta`，每 function 设 `strict:true`，要求全属性进 `required` + `additionalProperties:false`，**strict 下不支持 `minItems/maxItems/minLength/maxLength`**） | 工具 schema 必须避开 min/max 约束；提取主路径继续 json_object + Pydantic 后校验。 |
| **C3** | DeepSeek KV Cache 支持部分命中 | **不准确。只认完整前缀匹配，无部分命中**；自动落盘、免标记、默认开；靠 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` 观测；best-effort 不保证 100%。 | **关键约束**：multiturn / 窗口间消息"稳定前缀在前、动态内容在后"；前缀一变缓存全失效。 |
| **C4** | Anthropic system ≥ 500 字符自动缓存即命中 | **不准确。** 最小可缓存 token 门槛：Opus 4.8 / Sonnet 4.6 = **1024**；Haiku 4.5 = **4096**；Fable 5 = 512。低于门槛即使标 `cache_control` 也静默不缓存。最多 **4 断点**；默认 TTL **5 分钟**（可选 `"ttl":"1h"`）；读 **0.1×**、5m 写 **1.25×**、1h 写 **2×**；前缀顺序 **tools → system → messages**；断点须落"跨请求完全相同的最后一个 block"，回看窗口 **20 blocks**。 | **当前 `backends/anthropic.py:17` 的 `_AUTO_CACHE_MIN_CHARS = 500`（已核实）低于 1024 tok 门槛，可能静默失效。** §G.2 修复任务：按模型路由阈值或 `count_tokens` 精确判定。 |
| **C5** | OpenAI 用 json_object | **可升级。** OpenAI Structured Outputs：`response_format={"type":"json_schema","json_schema":{"name":…,"strict":true,"schema":…}}`，全字段 `required` + `additionalProperties:false` + 根为 object（gpt-4o-2024-08-06+）。 | OpenAI 路径也走严格 schema。 |
| **C6** | 硬编码 `deepseek-v4-pro`/`claude-sonnet-4`/`gemini-3-flash` | 多为臆测或过时。项目实际使用 `claude-opus-4-8` 及 anthropic 兼容网关（MiniMax/Kimi/DeepSeek），配置见 `config/sciforge.toml`。 | **文档不硬编码模型名**，一律经 `TaskLLMConfig` / `task_llm` 间接路由；本文模型名均为示例。 |
| **C7** | 新建 `AgentConversation`/`conversation.py`/`BaseAgent[LocatorInput]` | **重复造轮子。** `LLMClient` 已有 `generate_multiturn`（`client.py:551`）/ `generate_with_tools[_sync]` / `tool_definitions.ToolSpec`+`to_*_format` / `capabilities.PROVIDER_CAPABILITIES` / `backends/anthropic.py` 缓存 + `_extract_usage`。 | 改为**薄封装复用**，删除整套新 Agent 框架。ExtractionHarness（子系统 I）= 围绕 `generate_multiturn` 的薄封装，**不是新 Agent 基类**。 |
| **C8**（新增） | multiturn 提取 harness 可跨任意 provider | **不准确（已核实）。** `generate_multiturn`（`client.py:623–634` / `703–714`）把 tool_result 回传**硬编码为 Anthropic Messages 格式**（`{"type":"tool_result","tool_use_id":...}` + role="user"）。OpenAI/Gemini 的 tool_result 格式不同，走这条回路会失败。 | **harness 的 multiturn 模式绑定 Messages-API 系**：Anthropic 原生 + MiniMax/Kimi/DeepSeek 的 `anthropic_compatible` 网关。恰好覆盖项目主力，可接受。**OpenAI/Gemini 退回单轮 JSON（子系统 I.6 降级）。** 若未来要跨 provider 多轮，需先参数化 `tool_content` 格式（列为 II 期可选项，非本方案前置依赖）。 |
| **C9**（新增） | tools 也会被 capabilities 过滤掉 | **不准确（已核实）。** `tools` 不在 `ALL_OPTIONAL_PARAMS`（`capabilities.py:97–115`），**不经 `filter_params`**；tools 是 `generate_with_tools` 的独立参数，直传 backend。 | `anthropic_compatible` 网关**能**用 tool call（取决于网关是否实现 `/v1/messages` tool_use）。harness 在兼容网关上是否启用 tool 由 `config` 显式开关 + 启动自检（§I capability test）决定，**不会被能力矩阵静默过滤掉**。 |

### 3.2 跨 Provider 能力矩阵（校正后，已对齐 `capabilities.py` 实际值）

| 能力 | Anthropic（本项目主力） | OpenAI | DeepSeek / anthropic_compatible 网关 |
|---|---|---|---|
| **严格结构化输出** | ✅ `output_config.format` json_schema（GA，无 beta 头；**注：response_format 不在能力集，须用 output_config**） | ✅ `response_format` json_schema `strict:true` | ⚠️ 仅 `json_object`（需 prompt 含 "json"+示例）|
| **Tool / Function Calling** | ✅ + `strict:true` + parallel | ✅ + strict + parallel | ✅（tools 不经 filter_params，C9）；strict 仅 `/beta`，禁 min/max |
| **Multiturn tool_result 格式** | ✅ 原生 Messages | ❌（格式不符，C8） | ✅ Messages 兼容 |
| **Prompt Cache** | ✅ `cache_control`（≥1024/4096 tok，4 断点，5m/1h，读 0.1×）| ✅ 自动（≥1024 tok 前缀）| ✅ KV Cache 自动、**完整前缀匹配**（C3）|
| **Thinking/推理预算** | ✅ extended thinking（`extra_body.thinking`）| ✅ reasoning_effort | ✅ thinking mode（兼容网关多支持）|
| **Vision** | ✅ | ✅ | ❌（兼容网关视具体而定）|

**已就绪能力（`src/sciforge/llm/`，子系统复用依据）**：`client.generate_multiturn/generate_with_tools[_sync]`、`tool_definitions.{ToolSpec,to_openai_format,to_anthropic_format}`、`capabilities.PROVIDER_CAPABILITIES` + `filter_params`、`backends/anthropic.py`（`_system_param` 自动 `cache_control:ephemeral` + `_extract_usage` 含 `cache_read_input_tokens`/`cache_creation_input_tokens`）、`retry.call_llm_json_with_schema_retry_with_feedback`（自动采集 `llm.last_usage` 上报 `llm_call_metrics`）。

### 3.3 strict JSON / tool fallback 决策表（C1/C2/C5/C8 综合）

harness 每次需要"结构化结果"时，按 provider 与场景选择最强可用模式，失败逐级降级：

| 场景 | Anthropic | anthropic_compatible | OpenAI | 通用兜底 |
|---|---|---|---|---|
| 提取主输出（items 数组） | `output_config.format`（C1）| `json_object` + prompt"json"+示例（C2）| `response_format` json_schema strict（C5）| Pydantic 后校验 + feedback retry |
| 工具调用（locate/audit/...） | tool_use + `strict:true` | tool_use（网关支持时，C9）| function strict | 无 tool → 退回纯 JSON 单轮（I.6）|
| 多轮校验回路 | 原生 multiturn | Messages 兼容 multiturn | **不支持（C8）→ 退单轮** | 单轮 JSON + 确定性工具离线对账 |

**原则**：能用确定性工具拿真值就不靠 LLM；能用 strict schema 约束就不靠后校验重试；provider 不支持多轮就退单轮但**不放弃确定性 coverage 审计**（子系统 III 的审计纯 Python，与 provider 无关）。

---

## §4 核心设计理念：Harness 原则如何映射到 Pipeline

> 依据 Anthropic 两篇工程文章：*Effective harnesses for long-running agents* (2025-11) 与 *Harness design for long-running application development* (2026-03)。

| Harness 原则（原文要点） | 映射到本 Pipeline |
|---|---|
| **Generator–Evaluator 分离（GAN 式）**："让 agent 评估自己的产出时，它们往往自信地夸赞——即便质量平庸。" | 子系统 I 的 Evaluator 与 Generator 用**独立 system、可独立模型**；关键判断交**子系统 II 确定性工具**做 ground truth，Evaluator 不复用 Generator 自述 |
| **Context reset + 结构化 handoff 优于 compaction**：避免 "context anxiety"（临近上限提前收尾）| 这正是"习题贴窗口末尾被误判 no_solution"的根因。子系统 III carry 用结构化交接，不靠模型"记得"上一窗 |
| **结构化 artifact 传递**；"JSON 比 Markdown 更不易被模型不当改写" | `HarnessResult` / `IncompleteItemCarry` / `MarkerLedger` 均为**结构化 JSON artifact** |
| **Contract negotiation / 可度量验收标准** | coverage 审计用确定性指标（marker 数 vs item 数、未覆盖区间、配对自洽）判 `pass/needs_patch`，非主观 |
| **每个 harness 组件都编码了"模型不会做的事"，应随模型变强而压测** | 全部新能力 **opt-in**：模型/场景够强时可关，退回单轮主路径 |
| **确定性工具替代模型猜测** | 子系统 II 全部纯 Python、无 LLM、结果即真值；LLM 只做"工具够不到的语义判断" |
| **"窗口即一次 agent 任务"** | 子系统 I：每个窗口 = 一个有界 agent 任务，输入 `WindowContext`、输出 `HarnessResult`，内部可多轮但对外是一个原子单元——这是"pipeline 整体 agent 化"的落地形态（需求 2）|

---

## 子系统 I — ExtractionHarness（窗口内 agent harness）

> **回应需求 2/3/9/11。** 把当前散落的"单轮 JSON 提取 + 被动防漏 + 离线审计"收拢为**一个窗口内的有界 agent**：对外是原子单元（输入一个窗口、输出一批 item），对内可按需多轮（locate→extract→audit→patch→carry），全程复用确定性工具做 ground truth。
> **定位**：harness **内嵌于 Stage1Extract**，替换 `_extract_batch` 的"单次 LLM JSON"为"一次 harness 运行"。**外部五阶段 queue 流水线、Stage1Review/Stage2/Stage3 全部零改动**——这正是 ChatGPT「Stage1 内部 harness 化、外部仍用 queue」的建议。
> **开关**：`extract_harness_enabled=false` 默认关；关闭时 `_extract_batch` 原样调用（行为完全不变）。

### I.1 输入/输出契约（结构化 artifact）

```python
class WindowContext(BaseModel):
    """harness 单次运行的全部输入（一个窗口 + 跨窗上下文）。"""
    window_lines: list[str]          # 带 7 位行号前缀的窗口（与现有 _annotate 同构）
    line_base: int                   # 窗口首行的全局行号
    is_last_window: bool             # 是否文档/章节末窗（影响 near-tail / force_extract）
    carry_in: IncompleteItemCarry | None = None   # 上一窗未闭合条目（子系统 III.2）
    genre: Literal["book","exam_paper","lecture","unknown"] = "unknown"  # §E.0 体裁画像
    format_profile: FormatProfile | None = None    # §E.0 格式画像（驱动窗口/正则策略）
    discipline: str = "math"
    provisional_id_base: str         # 本窗 provisional id 前缀（如 "w0012_"）

class HarnessResult(BaseModel):
    """harness 单次运行的全部输出（ChatGPT 建议的 7 字段全覆盖）。"""
    accepted_items: list[ExtractedItem]      # 已确认入流的条目（带 per-item 状态=accepted）
    carry_out: IncompleteItemCarry | None    # 传给下一窗的未闭合条目
    marker_ledger: MarkerLedger              # 本窗标记真值清单 + 覆盖状态（子系统 III.1）
    coverage_intervals: list[CoverageInterval]  # 已覆盖行区间（审计用）
    patch_log: list[PatchRecord]             # 本窗做过的局部补抽记录（可观测）
    risk_score: float                        # 0–1 本窗风险（决定是否触发 evaluator/二次验证）
    token_usage: dict[str, int]              # input/output/cache_read/cache_creation（成本仪表盘）
    last_consumed_line: int                  # 滑窗游标（与现状语义一致，保证 resume 兼容）
    stop_reason: Literal["all_complete","incomplete_at_tail","no_items_found"]
```

> `ExtractedItem` = 现有 `SingleItemExtractLLM` 加三个可选字段：`provisional_id:str`、`status:ItemStatus`、`anchors:ItemAnchors|None`（子系统 II.4）。默认值使其对旧路径透明。

### I.2 单窗口 harness 回路（locate → extract → audit → patch → carry）

```
WindowContext
   │
   ▼ ① LOCATE（确定性，0 LLM）
   │   子系统 II: find_item_markers + detect_structure + normalize_text
   │   → 建立 MarkerLedger（真值标记清单）+ FormatProfile 微调
   │
   ▼ ② EXTRACT（1 次 LLM，主路径）
   │   主力模型单轮 JSON：在"已知 marker 位置"提示下批量提取 items
   │   （prompt 携带 marker_ledger 摘要 → LLM 不必自己找边界，省 token 提准）
   │   carry_in 非空 → 要求 items[0] 优先续完上一窗条目
   │
   ▼ ③ AUDIT（确定性，0 LLM）  ← 子系统 III.1 覆盖率审计
   │   把 accepted items 的 [start,end] 投影为 coverage_intervals
   │   与 marker_ledger 对账 → 三类问题：
   │     · missed_marker：有 marker 但无 item 覆盖 → 候选遗漏
   │     · boundary_suspect：item 边界越界包含了相邻 marker / 留有多余尾巴
   │     · pairing_suspect：solution_status 与 proof/solution 不自洽
   │   全 0 → 直接收尾（②之后 0 额外 LLM，覆盖 70–80% 干净窗口）
   │
   ▼ ④ PATCH（条件触发，micro-extraction）  ← 子系统 III.3
   │   仅对 missed_marker / boundary_suspect 的**小区间**做局部补抽：
   │     · 优先确定性修复（边界回退、配对重绑，0 LLM）
   │     · 必须 LLM 时：只向 LLM 输入遗漏 marker 附近 ±N 行（不重跑整窗）
   │   产出补的 item（provisional_id）插入正确 order 位置
   │   高风险窗口（risk_score 高 / genre=exam_paper）→ 可选 evaluator 多轮复核（I.4）
   │
   ▼ ⑤ CARRY + REMAP（确定性）
   │   尾部未闭合条目 → carry_out（子系统 III.2 四态判别）
   │   per-item 状态收敛后，按最终 start_line 排序，一次性 remap provisional→final id（I.3）
   │
   ▼ HarnessResult
```

**关键：② 只调 1 次 LLM 是常态**。①③⑤ 全确定性；④ 仅在审计发现问题时触发，且只补局部。**干净窗口 = 1 次 LLM；高风险窗口 = 1 + 少量 micro patch / 1 轮 evaluator**。这同时满足需求 9（降调用）与需求 5/11（不漏、增量修复）。

### I.3 per-item 四态 + provisional id → 统一 remap（需求 11，G7）

现状痛点（已核实）：item_id 在提取成功后**立即**连续分配并 emit（`_allocate_item_ids` line 979），无法"先提取、审计后再补、最后统一编号"。harness 引入两层 id：

| 阶段 | id 形态 | 说明 |
|---|---|---|
| harness 内 ② 后 | `provisional_id`（`w0012_03`）| 窗口内序号，不占全局 order_index |
| harness 内 ④ 补抽 | `provisional_id`（`w0012_03b`）| patch 插入也只给临时 id |
| harness 出口 ⑤ | `final item_id`（全局连续）| 按最终 start_line 排序，一次性向 `OrderIndexAllocator.reserve(n)` 申请 |

**per-item 状态机**（`ItemStatus` 枚举）：

| 状态 | 含义 | 处置 |
|---|---|---|
| `accepted` | 确定性审计通过 / evaluator 确认 | 进 accepted_items，分配 final id |
| `uncertain` | 试探合并、低置信边界、carry 收尾的截断条目 | **仍入库**（宁可入库也不丢弃），标记待 §I human review queue / reconcile 复核 |
| `needs_patch` | 审计发现遗漏/错配，等 ④ 补 | ④ 完成后转 accepted；④ 失败转 uncertain |
| `rejected` | 重复（content_hash 命中）/ 越界 / 仲裁淘汰 | 不入库，记 patch_log 备查 |

**为什么 remap 安全**：`order_index = item_id` 不变量只要求"出口时连续递增"，不要求"提取时即分配"。harness 出口一次性排序分配，等价现状但允许中途补条目。`OrderIndexAllocator`（`orchestrator.py:158`）的 `reserve(count)` 原子接口**无需改动**，只是改在 harness 出口调用。**目录并发跨文件不重叠语义不变。**

> **降级**：`extract_harness_enabled=false` 时跳过两层 id，走现状"提取即分配"。开关打开但某窗口 harness 异常 → 退回 `_extract_batch` 单轮结果 + 现状立即分配（harness 是增强层，最坏情况下退回到现状水平，不会更差）。

### I.4 multiturn + tool + json 的落地（复用 `generate_multiturn`，需求 3/9）

- **Generator（②）**：单轮 JSON，主力模型，走 §3.3 决策表（Anthropic→`output_config.format`；兼容网关→`json_object`+Pydantic 后校验）。**默认不进多轮**——多数窗口 ② 一次成。
- **Evaluator（④ 高风险分支）**：复用 `generate_multiturn`（`client.py:551`），独立 system（"你是审稿人，对照原文与工具真值找遗漏/错配/边界错，不要相信提取者自述"），可配独立 `task_llm["extract_validate"]`（默认 fallback 到 `review` 廉价模型）。LLM 每轮自主调用子系统 II 的只读工具（`find_item_markers`/`check_numbering_continuity`/`check_pairing`），工具结果是 ground truth、LLM 不能否决。
- **provider 约束（C8）**：multiturn 回路**仅在 Anthropic / anthropic_compatible 启用**；OpenAI/Gemini 自动退化为"单轮 Generator + 确定性 AUDIT 离线对账（无多轮 LLM）"——审计仍在（纯 Python），只是少了 LLM 二次语义判断。
- **缓存约束（C3/C4，§G.1）**：multiturn 内 `system`（静态提取指令 ≥1024 tok）+ `tools`（固定定义）在前且 append-only，window 正文 + carry 注入在 user 尾部 → 每轮命中前缀缓存。**这是 multiturn 省 token 的根本原因**：N 轮复用同一份 system+tools 缓存，只为增量 user 付全价。

### I.5 与五阶段 queue 流水线的关系（需求 6 的"仍流水线化"）

```
Task0 ──q_md──▶ [Stage1Extract: 每窗口跑一次 ExtractionHarness] ──q_raw──▶ Stage1Review ──▶ Stage2 ──▶ Stage3
                       │
                       └─ harness 内部多轮/工具/补抽，但对 q_raw 仍是"一批 accepted_items"
```

- harness 是 Stage1Extract 的**内部实现**；对上游（Task0/q_md）、下游（q_raw/Stage1Review）的接口契约不变（仍 emit `ExtractedBatch`）。
- Stage1Review 的 `_should_skip_review` 风险信号与 harness `risk_score` 协同：harness 已做的确定性审计可写入 `ExtractedBatch.meta`，Stage1Review 据此进一步减少重复 LLM 校验（省调用）。
- **并行**：harness 是窗口级原子单元，天然可并行（子系统 III.4 的 segment halo 在 harness 之上做段级并行 + 仲裁）。

### I.6 条件化触发与降级（需求 9 "非每窗默认 multiturn"）

| 窗口风险 | 触发路径 | LLM 调用数 |
|---|---|---|
| 低（纯定义定理、编号连续、marker 数==item 数、无 near-tail 悬挂）| ② 单轮，③ 审计全 0，直接收尾 | **1** |
| 中（编号跳号 / near-tail no_solution / 边界可疑）| ② + ④ 确定性修复（0 LLM）或 1–2 个 micro patch | **1 + 少量** |
| 高（genre=exam_paper 卷末密集短题 / carry 多窗未闭合 / marker≫item）| ② + ④ evaluator 多轮（≤ `extract_validator_max_turns`）| **1 + ≤N** |

- 风险判定复用现有 `_should_skip_review` 信号 + marker/item 数差，**确定性、0 成本**。
- **全程降级链**：harness 异常 → `_extract_batch` 单轮 → feedback retry → 接受 best items + 写 `state.errors` 交入库后 `numbering_audit`/`reconcile` 兜底。**任一层失败都不阻断流水线。**

---

## 子系统 II — TextToolLibrary（确定性工具库）

> **回应需求 9/10。** ChatGPT：「确定性工具优先，LLM 只做判断和补充；工具输出必须可追溯到 line/char/page span」。本子系统把现有仅 4 个工具（v3.0 的 resolve_span + 3 校验）扩为 **13 类通用工具** + **通用正则 DSL**，全部纯 Python、无 LLM、结果即真值。
> **文件位置**：新建 `pipeline/text_tools/`（按类分模块）+ `pipeline/extract_tools.py`（注册为 LLM 可调用 ToolSpec）。现有解题类工具（`tool_definitions.py` 的 execute_python/simplify/verify_numerical）不动。

### II.1 设计原则

1. **确定性优先**：能用规则/正则/解析拿到的真值，绝不问 LLM。LLM 只在"工具够不到的语义判断"出场（如"这段是不是上一条的续写"）。
2. **可追溯 span**：每个工具的每条输出都带 `(start_line, start_char, end_line, end_char, page?)`，可定位回原文字符。这是字符级溯源（§F）与边界仲裁（III.4）的基础。
3. **通用不特化**（需求 10 重点）：正则不能只匹配少数范式。掐头去尾要能处理"不同书本/讲义/试卷的定理/题目头尾的各种花样"——用**可组合的正则 DSL + 分类器**，而非一串硬编码 pattern。
4. **跨 provider tool schema**：全属性进 `required` + `additionalProperties:false`，**避开 `minItems/maxItems/minLength/maxLength`**（C2 DeepSeek strict 约束）。

### II.2 工具清单（13 类）

| # | 工具 | 类别 | 输入→输出 | 服务于 |
|---|---|---|---|---|
| T1 | `normalize_text` | 数据预清洗 | 原文 → 归一化文本（全角半角、Unicode NFKC、连字符/引号统一、零宽字符剥离），保留 char 映射表 | 所有下游 |
| T2 | `strip_running_headers` | 页眉页脚剥离 | 多页文本 → 去重复页眉/页脚/页码/水印（基于跨页重复行检测 + 位置启发） | EXTRACT 前净化 |
| T3 | `detect_structure` | 结构检测 | 文本 → 结构骨架（标题层级 / 列表 / 表格区 / 公式块 / 定理环境），带 span | LOCATE / FormatProfile |
| T4 | `find_item_markers` | 候选条目标记 | 行范围 → 所有条目起始标记 `[{line,char,marker,kind,number_hint}]`（**真值清单**）| marker ledger（III.1）|
| T5 | `resolve_span` | char span resolver | 锚点短串 + 粗行号 → 精确 `ResolvedSpan`（exact/fuzzy/line_fallback 三态）| 字符级提取（II.4）|
| T6 | `audit_coverage` | 覆盖率审计 | marker ledger + items → 未覆盖 marker / 重叠 / 多余尾巴 | AUDIT（III.1）|
| T7 | `check_numbering_continuity` | 编号连续性 | number 列表 → 跳号/重号/乱序缺口（复用 `parse_number_components`）| 查漏 / Evaluator |
| T8 | `check_pairing` | 答案配对 | items → solution_status 与 proof/solution 自洽性（complete 必有正文；near-tail 不得 no_solution）| 题答成对（III.2）|
| T9 | `extract_subinterval` | 局部子区间提取 | 行区间 → 该区间纯正文（剥离行号前缀，供 micro patch 输入 LLM）| PATCH（III.3）|
| T10 | `regex_dsl` | 通用正则 DSL | DSL 表达式 + 文本 → 匹配 span 列表（II.3）。内置 50+ 命名片段，支持组合、OR、量化。**不直接暴露正则**——LLM/Evalautor 用语义级表达式如 `compose("marker_head","number?","title_paren?")` 调用，后端编译为精确正则。 | 掐头去尾 / 自定义查找 / Evaluator 工具 |
| T11 | `repair_ocr_noise` | OCR 噪声修复 | 文本 → 修复常见 OCR 错误并标记低置信处。处理：**字符混淆**（l↔1、O↔0、I↔l↔1、rn↔m、vv↔w）、**标点错位**（中文句号变英文点）、**断字粘连**（hyphen 处合词）、**上下标错位**（"x2"→"x²" 启发式探测）、**全角半角乱序**（数字/字母区内不一致）。修复后输出 `repaired_text + confidence_spans[]`。 | 扫描件 / VLM 路径 / 低质量 MD |
| T12 | `check_math_balance` | 数学环境平衡检测 | 文本 → `$...$` / `$$...$$` / `\\[...\\]` / `\\(...\\)` / `\\begin{...}...\\end{...}` / 各种括号 `() [] {}` 配平检查。输出不平衡 span 列表，含 `unclosed_open` 和 `unopened_close` 两类，带行号+列号。**跨窗检测**：若窗口末尾有未闭合环境 → 生成 `open_env_at_tail` 信号强提示 carry `content_truncated`。另外检测**数学模式内异常**（如 `$` 内出现裸换行、`\\begin{align}` 内列数不一致）。 | 边界判定 / 截断检测 / carry 四态判据 |
| T13 | `parse_structured_q` | 表格/选择题解析 | 题面 → 结构化（选项 A/B/C/D/E/F、填空位数量与位置、判断对错、匹配题连线对、表格内子题）。对选择题：识别**多种选项排版**——`A. xxx B. xxx`（同行）、`(A) xxx (B) xxx`、逐行 `\nA．xxx\nB．xxx`、带括号 `（ ）` 的填空位。对表格题：识别 markdown table 或 LaTeX tabular 中每行/每格对应的子题。输出对齐现有 `options/correct_option/blanks`（REC-19），未识别返回空而非伪造。 | 题型结构化入库 / 对齐 Question schema |

> T4/T6/T7/T8 同时注册为 **LLM 可调用 ToolSpec**（Evaluator 多轮用）；其余主要由 harness 确定性步骤直接调用。所有工具复用现有 `tool_definitions.ToolSpec` 结构与 `to_anthropic_format`/`to_openai_format`，**不新建格式层**。

### II.3 通用正则 DSL（需求 10 核心：字符级掐头去尾）

痛点：不同书/讲义/试卷里一个条目的"头"可能是 `**定理 3.1（中值定理）**`、`定理3.1.`、`Theorem 3.1.`、`【例 2】`、`5. `、`(1)`……"尾"可能误包含了下一条标记、页眉、`\hfill$\square$`、答案分隔符。硬编码 pattern 无法穷举。

**方案：可组合的正则 DSL（`regex_dsl`）**，由命名片段拼装，而不是一条硬编码的正则表达式：

```python
# 命名片段（可复用、跨学科、跨体裁）——示意
FRAGMENTS = {
  "marker_head": r"(?:\*{0,2})(?P<kind>定理|定义|引理|推论|命题|例题?|习题|练习|问题|证明|解|Theorem|Definition|Lemma|Example|Exercise|Problem|Proof|Solution)",
  "number": r"(?P<num>\d+(?:[.．、]\d+)*)",          # 1 / 3.1 / 3.1.2，全角半角
  "enum_paren": r"[(（]\s*(?P<en>\d+|[a-zA-Z]|[ⅰ-ⅹ])\s*[)）]",   # (1) （a） (ⅱ)
  "title_paren": r"[(（](?P<title>[^)）]{0,40})[)）]",  # （中值定理）
  "qed": r"(?:\\hfill)?\s*(?:\$?\\square\$?|∎|□|证毕|Q\.?E\.?D\.?)",
  "answer_sep": r"(?:^|\n)\s*(?:答案?|解答?|证明?|Answer|Solution|Proof)\s*[:：.]",
}
# 掐头：在 item 起始 ±2 行内，匹配 marker_head(+number)(+title_paren) 的整体 span → 切到正文真正开头
# 去尾：在 item 结尾向后看，遇 qed/answer_sep/下一条 marker_head → 精确回退 end，不吞入下一条
```

- DSL 暴露给 harness 与 Evaluator：`regex_dsl(expr=compose("marker_head","number?","title_paren?"), text=..., scope=lines)` → 返回所有命中 span。
- **掐头去尾算法**（确定性，无 LLM）：对每条 item 的 `[start,end]`，在边界邻域用 DSL 片段定位"真正的正文首字符 / 末字符"，结合 T12 数学配平避免在公式中途截断。无法精确定位时退回到行级（不会比现状更差）。
- **通用性保证**：片段库随学科 `DisciplineSpec` 可扩展（复用现有 `branch_keywords`/`tag_aliases` 思路）；新增体裁只加片段、不改算法。

### II.4 char span anchor resolver（原 v3.0 §A，并入工具库）

**痛点（需求 10）**：tool 按行号截取，开头混入编号标签、结尾越界包含了下一条目。**核心决策**：**不让 LLM 输出整数 offset**（中文+LaTeX 混排时数字符极不可靠），改让 LLM 输出**锚点逐字短串** + 粗行号，由确定性 Python 定位精确 offset。

```python
class ItemAnchors(BaseModel):
    start_anchor: str = Field(min_length=2, max_length=40)   # 条目真正开头逐字短串
    end_anchor:   str = Field(min_length=2, max_length=40)
    start_line: int = Field(ge=1); end_line: int = Field(ge=1)   # 粗定位行
    allow_window_fallback: bool = True

class ResolvedSpan(BaseModel):
    start_line: int; start_char: int          # 行内 0-based codepoint 偏移
    end_line: int;   end_char: int            # exclusive
    text: str                                 # 切片后精确正文（已 trim）
    confidence: Literal["exact","fuzzy","line_fallback"] = "exact"
    resolver_note: str = ""
```

**定位算法（`resolve_span`，按可靠性降级，最坏退回现状行级）**：
1. **剥前缀建映射**：构造「全局 char offset → (line_no, line_char)」映射（O(n)）。
2. **start_anchor 精确定位**：`str.find` 唯一命中→取该处；多命中→过滤 `abs(cand_line-start_line)>5` 后选最靠近者；0 命中→step 3。
3. **模糊兜底**：`difflib.SequenceMatcher` 在 ±2 行滑动取 ratio ≥ `fuzzy_threshold(0.86)` 最高分，`confidence="fuzzy"`。
4. **行级兜底**：直接 `start_line` 行首/`end_line` 行尾，`confidence="line_fallback"`（等价现状）。
5. **end_anchor** 取锚点结尾位置，约束 `end>=start`。
6. **防多余尾巴**：切片后若仍以下一条 marker 结尾 → 确定性回退（不调 LLM）。结合 II.3 掐头去尾 + T12 配平。

**集成**：`ExtractedItem` 新增可选 `anchors`；开关 `extract_char_span_enabled=false`。开启时 harness 对每条 item 调 `resolve_span`，`ResolvedSpan.text` 写回 `corrected_content`（仅 `confidence!=line_fallback` 才覆盖），`start_char/end_char/page` 透传 Stage3（§F）。**char span 是质量增强层，不改"按行号滑窗 + content_hash 去重"主干。**

---

## 子系统 III — BoundaryAndPatchEngine（边界与补丁引擎）

> **回应需求 4/5/6/11。** ChatGPT：「专门处理窗口/segment 边界、遗漏 marker、局部补抽、重复合并」。本子系统是 harness 的"不漏不错"内核：marker 台账查漏（III.1）、carry 四态防截断（III.2）、PatchInstruction 局部补抽（III.3）、并行 halo+仲裁（III.4）、重复合并（III.4）。

### III.1 marker ledger + coverage intervals（查漏底座，需求 5，G2）

现状（已核实）：**无任何 marker 清单 / 覆盖区间概念**；查漏只靠入库后 `numbering_audit` 跳号检测（离线、粗粒度）。新增确定性查漏底座：

```python
class MarkerEntry(BaseModel):
    global_line: int; char_offset: int
    marker_text: str
    kind: Literal["definition","theorem","lemma","corollary","proposition",
                  "example","exercise","problem","solution","proof","section"]
    number_hint: str = ""
    status: Literal["unseen","covered","patched","ambiguous"] = "unseen"
    covered_by: str = ""             # 覆盖它的 item provisional_id

class MarkerLedger(BaseModel):
    markers: list[MarkerEntry]       # 由 T4 find_item_markers 扫全窗得到（真值）
    window_line_span: tuple[int,int]

class CoverageInterval(BaseModel):
    start_global_line: int; end_global_line: int
    kind: Literal["item","gap","carry"]
    item_pid: str = ""               # provisional id（item 类）
```

**审计算法（`audit_coverage` = T6，0 LLM）**：
1. 把每条 accepted item 的 `[start_line,end_line]` 投影为 `CoverageInterval(kind="item")`。
2. 逐 marker 检查：落在某 item 区间内 → `status="covered"`；否则 → **候选遗漏**，生成 `gap` 区间。
3. 相邻 item 间的未覆盖非空行段（含 marker）→ `gap`；窗口尾部未闭合 → `carry`。
4. 输出：`missed_markers`（→ III.3 patch）、`boundary_suspects`（item 越过/未达相邻 marker）、`pairing_suspects`（接 T8）。

**这是"检测中间条目遗漏"的确定性手段**（需求 5）：一个窗口里 10 个 marker、LLM 只提了 8 条 → 审计立刻指出缺哪 2 个 marker 的行位置，无需 LLM 重读整窗。

### III.2 统一结构化 Carry + 四态判别（需求 4，G3 前置）

**统一原则**：carry = `incomplete_at_tail` 的**结构化升级**，不引入并存的第二套机制。开关 `extract_active_carry_enabled=false` 默认关。

```python
class IncompleteItemCarry(BaseModel):
    item_type: str; number: str = ""
    category: Literal["knowledge","question"]
    head_anchor_start: str           # 条目开头逐字短串（下窗 resolve 用，复用 II.4）
    head_content: str = Field(max_length=2000)   # 已见正文（截断省 token）
    tail_state: TailState            # ★ 四态判别（见下）
    original_start_line: int
    carry_window_count: int = 1
    content_hash_head: str = ""
```

**四态判别（`TailState`，需求 4 重点——区分"题答阶段"的四种截断）**：

| tail_state | 含义 | 判据（确定性 + LLM 辅助） | 下窗处置 |
|---|---|---|---|
| `truly_no_answer` | 真无答案（习题集只有题、定义无证明）| 距文末远 + 下文已是新 marker + genre 先验（习题册）| 收尾 `no_solution_in_source`，不 carry |
| `answer_deferred` | 答案延后（题在前、答案集中在章末/书末）| genre=exam_paper/习题册 + T8 配对缺口 + 编号体系提示 | 收尾 `solution_deferred`，交 `answer_linker` 跨章回填 |
| `content_truncated` | 内容被窗口物理截断（正文中途断）| T12 数学环境不平衡 / 句子未完 / `incomplete_at_tail` | **carry**：下窗续接正文 |
| `stem_unfinished` | 题干未完（题目本身没说完，下窗是续题）| 题干无终止标点 + 无选项/无问号 + 紧邻窗口末行 | **carry**：下窗续题干 |

> **这是需求 4 最难点的正面解法**：定理/题目/习题"出现在窗口末尾、下窗开头是否有答案/证明难以判断"——四态把"无答案"细分为 `truly_no_answer`（真没有）vs `answer_deferred`（延后）vs `content_truncated`/`stem_unfinished`（物理截断），分别处置，而非一律误判 no_solution。判据用 T8 配对 + T12 配平 + genre 先验三路交叉，LLM 仅在三路不一致时介入。

**carry resolve/merge（下窗，确定性 `_resolve_carry`）**：① 编号匹配 → ② 锚点匹配（II.4 resolver）→ ③ 试探合并（item_type 一致 + start_line 紧接，标 `uncertain`）→ ④ 未闭合则 `carry_window_count+=1` 续传。**收敛上限** `extract_carry_max_windows(3)`：到顶强制收尾为 `solution_deferred`（非 no_solution）。

### III.3 PatchInstruction 局部补抽（需求 5/11 核心，G2/G7）

ChatGPT：「对遗漏 marker 附近做 micro extraction，而不是重跑整个窗口」。这是慎用 retry（需求 11）的关键替代：

```python
class PatchInstruction(BaseModel):
    reason: Literal["missed_marker","boundary_fix","pairing_fix","type_fix"]
    target_line_start: int           # 只补这一小段
    target_line_end: int
    around_marker: MarkerEntry | None
    expected_kind: str = ""
    context_lines: int = 8           # 上下文裕量
    prefer_deterministic: bool = True

class PatchRecord(BaseModel):        # 入 HarnessResult.patch_log（可观测）
    instruction: PatchInstruction
    method: Literal["deterministic","micro_llm","skipped"]
    result_pid: str = ""
    token_cost: int = 0
```

**补抽流程（harness 步骤 ④）**：
1. **确定性优先**（0 LLM）：`boundary_fix`（II.3 掐头去尾回退）/ `pairing_fix`（T8 重绑 solution_status）/ `type_fix`（kind 与 marker 不符则纠正）直接改，不调 LLM。
2. **micro LLM**（仅 `missed_marker` 且确定性补不出时）：`extract_subinterval(target±context)`（T9）取小段 → 输入**单条提取 prompt**（不是整窗）→ 产出 1 条 item（provisional_id）。**token 成本 ≈ 小段行数**，远低于重跑整窗。
3. 补出的 item 按 start_line 插入正确 order 位置，状态 `accepted`；补不出 → 原 marker 标 `ambiguous`，造一条 `uncertain` 占位（宁可入库待复核，不丢）。

> **与 retry 的本质区别**：retry 是"整窗重发、重算全部 token、可能再错一样的地方"；patch 是"只补缺口那几行、其余已 accepted 的 item 一字不重算"。这把"批量部分出错"的代价从 O(窗口) 降到 O(缺口)。

### III.4 并行 segment halo + 边界仲裁器（需求 6，G3 核心）

现状（已核实）：`_run_parallel`（`stage1_extract.py:665`，在 `_process_single_document` 内调用 `ParallelSegmentExtractor.run()`）按 section 边界**精确切段、段间无重叠、段尾不完整条目直接收尾**——这是审计指出的关键缺陷：若一个条目跨 section 边界（或 section 探测不准），并行下会被切断且无人续接。关键代码路径：`document_segmenter` 按 `detect_section_boundaries` 返回的边界切段，`ParallelSegmentExtractor.run()` 对每段独立跑 `_extract_batch`，段间零通信、零重叠、零 carry。

**升级：segment halo（重叠区）+ boundary arbiter（仲裁器）**，让"大文本前后内容并行提取而不漏中间截断"（需求 6 原话）：

```
全文档行序列
   │ 按 section 边界切段
   ▼
段A: [a0 .................. a1]
段B:              [b0 .................. b1]      ← 段A末尾与段B开头各扩 halo_lines（如 60 行）重叠
                  └── halo 重叠区 ──┘
   │ 段A、段B 并发各自跑 ExtractionHarness（互不阻塞）
   ▼
boundary_arbiter（确定性，0 LLM）：对相邻段 halo 区的条目去重合并
   · 完整重复（content_hash 相同）           → 保留一份
   · 部分重复（A 只提到前半 / B 提到完整）    → 保留 char span 更完整者，丢弃残片
   · 跨界条目（A 提到头 + B 提到尾，halo 内可拼）→ 用 II.4 char span 拼接为一条
   · 条目长度 > halo（极少）                  → 退回 carry：段间做一次 III.2 交接
```

**关键保证**：
- **不漏中间截断**：halo 让边界处的条目被相邻两段**同时看到**，至少一段能完整提取；boundary arbiter 用 char span + content_hash 合并去重。
- **不双写**：仲裁后唯一化 + Stage3 `content_hash + INSERT OR IGNORE` 二重保险。
- **仍可并行**：段间 halo 区是只读重叠，不引入段间同步依赖；并发度不降。
- **halo 宽度自适应**：`segment_halo_lines` 默认 0（关，等价现状）；开启时按 genre 设（exam_paper 卷末密集→大 halo；book 定理证明长→更大 halo）。`halo > 典型条目长度` 即可覆盖绝大多数跨界。
- **超长跨界兜底**：条目长于 halo（如跨多段的长证明）→ 退回 III.2 carry 在仲裁层拼接，`carry_window_count` 上限保护。

> 开关 `intra_doc_parallel_extract=true` 时新增 `segment_halo_lines>0` 即启用安全并行；默认仍可关闭 halo 保持现状行为不变。**这同时回应了审计关于「parallel extract 没有安全处理跨段 carry」的指正。**

### III.4 重复合并与幂等去重

- **三层去重**：harness 内 per-item `rejected`（content_hash 命中即丢）→ boundary arbiter 段间合并 → Stage3 `content_hash UNIQUE + INSERT OR IGNORE`。
- **重叠窗口兜底**（独立开关 `extract_window_overlap_lines=0` 默认关）：串行路径高风险体裁可开 `stride=window-overlap` 重扫，去重同样靠 content_hash。
- **合并优先级**：char span 更完整 > confidence 更高（exact>fuzzy>line_fallback）> 行号更早。仲裁决策写 patch_log 可审计。

---

## 子系统 IV — ConceptGraphAndEvaluation（知识图谱与评价）

> **回应需求 7/8。** ChatGPT：「把 tag tree 升级为 concept graph，并把评价分数作为入库一等数据」。这是当前文档**最大的两块空白**（v3.0 把 T3/T4 标"基线已实现"跳过）。本子系统**入库后/入库时**运行，与提取 harness 解耦，可独立上线。

### IV.0 现状与目标差距（已核实）

| 维度 | 现状（v10）| 目标（知识图谱）|
|---|---|---|
| 条目标识 | `tags:list[str]` 纯字符串路径，**无稳定 concept id** | 稳定 `concept_id`，跨书/跨文件同概念归一 |
| 别名归并 | `TagCanonicalizer` 仅**单 pipeline 流程内**，重启丢失 | `concept_aliases` 表**持久化**跨流程归并 |
| 关系边 | `item_relations` 边连 **item_id**，确定性只推 3 种（corollary_of/tests/successor）| `concept_relations` 概念粒度，扩展关系类型（已有 `RelationType` 14 种枚举可用）|
| 评分 | 入库仅 `difficulty:int(1-10)`；10 维 `DifficultyScore` 只服务生成引擎、入库丢弃 | `item_evaluations` 多维 rubric 入库一等数据 |

### IV.1 Concept 层 schema（需求 7，G4）

新增 4 张表（与现有 `item_relations` / `discipline_tag_trees` **共存不替换**）：

```sql
-- ① concepts：跨书跨文件稳定的知识实体（canonical identity）
CREATE TABLE concepts (
    id           TEXT PRIMARY KEY,    -- 稳定 concept id，确定性派生：slug(discipline.branch.canonical_en)
    discipline   TEXT NOT NULL,
    canonical_en TEXT NOT NULL,       -- 规范英文（与 tag 段 english 对齐）
    canonical_zh TEXT NOT NULL,       -- 规范中文
    tag_path     TEXT NOT NULL,       -- 展示层路径快照（"math:数学/analysis:分析/..."）
    kind         TEXT NOT NULL DEFAULT 'concept',  -- concept/method/object/theorem_named/...
    depth        INTEGER NOT NULL DEFAULT 0,       -- 在概念层级中的深度（梯度/难度匹配用）
    created_at TEXT, updated_at TEXT
);
-- ② concept_aliases：别名→concept 的持久化归并（解决"重启丢失"+"跨书同概念")
CREATE TABLE concept_aliases (
    alias       TEXT NOT NULL,        -- 归一化别名（en 或 zh，NFKC + lower）
    concept_id  TEXT NOT NULL REFERENCES concepts(id),
    source      TEXT NOT NULL DEFAULT 'pipeline',  -- pipeline/spec/manual
    confidence  REAL NOT NULL DEFAULT 1.0,
    UNIQUE(alias, concept_id)
);
-- ③ item_concepts：item↔concept 多对多（一个条目命中多个概念，带角色）
CREATE TABLE item_concepts (
    item_id     TEXT NOT NULL,        -- knowledge_points.id / questions.id
    item_kind   TEXT NOT NULL,        -- knowledge/question
    concept_id  TEXT NOT NULL REFERENCES concepts(id),
    role        TEXT NOT NULL DEFAULT 'primary',   -- primary/uses/prerequisite/tests/defines
    weight      REAL NOT NULL DEFAULT 1.0,
    UNIQUE(item_id, concept_id, role)
);
-- ④ concept_relations：概念粒度的知识图谱边
CREATE TABLE concept_relations (
    src_concept TEXT NOT NULL REFERENCES concepts(id),
    dst_concept TEXT NOT NULL REFERENCES concepts(id),
    relation_type TEXT NOT NULL,      -- 复用 RelationType 14 种：prerequisite/generalization/...
    confidence  REAL NOT NULL DEFAULT 1.0,
    source      TEXT NOT NULL DEFAULT 'structural',  -- structural/llm/manual
    note        TEXT DEFAULT '',
    UNIQUE(src_concept, dst_concept, relation_type)
);
```

**稳定 concept id 的确定性派生**（需求 7"不要随意命名、有层次可复用"）：
- `concept_id = slug("{discipline}.{branch}.{canonical_en}")`，如 `math.analysis.uniform_continuity`。
- **slug 算法**（纯确定性，输入→输出同 concept 必同 id）：① `canonical_en` → 小写 → 替换非 `[a-z0-9_]` 字符为 `_` → 压缩连续 `_` → 去掉首尾 `_`；② `branch` 从 `DisciplineSpec.branch_keywords` 的受控词表匹配（取最长匹配的 canonical branch key）；③ `discipline` 取 `Discipline.value`；④ 三部分用 `.` 连接。如 `"Uniform Continuity"` + `analysis` + `math` → `math.analysis.uniform_continuity`。
- canonical_en 来自**受控词表**（`DisciplineSpec.tag_aliases` + `branch_keywords`，已有）。LLM **不能凭空造新 concept**：标注只能从受控词表选；命中不了的进 `concepts` 的 `pending` 子集 + 人工/批量补录（§I human review queue）。
- 这与需求 7「不要随意命名、创建新 tag，而是有层次可复用的 tag 链条」一致：concept_id 本身就是层次路径（`discipline.branch.concept`），每个概念在体系中有且仅有一个规范位置。展示层的 `tag_path`（如 `"math:数学/analysis:分析/continuity:连续性/uniform:一致"`）由 concept_id 逆向展开，而非 concept_id 依赖 tag_path。
- 这正是 ChatGPT「canonical identity 必须是稳定 concept id」+「不要随意创建新 tag」的落地。

### IV.2 tag path 降为展示层（需求 7）

- `KnowledgePoint.tags` / `Question.tags`（纯字符串路径）**保留不变**，但语义降级为 **concept 的展示投影**：每条 item 入库时除写 `tags` 外，同时解析出 `concept_id` 列表写 `item_concepts`。
- `discipline_tag_trees`（现有 TagTree 序列化）继续作 UI 展示树；新增"tag 段 ↔ concept_id"映射使树节点可点开为概念页。
- **向后兼容**：旧 `tags` 字段、`TagTree`、`taxonomy_validator` 全部不动；concept 层是**叠加的标识层**，旧查询路径零改动。

### IV.3 概念归并与别名持久化（需求 7"跨书本/文件/文本"）

入库后 post-pass 新增 `concept_linker`（无 LLM 为主，复用 `TagCanonicalizer` + `taxonomy_validator` 逻辑）：
1. 取 item 的 canonical `tags` 末段（叶概念）→ 归一化（NFKC+lower+别名表）。
2. 查 `concept_aliases`：命中 → 复用既有 `concept_id`（**跨书归并**）；未命中且在受控词表 → 新建 `concepts` 行 + 注册 alias；都不命中 → `pending`（待复核，不污染图）。
3. 写 `item_concepts(item_id, concept_id, role)`。role 由 kind 推断（definition→defines，question→tests，证明引用→uses）。
4. **持久化**：alias 表落库 → 下次/跨流程/跨书直接命中，解决现状"重启丢失"。

### IV.4 关系图升级：item-level → concept-level（需求 7）

- **保留** `relation_inference`（item 级 corollary_of/tests/successor）作"阅读顺序骨架"。
- **新增** `concept_relation_inference`（入库后 post-pass）：把 item 级边**聚合上升**到 concept 级——
  - item A(concept X) `corollary_of` item B(concept Y) → `concept_relations(X, Y, corollary_of)`（累积 confidence）。
  - 跨书出现的同概念关系自动合并（因 concept_id 稳定）→ 形成**跨书本/文件的通用知识关系图**（需求 7 原话）。
  - 可选 LLM 增强：对高频概念对，Evaluator 用 multiturn 判定 `prerequisite/generalization/equivalent` 等高阶关系（`RelationType` 14 种已就绪），结果标 `source=llm`、低默认信任，交 reconcile。
- 图查询服务（IV.6）：给定 concept → 前驱（prerequisite）/后继（successor）/泛化-特化链 → 支撑"有梯度有深度"的讲义编排。

### IV.5 多维评价 rubric（需求 8，G5）

ChatGPT 建议维度：抽取质量、解答完整性、认知层级、依赖深度、证明复杂度、讲义适用性、跨书通用性。**复用** 现有 `DifficultyScore` 10 维（`models.py:238`），扩为**版本化、标准化、入库一等数据**：

```sql
CREATE TABLE item_evaluations (
    item_id        TEXT NOT NULL,
    item_kind      TEXT NOT NULL,            -- knowledge/question
    rubric_version TEXT NOT NULL,            -- 版本化（rubric 演进不破坏旧分）
    -- A. 抽取质量（pipeline 自评，确定性为主）
    extraction_quality   REAL,               -- 0–1：char span confidence + 边界精度 + 配对自洽
    solution_completeness REAL,              -- 0–1：T8 配对 + 解答非空 + 证明闭合
    -- B. 认知/难度（复用 DifficultyScore 10 维 + Bloom）
    difficulty_overall   REAL,               -- 0–10（compute_overall）
    difficulty_dims      TEXT,               -- JSON：10 维明细（concept_count/step_count/...）
    cognitive_level      TEXT,               -- Bloom：remember/understand/apply/analyze/evaluate/create
    prerequisite_depth   INTEGER,            -- 依赖深度（接 concept depth）
    proof_complexity     REAL,               -- 0–1：证明结构复杂度
    -- C. 应用价值（服务上层）
    lecture_suitability  REAL,               -- 0–1：讲义适用性（自洽性/示例性/可独立讲授）
    cross_book_generality REAL,              -- 0–1：跨书通用性（越不依赖特定教材上下文越高）
    -- 元信息
    source        TEXT NOT NULL,             -- pipeline_auto/llm_judge/human
    confidence    REAL NOT NULL DEFAULT 0.7,
    evaluated_at  TEXT,
    UNIQUE(item_id, rubric_version, source)
);
```

**评分来源分层（省 token）**：
- **A 抽取质量**：**确定性**（来自 harness 的 char span confidence、coverage 审计、T8 配对）→ 0 LLM，入库时直接落。
- **B 难度/认知**：复用生成引擎已有的 `DifficultyAnnotator`（`agents/qgen/annotator.py`）+ `difficulty_scoring.txt` prompt——pipeline 入库时**可选**对条目跑一次（或复用 Stage2 标注的 multiturn 顺带产出，不额外加调用）。10 维不再丢弃，落 `difficulty_dims`。
- **C 应用价值**：`lecture_suitability`/`cross_book_generality` 由轻量 LLM 判定（批量、可缓存）或先用确定性启发（独立性=外部引用密度、自洽性=前置概念是否本条已定义），后续校准。
- **版本化 + 多 source**：同一 item 可有 `pipeline_auto` / `llm_judge` / `human` 三套分，`rubric_version` 演进不破坏历史——支撑 §I 难度校准回归。

### IV.6 评价服务化（需求 8"服务讲义生成/关联查找/难度匹配/梯度构建"）

评价分入库后，上层（`cli/generate.py` 的 `lecture` / `DocAssemblyAgent`、推荐系统）按需查询：

| 上层用途 | 用到的评价/图字段 | 机制 |
|---|---|---|
| **讲义生成（有梯度有深度）** | `difficulty_overall` + `prerequisite_depth` + `concept_relations`(prerequisite 链) | 按概念前驱链拓扑排序 + 难度递增编排，挑 `lecture_suitability` 高的条目 |
| **关联查找** | `item_concepts` + `concept_relations` | 由 concept 出发查同概念/相邻概念的跨书条目 |
| **难度匹配** | `difficulty_dims`（多维而非单值）| 按目标维度画像（如"重计算轻证明"）检索匹配题 |
| **跨书组卷** | `cross_book_generality` + concept 覆盖 | 选通用性高、概念覆盖全的题，避免教材绑定 |
| **质量门禁** | `extraction_quality` + `solution_completeness` | 低于阈值的条目进 human review queue（§I），不进生成池 |

> **闭环**：评价系统不仅描述知识库，还**反哺生成引擎**（现有 QGenAgent 已用 `DifficultyScore`）——pipeline 入库的多维分与生成引擎打通同一 rubric，知识库与生成器共用一把尺。

---

## §E 多格式 FormatAdapter + 早期 Format/Genre Profile

> **回应需求 1。** ChatGPT：「多格式至少要有早期 profile 层，不能完全放最后」。本版把 **profile 层前移到 M1**（驱动窗口/体裁策略，立即提升自适应），完整 adapter 仍排 M7。

### §E.0 早期 Format/Genre Profile（M1 前移，G8）

**与完整 adapter 解耦**：profile 是**轻量画像**，不解析新格式，只对**已支持格式**给出"该怎么提取"的策略参数，立即改善"长短/类别自适应"。

```python
class FormatProfile(BaseModel):
    source_format: DocumentFormat
    has_text_layer: bool             # 复用 REC-01 pdf_has_extractable_text_layer
    avg_line_length: int             # 长短自适应：决定 base_window
    density: Literal["sparse","normal","dense"]   # 公式/条目密度
    ocr_noisy: bool = False          # 触发 T11 repair_ocr_noise
    suggested_base_window: int       # 自适应窗口（密集试卷→小窗，长定理书→大窗）
    suggested_halo_lines: int        # 并行 halo 宽度建议
    multi_column: bool = False       # 多栏排版 → 启用分栏拆解预处理

class GenreProfile(BaseModel):       # 复用现有 source_classifier.detect_genre
    genre: Literal["book","exam_paper","lecture","problem_set","unknown"]
    has_inline_answers: bool         # 答案随题 / 答案集中末尾 → 影响 carry 四态先验
    marker_style: str                # 编号风格（驱动 II.3 正则 DSL 片段选择）
    fast_skip_recommended: bool      # REC-14：试卷/讲义自动关 fast-skip 提召回
```

**驱动点**（需求 1"自动识别并自适应多种类别和长短格式"的正面解法）：
- **窗口自适应**：`suggested_base_window` 替代固定 500——稀疏长文（教材正文，密度 low）用大窗（800–1200 行）省调用；密集试卷（每题 3–8 行，密度 high）用小窗（200–350 行）防漏；普通讲义取中（500–600 行）。算法：`base_window = clamp(300, avg_line_length × density_factor, 1500)`，`density_factor` 按 `density` 查表 {sparse:2.0, normal:1.0, dense:0.5}。
**体裁策略表**（覆盖 12 个维度，0 LLM）：

| 维度 | book（教材） | exam_paper（试卷） | lecture（讲义） | problem_set（习题集） |
|---|---|---|---|---|
| fast_skip | ✅ 推荐开（章节首尾有标题，中间定义定理密集） | ❌ **强制关**（短题可能无关键字，靠 weakly_signal 兜底） | ✅ 开 | ❌ 关（题型混杂） |
| carry 四态先验 | `content_truncated` 优先（定理证明跨窗常见） | `answer_deferred` 优先（答案集中于卷末） | `content_truncated` | `truly_no_answer` 优先（习题集常无解答） |
| halo_lines | 80–120 | 50–80（段短） | 60–100 | 60–100 |
| base_window | 800–1200 | 200–350 | 500–600 | 400–600 |
| near_tail_margin | 15 行 | 5 行 | 10 行 | 8 行 |
| T11 OCR 修复 | 仅扫描件 | 扫描件+降质图 | 仅扫描件 | 仅扫描件 |
| 编号风格 | 教科书式（§3.1 / 定理 3.1.2） | 题号式（1. / (1) / 【1】） | 混合式 | 题号式 |

- **正则 DSL 选择**（II.3）：`marker_style` 决定加载哪组命名片段。如 `book` → 加载 `definition/ theorem/ lemma/ corollary/ example_head` 片段组；`exam_paper` → 加载 `enum_paren/ question_number/ answer_sep/ option_marker` 片段组。
- **profile 计算时机**：Task0 识别后、进 harness 前，在 `_process_one_block`（`stage1_extract.py`）入口调用一次 `compute_format_profile(text_head, format, has_text_layer)` + `detect_genre()`（复用现有）。结果写入 `WindowContext.format_profile/genre`。**纯确定性，0 LLM，计算耗时 < 50ms。**
- **自适应反馈闭环**：harness 运行后若 marker_ledger 与 item 数严重不匹配（如 marker 数 > 2× item 数），自动降级 denser 策略（减窗、关 fast-skip）用于该文档后续窗口——这是 profile 的运行时自校正。见 III.1 覆盖率审计触发。

### §E.1 完整 FormatAdapter 协议（M7）

**复用而非替换 Recognizer**：FormatAdapter 把"非 PDF/MD"格式**预转 markdown**，再喂现有 `MarkdownRecognizer` 路径，最小侵入。

```python
class FormatAdapter(Protocol):
    formats: frozenset[DocumentFormat]
    def to_markdown(self, file_path: Path, *, config: AppConfig,
                    llm: LLMClient | None = None) -> Iterator[tuple[int,int,str]]: ...
_ADAPTER_REGISTRY: dict[DocumentFormat, FormatAdapter] = {}
```

| 格式 | 方案 | 依赖 | 页码/溯源 |
|---|---|---|---|
| CSV | `pandas.read_csv` → `df.to_markdown()` | pandas | `block_index` 区分 |
| Excel(xlsx) | `pandas.read_excel(sheet_name=None)` → 每 sheet 一表 | pandas+openpyxl | sheet 名进 `source_file` |
| HTML | BeautifulSoup 清洗 → markdownify | bs4+markdownify | `source_file` 含路径 |
| LaTeX(.tex) | 已支持；增强 `\begin/\end` 作 section_boundaries | 无 | section 标题溯源 |
| 图片/扫描件 | VLM 单页（复用 VlmRecognizer）| 现有 | 有页码 |
| EPUB | ebooklib 解 spine → 每 chapter→markdownify | ebooklib+bs4 | chapter+block_index |

- 集成：`orchestrator._detect_format/_collect_files` 扩白名单（默认仍只收现有集合，新增由 `config.enabled_input_formats` 控制）；`recognizers.get_recognizer` 开头加 `get_adapter(fmt)`，命中包成 `AdapterRecognizer`。**PDF/DOCX/MD/TeX 路径完全不变；下游 Stage1–3 零改动。**
- 无页码格式：`page` 落 `NULL`，`source_file`+`block_index`+`start_line/end_line` 提供完整定位。缺少依赖时明确报错，不静默丢弃文件。

---

## §F Schema 迁移 v10 → v11

### F.1 模型与表新增（全部可选/默认值，向后兼容）

```python
# core/models.py：KnowledgePoint 与 Question 各加（字符级溯源，子系统 II.4）
start_char: int = 0
end_char: int = 0
page: int | None = None
```

```python
_SCHEMA_VERSION = 11
_MIGRATIONS[10] = [
  # 字符级溯源（M1）
  "ALTER TABLE knowledge_points ADD COLUMN start_char INTEGER NOT NULL DEFAULT 0",
  "ALTER TABLE knowledge_points ADD COLUMN end_char INTEGER NOT NULL DEFAULT 0",
  "ALTER TABLE knowledge_points ADD COLUMN page INTEGER",
  "ALTER TABLE questions ADD COLUMN start_char INTEGER NOT NULL DEFAULT 0",
  "ALTER TABLE questions ADD COLUMN end_char INTEGER NOT NULL DEFAULT 0",
  "ALTER TABLE questions ADD COLUMN page INTEGER",
  # 知识图谱（M5，子系统 IV.1）
  "CREATE TABLE IF NOT EXISTS concepts (...)",
  "CREATE TABLE IF NOT EXISTS concept_aliases (...)",
  "CREATE TABLE IF NOT EXISTS item_concepts (...)",
  "CREATE TABLE IF NOT EXISTS concept_relations (...)",
  # 评价系统（M6，子系统 IV.5）
  "CREATE TABLE IF NOT EXISTS item_evaluations (...)",
]
```

- **分里程碑迁移**：char span 列随 M1，concept 表随 M5，eval 表随 M6——每个里程碑一段 ALTER/CREATE，独立可上线。`_MIGRATIONS` 增量执行，幂等容忍 duplicate。
- **向后兼容约束**：`content_hash` **仍只基于 content**（不纳入 char/page/concept/eval）→ 去重语义、`order_index=item_id` 不变量、resume 全不受影响。所有新列/表关闭对应开关时为空/默认，等价现状。
- 数据流：`ResolvedSpan` → start_char/end_char；`MdBlockSource.page_start` → page；`concept_linker` → concept 四表；harness 审计 + `DifficultyAnnotator` → `item_evaluations`。

---

## §G 成本/缓存/结构化输出工程

> **回应需求 9。** ChatGPT：「cache/message layout 合理，但要加 hit/miss 统计 + provider 阈值校验 + 风险触发式二次验证，而不是每窗口默认 multiturn」。

### G.1 消息排布约束（DeepSeek KV Cache + Anthropic cache_control 共用）

```
[tools: 固定提取工具定义]          ← Anthropic 前缀顺序 tools→system→messages（C4）
[system: 静态提取指令 ≥1024 tok]   ← cache_control 自动标记（须过门槛，G.2）
[已确认对话历史 (multiturn)]        ← append-only，只追加不修改 → 前缀稳定
──── 以下每窗口/每轮可变 ────
[user: carry 注入 + marker ledger 摘要 + 窗口行号基准]
[user: 窗口正文 chunk_text]
```

- 现状已对齐：`stage1_extract` 静态指令在 system、动态在 user 尾部 ✅；`generate_multiturn`（`client.py:572-642`）每轮 append ✅；carry/marker 注入放 user 当前窗口段开头，不污染 system 前缀缓存 ✅。
- **multiturn 省 token 的根本**：N 轮 evaluator 复用同一 system+tools 缓存（读 0.1×），只为增量 user 付全价（C3/C4）。

### G.2 缓存工程（校正数值 + 代码修复）

- 数值（C4）：门槛 Opus/Sonnet **1024**、Haiku **4096**；4 断点；5min/1h；读 0.1×、写 1.25×/2×。
- **🔧 修复（M4）**：`backends/anthropic.py:17` `_AUTO_CACHE_MIN_CHARS=500` 低于门槛。该常量用于 `_system_param()` 方法决策是否自动给 system 消息添加 `cache_control: {"type":"ephemeral"}` 标记（见 `_system_param` 约 line 260）。当前值 500 字符对 Opus/Sonnet 可能不足 1024 token（中文约 200–400 token，英文约 80–130 token）→ 标记的 cache_control 会被 Anthropic API **静默忽略**，缓存不生效。修复方案：
  1. 改 `_AUTO_CACHE_MIN_CHARS` 为按模型路由：`{"claude-opus-4-8": 2048, "claude-sonnet-4-6": 2048, "claude-haiku-4-5": 8192}`（安全边际 2× token 门槛，覆盖中英文）。
  2. 或改用 `count_tokens` 精确判定（复用 `backends/anthropic.py` 已有的 `_count_tokens_for_caching` 逻辑），在 `_system_param` 内对拼好的完整 system 文本算 token 数，≥ 门槛才加 `cache_control`。此方案最精确但需一次额外 API 调用（或本地 tiktoken 估算）。
  3. 优先方案 1（低成本、立即生效），方案 2 作为后续精细化项。
- **hit/miss 统计（ChatGPT #9 要求）**：每窗口结束读 `last_usage.cache_read_input_tokens`，写入 `HarnessResult.token_usage` → §I 成本仪表盘聚合 cache 命中率；连续 miss 打 WARNING。`last_usage` 在 `backends/anthropic.py:_extract_usage` 已采集 `cache_read_input_tokens` / `cache_creation_input_tokens`，只需接入 harness 出口。
- 兼容网关：`supports_prompt_cache=true` 放行 `cache_control`；其 KV Cache 本身自动、完整前缀匹配（与 Anthropic cache_control 是独立两套，同启不冲突）。

### G.3 结构化输出升级（C1/C2/C5，§3.3 决策表）

- Anthropic：下发 `output_config.format`（GA，无 beta；**非 response_format**，C1/capabilities 核实）。
- OpenAI：`response_format` json_schema `strict:true`。
- DeepSeek/兼容网关：`json_object` + prompt 含 "json" + 示例 + Pydantic 后校验。
- schema 满足 strict（全字段 required + `additionalProperties:false`），可选字段用 `anyOf[...,null]` 而非省略 required；工具 schema 避开 min/max（C2）。

### G.4 分级路由 + 风险触发式二次验证（ChatGPT #9）

- task key：`extract`（主力）/ `extract_locate`（可选廉价定位）/ `extract_validate`（evaluator，默认 fallback `review`）。
- **二次验证不默认开**：仅 `risk_score` 高的窗口触发 evaluator 多轮（I.6），70–80% 干净窗口 0 额外调用。
- token 节约清单：prompt cache + thinking disabled（提取/标注 `_THINK_OFF`）+ fast-skip + 条件化 evaluator + 严格 JSON 降重试 + **patch 局部补抽替代整窗 retry**（子系统 III.3，最大单项节约）。

---

## §H 大书吞吐与速度

> **回应需求 6。** 现有并行 + 子系统 III.4 halo 安全并行。

- 复用 `intra_doc_parallel_extract`（单文档按 section 并行，`parallel_extract.py`）+ `max_concurrent_files`（目录并发）+ `OrderIndexAllocator`（跨文件 id 不重叠）。
- **并行安全升级**：开 `segment_halo_lines>0` → 段间 halo 重叠 + boundary arbiter 仲裁（III.4），**前后段并发提取且不漏跨界条目**（需求 6 核心，修复 ChatGPT 点名的"并行禁用 carry"漏洞）。
- harness 是窗口级原子单元，段级并行天然成立；段内仍 carry-aware 滑窗。
- 调度参数（文档化最佳值）：`pipeline_stage_queue_size`、`stage2_annotate_concurrency` + `ReorderBuffer`、`max_concurrent_llm_calls`、post-pass 走 `asyncio.to_thread`。
- 注意 opt-in 单文档并行先 drain 全 block 再并行（失去 Task0↔Stage1 重叠），中断 resume 整段重提（靠 content_hash + provisional id remap 去重兜底）——适合单个超大文件；目录多文件已自带跨文件并行。

---

## §I 验收、基准与可观测

> **回应需求 12。** ChatGPT：「否则 v11 很难证明比当前 pipeline 更稳」。无可观测的"升级"无法证伪。

### I.1 golden benchmark 设计

**目的**：为每个里程碑提供**可量化的开关前后对比**，而非主观声称。

**测试集构成**（固定 N=8 份代表性文档，覆盖全部体裁 × 格式 × 质量的交叉）：

| # | 文档 | 体裁 | 格式 | 特点 | 测试重点 |
|---|---|---|---|---|---|---|
| 1 | 数学分析（一） | book | MD（多分卷） | 长文、定理证明密集 | carry 四态、跨窗 merge |
| 2 | 2024一试.tex | exam_paper | TeX | 密集短题、答案集中 | fast-skip 关闭、密集防漏 |
| 3 | Algebra2_线性方程组与矩阵.md | lecture | MD | 中等密度、混合条目 | 窗口自适应、marker ledger 查漏 |
| 4 | 近世代数三百题.pdf | problem_set | PDF→VLM | 习题集无答案、OCR 噪声 | OCR 修复、truly_no_answer 四态 |
| 5 | XZ_抽象代数I.pdf | book | PDF→PyMuPdf | 扫描质量好、图多 | char span 精度、figure 关联 |
| 6 | 202510new.tex | exam_paper | TeX | 大题、多问子题 | 结构化解析 T13、子题关联 |
| 7 | 数学分析（一）_part_181_210.md | book（分卷片段） | MD | 高 fallback 率片段（94.3%）| 高风险窗口 evaluator 触发、局部 patch |
| 8 | CSV/Excel 样例 | mixed | CSV/Excel | 非 PDF 格式 | FormatAdapter（M7）|

**ground-truth 标注**（人工，每文档）：
- `gt_markers[]`：所有条目起始标记的精确位置（行号 + 行内偏移）
- `gt_items[]`：每条目的边界（[start_line, start_char] – [end_line, end_char]）、类型、题答配对
- `gt_concepts[]`：每条目对应的规范 concept_id 列表
- `gt_evaluations`：抽取质量 / 解答完整性 / 认知层级的参考分

### I.2 端到端验收集（每里程碑跑 golden）

| 指标 | 含义 | 计算方式 | 目标（全开 vs 关） |
|---|---|---|---|
| **召回率** (Recall) | 提取到的条目数 / gt_markers 数 | TP / (TP + FN)，marker 粒度 | 关基线 → 开显著提升 |
| **边界精度** (Char IoU) | 提取条目与 gt 条目的字符级交集/并集 | mean(IoU) over matched items | 开 > 关（行级→字符级） |
| **配对正确率** | solution_status 与 gt 一致的比例 | correct / total items | 四态 > 现状（被动） |
| **跨窗合并率** | 被窗口截断但成功 carry 拼合的条目比例 | merged / total boundary-crossing | 关 0% → 开 >80% |
| **遗漏可检测率** | marker ledger 发现的遗漏占实际遗漏的比例 | detected_missed / total_missed | 开 → >90%（marker ledger 使遗漏可见） |
| **局部 patch 占比** | 用 patch 修复的条目 / 所有修复（vs 整窗 retry） | patch_fixes / total_fixes | 开 → >70% |
| **跨书概念归并率** | 同 concept 跨书被正确归并的比例 | correctly_merged / total_same_concept | M5 后 >80% |
| **开销** | LLM 调用数 / token / cache 命中率 / 处理时间 | 聚合 `HarnessResult.token_usage` | 调用数↓ token↓ cache↑ |

CI gate：任一指标较基线退化 > 5% → 回归，阻塞合并。

### I.3 可观测设施

| 设施 | 内容 | 实现方式 |
|---|---|---|
| **golden benchmark** | 固定 N 份代表性文档（书/试卷/讲义/扫描件/多格式）+ 人工标注的 ground-truth 条目集（marker 位置、题答配对、边界）| `tests/golden/` + `parse bench` 子命令 |
| **端到端验收集** | 每里程碑跑 golden，输出：召回率（提取数/真值数）、边界精度（char IoU）、配对正确率、跨窗合并率、漏解率 | CI gate，回归即红 |
| **成本仪表盘** | 聚合 `HarnessResult.token_usage`：每文档 LLM 调用数 / token / **cache 命中率** / patch 占比 / evaluator 触发率 | `parse cost-report` |
| **human review queue** | `uncertain`/`needs_patch` 未决 + `extraction_quality<阈值` + concept `pending` → 待人工复核队列 | `item_evaluations` + 新表 `review_queue` |
| **provider capability tests** | 启动自检：探测 provider 是否真支持 tool_use（C9）/ output_config（C1）/ multiturn 格式（C8），不支持自动降级并告警 | `llm doctor` 子命令 |
| **OCR 质量门禁** | VLM/扫描件路径：T11 OCR 噪声率 + T12 数学配平失败率超阈 → 标 `ocr_noisy`，降级人工 | profile 阶段 |
| **prompt & tool trace 版本化** | prompt `.txt` + tool schema + rubric_version 打 hash，每次运行记录，便于复现/对比"哪版更稳" | `state.json` + trace 日志 |

- **可证明性原则**：每个里程碑的"验收"列（见下表）必须能在 golden benchmark 上**量化对比开关前后**，而非主观声称。
- **遗漏率说明**：v10 未直接统计遗漏率；v11 用 golden 的"真值 marker 数 vs 提取数"给出**可测召回率**，marker ledger（III.1）使遗漏**可检测**，这是相对 v10 的硬指标进步。

---

## 里程碑与配置开关

> 准确不漏(M1–M3) > harness 整合省 token(M4) > 知识图谱(M5) > 评价系统(M6) > 多格式完整化(M7)。每个里程碑独立可上线、可回滚，默认行为与现有版本完全一致。**采纳 ChatGPT 建议的优先级重排，并把多格式 profile 层前移 M1。**

| 里程碑 | 内容 | 子系统/章节 | 风险 | 验收（golden benchmark 量化）|
|---|---|---|---|---|
| **M1** | 字符级 span + marker ledger + coverage audit + schema char 列 + **format/genre profile 前移** | II.4 + III.1 + §F + §E.0 | 低 | 关=现状；开=char IoU↑、未覆盖 marker 可观测、窗口自适应；旧库迁移幂等 |
| **M2** | carry 四态 + boundary arbiter + segment halo（安全并行）| III.2 + III.4 | 中 | 关=现状；开=跨窗/跨段题答合并率↑、漏解率↓、并行不漏中间截断；content_hash 无双写 |
| **M3** | TextToolLibrary 13 类 + 正则 DSL + PatchInstruction 局部补抽 + per-item 四态 + provisional id remap | II + III.3 + I.3 | 中 | 关=现状；开=遗漏被局部补全、整窗 retry 占比↓、token/缺口比↓ |
| **M4** | ExtractionHarness multiturn/tool/json 整合 + 缓存阈值修复 + 结构化输出升级 + 风险触发二次验证 | I + §G | 低 | `cache_read>0`、JSON 重试率↓、干净窗口仍 1 次 LLM、高风险窗口召回↑ |
| **M5** | Concept 层（4 表）+ 别名持久化 + item-level→concept-level 关系图 | IV.1–IV.4 | 中 | 跨书同概念归并率、concept 图边数/质量、tag 展示层不回归 |
| **M6** | 评价 rubric 入库（10 维落库 + 7 维 rubric）+ 评价服务化 | IV.5–IV.6 | 中 | 多维分入库、讲义/关联/难度匹配可用查询、质量门禁生效 |
| **M7** | 多格式完整 FormatAdapter（CSV/Excel/HTML/图片/EPUB）| §E.1 | 低 | 现有格式回归不变；新格式跑通整条 pipeline |

**配置开关汇总（全部默认保持现状）**：

```toml
[pipeline]
# M1
extract_char_span_enabled = false
format_profile_enabled = false        # 早期 format/genre profile
marker_ledger_enabled = false         # coverage 审计底座
# M2
extract_active_carry_enabled = false  # carry 四态
extract_carry_max_windows = 3
segment_halo_lines = 0                 # 并行 halo（>0 启用安全并行）
extract_window_overlap_lines = 0
# M3
text_tool_library_enabled = false     # 13 类工具
patch_local_extraction_enabled = false  # PatchInstruction 局部补抽（替代整窗 retry）
provisional_id_remap_enabled = false  # per-item 四态 + remap
# M4
extract_harness_enabled = false       # ExtractionHarness 总开关
extract_validator_enabled = false     # evaluator 多轮（仅高风险触发）
extract_validator_max_turns = 3
extract_two_pass_enabled = false
# M5 / M6
concept_graph_enabled = false
item_evaluation_enabled = false
rubric_version = "v1"
# M7
enabled_input_formats = ["pdf","docx","pptx","md","markdown","tex"]

[tasks.extract]          # 主力提取
[tasks.extract_locate]   # 可选廉价定位
[tasks.extract_validate] # evaluator，默认 fallback 到 review
```

---

## 风险与降级矩阵

| 机制 | 风险 | 降级 |
|---|---|---|
| ExtractionHarness | 某窗 harness 异常 | 退回 `_extract_batch` 单轮 + 现状立即分配 id（最坏情况下退回到现状水平，不会更差）|
| multiturn evaluator | C8 仅 Messages 系；不收敛/烧 token | OpenAI/Gemini 退单轮 + 确定性审计；条件化触发 + max_turns + numbering_audit 兜底 |
| anchor resolve | LLM 抄错锚点/不唯一 | fuzzy → line_fallback（退现状行级）|
| carry 四态 | 四态误判 / 多窗不闭合 | 三路交叉（T8+T12+genre），不一致才问 LLM；`carry_max_windows` 强制收尾 `solution_deferred` |
| segment halo 仲裁 | 条目长于 halo / 重复合并错 | 退回 carry 拼接；content_hash + char span 优先级裁决；Stage3 INSERT OR IGNORE 二保险 |
| PatchInstruction | 局部补抽仍补不出 | marker 标 `ambiguous` + 造 `uncertain` 占位（宁可入库也不丢弃）+ human review queue |
| provisional id remap | 出口排序错乱 | 出口断言 order_index 严格递增；异常退回现状"提取即分配" |
| concept 归并 | 误并/造新概念 | 受控词表约束（不命中进 pending）；alias confidence + reconcile；tag 展示层不受影响 |
| 评价 rubric | 维度分不准 | 版本化 + 多 source（auto/llm/human）；§I 难度校准回归；低分进门禁不进生成池 |
| 结构化输出 | DeepSeek 误用 JSON Schema | 主路径 json_object + Pydantic 后校验；strict 仅 tool call 且禁 min/max |
| Anthropic 缓存 | system < 门槛静默不缓存 | 静态指令做厚 ≥1024 tok；运维校验 `cache_read>0` + capability test |
| FormatAdapter | 缺依赖库 | `get_adapter` 返回 None → 明确报错，不静默丢弃文件 |
| schema v11 | 迁移中断 | 分里程碑 ALTER/CREATE 幂等容忍 duplicate |

---

## 附录

### A.1 复用 vs 新建 速查

| 类别 | 清单 |
|---|---|
| **直接复用（零改动）** | `generate_multiturn`/`generate_with_tools`、`tool_definitions.{ToolSpec,to_*_format}`、`capabilities.PROVIDER_CAPABILITIES`+`filter_params`、`anthropic._system_param`/`_extract_usage`、`retry.call_llm_json_with_schema_retry_with_feedback`、`content_hash`/INSERT OR IGNORE、`OrderIndexAllocator.reserve`、`parallel_extract`/`document_segmenter`/`reorder_buffer`、`tag_tree`/`tag_canonicalizer`/`taxonomy_validator`、`relation_inference`、`source_classifier.detect_genre`、`DifficultyScore`/`DifficultyAnnotator`/`difficulty_scoring.txt`、`numbering.{canonicalize_number,parse_number_components}` |
| **新增（薄封装/确定性）** | `pipeline/extraction_harness.py`（WindowContext/HarnessResult，围绕 generate_multiturn 薄封装）、`pipeline/text_tools/`（13 类工具）、`pipeline/extract_tools.py`（ToolSpec 注册）、`pipeline/char_span.py`（resolve_span）、`pipeline/boundary_patch.py`（marker ledger/coverage/carry 四态/patch/halo 仲裁）、`pipeline/format_profile.py`、`pipeline/concept_linker.py`、`pipeline/concept_relation_inference.py`、`pipeline/item_evaluator.py`、`pipeline/format_adapters.py` |
| **小改** | `llm_schemas.py`（ExtractedItem.{provisional_id,status,anchors} + carry_out + MarkerLedger）、`core/models.py`+`sqlite_backend.py`（start_char/end_char/page + concept 4 表 + item_evaluations + v11 迁移）、`stage1_extract.py`（_extract_batch→harness 集成点 + 出口 remap）、`client._default_json_mode`（output_config 升级）、`backends/anthropic.py`（缓存阈值）、`recognizers.get_recognizer`+`orchestrator._detect_format/_collect_files`（profile + adapter 接入）、`orchestrator` post-pass（concept_linker/item_evaluator 调度）|

### A.2 关键集成文件

- `src/sciforge/pipeline/stages/stage1_extract.py` — 子系统 I 主集成点（harness 替换 _extract_batch，出口 remap）
- `src/sciforge/pipeline/llm_schemas.py` — ExtractedItem 扩展 / MarkerLedger / IncompleteItemCarry 四态 / ValidatorVerdict
- `src/sciforge/core/models.py` + `storage/sqlite_backend.py` + `storage/schema.sql` — §F 字段 + concept/eval 表 + `_MIGRATIONS` v10→v11
- `src/sciforge/pipeline/recognizers.py` + `orchestrator.py` — §E profile + FormatAdapter 接入 + post-pass 调度
- 新建 `src/sciforge/pipeline/{extraction_harness,boundary_patch,format_profile,concept_linker,concept_relation_inference,item_evaluator,char_span,extract_tools,format_adapters}.py` + `pipeline/text_tools/`
- 复用依据：`src/sciforge/llm/{client,tool_definitions,capabilities,retry}.py` + `backends/anthropic.py` + `agents/qgen/annotator.py`

### A.3 参考文献（核实日期 2026-06）

**Anthropic 工程文章**
1. *Effective harnesses for long-running agents* (2025-11). https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
2. *Harness design for long-running application development* (2026-03). https://www.anthropic.com/engineering/harness-design-long-running-apps

**Anthropic API 文档**（`docs.anthropic.com` 已 301 → `platform.claude.com/docs`）
3. Prompt caching. https://platform.claude.com/docs/en/build-with-claude/prompt-caching
4. Structured outputs（`output_config.format` + strict tool use）. https://platform.claude.com/docs/en/build-with-claude/structured-outputs

**OpenAI API 文档**（`platform.openai.com` 已跳转 `developers.openai.com`）
5. Structured Outputs（`response_format` json_schema `strict`）. https://developers.openai.com/api/docs/guides/structured-outputs

**DeepSeek API 文档**
6. JSON Output（仅 json_object）. https://api-docs.deepseek.com/guides/json_mode
7. Function Calling（strict 仅 /beta，禁 min/max 约束）. https://api-docs.deepseek.com/guides/function_calling
8. Context Caching / KV Cache（自动、完整前缀匹配）. https://api-docs.deepseek.com/guides/kv_cache

### A.4 与 v10 预期对比

| 指标 | v10（当前）| v11（全开）| 变化 |
|---|---|---|---|
| 提取范式 | 每阶段单轮 JSON API | 窗口内 agent harness（locate→extract→audit→patch→carry）| 体系化 agent 化（需求 2）|
| 格式支持 | PDF/MD/TeX + DOCX/PPTX | + format/genre profile 自适应 + HTML/CSV/Excel/图片/EPUB | 5→10+ 且窗口策略自适应 |
| 提取边界精度 | 行级 | 字符级（exact/fuzzy/line_fallback）+ 正则 DSL 掐头去尾 | 显著提升，最坏不劣化 |
| 条目遗漏 | 四道防线被动 + 离线跳号审计 | marker ledger + coverage 主动查漏 + 局部 patch 补全 | 遗漏可检测、可局部补，不重跑整窗 |
| 窗口截断 | 被动 `incomplete_at_tail` | carry 四态（真无答案/延后/截断/题干未完）| 题答阶段截断正面解决 |
| 并行安全 | 按 section 切、跨段会断 | segment halo + 边界仲裁 + char span 合并 | 前后并行不漏中间截断 |
| 批量错误处理 | 整窗 retry | per-item 四态 + provisional id remap + 局部 patch | 增量精准，慎用 retry |
| 知识组织 | tag 字符串路径（无稳定 id，重启丢失）| concept graph（稳定 id + 持久化别名 + concept 关系）| 跨书知识关系图 |
| 评价 | 入库仅 difficulty 单值 | 多维 rubric 入库一等数据（抽取质量/认知/讲义适用性/跨书通用性…）| 体系化评价，服务讲义/关联/难度匹配 |
| JSON 合规 | Anthropic 纯 prompt + 后校验 | `output_config.format` 严格约束 | 非法 JSON 重试↓ |
| Token 成本 | cache + fast-skip + batch annotate | + 条件化 evaluator + 局部 patch 替代 retry + multiturn 缓存复用 + 阈值修正 | 整体 token↓ |
| 可证明性 | 无遗漏率统计 | golden benchmark + 成本仪表盘 + capability test + review queue | 量化对比、可证伪 |
| 兼容性 | — | **全 opt-in，默认行为与现有版本完全一致，分里程碑可回滚** | 零回归风险 |

> **关于"遗漏率"**：v10 未直接统计；v11 不声称具体百分比，而是用 golden benchmark 的"真值 marker 数 vs 提取数"给出**可测召回率**，并用 marker ledger 使遗漏**可检测、可局部补全**——这是相对 v10 的方法论进步，而非数字承诺。

