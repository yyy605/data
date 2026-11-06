"""Custom exceptions raised by the application."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class CollectorError(Exception):
    """Raised when external work data cannot be collected."""


class GeneratorError(Exception):
    """Raised when a daily report cannot be generated."""


class NotifierError(Exception):
    """Raised when a report cannot be delivered."""


class ConfigurationError(Exception):
    """Raised when the application configuration is invalid."""


def notify_collector_error(params: Mapping[str, Any], error: Exception) -> None:
    """Invoke the optional ``on_error`` callback supplied by the orchestrator."""
    callback = params.get("on_error")
    if callable(callback):
        callback(error)
