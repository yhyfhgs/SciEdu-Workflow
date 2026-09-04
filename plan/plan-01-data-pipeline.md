# 详细计划 · plan-01 — 数据处理 Pipeline

> 消化 [report-data-pipeline.md](../audit/report-data-pipeline.md) 的全部发现。按阶段组织，每节标注消化的发现 ID。
> 代码片段均为 **proposed / not-yet-implemented**，仅为目标态说明，未写入仓库。
> **重审说明**：先执行下述 P0-R，再执行历史 P0/P1；完整数据契约、指标与 PR 切分见 [plan-05 §3–5](plan-05-modernization-program.md)。

---

## P0-R — 先建立运行与数据不变量（DP-01~13）

### P0-R.1 · 三层身份与 sink commit（DP-01）

- `SourceManifestDigest`：对每个 volume 加长度边界、相对路径、媒体类型和内容 digest，禁止裸字节拼接。
- `RecognitionCacheKey`：只含会影响识别字节的 recognizer/version/DPI/pages-per-batch/model/prompt；允许跨目标 DB 复用。
- `IngestionRunManifest`：另含 discipline、有效配置、LLM task/provider/model、prompt/schema/harness/post-pass 版本、运行模式与目标 sink identity。
- `sink_commits(run_id, sink_id, schema_fingerprint, committed_at, counts_digest)` 是 early-return 的必要条件；完成 state 但 sink 未提交时必须 replay persist/post-pass，不得返回 completed。
- `dry-run/skip-llm/force-vlm/backend` 进入 run namespace；禁止把演练 state 写成正式完成。

验收包含已经复现的两个反例：完成 checkpoint + 新 DB 不得假成功；`[ab,c]` 与 `[a,bc]` manifest 不得相等。

### P0-R.2 · typed config 与全局资源预算（DP-02/08/12）

把动态 `[pipeline]` setattr 改成 Pydantic `PipelineConfig`，对所有正整数、枚举、字节/行数和 cross-field 乘积做 validation；`config validate` 必须解析 provider endpoint/capability（包括 DeepSeek Anthropic URL + provider type 与 env key 拼写）。新增进程级 `ResourceGovernor`：LLM call、rendered page、pixel、image byte、queue memory 和 DB writer 分别有 token，并按公平 scheduler 获取，禁止“每文件 10 VLM × 20 文件”相乘失控。

### P0-R.3 · stable-ID review、SourceMap 与 post-pass DAG（DP-04/05/06/09/10/11）

- review 输入/输出带 immutable `item_uid`，输出 patch op 而非位置数组；`null` 不再意味着丢弃，必须转成 `reject_to_uncertain(reason)`。
- adapter 每行 locator 经 MdBlock/window/item 持续传递，accepted/uncertain item 都必须能回到具体 source span。
- Stage2 组异常逐 item 写 conservative annotation/uncertain，不可 `continue` 整组；Stage3 倒序是 invariant violation，拒写而非日志后继续。
- `failed_batches/failed_blocks` 在成功重试后原子清除；完成条件读取 unresolved ledger。
- post-pass 变成可重放 DAG，声明输入 scope、输出表、版本、required/optional；required 失败则 run 不得 completed。

### P0-R.4 · canonical storage 与只读检查（DP-03/07、XC-03）

body hash 只用于候选匹配，不再承担 identity/provenance。用 `canonical_items`、`item_occurrences`、`item_revisions` 表达“同一内容在多书多处出现”和“后来版本更完整”；winner 由可审计 policy 选出。`parse validate`/`db-stats` 使用 SQLite URI `mode=ro`，不得建库、migration、切 journal 或写 pragma；另做 schema shape manifest 校验列/索引/trigger/check。

### P0-R.5 · 内容日志与 prompt-injection 边界（DP-13/XC-04）

raw prompt/response 默认只记录 digest、长度、provider metadata、usage 与结构化错误；需要内容级调试时必须显式 opt-in、加密、tenant 分区、最小权限并设 TTL。教材/用户内容用明确 data envelope 与控制指令分区，adapter/VLM/提取 prompt 建 adversarial corpus，验证文档内伪指令不能改写 system policy、tool policy 或输出 schema。

## P0 — 历史 Critical 止血（PA-01, PC-03；依赖 P0-R 身份契约）

### P0.1 · answer_linker 重设计（PA-01 🔴）

**问题回顾**：分组键 `(subject, number_canonical)` 过粗 + "证明:"题面误判为答案 + 命中删全部 answer 条目 → 跨章误绑 + 不可逆删题。

**目标态设计**（三处独立加固，任一即可显著降险，建议全做）：

1. **分组键加维度**——把章节/来源纳入分组，杜绝跨节冲突：
```python
# proposed answer_linker.py
def _group_key(q: Question) -> tuple:
    return (q.source_file, q.subject, q.seq_chapter, q.seq_section, q.number_canonical)
```
仅在同一来源同一节内配对；跨文件题答联动（习题册+答案册）改为**显式**模式（见 3）。

2. **答案判别加"题目型"负判**——"证明"起头需再排除题面特征（问句、"求"、"设…证明"结构、题号紧邻）：
```python
# proposed: 答案样式 AND 非题面样式 才算答案
def looks_like_answer(content: str) -> bool:
    if not _ANSWER_MARKER_RE.match(strip_noise(content)):
        return False
    return not _looks_like_problem_stem(content)  # 问句/求/设...证明/无解答正文
```
或更保守：把"证明/证/Proof"从 `_ANSWER_MARKER_RE` 移除，只对"解答/答案/Solution/Answer"这类**无歧义**标记做联动（证明题的题答分离交给显式跨文件模式）。

3. **可逆软删 + dry-run**——`delete_question` 改为标记 `ingestion_status='superseded'`（保留行、退出发布查询）而非物理删除；`parse relink` 默认 `--dry-run` 打印计划，`--apply` 才执行，并要求 `--confirm`：
```python
# proposed cli/parse.py
def parse_relink(db, discipline, dry_run: bool = True, confirm: bool = False):
    plan = link_answers(questions)
    print_plan(plan)  # 逐条显示"将把 X 的答案回填到 Y、软删 Z"
    if dry_run or not confirm:
        return  # 只报告不写库
    ...
```

**接口/数据变更**：`delete_question` 增软删语义（或新增 `supersede_question`）；`LinkResult` 增 `source_scope` 字段；`schema` 的 `ingestion_status` CHECK 增 `superseded`（走 v14→v15 迁移）。

**测试**（补 PA-19 缺口）：跨章同号题不误绑、"3.证明："题目不被判答案、多 answer 条目不全删、relink dry-run 不写库、软删条目退出发布查询。

**验收**：构造"第3.1节 1.求值 + 第5.2节 1.证明"夹具，跑 answer_linker 后两题都完整保留、无误绑。

### P0.2 · block_index 稳定标识（PC-03 🔴）

**问题回顾**：空 md batch 在 producer（占号）与 replay（跳过）两侧编号不对称，resume 后 Stage1 watermark 跳过真实 block。

**目标态设计**——用**稳定内容标识**替代位置序号：
```python
# proposed: block 身份 = (file_idx, page_start)，与是否空、是否跳过无关
@dataclass
class MdBlockSource:
    block_uid: str  # f"{file_idx:03d}:{page_start:05d}"  ← 稳定
    ...
# Stage1 恢复光标改为"已处理 block_uid 集合"
if current_block.block_uid in state.processed_block_uids:
    continue
state.processed_block_uids.add(current_block.block_uid)
```
统一 producer 与 replay 的空 batch 策略：**两侧都产出 block（含空）**并占据稳定 uid，或**两侧都跳过空**——关键是对称。推荐两侧都产出（保留 uid 连续性 + 让空 block 也进 processed 集合，Stage1 对空 block 快速跳过但记录 uid）。

**接口/数据变更**：`FileExtractionState.current_block_idx`（int）→ `processed_block_uids`（set[str]），走 state schema v2→v3 迁移（旧 state 无 uid 时降级为整段重提，靠 content_hash 去重兜底）。

**测试**：空 batch + 中断恢复端到端——首跑含 2 空白页 block、Stage1 处理到中途崩溃、resume 后确认所有非空 block 都被提取（无 uid 被跳过）。

---

## P1 — 无损摄取加固（消化 IS-01/02/03/06/07/08/11, PC-01/02/04/05/06/09/11/12, PA-07/11/13+IS-09）

### P1.1 · 去重查询命中索引（IS-01 🟠，实测已验证修复有效）
所有 `content_hash IN (...)` / promote 的 WHERE 补 `AND content_hash != ''`：
```sql
-- proposed，实测加此谓词后 SEARCH USING INDEX idx_kp_hash_uniq
... WHERE content_hash IN (?, ...) AND content_hash != ''
```
覆盖 `count_existing_content`/`resolve_content_ids`/`find_duplicates`/`_KP_PROMOTE_SQL`/`_Q_PROMOTE_SQL`/review 消解子查询。**验收**：EXPLAIN QUERY PLAN 全部命中索引 + 10 万条入库基准无 O(N²) 退化。

### P1.2 · VLM 截断检测（IS-02 🟠）
`generate_vision`/`generate_vision_multi` 返回 `stop_reason`；`vlm_converter` 见 `max_tokens` 时：不缓存为完成、记 `failed_batches`、可选降 dpi 或拆页重试。**验收**：mock 截断响应 → 该批进 failed_batches 而非静默缓存。

### P1.3 · 文字层探测逐页 + 空率校验（IS-03 🟠）
探测改逐页判定（而非前 5 页整档路由）；`PyMuPdfRecognizer` 对空文本页记账 + 事后"空文本率 > 阈值"告警 → 建议 `--force-vlm`；无 vision 降级路径加 warning。

### P1.4 · Stage1Review 豁免占位（PC-01 🟠）
`_check_exercise_near_tail` 对 `status=uncertain`/force_extract 批次跳过强制 null（这些内容已无窗口可回退）。**验收**：REC-12 占位条目经 Review 后仍保留。

### P1.5 · resume 集合差重放（PC-02 🟠）
`state` 记录各 stage **已完成 item_id 集合**（或位图），重放取集合差而非 `> max`。与 P0.2 的 processed 集合思路一致。

### P1.6 · mid-doc force-consume 占位（PC-04 🟠）
marker-conflict 滑走分支加 `_build_uncertain_fallback(consumed_region)`，与终端/retry 路径一致。

### P1.7 · 其余无损/性能项
| ID | 动作 |
|---|---|
| PA-13+IS-09 | `delete_question`（及新软删）级联清 figures/item_concepts/item_evaluations/review_queue/item_embeddings，或改这些表 FK ON DELETE CASCADE + 软删过滤 |
| PA-07 | `save_discipline_tag_tree` 不再 merge 已含全局计数的文件树；改为增量 delta 或全量替换 |
| PA-11 | `BatchAnnotationLLM` validation_context 传 expected_count，validator 拒绝长度不符 → retry-with-feedback 挽回 |
| IS-06 | JSONL 非 dict 记录 `"\n".join(_render_json_node(...))` 而非 `[0]` |
| IS-07 | XLSX 缺 `r` 属性按"前一单元格列号+1"回退 |
| IS-08 | `save_discipline_tag_tree` read-modify-write 包进 BEGIN IMMEDIATE |
| IS-11 | 引入 defusedxml + 单 XML entry 字节上限（非仅总量） |
| PC-05 | `failed_blocks` 增重试 CLI（`parse retry-failed`）或 resume 时按策略重试；提供清除路径 |
| PC-06 | 并行 `_extract_segment_windows` 接入 carry 状态机与编号先验，或文档明示并行模式契约差异 |
| PC-09 | replay put 加超时/下游存活检查，避免早亡挂死 |
| PC-11 | 扩窗只重发新增行（增量），或缓存上轮 prompt 前缀 |
| PC-12 | mirror 整窗改为存一次 window + item 引用 offset；禁用 stage 跳过 mirror |

### P1.8 · 确定性 text_tools 修复（SUP-01 🟠, SUP-05/02 🟡；补充审计轮）

**SUP-01（trim_tail 过度截断，默认路径改写持久化内容）**——回退点改用 `check_math_balance` 已返回的**精确未闭合位置**，而非 `re.search` 找到的首个 opener：
```python
# proposed regex_dsl.py:133-139  —— 基于真实 API：MathBalanceReport.issues[*] 的 kind=="unclosed_open" 带 (line,char)
prefix_balance = check_math_balance(text[:end].splitlines())
if prefix_balance.open_env_at_tail:
    # 回退到 balance 已定位的首个未闭合 opener，而非文本首个 opener
    first_unclosed = next((i for i in prefix_balance.issues if i.kind == "unclosed_open"), None)
    end = _offset_of(text, first_unclosed.line, first_unclosed.char) if first_unclosed else len(text)
```
（`_offset_of` 把 `(line, char)` 换算为字符串 offset；`BalanceIssue.line/char` 已存在于 `math_balance.py:34-40`，无需扩展返回结构。）
**验收**：构造 `$$x=1$$ 中间正文 $$y=2`（先闭合后未闭合）夹具，trim_tail 只回退到第二个 `$$`，`$$x=1$$` 与中间正文完整保留；接 `apply_deterministic_fixes` 端到端确认 `corrected_content` 不被误截。**这是 P1 优先项**（默认开启 + 改写入库内容）。

**SUP-05（math_balance 假阳，放大 SUP-01）**——`[()\[\]{}]` 跟踪加 `(?<!\\)` lookbehind 排除转义 `\{`/`\}`；`\left(…\right.` 的 null 定界符 `\right.` 纳入出栈匹配（当前 `\right\s*[)\]}]` 漏了 `.`）。降低误报 unclosed → 减少误入 review_queue + 减少 SUP-01 触发面。

**SUP-02（structure.py 单行环境闭合漏检，latent）**——`math_block`/`env_block` 进入合并循环前的 `i += 1` 改为先检测起始行自身闭合定界符（与 `table_row` 分支一致）。当前无生产调用方，接入内容路径前修。

---

## P2 — 治理闭环（消化 PA-02/05/06/09/10/12/14, IS-12；缺失功能：分类存证/图谱消费/难度校准/review 处理）

### P2.1 · taxonomy 覆盖对齐（PA-02 🟡 → 治理连锁根因）
- 补齐 `taxonomy.json` 的 math 分支至 `allowed_branches` 全集（21 个缺失分支）。
- 新增**不变量测试** `allowed_branches ⊆ taxonomy 顶层`（本可直接拦下 PA-02，也防其他学科回归）。
- 修 PA-05：`taxonomy_path=="math"` 时不写悬挂 `item_concepts`（`primary_concept_id` 仅在 `ancestor_paths` 非空时赋值），消除门禁倒置。

### P2.2 · subject 规范化（PA-06 🟡）
`subject` 增 `english:中文` 格式校验（schema）+ `TagCanonicalizer` 纳入 subject；统一 answer_linker/relation/reconciliation 分组键的稳定性。

### P2.3 · 评价→转正 + review 处理（GN-02 🟠、PA-10 🟡）
- 生成题保存时写 `pipeline_auto` 评价（或专用生成题 rubric），使其可进入质量门禁。
- `review_queue` 增处理接口：CLI `parse review list/resolve <id>` + API `/v1/review/*`（管理 scope）+ 前端队列视图（plan-03）。定义 open→resolved/rejected 流转。

### P2.4 · 知识图谱消费 + prerequisite（PA-08 🟡、PA-09 🟡；缺失功能）
- 新增 `prerequisite` 边推断（taxonomy 依赖 + concept 先修关系），改进 `tests` 边（不止就近，用 tag 重叠）。
- 让 retriever/推荐**消费**图谱：检索时按 prerequisite 补充前置知识点、按图距离调 novelty。
- `parse validate` 外新增图谱查询 CLI/API 暴露 `items_for_concept`/`concept_neighbors`。

### P2.5 · 难度一致性/校准（PA-12 🟡；缺失功能）
- Stage2 难度写入后，post-pass 用 evaluation 的 `difficulty_overall` **回写** `questions.difficulty`（当前只存 item_evaluations）。
- 建立跨条目校准（同分支难度分布归一），fallback=5 加字段级标记 `difficulty_source`。

### P2.6 · 文档分类产品化（IS-12 🟡；缺失功能）
- genre 结果**入库**（KP/Question 增 `source_genre` 字段或独立表），提供准确率度量入口。
- 扩充英文信号词表，降低英文材料落 unknown 率。

---

## 目标态数据模型增量（版本号实施时再定，proposed）

```sql
CREATE TABLE canonical_items (...);       -- stable canonical_id + current_revision_id
CREATE TABLE item_revisions (...);        -- immutable payload/version/digest
CREATE TABLE item_occurrences (...);      -- source_manifest_id + exact locator + revision
CREATE TABLE source_manifests (...);      -- bounded volume/path/content identity
CREATE TABLE ingestion_runs (...);        -- immutable run_manifest_digest
CREATE TABLE sink_commits (...);          -- unique(run_id, sink_id)
CREATE TABLE postpass_runs (...);          -- pass/version/scope/status/output_digest
```

`knowledge_points` 与 `questions` 可在兼容期作为 typed revision projection，但卫星表必须引用一个真实父表；**不要设计一个 `item_id` 多态外键同时指向两张表**，SQLite 无法执行这种 referential integrity。若保留两类父表，则每个卫星表使用两个互斥 FK + CHECK，或更推荐统一 `canonical_items` 父表。

迁移需有 schema shape manifest 与 fresh/upgrade/malformed/mid-failure 四类测试；不能只依赖 `user_version`、`IF NOT EXISTS` 或吞掉 duplicate-column 错误来声称幂等。

## 测试与验收总表

| 阶段 | 新增回归测试 |
|---|---|
| P0-R | manifest collision/new-sink、typed config property tests、资源乘法、stable-ID reorder/insert/reject、locator round-trip、read-only no-write、schema shape、post-pass replay、日志脱敏与文档伪指令 adversarial corpus |
| P0 | answer_linker 5 场景 + block_index 空batch恢复端到端 |
| P1 | 去重 EXPLAIN 命中 + VLM 截断记账 + 文字层空率 + Review 占位保留 + resume 集合差 + delete 全级联 |
| P2 | `allowed_branches⊆taxonomy` 不变量 + 悬挂 concept + 生成题门禁可见 + review 流转 + prerequisite 边 + 难度回写 |

**每阶段完成的"完成"定义**（沿用 PROGRESS §7 精神）：不仅 pytest 绿，还需在真实/构造语料上跑 `parse run` + `parse validate`，确认 marker 有去向、无静默丢失、门禁行为符合预期。
