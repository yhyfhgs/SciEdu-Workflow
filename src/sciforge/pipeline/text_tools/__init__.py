"""TextToolLibrary（v11 子系统 II）——确定性文本工具库，纯 Python、无 LLM、结果即真值。

工具分批落地（实施计划裁定 A）：
- M1：T4 ``find_item_markers``、T7 ``check_numbering_continuity``
  （T6 ``audit_coverage`` 操作 ledger + items，住 ``pipeline/boundary_patch.py``）
- M2：T8 ``check_pairing``、T12 ``check_math_balance``
- M3（本批）：T1 ``normalize_text``、T2 ``strip_running_headers``、
  T3 ``detect_structure``、T9 ``extract_subinterval``、T10 正则 DSL

所有工具输出可追溯 span（line / char），供边界仲裁与字符级溯源。
"""

from sciforge.pipeline.text_tools.headers import StripHeadersResult, strip_running_headers
from sciforge.pipeline.text_tools.markers import (
    ITEM_KINDS,
    MarkerEntry,
    MarkerKind,
    find_item_markers,
)
from sciforge.pipeline.text_tools.math_balance import (
    BalanceIssue,
    MathBalanceReport,
    check_math_balance,
)
from sciforge.pipeline.text_tools.normalize import NormalizedText, normalize_text
from sciforge.pipeline.text_tools.numbering_check import (
    NumberingContinuityReport,
    NumberingIssue,
    check_numbering_continuity,
)
from sciforge.pipeline.text_tools.pairing import (
    PairingIssue,
    PairingItem,
    PairingReport,
    check_pairing,
)
from sciforge.pipeline.text_tools.regex_dsl import (
    FRAGMENTS,
    DslMatch,
    compose,
    regex_dsl,
    trim_head,
    trim_item,
    trim_tail,
)
from sciforge.pipeline.text_tools.structure import (
    StructureNode,
    detect_structure,
    extract_subinterval,
)

__all__ = [
    "FRAGMENTS",
    "ITEM_KINDS",
    "BalanceIssue",
    "DslMatch",
    "MarkerEntry",
    "MarkerKind",
    "MathBalanceReport",
    "NormalizedText",
    "NumberingContinuityReport",
    "NumberingIssue",
    "PairingIssue",
    "PairingItem",
    "PairingReport",
    "StripHeadersResult",
    "StructureNode",
    "check_math_balance",
    "check_numbering_continuity",
    "check_pairing",
    "compose",
    "detect_structure",
    "extract_subinterval",
    "find_item_markers",
    "normalize_text",
    "regex_dsl",
    "strip_running_headers",
    "trim_head",
    "trim_item",
    "trim_tail",
]
