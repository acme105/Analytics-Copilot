"""Structured JSON logging: one line per event, with request id, latency and usage."""

from __future__ import annotations

import json
import logging

_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Format a record as JSON, including any ``extra=`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload |= {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Send the package's logs to stderr as JSON lines."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("analytics_copilot")
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
