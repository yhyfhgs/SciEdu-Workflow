# FastAPI 服务层

> 版本: v3.0 | 日期: 2026-03-31 | 阶段: Phase 5 (Post-MVP)
>
> ⚠️ **未实现**：MVP 只有 CLI。本文档是 Post-MVP 蓝图，供后续实现参考。

## 1. 定位

将 MVP 的 CLI 能力对外暴露为 REST API，供 Web 前端和第三方集成使用。API 支持多学科、多学段数据的 CRUD 和 AI 生成能力。

---

## 2. 总体架构

```
Client (浏览器 / 移动端 / 第三方)
    │
    ▼  REST / WebSocket
┌─────────────────────────────────────────────┐
│  FastAPI Application (uvicorn × N workers)  │
│                                             │
│  路由: Auth | Question | Knowledge          │
│        Generate | Document | Recommend      │
│        Subject                              │
│                                             │
│  中间件: CORS, Rate Limit, Logging, Auth    │
└──────────────────┬──────────────────────────┘
                   │
      ┌────────────┼────────────┐
      ▼            ▼            ▼
  SQLite +     asyncio       TeX Live
  sqlite-vec   task queue    (PDF 编译)
```

---

## 3. API 路由设计

### 3.1 认证 (`/api/v1/auth`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/register` | 用户注册 |
| POST | `/login` | 登录，返回 JWT |
| POST | `/refresh` | 刷新 token |
| GET | `/me` | 当前用户信息 |

### 3.2 题库 (`/api/v1/questions`)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 列表（分页 + 过滤） |
| GET | `/{id}` | 详情 |
| POST | `/` | 创建（手动录入） |
| GET | `/{id}/solutions` | 获取解答 |
| POST | `/import` | 批量导入（TeX/JSON） |
| GET | `/search` | 全文 + 语义搜索 |

查询参数: `subject_domain`, `subject`, `education_level`, `topic`, `difficulty_min/max`, `type`, `is_generated`, `page`, `per_page`, `sort`

### 3.3 知识库 (`/api/v1/knowledge`)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 列表 |
| GET | `/{id}` | 详情（含关联题目） |
| POST | `/import` | 导入（PDF 上传 → 异步处理） |
| POST | `/import/universal` | **通用 Pipeline 导入**（任意格式） |
| GET | `/import/{task_id}` | 查询导入进度 |
| GET | `/search` | 语义搜索 |

### 3.4 生成 (`/api/v1/generate`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/free` | 自由模式生成 |
| POST | `/variant` | 变式生成 |
| POST | `/batch` | 批量生成（异步） |
| GET | `/batch/{task_id}` | 查询进度 |
| POST | `/solve` | 为指定题目生成解答 |

### 3.5 文档 (`/api/v1/documents`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/exam` | 生成试卷（异步） |
| POST | `/handout` | 生成讲义（异步） |
| GET | `/{task_id}/download` | 下载 PDF |

### 3.6 推荐 (`/api/v1/recommend`)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 获取推荐题目 |
| POST | `/record` | 提交做题记录 |
| GET | `/profile` | 能力画像 |

---

## 4. 异步任务

使用 FastAPI `BackgroundTasks` + asyncio 管理耗时任务（替代 Celery + Redis）:

- 批量生成、文档编译等提交为后台任务
- 任务状态存储在 SQLite `tasks` 表中
- WebSocket 推送生成进度

---

## 5. 数据库连接

使用 SQLite（`sqlite3` 标准库或 `aiosqlite` 异步版），数据库文件位于 `data/sciforge.db`。无需外部数据库服务。

---

## 6. Pydantic Schema

API 请求/响应模型使用 Pydantic BaseModel 定义，与核心 models 共享或继承。FastAPI 自动从 Pydantic 模型生成 OpenAPI 文档。

关键 Schema:

| 模型 | 用途 |
|------|------|
| `QuestionListResponse` | 题目列表响应（含分页） |
| `QuestionBrief` | 题目摘要（列表项） |
| `GenerateRequest` | 生成请求（含可选 LLM 配置覆写） |
| `LLMConfigOverride` | LLM 参数覆写 |

---

### 3.7 学科管理 (`/api/v1/subjects`)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 列出所有已注册学科（含层级关系） |
| GET | `/{domain}/stats` | 学科数据统计（知识点数、题目数、覆盖率） |
| GET | `/{domain}/tags` | 获取学科专属标签体系 |

---

## 7. 实现文件

| 文件 | 职责 |
|------|------|
| `backend/main.py` | FastAPI 入口 |
| `backend/api/auth.py` | 认证路由 |
| `backend/api/questions.py` | 题库 CRUD（支持多学科过滤） |
| `backend/api/knowledge.py` | 知识库接口（支持多学科过滤） |
| `backend/api/generate.py` | 生成接口（支持指定学科+学段） |
| `backend/api/documents.py` | 文档接口 |
| `backend/api/subjects.py` | 学科管理接口 |
| `backend/api/schemas.py` | Pydantic 请求/响应模型 |
| `backend/core/database.py` | SQLite 连接管理 |
