"""SciForge 异常层次"""


class SciForgeError(Exception):
    """基础异常"""


class LLMError(SciForgeError):
    """LLM 调用异常"""


class LLMParseError(LLMError):
    """LLM 响应解析失败"""

    def __init__(self, raw_response: str, parse_error: str):
        self.raw_response = raw_response
        super().__init__(f"JSON 解析失败: {parse_error}\n原始响应: {raw_response[:500]}")


class LLMTimeoutError(LLMError):
    """LLM 请求超时"""


class LLMRateLimitError(LLMError):
    """LLM 遇到速率限制或配额限制 (HTTP 429 等)。

    这类错误是**临时性**的，调用方应当长睡后重试，而不是把任务标记为失败。
    典型场景：
    - OpenAI tokens-per-minute / requests-per-minute 触顶
    - Anthropic rate_limit_error
    - Minimax 5 小时 4500 次配额耗尽
    - 商业代理层的 quota 限流

    ``retry_after_seconds`` 若非 None 表示服务端建议的等待秒数（来自
    HTTP ``Retry-After`` header 或错误消息解析），调用方应至少等待这个时长。
    """

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


class ParseError(SciForgeError):
    """解析异常"""


class TexParseError(ParseError):
    """TeX 解析错误"""

    def __init__(self, filepath: str, line: int = 0, detail: str = ""):
        self.filepath = filepath
        self.line = line
        super().__init__(f"{filepath}:{line} - {detail}" if line else f"{filepath} - {detail}")


class MdParseError(ParseError):
    """MD 解析错误"""


class PdfParseError(ParseError):
    """PDF 解析错误"""

    def __init__(self, filepath: str = "", detail: str = ""):
        self.filepath = filepath
        super().__init__(f"{filepath} - {detail}" if detail else filepath)


class DocxParseError(ParseError):
    """Word 文档解析错误"""


class CsvParseError(ParseError):
    """CSV 解析错误"""


class StorageError(SciForgeError):
    """存储操作异常"""


class OcrRepairError(SciForgeError):
    """OCR 修复异常"""


class GenerationError(SciForgeError):
    """生成流水线异常"""


class SandboxError(SciForgeError):
    """代码沙箱执行异常"""


class SandboxTimeoutError(SandboxError):
    """代码沙箱执行超时"""

    def __init__(self, timeout_seconds: int, code_snippet: str = ""):
        self.timeout_seconds = timeout_seconds
        snippet = code_snippet[:200] if code_snippet else ""
        super().__init__(f"代码执行超时 ({timeout_seconds}s): {snippet}")


class LLMVisionError(LLMError):
    """LLM 多模态视觉调用异常"""


class ToolCallError(LLMError):
    """LLM tool calling 异常"""


class EvolutionExhaustedError(GenerationError):
    """进化引擎耗尽所有改进轮次"""

    def __init__(self, rounds: int, candidates_tried: int):
        self.rounds = rounds
        self.candidates_tried = candidates_tried
        super().__init__(f"进化引擎耗尽: {rounds} 轮改进, {candidates_tried} 个候选均未通过验证")


class PipelineError(SciForgeError):
    """Pipeline 处理异常"""


class VlmConversionError(PipelineError):
    """VLM 文档转换异常"""


class FingerprintMatchError(PipelineError):
    """指纹匹配失败"""


class MetaExtractorError(PipelineError):
    """MetaExtractor 提取失败"""


class ItemCorrectionError(PipelineError):
    """条目校准失败"""


class UnificationError(PipelineError):
    """条目统一转换失败"""
