"""BaseAgent — 所有 Agent 的抽象基类

统一：
- async `run(input)` 入口（由子类实现）
- prompt 模板加载 + 学科参数化（通过 DisciplineRegistry）
- LLM 调用（带重试的 JSON 解析）
- 结构化日志（统一字段 discipline/agent_name）
- 异常封装（AgentError 作为 raise 点，便于上游捕获）

子类约定：
- 类字段 `name: str` — 日志/检索用的短名
- 类字段 `prompt_file: str` — 相对 `prompts/templates/` 的 .txt 路径，空字符串表
  示不加载
- 泛型 TIn/TOut 都是 Pydantic BaseModel 子类

示例：
    class MyAgent(BaseAgent[MyIn, MyOut]):
        name = "my_agent"
        prompt_file = "solving/solve.txt"

        async def run(self, input: MyIn) -> MyOut:
            self._log_start(question_id=input.question_id)
            data = await self._call_llm_json(prompt=...)
            result = MyOut(**data)
            self._log_end(passed=result.passed)
            return result
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Generic, TypeVar

import structlog
from pydantic import BaseModel

from sciforge.core.exceptions import SciForgeError
from sciforge.core.models import Discipline
from sciforge.disciplines import DISCIPLINE_REGISTRY
from sciforge.pipeline.retry import call_llm_json_with_retry

if TYPE_CHECKING:
    from sciforge.disciplines.registry import DisciplineSpec
    from sciforge.llm.client import LLMClient

logger = structlog.get_logger()


class AgentError(SciForgeError):
    """Agent 运行时异常（包装 LLM/解析/验证失败）"""


TIn = TypeVar("TIn", bound=BaseModel)
TOut = TypeVar("TOut", bound=BaseModel)


class BaseAgent(ABC, Generic[TIn, TOut]):
    """所有 Agent 的抽象基类"""

    name: str = ""
    """Agent 短名，用于日志事件名（如 "qgen" → "agent_qgen_start"）"""

    prompt_file: str = ""
    """相对 `prompts/templates/` 的 .txt 路径；空字符串表示不加载 prompt"""

    def __init__(
        self,
        llm: LLMClient,
        discipline: Discipline = Discipline.MATH,
    ) -> None:
        if not self.name:
            raise ValueError(f"{type(self).__name__} must set class attribute `name`")
        self.llm = llm
        self.discipline = discipline
        self.discipline_spec: DisciplineSpec = DISCIPLINE_REGISTRY.get(discipline)
        self._prompt_template: str = self._load_prompt()

    def _load_prompt(self) -> str:
        """加载 prompt_file 对应的模板文件；空 prompt_file 返回空字符串

        子类可 override 返回按学科注入后的模板。
        """
        if not self.prompt_file:
            return ""
        from sciforge.prompts.loader import load_prompt

        return load_prompt(self.prompt_file)

    @abstractmethod
    async def run(self, input: TIn) -> TOut:
        """Agent 主入口。子类必须实现"""
        ...

    # ── LLM 调用工具 ──────────────────────────────────────

    async def _call_llm_json(
        self,
        prompt: str,
        *,
        max_tokens: int = 16384,
        max_attempts: int = 3,
        base_delay: float = 2.0,
        validate: Any = None,
    ) -> dict[str, Any]:
        """带重试的 LLM JSON 调用（包装 pipeline.retry.call_llm_json_with_retry）"""
        return await call_llm_json_with_retry(
            self.llm,
            prompt,
            max_tokens=max_tokens,
            max_attempts=max_attempts,
            base_delay=base_delay,
            validate=validate,
        )

    # ── 日志工具 ──────────────────────────────────────────

    def _log_start(self, **kwargs: Any) -> None:
        logger.info(
            f"agent_{self.name}_start",
            discipline=self.discipline.value,
            **kwargs,
        )

    def _log_end(self, **kwargs: Any) -> None:
        logger.info(
            f"agent_{self.name}_end",
            discipline=self.discipline.value,
            **kwargs,
        )

    def _log_error(self, err: Exception, **kwargs: Any) -> None:
        logger.error(
            f"agent_{self.name}_error",
            discipline=self.discipline.value,
            error=str(err),
            error_type=type(err).__name__,
            **kwargs,
        )
