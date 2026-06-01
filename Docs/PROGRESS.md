# SciForge 开发进度总览

> 更新日期: 2026-06-02 CST
> 当前阶段: 本地 CLI MVP。主线是 **5 阶段并发 Pipeline** + **多 Agent 生成系统** + **学科参数化**。

---

## 0. 2026-06 提取质量重构（9 项诉求，已完成 #1–#9 核心）

围绕用户 9 项诉求做了系统性重构，schema 升至 **v8**：

- **#2 不漏 / #8 不重不分离**：结构感知 fast-skip（强标记+弱信号双层）杜绝静默丢条目；
  `content_hash` UNIQUE 写入去重；`numbering_audit` 编号连续性兜底。
- **#3 题答精准绑定**：`solution_status` 三态 + `answer_linker` 跨章节回填（习题在正文、答案在附录）。
- **#4 字段规范化**：`number_canonical` + `seq_*` 自动派生（消除 "1" vs "第1题"）；DB CHECK 约束。
- **#5 类型双维度**：`question_form`（选择/填空/判断/解答）与 `question_type`（认知类别，含
  反例/构造性证明/存在性证明…）正交；`SingleItemExtractLLM.question_form` 由 Stage1 识别。
- **#6 tag/知识图谱**：`tag_canonicalizer` 受控词表统一（别名表 + 双向一致性）；
  `relation_inference` 确定性推断 `corollary_of/tests/successor` 边写 `item_relations`。
- **#7 自动纠错**：`reconciliation` 复核重复条目 + 类型误判（报告式，不改库）。
- **#9 token 效率**：prompt caching（静态指令拆可缓存 system 前缀）；推理模型 `thinking` 预算分级
  （MiniMax-M3 实测单窗口 127s→3.7s）；经 `extra_body` 透传兼容老 SDK / anthropic 兼容网关。
- **#1 单文档并行（opt-in）**：`document_segmenter`（覆盖不变量断言）+ `parallel_extract`
  段级并发 + `assign_order_index` 段序保序重排。
- **鲁棒性**：VLM 内容审核误判（MiniMax 1026）自动回退逐页识别；`parse validate` 只读体检命令。
- **测试**：573 全量通过，ruff + mypy 干净（82 源文件）。

> 注：真实教材端到端跑通受 provider 限制（MiniMax-M3 推理慢、需配 `thinking` budget；
> 已验证组件级 + 直连提取产出正确）。生产建议提取/标注用快模型（Kimi K2 / 4o-mini），
> MiniMax-M3 留给 generate/solve。

---

## 1. 总体结论

| 模块 | 状态 | 当前进度 |
|------|------|----------|
| 项目脚手架 | ✅ 完成 | `uv` + Python 3.11+ + Pydantic v2 + Typer/Rich + structlog |
| SQLite 存储 | ✅ 完成 | schema **v8**：`knowledge_points`/`questions`（+ number_canonical/seq_*/question_form/content_hash UNIQUE）+ FTS5 + `discipline_tag_trees` + `item_relations` 边表 |
| Pipeline 主线 | ✅ 完成 | Task0 + 5 Stage + 4 queue；Stage1 已升级为**批量窗口提取**，Stage2 支持可选并发 |
| LLM 配置 | ✅ 完成 | v2 TOML schema：`[providers.*]` + `[tasks.*]`，支持 provider_ref、env 插值、per-model 并发限流 |
| 多学科参数化 | ✅ 完成 | 顶层 `Discipline` + `DisciplineSpec`；数学/物理 spec 已落地 |
| Agent 系统 | ✅ 完成 MVP | QGen / Solve / Tag / DocAssembly-lite 完整；KB/Recommend/Format/OCR/ContentTagger 为 Post-MVP 骨架 |
| CLI | ✅ 完成 MVP | `parse run/run-book/audit/audit-tags/migrate-legacy/db-stats`，`generate free/solve/batch/lecture`，`config show/validate` |
| 数据库内容 | ⚠️ 可用但需重跑 | 当前 DB 有 3418 条数学记录，但全部 66 个 source_file 被质量审计标为需重跑，主因是旧导入遗留的 `order_index` 重复 |
| FastAPI / Web / 用户系统 | 🔲 未开始 | Docs 08/10/11/12/13 仍是蓝图 |

---

## 2. 当前代码规模

| 区域 | 当前规模 |
|------|----------|
| `src/sciforge/**/*.py` | 14,592 行 |
| `tests/**/*.py` | 7,268 行 |
| Prompt `.txt` 模板 | 28 个 |
| 顶层文档 `Docs/*.md` | 19 个，约 4,610 行 |

---

## 3. Pipeline 当前事实

当前 Pipeline 是 **Task0 Producer + Stage1Extract + Stage1Review + Stage2Annotate + Stage2Review + Stage3Persist**：

- **Task0**：`VlmRecognizer` / `PyMuPdfRecognizer` / `MarkdownRecognizer` 按 batch 生成 markdown checkpoint，支持 PDF 1-based 页号、Task0 cache replay、失败 batch 记录。
- **Stage1Extract**：已从“每次一条”升级为**每窗口一次 LLM 调用批量提取所有完整条目**，输出 `BatchItemExtractPayloadLLM` 和 `ExtractedBatch`，用 `OrderIndexAllocator` 动态预留连续 `item_id`。
- **Stage1Review**：批量 review，一次 LLM 校验同窗口 N 条 item，输出 `BatchReviewPayloadLLM`，低风险 batch 可条件化跳过。
- **Stage2Annotate / Stage2Review**：单 item 标注与审核，支持可选 N-worker 并发；并发时用 `ReorderBuffer` 恢复下游顺序。
- **Stage3Persist**：无 LLM，校正 taxonomy、补齐学科根 tag、集中累积 `TagTree`，写入 SQLite。Stage3 允许上游拒绝 item 造成 gap，但禁止 item_id 倒退。
- **可靠性**：retry with feedback、429 长睡预算、checkpoint 节流保存、stage mirror replay、目录级 progress、per-model semaphore 都已实现。

当前主线文件：

| 文件 | 状态 |
|------|------|
| `pipeline/orchestrator.py` | ✅ 6 Task 编排、目录并发、cache replay、stage mirror replay |
| `pipeline/stages/stage1_extract.py` | ✅ 批量窗口提取、regex fast-skip、force-consume、全局 ID 分配 |
| `pipeline/stages/stage1_review.py` | ✅ 批量复核、条件化跳过、fallback 直通 |
| `pipeline/stages/stage2_annotate.py` | ✅ 单条标注、局部 taxonomy 注入、可并发 |
| `pipeline/stages/stage2_review.py` | ✅ annotation 审核、条件化跳过、可并发 |
| `pipeline/stages/stage3_persist.py` | ✅ KP/Question 合并入库、tag root/taxonomy root 兜底 |
| `pipeline/llm_schemas.py` | ✅ `BatchItemExtractPayloadLLM` / `BatchReviewPayloadLLM` / `SingleAnnotationLLM` |
| `pipeline/retry.py` | ✅ schema retry with feedback + 429 长睡预算 |
| `pipeline/reorder_buffer.py` | ✅ Stage2 并发保序 |
| `pipeline/taxonomy_validator.py` | ✅ taxonomy 精确/前缀/模糊校验 |

---

## 4. Agent 当前事实

| Agent | 状态 | 说明 |
|------|------|------|
| QGenAgent | ✅ 完整 | retriever → generator → solver → verifier → difficulty；新增轻量预筛和 verifier 合并 difficulty 可选路径 |
| SolveAgent | ✅ 完整 | 复用 `qgen/solver.py`，用于 `generate solve` |
| TagAgent | ✅ 完整 | 复用 `qgen/annotator.py` |
| DocAssemblyAgent | ✅ Lite | 生成 markdown 讲义，不做 LaTeX/PDF 编译 |
| KB / Recommend / FormatAnalyzer / ContentExtractor / OcrCorrector / ContentTagger | 🔶 骨架 | 在 `stub_agents.py`，Post-MVP |

QGen 的实际优化状态：

- `qgen/prefilter.py` 已实现内容长度、括号/美元符号平衡、negative examples 相似度预筛。
- `VerificationPipeline` 可在 PASS 时读取 `difficulty_scores`，`QGenAgent(merge_difficulty_into_verify=True)` 可跳过独立 annotator 调用。
- `generate variant/evolve/solve-deep` 仍是显式 Post-MVP 占位。

---

## 5. 当前数据库状态

数据库文件：`data/sciforge.db`（运行时数据，不纳入 git）。

本次检查结果：

| 项目 | 数值 |
|------|------|
| `knowledge_points` | 1262 |
| `questions` | 2156 |
| 总 item | 3418 |
| `discipline_tag_trees` | 1 |
| 学科分布 | 全部为 `math` |
| checkpoint `state.json` | 41 个 |
| checkpoint failed blocks/batches | 0 |

主要类型分布：

- KnowledgePoint：`theorem=519`，`definition=317`，`corollary=125`，`property=97`，`proposition=79`，`lemma=72`。
- Question：`proof=1384`，`calculation=626`，`fill_blank=94`，`construction=45`，`true_false=7`。

质量审计结论：

- `scripts/audit_quality.py` 判定 **66/66 个 source_file 都需要重跑**。
- 主因：现有 DB 是旧导入结果，`order_index` 只有 1069 个 distinct 值，却有 3418 条记录；跨文件重复槽位 951 个。
- 还发现 38 个重复 `content_hash`；部分数学分析分卷存在 fallback tag 或结构化错误比例超阈值。
- 新代码已通过 `OrderIndexAllocator` 修复跨文件并发分配问题，但当前 DB 仍需用新 pipeline 清理/重跑后才可作为高质量知识库使用。

---

## 6. 验证记录

本次已执行：

```bash
scripts/fix-pth.sh .venv
uv run sciforge --help
PYTHONPATH=src uv run sciforge parse db-stats data/sciforge.db
PYTHONPATH=src python scripts/audit_checkpoints_for_resume.py
PYTHONPATH=src python scripts/audit_quality.py
sqlite3 data/sciforge.db "<schema/count/distribution/order_index queries>"
```

当前环境注意点：

- 该机器的 `.venv` 可能出现 macOS 隐藏 `.pth` 导致 `uv run sciforge` 找不到 `sciforge` 包；`scripts/fix-pth.sh .venv` 已能修复。
- `data/`、`Raw Data/`、`.env`、`.venv` 均为运行时/本地数据，不提交。

---

## 7. 下一步建议

| 优先级 | 事项 | 说明 |
|--------|------|------|
| P0 | 用新 pipeline 重跑当前 DB | 先备份/清理 `data/sciforge.db` 与 `data/checkpoint`，再 `SCIFORGE_RESTART=1 ./run_pipeline.sh` 或手动 `parse run --restart` |
| P0 | 重跑后再执行质量审计 | 目标是 `duplicate_order_slots=0`，fallback/结构错误低于阈值 |
| P1 | 真实端到端 smoke | 选 1 个 MD、1 个 PDF，用小并发验证 batch extract/review/persist 全链路 |
| P1 | mypy 严格收敛 | 当前代码量已大，建议按 `pipeline/`、`llm/`、`agents/` 分批修 |
| P2 | Post-MVP 服务层 | FastAPI/Web/用户系统仍按蓝图推进 |

---

## 8. 架构决策

- **schema v7**：保留 `source_file`，删除 `source_chapter` / `exam_type` / `exam_year`，proof/solution 嵌入主表。
- **Stage1 批量化**：实际主线是 `BatchItemExtractPayloadLLM` + `ExtractedBatch`；`ExtractedSingleItem` / `SingleItemExtractPayloadLLM` 仅保留兼容说明。
- **Pipeline 与 Agent 分离**：Pipeline 用 queue/checkpoint/背压处理批量文档；Agent 用 `BaseAgent.run()` 处理一次生成/求解/讲义请求。
- **Tag 一致性**：Stage3 集中更新 `TagTree`，Stage2 prompt 注入当前 tag subtree，Stage3 兜底补齐学科根。
