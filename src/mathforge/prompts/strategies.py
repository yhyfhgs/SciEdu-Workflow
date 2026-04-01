"""八种变式策略指令 — 从 .txt 文件加载"""

from mathforge.prompts.loader import load_prompt

STRATEGY_INSTRUCTIONS: dict[str, str] = {
    "A1_parameter_shift": load_prompt("strategies/A1_parameter_shift.txt"),
    "A2_method_shift": load_prompt("strategies/A2_method_shift.txt"),
    "A3_type_shift": load_prompt("strategies/A3_type_shift.txt"),
    "A4_escalate": load_prompt("strategies/A4_escalate.txt"),
    "A5_simplify": load_prompt("strategies/A5_simplify.txt"),
    "A6_reverse": load_prompt("strategies/A6_reverse.txt"),
    "B1_topic_generation": load_prompt("strategies/B1_topic_generation.txt"),
    "B2_cross_topic": load_prompt("strategies/B2_cross_topic.txt"),
}
