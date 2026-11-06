"""Lark/Feishu project task collector."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Any

import httpx

from shared.errors import CollectorError, notify_collector_error
from shared.logger import configure_logging, log_extra


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    title: str
    status: str
    assignee: str
    updated_at: datetime


def collect(params: Mapping[str, Any]) -> list[TaskRecord]:
    """Collect project tasks changed within the requested time range."""
    project_id = _required_text(params, "project_id")
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
        tasks_url = str(params.get("tasks_url", f"{base_url}/open-apis/project/v2/tasks"))

        records: list[TaskRecord] = []
        page_token: str | None = None

        while True:
            request_params = {
                "project_id": project_id,
                "page_size": int(params.get("page_size", 100)),
            }
            if page_token:
                request_params["page_token"] = page_token
            if params.get("since"):
                request_params["updated_since"] = _format_api_datetime(params["since"])
            if params.get("until"):
                request_params["updated_until"] = _format_api_datetime(params["until"])

            response = _request_with_token_refresh(
                http_client,
                tasks_url,
                headers,
                request_params,
                lambda: _get_tenant_token(http_client, base_url, app_id, app_secret, retry_interval),
                retry_interval,
            )
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise CollectorError("Unexpected Lark task response")

            if payload.get("code", 0) != 0:
                raise CollectorError(
                    f"Lark task API error {payload.get('code')}: {payload.get('msg', 'unknown error')}"
                )

            data = payload.get("data", {})
            if not isinstance(data, Mapping):
                data = {}
            items = data.get("items", data.get("tasks", []))
            if not isinstance(items, list):
                raise CollectorError("Lark task response items must be a list")

            records.extend(_to_task_record(item, project_id) for item in items if isinstance(item, Mapping))

            page_token = data.get("page_token") or data.get("next_page_token")
            has_more = data.get("has_more")
            if not page_token or has_more is False:
                break

        return records
    except (CollectorError, httpx.HTTPError, ValueError, KeyError) as exc:
        notify_collector_error(params, exc)
        configure_logging().error(
            "Lark task collection failed",
            exc_info=True,
            extra=log_extra(source="lark_task", project_id=project_id, error=str(exc)),
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
        extra=log_extra(source="lark_task", attempt=1),
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
                "Lark task request timeout; retrying",
                extra=log_extra(source="lark_task", attempt=attempt + 1),
            )
            time.sleep(retry_interval)
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == 2:
                break
            configure_logging().warning(
                "Lark task request failed; retrying",
                extra=log_extra(source="lark_task", attempt=attempt + 1),
            )
            time.sleep(retry_interval)

    raise CollectorError("Lark task API request timed out or failed") from last_error


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
    return _token_from_response(response)


def _token_from_response(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError as exc:
        raise CollectorError("Lark token response is not valid JSON") from exc
    if response.status_code >= 400 or payload.get("code", 0) != 0:
        raise CollectorError(f"Lark token request failed: {payload.get('msg', 'unknown error')}")
    token = payload.get("tenant_access_token") or payload.get("app_access_token")
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
    return payload.get("code") in (
        99991663,
        99991664,
        99991668,
        99991672,
    )


def _to_task_record(payload: Mapping[str, Any], project_id: str) -> TaskRecord:
    task_id = payload.get("task_id") or payload.get("id") or payload.get("task_guid")
    title = payload.get("title") or payload.get("summary") or payload.get("name")
    status = payload.get("status") or payload.get("status_name")
    if isinstance(status, Mapping):
        status = status.get("name") or status.get("key") or status.get("code")

    assignee_data = payload.get("assignee") or payload.get("owner") or payload.get("owners") or []
    if isinstance(assignee_data, Mapping):
        assignee = assignee_data.get("name") or assignee_data.get("display_name") or assignee_data.get("user_id") or assignee_data.get("id")
    elif isinstance(assignee_data, list):
        assignee = ", ".join(
            str(item.get("name") or item.get("display_name") or item.get("user_id"),)
            for item in assignee_data
            if isinstance(item, Mapping)
        )
    else:
        assignee = assignee_data

    updated_value = (
        payload.get("updated_at")
        or payload.get("update_time")
        or payload.get("updated_time")
        or payload.get("modified_at")
    )

    if not task_id or not title or status is None or assignee is None or updated_value is None:
        raise ValueError(f"Incomplete Lark task payload for project {project_id}")

    return TaskRecord(
        task_id=str(task_id),
        title=str(title),
        status=str(status),
        assignee=str(assignee),
        updated_at=_parse_datetime(updated_value),
    )


def _required_text(params: Mapping[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CollectorError(f"Missing required Lark parameter: {key}")
    return value.strip()


def _format_api_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


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
        raise ValueError(f"Invalid Lark task timestamp: {text}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
