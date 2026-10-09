"""
Structured logging configuration for AWS Tagging Utilities.

Usage:
    from src.logging_config import get_logger
    logger = get_logger(__name__)

Produces JSON logs in production (LOG_FORMAT=json) and human-readable
text in local dev (LOG_FORMAT=text).

All loggers (ours, Flask/werkzeug, gunicorn, botocore) share a single root
handler so formatting and request correlation IDs are applied uniformly.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.config import LOG_FORMAT, LOG_LEVEL

# Attributes present on every LogRecord; anything else came from `extra={...}`
_STD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


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

        # Include exception info
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Extra fields attached via `logger.info("msg", extra={...})` and the request-ID filter
        for key, val in record.__dict__.items():
            if key not in _STD_ATTRS and not key.startswith("_") and val is not None:
                log_entry[key] = val

        return json.dumps(log_entry, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self.FORMAT, datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        out = super().format(record)
        cid = getattr(record, "correlation_id", None)
        return f"{out} [req={cid}]" if cid else out


_configured = False
_config_lock = threading.Lock()


def configure_logging() -> None:
    """Install one stdout handler on the root logger (idempotent)."""
    global _configured
    if _configured:
        return
    with _config_lock:
        if _configured:
            return
        root = logging.getLogger()
        level = getattr(logging, LOG_LEVEL, logging.INFO)
        root.setLevel(level)

        # LOG_STREAM=stderr is required for stdio protocols (the MCP server), where stdout
        # carries JSON-RPC and any stray log line would corrupt the stream.
        stream = sys.stderr if os.environ.get("LOG_STREAM", "stdout").lower() == "stderr" else sys.stdout
        handler = logging.StreamHandler(stream)
        handler.setLevel(level)
        handler.setFormatter(JSONFormatter() if LOG_FORMAT == "json" else TextFormatter())
        handler._tagging_utils = True  # type: ignore[attr-defined]

        # Replace only handlers we installed previously; keep others (e.g. pytest caplog)
        for h in list(root.handlers):
            if getattr(h, "_tagging_utils", False) or isinstance(h, logging.StreamHandler) and h.stream in (sys.stdout, sys.stderr):
                root.removeHandler(h)
        root.addHandler(handler)

        # botocore/urllib3 are very chatty at INFO
        for noisy in ("botocore", "boto3", "urllib3", "s3transfer"):
            logging.getLogger(noisy).setLevel(max(level, logging.WARNING))
        _configured = True


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """Return a named logger that propagates to the shared root handler."""
    configure_logging()
    logger = logging.getLogger(name)
    if level:
        logger.setLevel(getattr(logging, level, logging.INFO))
    return logger
