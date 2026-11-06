"""Lark custom-bot webhook notifier."""

from __future__ import annotations

from collections.abc import Mapping
import time
from typing import Any

import httpx

from shared.logger import configure_logging, log_extra


MAX_ATTEMPTS = 3


def send(report: Any, target: Mapping[str, Any]) -> bool:
    """Push the Markdown report to a Lark chat through a custom-bot webhook."""
    logger = configure_logging()
    try:
        webhook_url = _required(target, "webhook_url", "url")
    except ValueError as exc:
        logger.error(
            "Lark webhook configuration invalid",
            exc_info=True,
            extra=log_extra(channel="lark_bot", error=str(exc)),
        )
        return False

    title = str(target.get("title") or _subject(report))
    payload = build_payload(report, title)

    client = target.get("client")
    owns_client = client is None
    http_client = client or httpx.Client(timeout=float(target.get("timeout", 30)))
    retry_interval = float(target.get("retry_interval", 5))

    try:
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = http_client.post(webhook_url, json=payload)
                if response.status_code >= 400:
                    raise ValueError(f"Lark webhook returned HTTP {response.status_code}")
                _raise_for_api_error(response)
                logger.info(
                    "Daily report pushed to Lark",
                    extra=log_extra(channel="lark_bot", attempts=attempt + 1),
                )
                return True
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    logger.error(
                        "Daily report Lark push failed",
                        exc_info=True,
                        extra=log_extra(channel="lark_bot", attempts=attempt + 1, error=str(exc)),
                    )
                    return False
                logger.warning(
                    "Daily report Lark push failed; retrying",
                    extra=log_extra(channel="lark_bot", attempts=attempt + 1, error=str(exc)),
                )
                time.sleep(retry_interval)
        return False
    finally:
        if owns_client and hasattr(http_client, "close"):
            http_client.close()


def build_payload(report: Any, title: str) -> dict[str, Any]:
    markdown = str(_field(report, "markdown", "")).strip()
    if markdown:
        content = markdown
    else:
        content = f"# {title}\n\n今日无记录"

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": content}},
            ],
        },
    }


def _raise_for_api_error(response: Any) -> None:
    try:
        payload = response.json()
    except ValueError:
        return
    if not isinstance(payload, Mapping):
        return
    code = payload.get("code", payload.get("StatusCode", 0))
    if code not in (0, None):
        raise ValueError(f"Lark webhook error {code}: {payload.get('msg', 'unknown error')}")


def _subject(report: Any) -> str:
    return f"{_field(report, 'team', '团队')} {_field(report, 'date', '')} 日报"


def _required(target: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = target.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"Missing required Lark webhook setting: {keys[0]}")


def _field(report: Any, name: str, default: Any) -> Any:
    if isinstance(report, Mapping):
        return report.get(name, default)
    return getattr(report, name, default)
