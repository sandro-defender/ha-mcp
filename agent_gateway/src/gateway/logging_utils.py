"""Logging setup with a redaction filter.

Log records never intentionally carry request headers or bodies.  The filter
is a second line of defence: it rewrites the fully formatted message and
arguments, and replaces exception tracebacks with a sanitized type/message so
that connector responses or credentials cannot hitch a ride into the log.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from .redaction import redact_text

ACCESS_LOGGER_NAMES = ("uvicorn.access", "uvicorn.error", "uvicorn")


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive for malformed records
            message = str(record.msg)
        record.msg = redact_text(message)
        record.args = ()

        if record.exc_info:
            exc_type, exc_value, _ = record.exc_info
            safe_message = redact_text(str(exc_value)) if exc_value is not None else ""
            record.exc_info = None
            record.exc_text = None
            record.msg = (
                f"{record.msg} [exception {exc_type.__name__ if exc_type else 'unknown'}: {safe_message}]"
            )

        if record.stack_info:
            record.stack_info = None
        return True


class DropAccessLogFilter(logging.Filter):
    """Drop access-log records entirely; the gateway writes audit rows instead."""

    def filter(self, record: logging.LogRecord) -> bool:
        return False


def configure_logging(level: str = "info") -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)sZ %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric)

    for name in ACCESS_LOGGER_NAMES:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(numeric)
        if name == "uvicorn.access":
            logger.addFilter(DropAccessLogFilter())

    # httpx logs request URLs at INFO; keep it quiet to avoid leaking internal
    # hostnames into ordinary logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncssh").setLevel(logging.WARNING)


def safe_for_log(value: Any, limit: int = 200) -> str:
    text = redact_text(str(value))
    return text if len(text) <= limit else text[: limit - 3] + "..."
