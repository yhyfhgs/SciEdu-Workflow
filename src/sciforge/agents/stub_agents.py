"""6 个骨架 Agent (Agent 1, 6, 7, 8, 9, 10)

Post-MVP 阶段实现。当前仅保留 BaseAgent 兼容的类定义 + raise NotImplementedError，
方便上层路由层 (LangGraph 或手写 StateGraph) 先把连线搭起来。

参见 Docs/09_Agent编排系统.md 和 Docs/16_Agent系统MVP.md。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from sciforge.agents.base import BaseAgent


class _StubInput(BaseModel):
    data: Any = None


class _StubOutput(BaseModel):
    data: Any = None


class _StubAgent(BaseAgent[_StubInput, _StubOutput]):
    """骨架 Agent 基类"""

    prompt_file = ""

    async def run(self, input: _StubInput) -> _StubOutput:  # pragma: no cover
        raise NotImplementedError(
            f"{type(self).__name__} 是 Post-MVP 骨架，尚未实现。参见 Docs/09_Agent编排系统.md。"
        )


class KBAgent(_StubAgent):
    """Agent 1: 知识库管理 — 协调 Pipeline、embedding、向量存储、依赖图构建"""

    name = "kb"


class RecommendAgent(_StubAgent):
    """Agent 6: 个性化推荐 — 基于用户画像推荐学习路径"""

    name = "recommend"


class FormatAnalyzerAgent(_StubAgent):
    """Agent 7: 格式分析 — 识别文档格式和结构 (MVP 由 recognizers.py 覆盖)"""

    name = "format_analyzer"


class ContentExtractorAgent(_StubAgent):
    """Agent 8: 内容提取 — 多模态 VLM 识别 (MVP 由 vlm_converter.py 覆盖)"""

    name = "content_extractor"


class OcrCorrectorAgent(_StubAgent):
    """Agent 9: OCR 修正 — 修正 OCR 错误、跨页合并 (MVP 内置于 stages/stage1_extract.py)"""

    name = "ocr_corrector"


class ContentTaggerAgent(_StubAgent):
    """Agent 10: 内容标签 — tag 分类 + 难度评估 (MVP 内置于 stages/stage2_annotate.py)"""

    name = "content_tagger"
