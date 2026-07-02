"""TextToolLibrary（v11 子系统 II）——确定性文本工具库，纯 Python、无 LLM、结果即真值。

工具分批落地（实施计划裁定 A）：
- M1（本批）：T4 ``find_item_markers``、T7 ``check_numbering_continuity``
  （T6 ``audit_coverage`` 操作 ledger + items，住 ``pipeline/boundary_patch.py``）
- M2：T8 check_pairing、T12 check_math_balance
- M3：T1/T2/T3/T9/T10 正则 DSL/T11/T13

所有工具输出可追溯 span（line / char），供边界仲裁与字符级溯源。
"""

from sciforge.pipeline.text_tools.markers import (
    ITEM_KINDS,
    MarkerEntry,
    MarkerKind,
    find_item_markers,
)
from sciforge.pipeline.text_tools.numbering_check import (
    NumberingContinuityReport,
    NumberingIssue,
    check_numbering_continuity,
)

__all__ = [
    "ITEM_KINDS",
    "MarkerEntry",
    "MarkerKind",
    "NumberingContinuityReport",
    "NumberingIssue",
    "check_numbering_continuity",
    "find_item_markers",
]
