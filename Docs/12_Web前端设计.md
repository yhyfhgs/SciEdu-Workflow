# Web 前端设计

> 版本: v2.0 | 日期: 2026-03-31 | 阶段: Phase 7 (Post-MVP)

## 1. 技术栈

| 组件 | 选型 | 理由 |
|------|------|------|
| 框架 | Next.js 15 (App Router) | SSR/SSG、API Routes、成熟生态 |
| UI 库 | shadcn/ui + Tailwind CSS | 高度可定制、现代设计 |
| 数学渲染 | KaTeX (行内) + MathJax (完整) | 快速渲染 LaTeX 公式 |
| Markdown | react-markdown + rehype-katex | 数学公式嵌入的 Markdown 渲染 |
| 状态管理 | Zustand | 轻量、TypeScript 友好 |
| 图表 | Recharts / Nivo | 能力雷达图、趋势图 |
| 桌面端 | Tauri v2 | 可选，比 Electron 轻量 |

---

## 2. 页面结构

```
App
├── / (Dashboard)
│   ├── 学习概览面板（按学科展示正确率趋势、薄弱专题雷达图）
│   ├── 今日推荐（AI 推荐题目卡片，多学科混合）
│   ├── 学科快捷入口（数学/物理/CS/...）
│   └── 最近学习的学科+知识点
│
├── /subjects (学科导航)
│   ├── 学科卡片列表（数学/物理/CS/化学/...）
│   ├── 学段切换（高中/大学）
│   └── 学科详情页（概览+子领域+统计）
│
├── /knowledge (知识库)
│   ├── 学科+学段筛选器
│   ├── 书籍列表 → 知识点树状浏览
│   ├── 知识点详情（LaTeX 渲染 + 关联题目 + 跨学科关联）
│   └── 知识图谱可视化（d3-force，支持跨学科链接）
│
├── /questions (题库)
│   ├── 筛选面板（学科/学段/专题/难度/题型）
│   ├── 题目列表（卡片视图 / 列表视图）
│   ├── 题目详情（题干 + 解答折叠 + 变式历史树）
│   └── 做题模式（逐题呈现 + 计时 + 提交答案）
│
├── /generate (AI 生成)
│   ├── 学科+学段选择器
│   ├── 变式生成（选择源题 → 配置参数 → 实时预览）
│   ├── 批量生成（配置面板 → 进度条 → 结果列表）
│   ├── 讲义生成（指定专题 → AI 组织大纲 → 生成讲义）
│   └── 文档生成（模板选择 → 参数配置 → 预览 → 下载）
│
├── /documents (文档中心)
│   ├── 已生成文档列表（按学科分类）
│   ├── 模板管理
│   └── PDF 预览 / 下载
│
├── /profile (个人中心)
│   ├── 多学科学习记录 & 错题本
│   ├── 能力画像（各学科独立雷达图 + 综合概览）
│   ├── 个性化设置（关注学科、学段偏好）
│   └── 推荐历史
│
└── /admin (管理后台, teacher/admin)
    ├── 用户管理
    ├── 学科管理（新增/编辑学科、标签体系）
    ├── 知识库管理（上传/审核，按学科分类）
    ├── 题目审核
    └── 系统设置
```

---

## 3. 核心组件

### 3.1 LaTeX 渲染器

```typescript
// frontend/components/math/LatexRenderer.tsx

interface LatexRendererProps {
  content: string;        // LaTeX 或 Markdown+LaTeX 混合内容
  mode: 'inline' | 'block' | 'markdown';
}

// 使用 KaTeX 做行内快速渲染
// 使用 MathJax 做完整渲染（支持更多宏）
// react-markdown + rehype-katex 做 Markdown 模式
```

### 3.2 题目卡片

```
┌─────────────────────────────────────────────┐
│ [数学] [数学分析] [极限] [ε-δ语言]  难度: ★★★★☆  │
│                                             │
│  设 $f: \R \to \R$ 在 $x_0$ 处连续，        │
│  且 $f(x_0) > 0$。                          │
│  证明：存在 $\delta > 0$，使得对所有          │
│  $x \in (x_0 - \delta, x_0 + \delta)$，    │
│  有 $f(x) > 0$。                            │
│                                             │
│ ┌───────┐ ┌────────┐ ┌──────────────────┐  │
│ │ 查看解答│ │ 生成变式│ │ 标记: ✓正确 ✗错误│  │
│ └───────┘ └────────┘ └──────────────────┘  │
└─────────────────────────────────────────────┘
```

### 3.3 文档生成配置器

```
┌─────────────────────────────────────────────┐
│  📄 生成试卷                                 │
│                                             │
│  模板:  [标准考试卷 ▼]                        │
│  科目:  [高等代数 ▼]                          │
│  专题:  [✓线性空间] [✓线性映射] [□特征值]      │
│  难度:  [===●=======] 3-7                    │
│  题数:  [10]                                 │
│  题型分布:                                    │
│    计算 [====●] 40%                          │
│    证明 [======●] 50%                        │
│    判断 [=●] 10%                             │
│                                             │
│  ☐ 包含答案卷                                │
│  ☐ 包含评分标准                               │
│                                             │
│  [预览]  [生成 PDF]                           │
└─────────────────────────────────────────────┘
```

### 3.4 做题模式

- 逐题呈现，支持翻页或跳题
- 计时器（可选）
- 答案提交（文本输入 + 未来支持手写识别）
- 即时反馈：正确/错误 + 解答展示 + AI 错因分析
- 做完后统计面板

### 3.5 知识图谱

- d3-force 或 vis.js 可视化
- 节点 = 知识点（按 kp_type 着色）
- 边 = 前置依赖关系
- 节点大小 = 关联题目数
- 点击节点展开详情 + 关联题目

---

## 4. 前端目录结构

```
frontend/
├── package.json
├── next.config.js
├── tailwind.config.ts
│
├── app/
│   ├── layout.tsx
│   ├── page.tsx                  # Dashboard
│   ├── subjects/
│   │   ├── page.tsx              # 学科导航
│   │   └── [domain]/page.tsx     # 学科详情
│   ├── knowledge/
│   │   ├── page.tsx              # 知识库浏览（学科筛选）
│   │   └── [id]/page.tsx         # 知识点详情
│   ├── questions/
│   │   ├── page.tsx              # 题库列表（学科筛选）
│   │   ├── [id]/page.tsx         # 题目详情
│   │   └── practice/page.tsx     # 做题模式
│   ├── generate/
│   │   ├── variant/page.tsx      # 变式生成
│   │   ├── batch/page.tsx        # 批量生成
│   │   ├── handout/page.tsx      # 讲义生成
│   │   └── document/page.tsx     # 文档生成
│   ├── documents/
│   │   └── page.tsx
│   ├── profile/
│   │   ├── page.tsx              # 多学科学习记录
│   │   └── settings/page.tsx
│   └── admin/
│       ├── page.tsx
│       └── subjects/page.tsx     # 学科管理
│
├── components/
│   ├── ui/                       # shadcn/ui 组件
│   ├── math/
│   │   ├── LatexRenderer.tsx     # KaTeX/MathJax 渲染
│   │   ├── QuestionCard.tsx      # 题目卡片
│   │   └── SolutionBlock.tsx     # 解答折叠块
│   ├── generate/
│   │   ├── VariantConfig.tsx     # 变式配置
│   │   └── DocBuilder.tsx        # 文档生成器
│   └── charts/
│       ├── RadarChart.tsx        # 能力雷达图
│       └── TrendChart.tsx        # 趋势图
│
└── lib/
    ├── api.ts                    # API 客户端 (fetch wrapper)
    ├── store.ts                  # Zustand store
    └── utils.ts
```

---

## 5. API 客户端

```typescript
// frontend/lib/api.ts

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export const api = {
  questions: {
    list: (params: QuestionFilter) =>
      fetch(`${API_BASE}/api/v1/questions?${new URLSearchParams(params)}`),
    get: (id: string) =>
      fetch(`${API_BASE}/api/v1/questions/${id}`),
    search: (query: string) =>
      fetch(`${API_BASE}/api/v1/questions/search?q=${query}`),
  },
  generate: {
    free: (req: GenerateRequest) =>
      fetch(`${API_BASE}/api/v1/generate/free`, {
        method: 'POST', body: JSON.stringify(req),
      }),
    variant: (req: VariantRequest) =>
      fetch(`${API_BASE}/api/v1/generate/variant`, {
        method: 'POST', body: JSON.stringify(req),
      }),
  },
  // ...
};
```

---

## 6. 状态管理

```typescript
// frontend/lib/store.ts

import { create } from 'zustand';

interface AppState {
  user: User | null;
  filters: QuestionFilter;
  setFilters: (f: Partial<QuestionFilter>) => void;
  currentQuestion: Question | null;
  setCurrentQuestion: (q: Question | null) => void;
}

export const useStore = create<AppState>((set) => ({
  user: null,
  filters: { subject: null, difficulty_min: 1, difficulty_max: 10 },
  setFilters: (f) => set((s) => ({ filters: { ...s.filters, ...f } })),
  currentQuestion: null,
  setCurrentQuestion: (q) => set({ currentQuestion: q }),
}));
```
