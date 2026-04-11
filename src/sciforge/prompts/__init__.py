"""SciForge 提示词模板包

仅导出当前 pipeline 主线使用的提示词。未使用的旧模板文件 (.txt) 保留
在 templates/ 目录下作为未来生成引擎重建时的参考。
"""

from sciforge.prompts.templates import (
    EXTRACT_AND_CLASSIFY_PROMPT,
    VLM_CONVERT_PROMPT,
)

__all__ = [
    "EXTRACT_AND_CLASSIFY_PROMPT",
    "VLM_CONVERT_PROMPT",
]
