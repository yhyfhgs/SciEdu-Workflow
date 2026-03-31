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
