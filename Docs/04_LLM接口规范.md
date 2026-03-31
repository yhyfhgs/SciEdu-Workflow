# LLM 接口规范

> 版本: v2.0 | 日期: 2026-03-31

## 1. 多提供者架构

### 1.1 支持的提供者

| 提供者 | 枚举值 | SDK | 多模态 | 使用场景 |
|--------|--------|-----|--------|----------|
| Anthropic (Claude) | `anthropic` | `anthropic` | Claude 3.5+ 支持 | 默认，数学推理能力强 |
| OpenAI | `openai` | `openai` | GPT-4V/4o 支持 | 备选，GPT-4o 等 |
| Google Gemini | `gemini` | `google-genai` | Gemini 1.5+ 支持 | 备选，长上下文 |
| OpenAI 兼容 | `openai_compatible` | `openai` (自定义 base_url) | 取决于后端 | 中转平台、本地模型 |

### 1.2 配置模型

```python
class LLMProvider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    OPENAI_COMPATIBLE = "openai_compatible"

class LLMConfig(BaseModel):
    provider: LLMProvider = LLMProvider.ANTHROPIC
    model: str = "claude-sonnet-4-20250514"
    temperature: float = Field(ge=0.0, le=2.0, default=0.7)
    max_tokens: int = Field(ge=1, le=65536, default=4096)
    api_key: str | None = None
    base_url: str | None = None
    reasoning_effort: str | None = None
    timeout: int = 120
    max_retries: int = 3
```

**任务预设**:

| 任务 | 模型 | 温度 | max_tokens | 说明 |
|------|------|------|-----------|------|
| `generate` | claude-sonnet-4 | 0.7 | 4096 | 题目生成（任意学科） |
| `solve` | claude-sonnet-4 | 0.0 | 8192 | 求解（需要精确，学科特定 prompt） |
| `review` | claude-sonnet-4 | 0.0 | 2048 | 学科正确性审核 |
| `tag` | claude-sonnet-4 | 0.0 | 1024 | 标签分类（含学科/学段识别） |
| `repair` | claude-sonnet-4 | 0.0 | 4096 | OCR 修复 |
| `format_analyze` | claude-sonnet-4 | 0.0 | 1024 | 格式分析（Pipeline） |
| `extract` | claude-sonnet-4 | 0.0 | 8192 | 内容提取（Pipeline，多模态） |

通过 `LLMConfig.for_task("generate")` 获取预设，支持 `**overrides` 覆写。

---

## 2. 统一客户端接口

### 2.1 LLM 后端协议

```python
class LLMBackend(Protocol):
    """LLM 后端协议"""
    async def generate(self, prompt: str, system: str, **kwargs) -> str: ...
    def generate_sync(self, prompt: str, system: str, **kwargs) -> str: ...
```

### 2.2 LLMClient 核心方法

`LLMClient` 封装统一接口，根据 `LLMConfig.provider` 自动选择后端：

| 方法 | 说明 |
|------|------|
| `generate(prompt, system, temperature, max_tokens)` | 异步文本生成 |
| `generate_sync(prompt, **kwargs)` | 同步文本生成（CLI 使用） |
| `generate_json(prompt, **kwargs)` | 生成并解析 JSON |
| `generate_with_images(prompt, images, system, **kwargs)` | **多模态**：带图像输入的生成 |
| `generate_structured(prompt, response_model, **kwargs)` | **结构化输出**：输出验证为 Pydantic 模型 |

### 2.3 多模态支持（新增）

`generate_with_images()` 支持将图像（PDF 页面截图、PPT 幻灯片等）发送给多模态 LLM：

- **Anthropic**: 通过 `messages` API 的 `image` content block（base64 编码）
- **OpenAI**: 通过 `messages` 中的 image_url（base64 data URI）
- **Gemini**: 通过 `contents` 中的 `Part.from_image()`

Pipeline 的 ContentExtractor Agent 使用此方法逐页识别 PDF 内容。

### 2.4 Pydantic 结构化输出（新增）

`generate_structured()` 确保 LLM 输出严格匹配指定 Pydantic 模型：

**流程**:
1. 将 `response_model` 的 JSON Schema 注入 prompt（作为输出格式约束）
2. 调用 LLM 生成 JSON
3. 使用 `response_model.model_validate_json()` 验证
4. 验证失败时自动重试（最多 2 次），将错误信息反馈给 LLM

这是 Pipeline Agent 输出固定 Schema 的核心机制。例如：

```python
# FormatAnalyzer Agent 调用示例
analysis = await llm.generate_structured(
    prompt=f"分析以下文档的格式和结构:\n{preview_text}",
    response_model=FormatAnalysis,
)
# analysis 是经过验证的 FormatAnalysis Pydantic 实例
```

---

## 3. JSON 响应解析

`parse_llm_json()` 提供鲁棒的 JSON 解析，按优先级尝试:

1. 去除 Markdown 代码块标记（` ```json ``` `）
2. 直接 `json.loads()` 解析
3. 正则提取 `{...}` JSON 对象
4. 修复常见错误（尾逗号、单引号）

解析失败时抛出 `LLMParseError`（含原始响应内容，便于调试）。

---

## 4. 提供者后端实现

各后端实现 `LLMBackend` 协议，核心差异:

| 后端 | 异步客户端 | 消息格式 | 多模态图像传递 |
|------|-----------|---------|--------------|
| Anthropic | `AsyncAnthropic` | `system` 参数 + `messages` | `image` content block (base64) |
| OpenAI | `AsyncOpenAI` | system role message + user message | image_url data URI |
| Gemini | `genai.Client.aio` | `system_instruction` + `contents` | `Part.from_image()` |
| OpenAI Compatible | 同 OpenAI | 同 OpenAI | 取决于后端 |

`OpenAICompatibleBackend` 继承 `OpenAIBackend`，要求提供 `base_url`（用于中转平台、vLLM、Ollama 等）。

---

## 5. 配置管理

### 5.1 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ANTHROPIC_API_KEY` | Anthropic API Key | - |
| `OPENAI_API_KEY` | OpenAI API Key | - |
| `GOOGLE_API_KEY` | Google API Key | - |
| `MATHFORGE_LLM_PROVIDER` | 默认提供者 | anthropic |
| `MATHFORGE_LLM_MODEL` | 默认模型 | claude-sonnet-4-20250514 |
| `MATHFORGE_LLM_BASE_URL` | 自定义 API URL | - |
| `MATHFORGE_LLM_TEMPERATURE` | 默认温度 | 0.7 |
| `MATHFORGE_LLM_MAX_TOKENS` | 默认 max_tokens | 4096 |

### 5.2 YAML 配置文件

`config/llm_config.yaml` 支持按任务覆写默认配置，也可指定中转平台。

### 5.3 CLI 覆写

所有 `generate` 命令支持 `--llm-provider`, `--llm-model`, `--llm-base-url`, `--llm-temperature` 参数，优先级: CLI > YAML > 环境变量 > 默认值。

---

## 6. 日志

每次 LLM 调用通过 structlog 记录: provider, model, task, temperature, max_tokens, prompt_length, response_length, latency_ms, success。

---

## 7. 实现文件

| 文件 | 职责 |
|------|------|
| `src/mathforge/llm/__init__.py` | 导出 LLMClient, LLMConfig |
| `src/mathforge/llm/client.py` | LLMClient 统一接口 |
| `src/mathforge/llm/config.py` | LLMConfig, LLMProvider |
| `src/mathforge/llm/parser.py` | JSON 响应解析 |
| `src/mathforge/llm/backends/anthropic.py` | Anthropic 后端 |
| `src/mathforge/llm/backends/openai.py` | OpenAI 后端 |
| `src/mathforge/llm/backends/gemini.py` | Gemini 后端 |
| `src/mathforge/llm/backends/compatible.py` | OpenAI 兼容后端 |
