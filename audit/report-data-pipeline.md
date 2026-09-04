# 审计报告 · Workstream 1 — 数据处理 Pipeline

> 范围：文档识别/格式适配、滑窗提取、标注、入库、知识图谱、SQLite 存储与迁移、LLM 效率。
> 架构图：[diagrams/data-pipeline.svg](diagrams/data-pipeline.svg)
> **重审优先级**：新增 DP-01~13 以 [RE-AUDIT-REGISTER §3](RE-AUDIT-REGISTER.md#3-data-pipeline-新增升级发现) 为准；本报告后续 PA/PC/IS/SUP 条目作为细粒度证据保留。

---

## 0. 2026-07-11 重审增量

原报告把主要风险归结为局部丢条目边界；本轮运行最小复现后，确认必须先修三层系统契约，再修各局部算法：

| ID | 等级 | 重审结论 | 目标边界 |
|---|---|---|---|
| DP-01 | **Critical** | 完成 checkpoint 不绑定目标 DB、discipline、配置、模型、prompt、schema 或运行模式；对新 DB 可直接返回 completed，多卷 hash 还可拼接碰撞 | `SourceManifestDigest`、`RecognitionCacheKey`、`IngestionRunManifest`、sink commit ledger 分离 |
| DP-02 | High | `[pipeline]` 值绕过 Pydantic 字段校验，0/负数/错误字符串可进入 semaphore、queue、窗口和批量逻辑 | typed nested `PipelineConfig` + cross-field validation |
| DP-03 | High | body hash + `INSERT OR IGNORE` 把“先到版本”永久当 winner，跨书 provenance 和后来更完整解答丢失 | canonical item + occurrence + revision |
| DP-04 | High | post-pass 异常只 warning，仍写 completed；各 pass 的 source scope 不一致且不能单独 replay | manifest 化 post-pass DAG + 必须/可选状态 |
| DP-05 | High | review 是位置型 1:1 数组；null 会删除已消费 span，reorder 可错绑，同长度错误不可检测 | stable-ID `ReviewOperation`：keep/update/reject-to-uncertain/insert |
| DP-06 | High | adapter locator 在 MdBlock/LLM item/DB 链上丢失，并行路径甚至统一归到首 block | per-line/per-item `SourceMap` + conservation ledger |
| DP-07 | High | `parse validate`/`db-stats` 构造可写 backend；0-byte DB 被“只读体检”扩成完整 v14 库 | SQLite URI `mode=ro` + 禁 migration/PRAGMA 写 |
| DP-08 | High | VLM 图像在全局 limiter 之前渲染；当前配置理论可同时占用约 2000 页像素，且 vision 截断照常缓存 | 全局 page/pixel/byte token bucket + stop_reason 门禁 |
| DP-09~13 | Medium/High | retry 成功不清失败记账、Stage3 倒序只日志仍写、harness/carry/evaluator 多为未消费骨架、配置校验漏 DeepSeek 错配、原文/响应日志无治理 | 状态不变量、真实 harness 接线、provider capability 校验、日志脱敏/保留期与 injection threat model |

最小复现及精确证据见统一台账；这组结论将原来的 P0 从“两个点修复”重排为“身份与资源 → 无损 patch/provenance → canonical storage”。

## 1. 架构盘点（现状）

管线为 **6 asyncio Task + 4 stage queue** 的并发 Producer-Consumer。顺序主要由 FIFO、`ReorderBuffer` 与 `item_id` 协作维持，但 Stage3 对倒退只记录日志仍会持久化（DP-10），上游拒绝和异常也允许 gap，故不能表述为“严格 FIFO 保序”。LLM 调用量随页数、窗口、review 开关和失败重试变化；代码上存在 7 类调用点，不能用固定 150–300 次代表所有文档：

```
Task0 识别 ──q_md──> Stage1Extract ──q_raw──> Stage1Review ──q_rev──> Stage2Annotate
  ①VLM 当前10页/批    ②滑窗批量提取         ⑤条件复核(风险信号)       ⑥批量标注≤8/组
  ③micro补抽                                                        ──q_ann──>
  ④evaluator(默认关)                                                Stage2Review(⑦默认关)
                                                                    ──q_done──> Stage3Persist(无LLM)
                                                                                └─> 确定性 post-pass:
                                                                                    numbering/answer_linker/
                                                                                    relation/concept/eval/reconcile
```

**识别器决策树**：`.md/.tex`→整文件；`.csv/.json/.html/.xlsx/.epub/.txt`→确定性 adapter（0-LLM）；`.pdf`→文字层探测（REC-01，≥100 字符/页走 fitz 0-LLM，否则 VLM）；`.docx/.pptx`→soffice→PDF→VLM。

**存储**：`sciforge.db`（schema v14、FTS5 external-content、concept/evaluation/review 表与 v8→v14 migration）+ checkpoint 目录（`state.json` 原子替换 + recognize 缓存）。迁移测试证明的是正常旧版本夹具可升级，**没有证明任意 `user_version=14` 数据库 shape 完整**；backend 只检查两张核心表（XC-03）。

**当前仓库配置**：`pages_per_batch=10`、`vlm_dpi=400`、`vlm_max_concurrent=10`、`max_concurrent_files=20`；`format_profile_enabled=True`、marker/carry/remap/patch/harness 开关多为开启、`stage2_review_enabled=False`。这些值可相乘形成危险资源上界，且 `[pipeline]` 覆盖未经过 typed validation（DP-02/08），不能只逐项判断“合理”。

## 2. 能力现状

| 能力（简报 §1.1） | 状态 | 依据 |
|---|---|---|
| 文档分类 | ◑ 部分（纯规则、无存证、无度量、强中文偏置） | `source_classifier.py` genre 只入日志不入库；IS-12 |
| 内容单元切分 | ● 已实现，含 2 边界缺口 | 滑窗数学总体正确；PC-04/PC-14 |
| 入库 | ● 已实现且事务化 | `save_batch` 单事务 INSERT OR IGNORE + promote |
| 自动打标 | ● 已实现，防线较厚 | schema 正则 + validation context + Stage3 校验；但 subject 在防线外（PA-06），taxonomy 覆盖缺口（PA-02） |
| 难度评分 | ◑ 有评分无一致性保障 | 三套并存，唯一 LLM 校准默认关；PA-12 |
| **知识关系图谱** | ◑ 部分（纯确定性、仅 4 种边、**无消费方**、无 prerequisite） | PA-08 |
| 去重 | ● 内容哈希唯一索引 + IGNORE | KP 全局 / Question tenant 域；但查询计划失效 IS-01 |
| LLM 效率 | ◑ 局部有效 | 0-LLM adapter 和批量真实存在；cache 仅在 provider capability、稳定长 system block 等条件成立时命中，QGen 与 compatible provider 不可泛化为已生效；PC-11/PA-11/DP-08/12 |

---

## 3. Critical 发现

### 🔴 PA-01 — answer_linker 跨章误绑 + 不可逆删题

| 字段 | 内容 |
|---|---|
| **组件** | Pipeline / post-pass / `answer_linker.py` |
| **类别** | 设计缺陷（数据损坏 + 数据丢失） |
| **复核** | **亲读✓**（逐行确认 `answer_linker.py:50-73`、`orchestrator.py:534-548`、`cli/parse.py:678-703`、`core/numbering.py:60-90`） |

**描述**：`link_answers` 仅按 `(subject, number_canonical)` 分组（`answer_linker.py:55`），不含 `seq_chapter`/`seq_section`/`source` 维度。而 `number_canonical` 会把 "第1题"→"1"（`core/numbering.py:74-76`，已亲读确认），按节重编号的教材中裸编号大量冲突。同时 `_ANSWER_MARKER_RE` 把以 **"证明"** 起头的内容判为答案（`answer_linker.py:29-33`）——而 "3. 证明：…" 是数学教材最常见的**题目**句式。当一个分组内恰好出现「1 条无解题目型 + ≥1 条 answer 样式条目」时触发：`backfills` 只取 `answers[0]`，但 `delete_ids` 删除**全部** answer 样式条目（`answer_linker.py:69-72`）。

**证据**：
```python
# answer_linker.py:55  分组键过粗
groups[(q.subject, q.number_canonical)].append(q)
# answer_linker.py:29-33  "证明"被判为答案
_ANSWER_MARKER_RE = re.compile(r"^\*{0,2}\s*(?:解答|解|证明|证|答案|答|Solution|...)(?=[:：.、)）\s]|$)", ...)
# answer_linker.py:69-72  删全部 answers，只回填 answers[0]
result.backfills.append((prob.id, sol_text))
result.delete_ids.extend(a.id for a in answers)
# orchestrator.py:544-545  真删
for did in link.delete_ids: storage_for_pipeline.delete_question(did)
```
触发实例：第 3.1 节 "1. 求 x 的值"（无解、非答案样式）+ 第 5.2 节 "1. 证明：…"（无解、答案样式），二者 `number_canonical` 均为 "1"、同 subject → 后者被当作前者的答案回填，且**被删除**，其题面文本成为前者的解答。

**影响**：(a) 跨节/跨章答案误绑到同号题（知识库内容错误）；(b) "证明："题目被当答案**整条删除**且不可逆（`delete_question` 直接 DELETE）。`parse relink`（`cli/parse.py:692-697`）把作用域扩大到**全学科全库**、无 dry-run、无确认，风险放大。orchestrator 内建路径按 `load_questions_by_source` 限定单文件（`orchestrator.py:535-537`），只缓解跨文件不缓解跨章。

**疑似根因**：分组键粒度不足 + 答案样式判定与题面句式重叠 + 全量删除策略三者叠加。

**触发频率**：机制确凿（High），线上频率取决于语料编号风格（数学教材按节重编号 + "证明"起头极常见，故非罕见）。

---

### 🔴 PC-03 — 空 batch 的 block_index 不对称致 resume 成段内容静默丢失

| 字段 | 内容 |
|---|---|
| **组件** | Pipeline / Task0 + resume |
| **类别** | 代码缺陷（数据丢失） |
| **复核** | **亲读✓**（逐行确认 `orchestrator.py:824-833` 首跑占号、`:892-893` replay 跳过、`stage1_extract.py:756/770` watermark） |

**描述**：`block_index` 是**位置序号**而非内容标识，且三条路径对"空 md batch 是否占号"决策不一致：
- 首跑 `_task0_producer` 对空 `md_text` 的 batch **照常**建 `MdBlockSource` 并递增 `block_index`（`orchestrator.py:824-833`，无空文本过滤，第 831 行甚至对空文本调 `detect_section_boundaries`）。
- resume 的 `_task0_replay_from_cache` 对空文本 `if not text.strip(): continue`（`orchestrator.py:892-893`）——**跳过不占号**。
- Stage1 以数值 `state.current_block_idx` 作恢复光标：`if current_block.block_index < state.current_block_idx: continue`（`stage1_extract.py:756`），并 `state.current_block_idx = current_block.block_index + 1`（`:770`）。

首跑存在 k 个空 batch（VLM 对封面/空白页输出空文本，`resume_state.py:274-276` docstring 自认常见），resume 时后续所有非空 block 的 `block_index` 整体**左移 k**。Stage1 用旧 watermark 跳过 `block_index < 阈值` 的 block → k 个**从未提取过**的真实 block（每个 5 页）被当作已处理跳过。

**证据链**（已亲读）：
```
首跑:  block0(空,占号)  block1(A)  block2(B)  block3(C)   Stage1处理到 current_block_idx=3 后崩溃
resume: block0(A)      block1(B)  block2(C)              ← replay 跳过空batch重新编号
        Stage1: 0<3 skip, 1<3 skip, 2<3 skip           ← 内容C(原block3) 永久丢失,无告警
```
`content_hash` 去重无法兜底（这是漏提取，不是重复）。

**影响**：断点续跑（设计核心场景）+ 文档含空白页/封面页时，成段（5 页）知识库内容缺失且**零告警**——比 VLM 失败页（有 `failed_batches` 记账）更隐蔽。同一根因被 IS-04（Medium 视角）独立发现。

**疑似根因**：`block_index` 位置化 + 三处生产者对"空 batch 是否产出 block"策略不一致。应改用稳定标识（如 `(file_idx, page_start)`）作 watermark。

**触发前置**：需 Task0 首跑完成（`task0_completed=True`）后 Stage1 中途崩溃 resume（Task0 最快、Stage1 最慢，此组合常见）+ 识别器对某些页产出 strip 后为空的 md（VLM/PDF 逐页可能空）。

---

## 4. High 发现

### 🟠 IS-01 — content_hash 部分索引对所有去重/promote 查询失效 → 入库 O(N²)

**组件** Storage · **类别** 性能 · **复核 实测✓**（EXPLAIN QUERY PLAN）

唯一索引带 `WHERE content_hash != ''` 谓词（`sqlite_backend.py:262-282`），而 `count_existing_content`/`resolve_content_ids`/`find_duplicates`/`_KP_PROMOTE_SQL`/`_Q_PROMOTE_SQL` 的 WHERE 均不含该谓词，SQLite 无法证明蕴含 → 拒用部分索引。**主线实测确认**：
```
SELECT ... WHERE content_hash IN (...)              → SCAN knowledge_points（全表扫描）
SELECT ... WHERE content_hash IN (...) AND !=''     → SEARCH USING INDEX idx_kp_hash_uniq
```
**影响**：Stage3 每 16 条批约 4 次全表扫 + 每 accepted 条目 2 次扫描，均在**写事务内**；10 万条时每批秒级、总摄取 O(N²)，且目录并发 8 文件时拉长写锁持有、可能击穿 `busy_timeout=5000ms`。**修复**：查询 WHERE 补 `AND content_hash != ''`（实测即命中索引）。

### 🟠 IS-02 — VLM vision 路径无截断检测，截断批缓存为完成 → 批尾页静默丢失

**组件** Pipeline/VLM · **类别** 代码缺陷（数据丢失） · **复核 代理**

VLM 每批 `max_tokens=16384`；Anthropic 文本路径有 REC-07 截断翻倍重抓，但 `generate_vision`/`generate_vision_multi` 完全不检查 `stop_reason=="max_tokens"`（`vlm_converter.py:229,237`；`anthropic.py:249-293` 无 stop_reason 处理；`:489` 注释自认 TODO）。截断输出照常裁图、写 `page_NNN-NNN.md` 缓存并推下游，resume 永命中缓存不再重试。公式密集教材 5 页逼近 16K token 时批尾 1-2 页内容截断丢失，**无日志无记账**。ImageRecognizer（8192）同病。

### 🟠 IS-03 — 文字层探测"前5页抽样+整档路由"→ 混合 PDF 扫描部分静默丢失

**组件** Pipeline/识别 · **类别** 设计缺陷（数据丢失） · **复核 代理**

探测只抽前 5 页且对整个文档二选一路由（`recognizers.py:159-197`）。① 文字版前言 + 扫描正文的混合 PDF → 判有文字层 → 全文走 fitz → 扫描页 `get_text` 返回空串，`PyMuPdfRecognizer` 首跑**无条件 yield 空 batch**（`:332-341` 无 strip 检查），不记 `failed_batches`、`task0_completed=True` → 页内容永久缺失且无记账。② garbled 判定仅统计 U+FFFD，坏 ToUnicode CMap 输出"合法但错误"汉字 → 探测通过 → 垃圾文本入库。③ backend=vlm 但 client 不支持 vision 时 fallthrough 到 fitz **无警告**（`:673-674`）。

### 🟠 PC-01 — Stage1Review 的 near-tail 校验清除 REC-12/无损兜底的终端条目

**组件** Pipeline/Stage1Review · **类别** 设计缺陷（违背无损声明） · **复核 代理**

`_build_uncertain_fallback` 占位硬编码 `solution_status="no_solution_in_source"` 且 `end_line=窗口末行`（`stage1_extract.py:471-474`），exercise/example/problem 类占位必然命中 Stage1Review 风险信号 2（`stage1_review.py:246-251`），`_check_exercise_near_tail`（`llm_schemas.py:296-317`）通过 feedback retry **强制** reviewer 将该位置设 null "让上游扩窗重提"——但滑窗已消费，"重提"不存在，null 即永久丢弃（`stage1_review.py:100-103` 直接 continue，无镜像、无 review_queue、无占位回落）。**越听话的模型丢得越稳定**。这与 README:24 无损声明矛盾：REC-12 专门保护的卷尾/章末题被 review 阶段反向清除。

### 🟠 PC-02 — resume 用 max-id watermark，Stage2 并发乱序下中间条目永久丢失

**组件** Pipeline/resume · **类别** 设计缺陷（数据丢失） · **复核 代理**

`_build_resume_replay_state` 取各 stage mirror 的**最大** item_id 作阈值（`orchestrator.py:1222-1234`），仅重放 `item_id > after_item_id` 的镜像。Stage2 默认 4/2 worker 并发，mirror 落盘顺序与 item_id 无关：崩溃时若 item 100-107 未写 stage2 镜像而 108-115 已写，resume 时 100-107（≤ 阈值 115）**不重放**也不再生成 → 静默丢失，Stage3 视为合法 gap 不报错。watermark 设计假设镜像按 id 顺序落盘（串行时代成立），REC-03 引入并发后未改为"集合差"重放。

### 🟠 PC-04 — mid-doc force-consume marker-conflict 路径直接滑走含标记行

**组件** Pipeline/Stage1 · **类别** 设计缺陷（违背无损声明） · **复核 代理**

窗口扩至 3000 行、LLM 连续返回空、regex 检出标记时，走 `slide = min(base_window, window_len)`（`stage1_extract.py:998-1013`）——被滑走的前 500 行标记内容**永久丢弃**，仅 error 日志。同文件终端路径（`:944-955`）和 retry-exhausted 路径（`:901-915`）都会造 `_build_uncertain_fallback` 占位，**唯独这条 mid-doc 路径没有**——与 README:24 "不会因 force-consume 静默丢失"直接矛盾。触发需 LLM 在 6 个递增窗口全部空产出（OCR 噪声区/持续拒绝区）。

### 🟠 SUP-01 — trim_tail 在多数学环境下过度截断，默认路径改写持久化内容

**组件** Pipeline / text_tools / `regex_dsl.py` · **类别** 代码缺陷（数据丢失） · **复核 亲读✓**（逐行确认 `regex_dsl.py:133-139`、`patch_instruction.py:91-94/157-160`、`settings.py:316/341`、`stage1_extract.py:579/1097`、`boundary_patch.py:138`）

**描述**：`trim_tail` 的数学配平回退分支用 `re.search` 找 `text[:end]` 中**第一个** math opener 并 `end = opener.start()`，而非"未闭合那个环境"的 opener（`regex_dsl.py:137`），且丢弃了 `check_math_balance` 已提供的精确未闭合位置。当条目内存在"先闭合再未闭合"的多个数学环境（如 `$$x=1$$ … $$y=2`〔未闭合〕）时，回退点跳到第一个**已闭合**环境的开头，其后全部有效内容（含 `$$x=1$$` 与其间正文）被丢弃。

**证据**（已亲读）：
```python
# regex_dsl.py:133-138
prefix_balance = check_math_balance(text[:end].splitlines())
if prefix_balance.open_env_at_tail:
    opener = re.search(r"\$\$|\\\[|\\\(|\\begin\{[A-Za-z*]+\}", text[:end])  # ← 第一个 opener，非未闭合那个
    end = opener.start() if opener else len(text)
# patch_instruction.py:157-160  boundary_fix 回写持久化内容
if instr.reason == "boundary_fix":
    end_offset = trim_tail(item.corrected_content)
    if 0 < end_offset < len(item.corrected_content):
        item.corrected_content = item.corrected_content[:end_offset].rstrip()
```

**默认路径确认**：`boundary_suspect`（条目内出现另一强标记，`boundary_patch.py:138`）→ 生成 `boundary_fix` 指令（`patch_instruction.py:91-94`）→ `apply_deterministic_fixes` 调 `trim_tail`；`marker_ledger_enabled`（`settings.py:316`）与 `patch_local_extraction_enabled`（`:341`）**默认均 True**，`_apply_local_patches` 在窗口处理主路径调用（`stage1_extract.py:1097/1716`）。

**影响**：命中 boundary_suspect 且尾部数学不平衡的条目会被截掉正文并回写入库——属"静默丢内容"主题，与 PC-01/PC-04 同类，但触发在确定性 post-fix 而非提取阶段。触发面被 SUP-05（math_balance 假阳）放大。**疑似根因**：回退应定位到 `check_math_balance` 返回的精确未闭合 opener，而非文本首个 opener。

---

## 5. Medium 发现（紧凑）

| ID | 类别 | 摘要 | 证据 |
|---|---|---|---|
| PA-02 | 设计/一致性 | math taxonomy.json 仅覆盖 5/26 主分支 → 线代/概率/几何等分类塌缩且被治理门禁**永久排除**（治理连锁） | `taxonomy.json` vs `specs/math.py:53-127`；无 `allowed_branches⊆taxonomy` 不变量测试 |
| PA-05 | 设计 | `taxonomy_path=="math"` 产生悬挂 `item_concepts` 且**反向绕过门禁**（兜底条目进池、规范 pending 条目被挡，治理倒置） | `concept_linker.py:214-269`；`query.py:122-126` |
| PA-06 | 缺失功能 | subject 无 schema 校验无 canonicalize → 碎裂 answer_linker/relation/reconciliation 分组键 | `llm_schemas.py:408`；`stage3_persist.py:170-175` |
| PA-07 | 性能 | `save_discipline_tag_tree` load-merge-save 使 item_count 每 run **双重计数**（指数膨胀），老 tag 永压新 tag 展示位 | `sqlite_backend.py:1627-1646`；`tag_tree.py:69-75` |
| PA-09 | 设计 | `tests` 边纯就近启发式（gap≤10），章末习题区系统性错/漏 | `relation_inference.py:121-124` |
| PA-10 | 缺失功能 | review_queue **只进不出**：无人工处理 CLI/API，open 项永久堆积压制生成池 | `load_review_queue` 无调用方 |
| PA-11 | 性能 | 批量标注长度不符不走 retry-with-feedback → 直接付 1+N 次调用；章节先验在默认配置下 Stage2 无效 | `llm_schemas.py:478-489`；`stage2_annotate.py:409-416` |
| PA-12 | 设计 | difficulty 无跨条目一致性机制，唯一校准（Stage2Review）默认关，`questions.difficulty` 无后验修正 | `annotate_stage2_batch.txt`；`stage2_review_enabled=False` |
| PA-13+IS-09 | 缺失功能 | `delete_question` 级联只清 item_relations，遗留 figures/item_concepts/item_evaluations/review_queue/item_embeddings **悬挂行**（两代理独立发现） | `sqlite_backend.py:703-713` |
| PA-14 | 设计 | 跨学科精确匹配放行 + Stage3 保留 = 防漂移留洞（当前多死路径） | `taxonomy_validator.py:143-145` |
| IS-05+FB-04 | 并发 | 单发 generate/solve/papers 配额 **check-then-record 竞态**可超卖；papers 单请求可超扣（两代理独立发现） | `app.py:413-417,485-521`；`saas.py:332-379` 仅 batch 原子 |
| IS-06 | 代码缺陷 | JSONL 非 dict 记录只渲染首行 `[0]` → 数组型记录内容静默丢失 | `format_adapters.py:268-275` |
| IS-07 | 代码缺陷 | XLSX 单元格缺 `r` 属性全坍缩到第 0 列 → 行数据互相覆写丢列 | `format_adapters.py:458-465` |
| IS-08 | 并发 | `save_discipline_tag_tree` load-modify-save 非原子（读不在 BEGIN IMMEDIATE），目录并发 lost update | `sqlite_backend.py:1627-1652` |
| IS-10 | 并发 | SaasStore 锁覆盖不完整（create_user/api_key/behavior/authenticate 不取锁），latent 事务污染 | `saas.py:184-271` |
| IS-11 | 安全 | ZIP 内单 XML entry 可达 256MB 且 `ET.fromstring` 全量建树，无 defusedxml → 本地 OOM DoS | `format_adapters.py:475-483,521` |
| IS-12 | 缺失功能 | 文档分类无准确性机制、无存证、单次首 block 判定、信号强中文偏置（英文材料落 unknown） | `source_classifier.py:74-95`；storage 无 genre 字段 |
| PC-05 | 设计 | `failed_blocks` 无重试/清除路径 → 文件永久无法 completed，目录模式每轮全量空跑 | `stage1_extract.py:898-900`；`resume_state.py:96-106` |
| PC-06 | 一致性 | 并行提取路径缺 carry 状态机与编号连续性提示，与 docstring/默认配置矛盾（no-op） | `stage1_extract.py:1599-1732` |
| PC-09 | 并发 | resume replay 阶段下游 task 早亡时 put 永久阻塞 → 进程静默挂死 | `orchestrator.py:432-467` |
| PC-11 | 性能 | 扩窗/重试/终端全量重发放大 LLM 输入（空 stretch ~3.5x）；Stage1Review 窗口内容发两遍 | `stage1_extract.py:1058-1062` |
| PC-12 | 性能/IO | checkpoint IO 放大：每 item 镜像整窗、state 全量序列化 carry、禁用 stage 仍全量 mirror | `stage1_extract.py:1770-1773` |
| SUP-05 | 代码缺陷 | math_balance 对转义 `\{`/`\}` 无 lookbehind + `\right.` null 定界符不出栈 → 误报 unclosed，误降 evaluation 质量分并**放大 SUP-01 截断** | `math_balance.py:28,99-108`；`evaluation.py:143-145` |
| SUP-02 | 代码缺陷（latent） | structure.py `detect_structure` 单行 `$$…$$`/`\begin…\end` 起始行自身闭合漏检（先 `i+=1`）→ 过并后续正文（当前无生产调用方） | `structure.py:81-114` |

## 6. Low 发现（登记）

PA-15（ReorderBuffer start_id 空转）· PA-16（persisted_count 高估）· PA-17（TagCanonicalizer 跨分支合并）· PA-18（Stage2Review 双开关漂移）· IS-14（HTML 双 unescape + 嵌套 table）· IS-15（gb18030 前置吞噬产 mojibake）· IS-16（缓存缺失静默跳过）· IS-18（audit 不显示 failed_batches）· IS-19（TenantView 放行 save_embedding，latent 投毒）· IS-20（FTS rowid + VACUUM 错位）· IS-21（storage_for_dir 不 close 等）· PC-08（bench 从错误路径读 char span，字符指标永失效；**SUP-07 补充审计逐字段确认成立**——bench 读 `item["anchors"]["resolved_start_char"]` 但该字段在 `SingleItemExtractLLM` 顶层，`anchors` 仅含 start/end_anchor，故 `char_iou` 永久退化行级；其余 metrics 算法核实无误）· SUP-03（ocr_repair `_SUPERSCRIPT_RE` 把 `x1,x2` 下标误改上标，与模块安全原则矛盾；latent——唯一调用方 evaluation 丢弃 repaired_text）· SUP-04（ocr_repair confidence_spans 三 pass 偏移漂移；latent）· SUP-06（headers 页边窗口检测但全文全局删除，误伤正文中段常量短行；latent 无生产调用方）· PC-10（cancel 后不 await + close 竞态）· PC-13（Stage2 worker 异常 skip 整组丢失，叠加 PC-02）· PC-14（schema 不校验行号落窗口内）· PC-15（跨文件 id 空洞逐一 skip 空转）· PC-16（evaluator 结论无消费方 + carry 死代码）· PC-17（compute_file_hash 同文件读≥3 次）· PC-18（并行失败语义分叉 + 裸 gather 孤儿任务）· PC-19（429 各 stage 处理不一致，限流风暴每批睡满 6h）· PC-20（Stage1 prompt 无字节上限）。

> **纠错**：原 IS-13“`page_%03d` 在 >999 页后按词典序错乱”不成立。扫描、恢复与迁移路径均从文件名解析数值页码再排序；该 ID 从 actionable 清单撤销，不应进入实施计划。

## 7. 测试缺口

现有约 60 个管线测试，覆盖良好：carry 四态/local patch/remap/halo/reorder/rate-limit/schema 迁移逐版本/conditional-skip/golden integrity/multitenant 隔离。**关键缺口**（均对应上列发现，无回归防护）：
- answer_linker 跨章/跨书编号冲突、"证明"误判、多 answer 全删语义（→ PA-01 漏网）
- `allowed_branches ⊆ taxonomy 顶层`不变量（→ 本可直接拦下 PA-02）
- 空 batch + 中断恢复的 block_index 对称性（→ PC-03）；乱序 mirror + resume 集合差（→ PC-02）
- VLM 截断（IS-02）、SaaS 原子预留/超卖、adapter 压缩比/列上限/编码链/JSONL 数组、迁移步内中断
- golden 集仅 3 文档、无 problem_set 体裁、无跨 crash-resume 端到端一致性

## 8. 正面结论（须并列呈现）

- **已有 migration 夹具链可重复升级**，但这只覆盖预期旧 schema；对伪造/残缺的 `user_version=14` shape、只读打开和 migration 中断注入仍缺验证（XC-03/DP-07）。
- **FTS5 external-content UPDATE/DELETE 触发器完整**（v13→14 迁移补齐），promote/update 路径同步正确。
- **ZIP 五重门禁扎实**（≤64MB/≤10000 entry/压缩比≤500×/累计≤256MB/禁路径穿越），纯内存读无 extractall，路径穿越无落盘面。
- **入库单事务化**（INSERT OR IGNORE + accepted promote + review 消解），参数元组与 SQL 列序已逐位核对正确。
- **无损兜底在若干正常失败路径有效**（如部分 retry 耗尽与终端尾部 uncertain），但位置型 review、resume watermark、Stage2 组异常、post-pass 与 source locator 仍可静默丢失或错绑，不能作为端到端无损保证。
- **质量门禁被 QGen retriever 真实消费**（非摆设），SQL 在 limit 前过滤。
- **static/dynamic prompt 拆分接口已存在**，0-LLM adapter/文字层可显著省调用；cache 是否真正命中必须按 provider capability 与 usage cache tokens 实测，不可从接口存在推断生产已命中。
