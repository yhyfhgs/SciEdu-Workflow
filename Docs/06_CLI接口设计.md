# CLI 接口设计

> 版本: v2.0 | 日期: 2026-03-31

## 1. 框架

Typer (Click 兼容，基于类型注解) + Rich 终端输出。入口: `mathforge` 命令（`pyproject.toml` 配置）。

> CLI 支持通过 `--subject` / `-s` 和 `--level` 全局参数指定学科和教育阶段。MVP 默认 `--subject 数学分析 --level undergraduate`。

---

## 2. 命令结构

```
mathforge
├── parse                    # 数据导入
│   ├── tex                  # TeX 真题文件
│   ├── md                   # MD 讲义/教材
│   ├── pdf                  # PDF 文件
│   ├── docx                 # Word 文件
│   ├── pptx                 # PowerPoint 文件
│   ├── csv                  # CSV 题目文件
│   └── auto                 # 自动检测格式（通用 Pipeline，自动识别学科）
│
├── kb                       # 知识库管理
│   ├── list                 # 列出知识点（--subject, --level 过滤）
│   ├── search               # 搜索（支持跨学科语义搜索）
│   ├── show <id>            # 详情（含关联知识点和题目）
│   ├── tree                 # 知识树结构浏览
│   └── repair               # OCR 修复
│
├── questions                # 题库管理
│   ├── list                 # 列出（--subject, --level, --type 过滤）
│   ├── show <id>            # 详情
│   ├── search               # 搜索
│   └── stats                # 统计（按学科/学段/难度分布）
│
├── generate                 # AI 生成
│   ├── free                 # 自由模式（指定学科+学段+专题）
│   ├── variant              # 变式模式
│   ├── batch                # 批量生成
│   ├── solve <id>           # 生成解答
│   └── handout              # 生成讲义（Post-MVP）
│
├── subjects                 # 学科管理
│   ├── list                 # 列出已注册学科
│   └── stats                # 各学科数据统计
│
├── config                   # 配置管理
│   ├── show                 # 显示配置
│   ├── set                  # 设置配置项
│   └── test-llm             # 测试 LLM 连接
│
└── version                  # 版本
```

---

## 3. 关键命令参数

### 3.1 parse 命令组

| 命令 | 关键参数 | 说明 |
|------|---------|------|
| `parse tex <path>` | `-o`, `--auto-tag`, `--dry-run` | 解析 TeX 真题 |
| `parse md <path>` | `-t` (lecture/textbook), `--repair/--no-repair`, `-o` | 解析 MD |
| `parse pdf <path>` | `--batch-size`, `--use-multimodal`, `-o` | 解析 PDF |
| `parse docx <path>` | `-o` | 解析 DOCX |
| `parse pptx <path>` | `--use-multimodal`, `-o` | 解析 PPTX |
| `parse csv <path>` | `--format` (flat/latex/structured), `-o` | 解析 CSV |
| `parse auto <path>` | `--use-multimodal`, `-o` | 自动检测格式 |

### 3.2 generate 命令组

| 命令 | 关键参数 |
|------|---------|
| `generate free` | `-s` (科目), `--level` (学段), `-t` (专题), `-d` (难度), `--type`, `-n` (数量), `--format` (rich/json/latex) |
| `generate variant <id>` | `--strategy` (A1-A6/B1-B2), `-d` |
| `generate batch` | `-s`, `--level`, `-t`, `-d`, `-n`, `--concurrent` |
| `generate solve <id>` | `--format` |
| `generate handout` | `-s`, `--level`, `-t`, `--depth`, `--examples` (Post-MVP) |

所有 generate 命令支持 LLM 覆写: `--llm-provider`, `--llm-model`, `--llm-base-url`, `--llm-temperature`。

---

## 4. 输出格式

| 格式 | 说明 | 用途 |
|------|------|------|
| `rich` | 终端美化输出（默认） | 人工查看 |
| `json` | JSON 结构化 | 程序处理 |
| `latex` | 纯 LaTeX | 直接编译 |

---

## 5. 使用示例

```bash
# === MVP: 大学数学 ===

# 解析全部 TeX（自动识别为大学数学）
mathforge parse tex "Raw Data/新领军真题整理/"

# 自动检测格式并解析
mathforge parse auto "Raw Data/抽代教材PPT习题/" --use-multimodal

# 自由生成（大学数学，默认学段）
mathforge generate free -s 数学分析 -t 数列极限 -d 5 --type proof

# 变式生成
mathforge generate variant xlj-lingshi-2021-03-001 --strategy A4 -d 7

# 批量生成
mathforge generate batch -s 高等代数 -t 特征值 -d 6 -n 5

# 使用自定义 LLM
mathforge generate free -s 数学分析 -t 极限 -d 5 \
  --llm-provider openai_compatible --llm-base-url https://relay.example.com/v1

# === Post-MVP: 多学科 ===

# 解析物理教材
mathforge parse auto "教材/大学物理.pdf" --use-multimodal

# 生成高中数学题目
mathforge generate free -s 函数 --level high_school -d 4 --type choice -n 5

# 生成大学物理题目
mathforge generate free -s 力学 --level undergraduate -t 牛顿定律 -d 5

# 查看各学科数据统计
mathforge subjects stats

# 按学科浏览知识库
mathforge kb list --subject 力学 --level undergraduate
```

---

## 6. 实现文件

| 文件 | 职责 |
|------|------|
| `cli/main.py` | Typer 入口（含全局 --subject, --level 参数） |
| `cli/parse.py` | parse 命令组 |
| `cli/kb.py` | 知识库命令 |
| `cli/questions.py` | 题库命令 |
| `cli/generate.py` | 生成命令 |
| `cli/subjects.py` | 学科管理命令 |
| `cli/config.py` | 配置命令 |
| `cli/display.py` | Rich 输出格式化 |
