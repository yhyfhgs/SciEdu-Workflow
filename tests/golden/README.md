# Golden Benchmark 标注规范（v11 §I.1）

本目录存放 golden benchmark 的 **pinned 文档 fixture** 与 **ground-truth 标注**，
为 Pipeline v11 各里程碑提供"开关前后"的量化对照（指标定义见
`src/sciforge/bench/metrics.py`，方案见 `Docs/20 §I`）。

## 目录结构

```
tests/golden/
├── README.md            # 本文件
├── docs/                # pinned 的 Task0 markdown 输出（fixture，提交进 git）
│   └── <doc_id>.md
└── gt/                  # 人工标注 ground truth
    └── <doc_id>.json    # GoldenDoc（schema 见 bench/gt_models.py）
```

## 关键约定

1. **gt 锚定 pinned markdown，不锚定原始 PDF/TeX**（实施计划裁定 B）。
   对每份 golden 文档跑一次 Task0 识别，把 checkpoint 里的
   `page_NNN-NNN.md` 拼接为单文件 `docs/<doc_id>.md` 提交。此后
   recognizer/VLM 升级不影响标注有效性；bench 从 Stage1 起跑（0 VLM 成本）。
2. **行号 1-based**，与 Stage1 `_annotate` 的 7 位行号前缀同语义；
   行内偏移 0-based codepoint，`end_char` 为 exclusive，`null` = 行尾。
3. **markers 标"所有条目起始行"**：定义/定理/例/习题…的标题行，一行一条。
   这是漏检率（marker_recall）的分母，务求穷尽。
4. **items 标"条目完整边界 + 题答配对"**：`solution_status` 与
   `SingleItemExtractLLM` 三态同语义；证明/解答属于条目本体（同一区间）。
5. **crosses_window**：默认窗口策略（500 行起步）下会被窗口边界切断的条目
   标 `true`，供跨窗合并率（cross_window_merge_rate）指标。
6. gt 一经提交**不可随意改动**——它是跨里程碑对照的基准；发现标注错误须在
   PR 中说明并同步重跑受影响的 baseline 报告。

## 首批标注（WP-02）

| doc_id | 来源 | 体裁 | 测试重点 |
|---|---|---|---|
| `exam-2024yishi` | Raw Data/新领军真题整理/2024一试.tex | exam_paper | 密集短题防漏、fast-skip 关闭 |
| `lecture-algebra2` | Raw Data/新领军讲义md/Algebra2_线性方程组与矩阵.md | lecture | 窗口自适应、marker 查漏 |
| `book-mathanalysis-p181` | Raw Data/数学分析课本md/数学分析（一）_part_181_210.md | book | 高 fallback 片段、carry 四态 |

后续补标：M2 期间 + 数学分析多分卷 2–3 个 part、202510new.tex；
M3 期间 + 近世代数三百题.pdf、XZ_抽象代数I.pdf（标注对象为 pinned VLM 输出）。

## 跑分

```bash
# 对单份 golden 文档跑 pipeline 并算指标（详见 parse bench --help）
uv run sciforge parse bench --doc <doc_id> --db /tmp/bench.db
# 复用既有 checkpoint 镜像（0 LLM）
uv run sciforge parse bench --doc <doc_id> --from-checkpoint data/checkpoint/<hash>
# 与基线对比（退化 > 5% 报红）
uv run sciforge parse bench --doc <doc_id> ... --compare tests/golden/baseline/v10.json
```
