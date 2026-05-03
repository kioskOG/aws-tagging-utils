"""
Structured logging configuration for AWS Tagging Utilities.

Usage:
    from src.logging_config import get_logger
    logger = get_logger(__name__)

Produces JSON logs in production (LOG_FORMAT=json) and human-readable
text in local dev (LOG_FORMAT=text).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.config import LOG_FORMAT, LOG_LEVEL


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line.

    Compatible with CloudWatch Logs Insights, ELK, Datadog, etc.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Correlation ID (set per-request if available)
        if hasattr(record, "correlation_id"):
            log_entry["correlation_id"] = record.correlation_id

        # Include exception info
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Extra fields attached via `logger.info("msg", extra={...})`
        for key in ("aws_region", "resource_type", "arn_count", "duration_ms"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        return json.dumps(log_entry, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self.FORMAT, datefmt="%H:%M:%S")


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """Return a named logger with the configured formatter.

    Named loggers prevent pollution of the root logger (a common pitfall
    when multiple Lambda handlers share the same runtime).
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level or LOG_LEVEL, logging.INFO))

    # Avoid adding duplicate handlers on repeat calls
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(getattr(logging, level or LOG_LEVEL, logging.INFO))

        if LOG_FORMAT == "json":
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(TextFormatter())

        logger.addHandler(handler)
        logger.propagate = False  # Don't bubble to root logger

    return logger
