"""SciForge 提示词模板包

导出当前 5 阶段 pipeline 主线使用的默认 prompt 常量（数学学科，学科变量已注入）。
其他学科请用 `get_*_template(discipline)` getter 按需加载。
"""

from sciforge.prompts.templates import (
    VLM_CONVERT_PROMPT,
)

__all__ = [
    "VLM_CONVERT_PROMPT",
]
