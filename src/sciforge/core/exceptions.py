"""MathForge 异常层次"""


class MathForgeError(Exception):
    """基础异常"""


class LLMError(MathForgeError):
    """LLM 调用异常"""


class LLMParseError(LLMError):
    """LLM 响应解析失败"""

    def __init__(self, raw_response: str, parse_error: str):
        self.raw_response = raw_response
        super().__init__(f"JSON 解析失败: {parse_error}\n原始响应: {raw_response[:500]}")


class LLMTimeoutError(LLMError):
    """LLM 请求超时"""


class ParseError(MathForgeError):
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


class StorageError(MathForgeError):
    """存储操作异常"""


class OcrRepairError(MathForgeError):
    """OCR 修复异常"""


class GenerationError(MathForgeError):
    """生成流水线异常"""


class SandboxError(MathForgeError):
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


class PipelineError(MathForgeError):
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
