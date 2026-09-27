# SciForge 开发入口

本文件维护 AI 协作的入口、常用命令与关键约束；详细设计留在对应文档。

## 团队工作流与分发

- 先读本仓库 AGENTS；涉及团队协作时，从 `../team-hub/AI维护/README.md` 按任务选读参考，不全量加载或复制整套流程。独立克隆可读[已发布参考列表](https://github.com/ActionMesh-AI/team-hub/blob/main/AI维护/README.md)，缺失项如实报告。
- team-hub 成员任务只同步和上传，不操作 PR；fhgs 管理员统一处理 PR 与每日收录。配置及权限边界见[每日同步](https://github.com/ActionMesh-AI/team-hub/blob/main/AI维护/每日同步.md)。本仓库产品要求、开发线和子模块保持原样。


## 任务范围

- `team-hub`、`WeAnalysis`、`homework-grading`、`Shopping-Router` 为默认初始化和自动同步仓库，以 `../team-hub/AI维护/工作区.json` 为准。本仓库仍按需下载，不要求指定主要负责人；默认仓库负责人记录见 `../team-hub/开发/项目索引.md`，定时同步见 `../team-hub/AI维护/每日同步.md`。
- SciForge 是 Python 3.11+ 的理工科内容处理与生成平台，入口为 CLI 与 FastAPI/Chatbox；先读 `README.md` 和所改模块。
- 属于 ActionMesh AI。跨仓库/初始化读 `../AGENTS.md`；Git/PR/发布读 `../team-hub/AI维护/协作规范.md`。独立克隆可从 [team-hub](https://github.com/ActionMesh-AI/team-hub) 获取已发布规范，按本次任务读取相关章节。
- 保留当前开发线与已有修改。本仓库 `CONTRIBUTING.md` 的 `dev` 流程与组织默认 `main + 短分支` 存在已记录差异；分支迁移作为独立任务处理，当前 Git 操作按本次授权执行。
- `Raw Data/` 原始材料保持只读；`Raw Data/`、`data/`、`.env` 与 API Key 留在本机私有存储。保留用户已有修改与云同步冲突双方内容，清理以内容与来源核验为依据。

## 常用命令

依赖配置以 `pyproject.toml` 为准，使用 uv。仅在任务需要且已授权时安装依赖或启动服务。

```bash
uv sync --extra all
uv run sciforge --help
uv run pytest
uv run pytest tests/test_pipeline -k stage1
uv run ruff check src/ tests/
uv run mypy
uv run sciforge parse run path/to/file --db data/sciforge.db
uv run sciforge parse validate data/sciforge.db
uv run sciforge serve --port 8000
```

- 按任务选择所需 extras；`pdf-ml` 与 `pdf-mineru` 互斥，聚合安装使用 `--extra all`，禁止 `--all-extras`。格式修改限定本次文件，使用 `uv run ruff format <files>`。
- LLM 配置样例是 `config/sciforge.example.toml`；基于现有 `config/sciforge.toml` 定点修改并保留用户设置。配置支持 `[providers.*]`、`[tasks.*]`、`${ENV_VAR}` 与 `SCIFORGE_*` 覆盖，加载规则见 `src/sciforge/config/settings.py`。
- `serve` 默认鉴权/计费开启；`--open` 只用于本地调试。完整 parse/generate/durable/resume/cancel 用法见 `README.md` 与 CLI 帮助。
- 按改动运行受影响检查。纯文档验证限定链接与 diff；代码提交前执行适用的 ruff/mypy 检查，缺陷验证覆盖实际触发条件并优先复用已有测试。

## 模块与按需阅读

| 修改范围 | 实现入口 | 先读文档 |
|---|---|---|
| 摄取、条目边界、恢复、质量门禁 | `src/sciforge/pipeline/`、`storage/` | `Docs/21_知识库构建标准与数据契约.md`、`Docs/05_数据模型与存储.md` |
| 生成、求解、验证、取消/恢复 | `src/sciforge/agents/qgen/`、`agents/runtime/` | `Docs/22_QGen_Harness_架构与验证协议.md`、`Docs/16_Agent系统MVP.md` |
| 学科或 prompt | `src/sciforge/disciplines/`、`prompts/`、`llm/` | `Docs/15_学科扩展指南.md` |
| SaaS、鉴权、租户、会话交付 | `src/sciforge/api/`、`storage/saas.py` | 上述数据/验证契约；前端计划另见 `Docs/17_前端产品化实施计划.md` |

## 工程契约

- 使用 async/await；LLM 经过 `llm/client.py`，存储遵循 `StorageBackend` Protocol，日志使用 structlog。共享领域模型集中在 `core/models.py`；Agent 私有 Input/Output 可留在模块内。保持 Pydantic v2、ruff 与 mypy 项目约定。
- Prompt 放在 `src/sciforge/prompts/templates/` 的 `.txt` 文件，按 `DisciplineSpec` 参数化；新增学科先读扩展指南。保持两阶段 discipline/runtime 注入的花括号转义规则。
- Pipeline 通过有界 queue、共享 `core/sentinel.py:SENTINEL` 和显式上下文传递协作。并发/恢复/局部修补须保持来源顺序与稳定身份；来源有疑问时保留内容并进入 review；生成内容明确记录为生成产物。
- `question_type` 与 `question_form` 分离；tags 保持根到叶的有序 `english:中文` 段列表，规范身份由独立的 concept/taxonomy 字段承载。
- Schema 版本通过 `storage/sqlite_backend.py` 的迁移表逐步推进；成功恢复须有目标库提交证据。历史审计按当前代码复核。
- 保持四库职责：内容 `sciforge.db`、会话/工件 `conversations.db`、执行/checkpoint/event `qgen_runtime.db`、租户/Key/用量 `saas.db`。执行状态直接读取权威 runtime store。
- 认证 API 统一使用 `TenantScopedStorageView`：仅本租户或 public 题目可见，在排序/limit 前过滤；unscoped backend 限于离线处理。生成交付使用 `save_question` 返回的 canonical ID，入库成功后发 `question.persisted`；runtime 原始输出与已交付历史工件保持不可变。
- 验证证据区分 `passed / failed / unavailable / error`；required 检查仅在 `passed` 时通过。生成/用户代码默认 `container_required`，缺容器时返回明确的不可用证据，禁止降级宿主执行。`trusted_local` 限于仓库内经人工明确审核的可信代码。Lean 编译与题目/解答翻译忠实性分别验证。
- 产品输出结构化解题步骤、方法、证据与修正记录。私有原始 chain-of-thought 禁止写入字段、日志、checkpoint 或 API 响应。

## 状态与文档维护

- 任务状态由组织协作规范指定的记录、当前代码与验证证据共同核对；入口负责导航。
- `audit/REPORT-INDEX.md`、`audit/RE-AUDIT-REGISTER.md` 与 `plan/PLAN-INDEX.md` 保存历史审计与计划。引用其中的 2026-07 基线、缺陷和计划时标注日期，当前实施状态通过相应源码与验证重新确认。
- `Docs/archive/AGENTS-2026-09-05.txt` 完整保存原 520 行入口，仅在追溯历史细节时读取；当前执行以本文件与对应权威文档为准。
- 行为或契约变化时更新对应权威文档；只有入口命令或上述长期约束变化才更新本文件。提交格式与流程见 `CONTRIBUTING.md` 和组织规范。
