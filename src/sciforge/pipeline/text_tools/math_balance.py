"""T12 ``check_math_balance``——数学环境/括号配平检测（v11 II.2，M2）。

检查 ``$...$`` / ``$$...$$`` / ``\\[...\\]`` / ``\\(...\\)`` /
``\\begin{env}...\\end{env}`` 配平；括号 ``()[]{}`` **只在数学模式内**跟踪
（正文括号如 "1)" 枚举噪声大）。输出不平衡 token 的行/列位置两类：
``unclosed_open``（开了没关）与 ``unopened_close``（关了没开）。

**跨窗信号**：``open_env_at_tail=True``（扫描结束仍有未闭合环境/数学模式）
是 carry 四态判别 ``content_truncated`` 的强判据（III.2）——公式中途断开
几乎必然是窗口物理截断，而非"真无答案"。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_BEGIN_RE = re.compile(r"\\begin\{([A-Za-z*]+)\}")
_END_RE = re.compile(r"\\end\{([A-Za-z*]+)\}")
# 一次扫描的全部关注 token（顺序：长 token 优先，避免 $$ 被拆成两个 $）
_TOKEN_RE = re.compile(
    r"\\begin\{[A-Za-z*]+\}|\\end\{[A-Za-z*]+\}"
    r"|\\\[|\\\]|\\\(|\\\)"
    r"|\\left\s*[([{]|\\right\s*[)\]}]"
    r"|\$\$|(?<!\\)\$"
    r"|[()\[\]{}]"
)
_OPEN_BRACKETS = {"(": ")", "[": "]", "{": "}"}
_CLOSE_BRACKETS = {v: k for k, v in _OPEN_BRACKETS.items()}


class BalanceIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["unclosed_open", "unopened_close"]
    token: str  # 如 "$$"、"\\begin{align}"、"("
    line: int = Field(ge=1)  # 全局行号
    char: int = Field(ge=0)  # 行内 0-based 列


class MathBalanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issues: list[BalanceIssue] = Field(default_factory=list)
    open_env_at_tail: bool = False  # 末尾仍未闭合 → carry content_truncated 强信号
    open_tail_tokens: list[str] = Field(default_factory=list)

    @property
    def balanced(self) -> bool:
        return not self.issues and not self.open_env_at_tail


def check_math_balance(lines: list[str], *, line_base: int = 1) -> MathBalanceReport:
    """扫描（已剥离行号前缀的）行序列做配平检查。纯确定性，0 LLM。"""
    # 栈元素：(token 规范形, line, char)。inline $ 单独跟踪（toggle 语义）。
    stack: list[tuple[str, int, int]] = []
    issues: list[BalanceIssue] = []
    inline_open: tuple[int, int] | None = None  # 未闭合的 $ 位置

    def in_math() -> bool:
        return inline_open is not None or any(
            t[0].startswith(("\\begin", "$$", "\\[", "\\(")) for t in stack
        )

    for offset, line in enumerate(lines):
        line_no = line_base + offset
        for m in _TOKEN_RE.finditer(line):
            tok = m.group()
            col = m.start()
            if tok.startswith("\\begin{"):
                stack.append((tok, line_no, col))
            elif tok.startswith("\\end{"):
                env = _END_RE.match(tok).group(1)  # type: ignore[union-attr]
                if stack and stack[-1][0] == f"\\begin{{{env}}}":
                    stack.pop()
                else:
                    issues.append(
                        BalanceIssue(kind="unopened_close", token=tok, line=line_no, char=col)
                    )
            elif tok in ("\\[", "\\("):
                stack.append((tok, line_no, col))
            elif tok in ("\\]", "\\)"):
                want = "\\[" if tok == "\\]" else "\\("
                if stack and stack[-1][0] == want:
                    stack.pop()
                else:
                    issues.append(
                        BalanceIssue(kind="unopened_close", token=tok, line=line_no, char=col)
                    )
            elif tok == "$$":
                if stack and stack[-1][0] == "$$":
                    stack.pop()
                else:
                    stack.append(("$$", line_no, col))
            elif tok == "$":
                inline_open = None if inline_open is not None else (line_no, col)
            elif tok.startswith("\\left"):
                stack.append((f"\\left{tok[-1]}", line_no, col))
            elif tok.startswith("\\right"):
                want_open = _CLOSE_BRACKETS.get(tok[-1], "")
                if stack and stack[-1][0] == f"\\left{want_open}":
                    stack.pop()
                else:
                    issues.append(
                        BalanceIssue(kind="unopened_close", token=tok, line=line_no, char=col)
                    )
            elif tok in _OPEN_BRACKETS:
                if in_math():  # 正文括号不跟踪（枚举 "1)" 等噪声大）
                    stack.append((tok, line_no, col))
            elif tok in _CLOSE_BRACKETS:
                if not in_math():
                    continue
                if stack and stack[-1][0] == _CLOSE_BRACKETS[tok]:
                    stack.pop()
                else:
                    issues.append(
                        BalanceIssue(kind="unopened_close", token=tok, line=line_no, char=col)
                    )

    open_tail: list[str] = []
    for tok, line_no, col in stack:
        issues.append(BalanceIssue(kind="unclosed_open", token=tok, line=line_no, char=col))
        open_tail.append(tok)
    if inline_open is not None:
        issues.append(
            BalanceIssue(kind="unclosed_open", token="$", line=inline_open[0], char=inline_open[1])
        )
        open_tail.append("$")
    return MathBalanceReport(
        issues=issues,
        open_env_at_tail=bool(open_tail),
        open_tail_tokens=open_tail,
    )
