"""Deterministic JSON logging with request correlation injection."""

from __future__ import annotations

import logging
import sys
from typing import TextIO

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from hennongxi_observability.correlation import current_correlation_id

_UNSAFE_ACCESS_LOGGERS = frozenset({"httpcore", "httpcore2", "httpx", "httpx2", "uvicorn.access"})


class _DropUnsafeAccessLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not any(
            record.name == name or record.name.startswith(f"{name}.")
            for name in _UNSAFE_ACCESS_LOGGERS
        )


def _add_correlation_id(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    correlation_id = current_correlation_id()
    if correlation_id is not None:
        event_dict["correlation_id"] = correlation_id
    return event_dict


def configure_logging(*, stream: TextIO | None = None) -> None:
    """Configure stdlib and structlog once per service process."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=stream or sys.stdout,
        force=True,
    )
    for logger_name in _UNSAFE_ACCESS_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    for handler in logging.getLogger().handlers:
        handler.addFilter(_DropUnsafeAccessLogs())
    processors: list[Processor] = [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        _add_correlation_id,
        structlog.processors.JSONRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
