"""核心提示词模板 — 从 .txt 文件加载

当前主线只用两个提示词：
- VLM_CONVERT_PROMPT: Producer (Task0) 多模态文档识别
- EXTRACT_AND_CLASSIFY_PROMPT: Consumer (Task1) 提取+校正+分类+打标签+难度评估

生成路径相关的 prompt 文件保留在 templates/generation/, solving/, verification/
下，待生成引擎重建时按需加载。
"""

from sciforge.prompts.loader import load_prompt

EXTRACT_AND_CLASSIFY_PROMPT = load_prompt("pipeline/extract_and_classify.txt")
VLM_CONVERT_PROMPT = load_prompt("pipeline/vlm_convert.txt")
