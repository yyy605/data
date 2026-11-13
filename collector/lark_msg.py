"""Lark/Feishu group-message collector."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time
from typing import Any

import httpx

from shared.errors import CollectorError, notify_collector_error
from shared.logger import configure_logging, log_extra


DEFAULT_SENSITIVE_KEYWORDS = ("薪资", "绩效", "工资", "奖金")

# Feishu rejects page_size > 50 on /open-apis/im/v1/messages with
# 99992402 "field validation failed", which hides the real cause.
MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 50

# Only human conversation is useful for the 协作沟通 section. Feishu returns
# system notices, bot cards and media as separate message types.
SUPPORTED_MSG_TYPES = ("text", "post")


@dataclass(frozen=True)
class MessageRecord:
    message_id: str
    sender_id: str
    content: str
    sent_at: datetime


def collect(params: Mapping[str, Any]) -> list[MessageRecord]:
    """Collect and filter project-related messages from a Lark chat."""
    chat_id = _required_text(params, "chat_id")
    app_id = _required_text(params, "app_id")
    app_secret = _required_text(params, "app_secret")

    client = params.get("client")
    owns_client = client is None
    http_client = client or httpx.Client(timeout=float(params.get("timeout", 30)))
    base_url = str(params.get("base_url", "https://open.feishu.cn")).rstrip("/")
    retry_interval = float(params.get("retry_interval", 5))

    try:
        tenant_token = params.get("tenant_access_token") or _get_tenant_token(
            http_client, base_url, app_id, app_secret, retry_interval
        )
        headers = {"Authorization": f"Bearer {tenant_token}", "Content-Type": "application/json"}
        messages_url = str(params.get("messages_url", f"{base_url}/open-apis/im/v1/messages"))
        records: list[MessageRecord] = []
        page_token: str | None = None

        while True:
            request_params = {
                "container_id_type": "chat",
                "container_id": chat_id,
                "page_size": _page_size(params),
            }
            if params.get("since"):
                request_params["start_time"] = _format_api_timestamp(params["since"])
            if params.get("until"):
                request_params["end_time"] = _format_api_timestamp(params["until"])
            if page_token:
                request_params["page_token"] = page_token

            response = _request_with_token_refresh(
                http_client,
                messages_url,
                headers,
                request_params,
                lambda: _get_tenant_token(http_client, base_url, app_id, app_secret, retry_interval),
                retry_interval,
            )
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise CollectorError("Unexpected Lark message response")
            if payload.get("code", 0) != 0:
                raise CollectorError(
                    f"Lark message API error {payload.get('code')}: {payload.get('msg', 'unknown error')}"
                )

            data = payload.get("data", {})
            if not isinstance(data, Mapping):
                data = {}
            items = data.get("items", [])
            if not isinstance(items, list):
                raise CollectorError("Lark message response items must be a list")

            keywords = _normalize_keywords(params.get("keywords", []))
            sensitive_keywords = _normalize_keywords(
                params.get("sensitive_keywords", params.get("excluded_keywords", DEFAULT_SENSITIVE_KEYWORDS))
            )
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                record = _to_message_record(item)
                if record is None:
                    continue
                if _matches_filters(record.content, keywords, sensitive_keywords):
                    records.append(record)

            page_token = data.get("page_token") or data.get("next_page_token")
            if data.get("has_more") is False or not page_token:
                break

        return records
    except (CollectorError, httpx.HTTPError, ValueError, KeyError) as exc:
        notify_collector_error(params, exc)
        configure_logging().error(
            "Lark message collection failed",
            exc_info=True,
            extra=log_extra(source="lark_msg", chat_id=chat_id, error=str(exc)),
        )
        return []
    finally:
        if owns_client and hasattr(http_client, "close"):
            http_client.close()


def _request_with_token_refresh(
    client: httpx.Client,
    url: str,
    headers: Mapping[str, str],
    params: Mapping[str, Any],
    refresh_token,
    retry_interval: float,
) -> httpx.Response:
    current_headers = dict(headers)
    response = _send_with_timeout_retry(client, url, current_headers, params, retry_interval)
    if not _token_expired(response):
        return response

    configure_logging().warning(
        "Lark token expired; refreshing token",
        extra=log_extra(source="lark_msg", attempt=1),
    )
    current_headers["Authorization"] = f"Bearer {refresh_token()}"
    response = _send_with_timeout_retry(client, url, current_headers, params, retry_interval)
    if _token_expired(response):
        raise CollectorError("Lark token refresh retries exhausted")
    return response


def _send_with_timeout_retry(
    client: httpx.Client,
    url: str,
    headers: Mapping[str, str],
    params: Mapping[str, Any],
    retry_interval: float,
    *,
    method: str = "GET",
    json_data: Mapping[str, Any] | None = None,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            if method.upper() == "POST":
                response = client.post(url, headers=headers, json=json_data)
            else:
                response = client.get(url, headers=headers, params=params)
            return response
        except httpx.TimeoutException as exc:
            last_error = exc
            if attempt == 2:
                break
            configure_logging().warning(
                "Lark message request timeout; retrying",
                extra=log_extra(source="lark_msg", attempt=attempt + 1),
            )
            time.sleep(retry_interval)
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == 2:
                break
            configure_logging().warning(
                "Lark message request failed; retrying",
                extra=log_extra(source="lark_msg", attempt=attempt + 1),
            )
            time.sleep(retry_interval)

    raise CollectorError("Lark message API request timed out or failed") from last_error


def _get_tenant_token(
    client: httpx.Client,
    base_url: str,
    app_id: str,
    app_secret: str,
    retry_interval: float,
) -> str:
    url = f"{base_url}/open-apis/auth/v3/tenant_access_token/internal"
    response = _send_with_timeout_retry(
        client,
        url,
        {"Content-Type": "application/json"},
        {},
        retry_interval,
        method="POST",
        json_data={"app_id": app_id, "app_secret": app_secret},
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise CollectorError("Lark token response is not valid JSON") from exc
    if response.status_code >= 400 or payload.get("code", 0) != 0:
        raise CollectorError(f"Lark token request failed: {payload.get('msg', 'unknown error')}")
    token = payload.get("tenant_access_token")
    if not token:
        raise CollectorError("Lark token response did not contain a tenant_access_token")
    return str(token)


def _token_expired(response: httpx.Response) -> bool:
    if response.status_code in (401, 403):
        return True
    try:
        payload = response.json()
    except ValueError:
        return False
    return payload.get("code") in (99991663, 99991664, 99991668)


def _page_size(params: Mapping[str, Any]) -> int:
    """Clamp page_size to the range the Feishu message API accepts."""
    try:
        requested = int(params.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return max(1, min(requested, MAX_PAGE_SIZE))


def _to_message_record(payload: Mapping[str, Any]) -> MessageRecord | None:
    """Convert one Feishu message, or return None when it is not useful content.

    System messages (``msg_type=system``) such as "X started the group chat"
    carry no sender and are not collaboration content, so they are skipped
    instead of failing the whole collection.
    """
    msg_type = str(payload.get("msg_type") or "").strip()
    if msg_type and msg_type not in SUPPORTED_MSG_TYPES:
        return None

    message_id = payload.get("message_id") or payload.get("id") or payload.get("uuid")
    sender_data = payload.get("sender") or payload.get("sender_id") or ""
    if isinstance(sender_data, Mapping):
        sender = sender_data.get("id") or sender_data.get("user_id") or sender_data.get("open_id")
    else:
        sender = sender_data

    body = payload.get("body")
    content_data = body.get("content") if isinstance(body, Mapping) else payload.get("content")
    content = _extract_content(content_data, payload.get("content"), msg_type)
    sent_value = payload.get("create_time") or payload.get("created_at") or payload.get("sent_at") or payload.get("timestamp")

    if not message_id or not sender or not content or sent_value is None:
        return None

    return MessageRecord(
        message_id=str(message_id),
        sender_id=str(sender),
        content=content,
        sent_at=_parse_datetime(sent_value),
    )


def _extract_content(body_content: Any, fallback: Any = None, msg_type: str = "") -> str:
    decoded: Any = None
    if isinstance(body_content, str):
        text = body_content.strip()
        if text.startswith("{"):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = None
        if decoded is None:
            return text
    elif isinstance(body_content, Mapping):
        decoded = body_content
    else:
        if fallback is not None and fallback != body_content:
            return _extract_content(fallback, None, msg_type)
        return ""

    if not isinstance(decoded, Mapping):
        return ""

    text_value = decoded.get("text")
    if text_value is not None:
        return str(text_value).strip()

    # Rich text ("post") nests text runs inside paragraphs.
    return _extract_post_text(decoded)


def _extract_post_text(payload: Mapping[str, Any]) -> str:
    parts: list[str] = []
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        parts.append(title.strip())

    paragraphs = payload.get("content")
    if isinstance(paragraphs, list):
        for paragraph in paragraphs:
            if not isinstance(paragraph, list):
                continue
            for element in paragraph:
                if not isinstance(element, Mapping):
                    continue
                value = element.get("text")
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
    return " ".join(parts).strip()



def _matches_filters(content: str, keywords: tuple[str, ...], sensitive_keywords: tuple[str, ...]) -> bool:
    if any(keyword.lower() in content.lower() for keyword in sensitive_keywords):
        return False
    if not keywords:
        return True
    return any(keyword.lower() in content.lower() for keyword in keywords)


def _normalize_keywords(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return tuple()
    return tuple(str(keyword).strip() for keyword in value if str(keyword).strip())


def _required_text(params: Mapping[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CollectorError(f"Missing required Lark parameter: {key}")
    return value.strip()


def _format_api_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return str(int(value.timestamp()))
    text = str(value)
    return text


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if text.isdigit() and len(text) in (10, 13):
        seconds = float(text) / 1000 if len(text) == 13 else float(text)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid Lark message timestamp: {text}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
