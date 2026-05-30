"""5 阶段 pipeline 的各个 Stage 实现

- Stage1Extract: 单条滑动窗口提取（LLM 调用 1）
- Stage1Review: 单条 item 校验（LLM 调用 2）
- Stage2Annotate: 单条 item 标注（LLM 调用 3）
- Stage2Review: 单条 annotation 审核（LLM 调用 4）
- Stage3Persist: 合并 item + annotation 入库（无 LLM）
"""

from sciforge.pipeline.stages.stage1_extract import Stage1Extract
from sciforge.pipeline.stages.stage1_review import Stage1Review
from sciforge.pipeline.stages.stage2_annotate import Stage2Annotate
from sciforge.pipeline.stages.stage2_review import Stage2Review
from sciforge.pipeline.stages.stage3_persist import Stage3Persist

__all__ = [
    "Stage1Extract",
    "Stage1Review",
    "Stage2Annotate",
    "Stage2Review",
    "Stage3Persist",
]
