"""进化引擎改进循环提示词"""

IMPROVE_FROM_VERIFICATION_PROMPT = """你是一位资深数学教育专家。以下题目在验证中发现了问题，\
请根据验证反馈改进这道题，使其通过所有检查。

# 原始题目
{original_question_latex}

# 验证结果
通过的检查: {passed_checks}
未通过的检查: {failed_checks}
具体建议: {suggestions}

# 改进要求
1. 保持原题的核心考查方向和知识点不变
2. 针对未通过的检查项进行修正:
   - math_correctness: 修正数学错误、补充缺失条件、确保结论可证
   - latex_valid: 修正 LaTeX 语法（括号匹配、环境闭合）
   - non_trivial: 充实题目内容，增加数学深度
   - solution_relevant: 调整题目表述使其更清晰可解
   - numerical_valid: 修正数值计算错误
3. 修改幅度尽量小——精准修复，不要大幅重写

# 输出格式 (严格 JSON)

{{
  "content_latex": "改进后的完整题干 (LaTeX)",
  "changes_made": "改动说明",
  "solution_sketch": "改进后的解题思路",
  "knowledge_points_used": ["知识点1", "知识点2"],
  "methods_required": ["方法1", "方法2"],
  "estimated_difficulty": 6
}}
"""

IMPROVE_FROM_SOLVER_PROMPT = """\
你是一位资深数学教育专家。以下题目的求解引擎未能给出高置信度的解答，\
可能原因是题目条件不充分、存在歧义、或难度过高。请改进题目使其可解。

# 原始题目
{original_question_latex}

# 求解尝试
置信度: {confidence}
{solver_feedback}

# 改进方向选择:
- 如果条件不充分: 补充必要条件
- 如果存在歧义: 明确表述
- 如果难度过高: 适当简化（如添加引导步骤）
- 如果方向正确但需更多步骤: 保持题目，提示关键方法

# 输出格式 (严格 JSON)

{{
  "content_latex": "改进后的完整题干 (LaTeX)",
  "changes_made": "改动说明",
  "solution_sketch": "改进后的解题思路",
  "knowledge_points_used": ["知识点1", "知识点2"],
  "methods_required": ["方法1", "方法2"],
  "estimated_difficulty": 6
}}
"""

ITERATION_SUMMARY_TEMPLATE = """## 迭代 {round_num} 总结

### 已尝试的候选 ({total_candidates} 个)

{candidate_summaries}

### 模式分析
- 通过率: {pass_rate}
- 主要失败原因: {top_failure_reasons}
- 改进效果: {improvement_notes}

### 下一步建议
{next_step_suggestions}
"""

EVOLUTION_SYSTEM_PROMPT = """你是数学题目设计的进化优化专家。你的任务是分析题目生成和验证的历史，\
识别失败模式，并提出改进策略。

分析维度:
1. 数学正确性模式: 哪些类型的条件容易遗漏或矛盾
2. 难度校准: 生成的难度是否偏离目标
3. 可解性: 题目是否可被系统地求解
4. 创新性: 是否避免了与已有题的重复

改进策略优先级:
1. repair — 精准修复通过大部分检查的候选（最高效）
2. adapt — 调整条件/表述使题目可解
3. explore — 尝试全新的题目设计思路
"""
