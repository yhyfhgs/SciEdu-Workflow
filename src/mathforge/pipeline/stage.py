"""泛型 Pipeline Stage 协议

提供逐条错误隔离 + 重试的默认 batch 处理。
各阶段按需采用，不强制。
"""

from __future__ import annotations

import time
from typing import Generic, TypeVar

import structlog

logger = structlog.get_logger()

In = TypeVar("In")
Out = TypeVar("Out")


class Stage(Generic[In, Out]):
    """Pipeline 阶段基类

    子类只需实现 process_one，即可获得：
    - 逐条错误隔离（一个 item 失败不影响其他）
    - 可配置重试（默认 1 次）
    - structlog 日志
    """

    stage_name: str = "unnamed"

    async def process_one(self, item: In) -> Out:
        """处理单个条目 — 子类必须实现"""
        raise NotImplementedError

    def fallback(self, item: In, error: Exception) -> Out | None:
        """处理失败时的 fallback — 子类可选覆写，返回 None 表示跳过"""
        return None

    async def process_batch(self, items: list[In], max_retries: int = 1) -> list[Out]:
        """批处理：逐条错误隔离 + 重试"""
        results: list[Out] = []
        for item in items:
            result = await self._process_with_retry(item, max_retries)
            if result is not None:
                results.append(result)
        return results

    async def _process_with_retry(self, item: In, max_retries: int) -> Out | None:
        for attempt in range(max_retries + 1):
            try:
                t0 = time.monotonic()
                result = await self.process_one(item)
                elapsed = time.monotonic() - t0
                logger.debug(
                    "stage_ok",
                    stage=self.stage_name,
                    elapsed=round(elapsed, 3),
                )
                return result
            except Exception as exc:
                if attempt < max_retries:
                    logger.warning(
                        "stage_retry",
                        stage=self.stage_name,
                        attempt=attempt + 1,
                    )
                    continue
                logger.error(
                    "stage_failed",
                    stage=self.stage_name,
                    error=str(exc),
                )
                return self.fallback(item, exc)
        return None
