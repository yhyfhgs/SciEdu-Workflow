# MathForge

AI 驱动的大学数学智能教育平台。从教材、讲义、试卷中自动提取结构化知识，并基于知识库生成题目与详细解答。

## 核心能力

- **数据处理 Pipeline** — 支持 TeX / MD / PDF / DOCX 等格式，LLM + Python 混合架构自动提取结构化题库与知识库
- **生成引擎** — 5 阶段闭环：上下文检索 → 题目生成 → 解答求解 → 验证 → 难度标注
- **CLI 接口** — 全部操作通过命令行完成，Rich 终端输出

## 快速开始

### 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 包管理器

### 安装

```bash
# 克隆仓库
git clone https://github.com/ZyphingFHGS/MathForge.git
cd MathForge

# 安装依赖
uv sync --all-extras

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 API Key
```

### 使用

```bash
# 解析 TeX 文件
mathforge parse tex path/to/file.tex

# 生成题目
mathforge generate --topic "极限" --difficulty 3

# 查看帮助
mathforge --help
```

## 项目结构

```
src/mathforge/
├── core/        # 核心领域逻辑（模型、生成引擎、验证）
├── llm/         # LLM 客户端层（Anthropic / OpenAI / Gemini）
├── pipeline/    # 数据处理流水线（TeX / MD / PDF / DOCX）
├── prompts/     # 提示词模板与生成策略
├── storage/     # 存储后端（JSON 文件 / SQLite）
├── cli/         # Typer CLI 接口
└── config/      # 应用配置与日志
```

## 技术栈

Python 3.11+ | Pydantic v2 | Typer + Rich | structlog | uv

## 开发

```bash
# 运行测试
uv run pytest

# 代码检查
uv run ruff check src/

# 类型检查
uv run mypy
```

提交规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

Private — All Rights Reserved.
