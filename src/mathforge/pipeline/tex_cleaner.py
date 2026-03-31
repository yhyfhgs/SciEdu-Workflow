"""LaTeX 标准化与清理"""

from __future__ import annotations

import re

# main.tex 中定义的自定义宏
CUSTOM_MACROS: dict[str, str] = {
    r"\bbR": r"\mathbb{R}",
    r"\bbC": r"\mathbb{C}",
    r"\bbZ": r"\mathbb{Z}",
    r"\bbN": r"\mathbb{N}",
    r"\bbQ": r"\mathbb{Q}",
}

# 纯格式命令（不影响数学内容）
STRIP_COMMANDS = [
    r"\noindent",
    r"\medskip",
    r"\bigskip",
    r"\smallskip",
    r"\par",
]


def expand_macros(latex: str) -> str:
    """展开自定义宏"""
    for macro, expansion in CUSTOM_MACROS.items():
        latex = latex.replace(macro, expansion)
    return latex


def strip_formatting(latex: str) -> str:
    """去除纯格式命令"""
    for cmd in STRIP_COMMANDS:
        latex = latex.replace(cmd, "")
    return latex


def normalize_whitespace(latex: str) -> str:
    """标准化空白符"""
    # \r\n → \n
    latex = latex.replace("\r\n", "\n")
    # 去除每行尾部空格
    latex = "\n".join(line.rstrip() for line in latex.split("\n"))
    # 3+ 连续空行 → 2 空行
    latex = re.sub(r"\n{3,}", "\n\n", latex)
    # 首尾空白
    latex = latex.strip()
    return latex


def validate_brackets(latex: str) -> list[str]:
    """验证 LaTeX 括号匹配，返回警告列表"""
    warnings: list[str] = []

    # 检查 { } 配对
    depth = 0
    for i, ch in enumerate(latex):
        if ch == "{" and (i == 0 or latex[i - 1] != "\\"):
            depth += 1
        elif ch == "}" and (i == 0 or latex[i - 1] != "\\"):
            depth -= 1
        if depth < 0:
            warnings.append(f"多余的 '}}' 在位置 {i}")
            depth = 0
    if depth > 0:
        warnings.append(f"缺少 {depth} 个 '}}'")

    # 检查 \begin{X} / \end{X} 配对
    begins = re.findall(r"\\begin\{(\w+)\}", latex)
    ends = re.findall(r"\\end\{(\w+)\}", latex)
    begin_counts: dict[str, int] = {}
    end_counts: dict[str, int] = {}
    for env in begins:
        begin_counts[env] = begin_counts.get(env, 0) + 1
    for env in ends:
        end_counts[env] = end_counts.get(env, 0) + 1
    for env in set(begin_counts) | set(end_counts):
        b = begin_counts.get(env, 0)
        e = end_counts.get(env, 0)
        if b != e:
            warnings.append(f"环境 '{env}': {b} 个 \\begin vs {e} 个 \\end")

    return warnings


def clean_latex(latex: str) -> str:
    """组合清理步骤"""
    latex = expand_macros(latex)
    latex = strip_formatting(latex)
    latex = normalize_whitespace(latex)
    return latex
