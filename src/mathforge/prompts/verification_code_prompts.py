"""数值验证提示词 — 从 .txt 文件加载"""

from mathforge.prompts.loader import load_prompt

GENERATE_NUMERICAL_TEST_PROMPT = load_prompt("verification/generate_numerical_test.txt")
