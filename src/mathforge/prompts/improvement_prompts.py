"""进化引擎改进循环提示词 — 从 .txt 文件加载"""

from mathforge.prompts.loader import load_prompt

IMPROVE_FROM_VERIFICATION_PROMPT = load_prompt("evolution/improve_from_verification.txt")

IMPROVE_FROM_SOLVER_PROMPT = load_prompt("evolution/improve_from_solver.txt")

ITERATION_SUMMARY_TEMPLATE = load_prompt("evolution/iteration_summary.txt")

EVOLUTION_SYSTEM_PROMPT = load_prompt("evolution/evolution_system.txt")
