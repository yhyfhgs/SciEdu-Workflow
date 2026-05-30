"""structlog 结构化日志配置"""

import logging
import sys
from pathlib import Path
from typing import Any, TextIO

import structlog


def setup_logging(
    level: str = "INFO",
    format: str = "console",
    log_file: Path | None = None,
) -> None:
    """初始化结构化日志。

    Args:
        level: 日志级别
        format: 输出格式 (console | json)
        log_file: 如果提供，同时写入该文件
    """
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if format == "json":
        renderer: Any = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # 日志工厂：stdout + 可选文件
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handle: TextIO = open(log_file, "a", encoding="utf-8")  # noqa: SIM115

        class _TeeLogger:
            def __init__(self, fh: TextIO) -> None:
                self._fh = fh

            def msg(self, message: str) -> None:
                sys.stderr.write(message + "\n")
                sys.stderr.flush()
                self._fh.write(message + "\n")
                self._fh.flush()

            log = debug = info = warning = error = critical = fatal = msg

        class _TeeLoggerFactory:
            """同时输出到 stderr 和文件"""

            def __call__(self, *args: object, **kwargs: object) -> _TeeLogger:
                return _TeeLogger(file_handle)

        logger_factory: Any = _TeeLoggerFactory()
    else:
        logger_factory = structlog.PrintLoggerFactory()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        logger_factory=logger_factory,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
    )
