"""Structured JSON Lines logging utilities."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Mapping


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key.startswith("extra_"):
                payload[key.removeprefix("extra_")] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int | str = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("smart_daily_report")
    logger.setLevel(level)
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonLineFormatter())
        logger.addHandler(handler)
    return logger


def log_extra(**values: Any) -> Mapping[str, Any]:
    return {f"extra_{key}": value for key, value in values.items()}
