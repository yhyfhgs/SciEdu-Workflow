# SciForge 学科库

本目录按学科集中管理 Pipeline/Agent 的学科特化参数。

## 目录结构

```
disciplines/
├── __init__.py        # 导出 DISCIPLINE_REGISTRY / DisciplineSpec / DisciplineRegistry
├── registry.py        # 核心数据类 + 注册表
├── specs/
│   ├── math.py        # MATH_SPEC
│   └── physics.py     # PHYSICS_SPEC
└── README.md          # 本文档
```

## 新增学科步骤

1. **确认顶层学科枚举** — 在 `sciforge/core/models.py` 的 `Discipline` 中新增（例如 `CHEMISTRY = "chemistry"`）
2. **新建 spec 文件** — 在 `specs/chemistry.py` 定义 `CHEMISTRY_SPEC: DisciplineSpec`，填写：
   - `allowed_kp_types` / `allowed_question_types`：该学科允许的类型字符串白名单
   - `tag_root`：tag 树根段（如 `"chemistry:化学"`）
   - `prompt_vars`：参数化 prompt 的占位符 → 值映射
3. **注册到 registry** — 在 `registry.py` 的 `DisciplineRegistry.default()` 中添加：
   ```python
   from sciforge.disciplines.specs.chemistry import CHEMISTRY_SPEC
   ...
   Discipline.CHEMISTRY: CHEMISTRY_SPEC,
   ```
4. **补齐 prompt 变量** — 参考 `math.py` / `physics.py` 的 `prompt_vars` 字段清单，所有键都要提供
5. **运行测试** — `uv run pytest tests/test_disciplines/`

## 关键约束

- **`prompt_vars` 的 value 里禁止单独的 `{` 或 `}`**。如需字面花括号（例如 LaTeX `\begin{matrix}` 或 `FIGURE:...`），写成 `{{` `}}` 双括号预转义。第一阶段 `str.replace` 不会触发 format 转义，第二阶段 `.format(current_block=..., tag_subtree=...)` 才会消费双括号。
- **`allowed_kp_types` / `allowed_question_types` 是白名单**，Task1Extractor 会用 `spec.kp_type_or(value, fallback)` 校验 LLM 输出的类型字符串，不在白名单时回退到 `fallback`
- **`Discipline` 枚举是稳定 API**，随意新增/删除会影响数据库里 `discipline` 列的解析。只增不减，废弃用 `OTHER` 兜底
