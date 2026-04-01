"""多轮求解 + Tool Calling 提示词 — 从 .txt 文件加载"""

from mathforge.prompts.loader import load_prompt

SOLVE_DEEP_SYSTEM = load_prompt("solving/solve_deep_system.txt")

SOLVE_DEEP_USER = load_prompt("solving/solve_deep_user.txt")
