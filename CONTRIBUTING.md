# Git Commit Convention

本项目采用 [Conventional Commits](https://www.conventionalcommits.org/) 规范。

## Commit Message 格式

```
<type>(<scope>): <subject>

[body]

[footer]
```

### Type (必填)

| Type       | 说明                       |
| ---------- | -------------------------- |
| `feat`     | 新功能                     |
| `fix`      | Bug 修复                   |
| `docs`     | 文档变更                   |
| `style`    | 代码格式（不影响逻辑）     |
| `refactor` | 重构（非 feat/fix）        |
| `test`     | 测试相关                   |
| `chore`    | 构建/工具/依赖变更         |
| `perf`     | 性能优化                   |
| `ci`       | CI/CD 配置变更             |

### Scope (可选)

模块范围，如 `pipeline`, `core`, `cli`, `storage`, `llm`, `api`, `docs`。

### Subject (必填)

- 使用祈使句，英文小写开头，不加句号
- 简明扼要，不超过 72 字符

### 示例

```
feat(pipeline): add PDF extraction with pymupdf4llm
fix(core): handle empty LaTeX blocks in parser
docs: update development setup instructions
test(storage): add integration tests for question repository
chore: upgrade pydantic to 2.10
refactor(llm): unify provider interface with abstract base class
```

## 分支策略

| 分支              | 用途               |
| ----------------- | ------------------ |
| `main`            | 稳定版本，保护分支 |
| `dev`             | 开发集成分支       |
| `feat/<name>`     | 功能开发           |
| `fix/<name>`      | Bug 修复           |
| `docs/<name>`     | 文档更新           |
| `refactor/<name>` | 重构               |

所有功能分支从 `dev` 切出，完成后通过 PR 合并回 `dev`。
`dev` 稳定后合并到 `main`。
