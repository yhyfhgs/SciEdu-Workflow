"""数值验证提示词：生成验证代码"""

GENERATE_NUMERICAL_TEST_PROMPT = """你是一位数学验证专家。请为以下数学题和解答生成 Python 验证代码。

# 题目
{question_latex}

# 解答摘要
{solution_sketch}

# 要求

编写 Python 代码对解答进行数值验证:
- 使用 sympy 和 numpy 进行符号和数值计算
- 针对题目类型选择合适的验证策略:
  - 极限题: 计算数列/函数在大 n 或趋近点处的数值，验证趋近性
  - 等式/恒等式: 代入多组随机值验证
  - 线性代数题: 用具体矩阵实例化并验证性质
  - 不等式: 代入多组值检查是否成立
  - 证明题: 检查若干特殊情况
- 至少 3 个测试用例
- 每个测试用例 print 输出 "PASS" 或 "FAIL: <原因>"
- 最后一行 print 总结: "ALL_PASSED" 或 "SOME_FAILED: N/M"

# 输出格式 (严格 JSON)

{{
  "verification_code": "完整 Python 代码",
  "test_description": "验证策略简述",
  "expected_test_count": 3
}}
"""
