"""GitHub commit collector."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
import time
from typing import Any

import httpx

from shared.errors import CollectorError, notify_collector_error
from shared.logger import configure_logging, log_extra


@dataclass(frozen=True)
class CommitRecord:
    """A normalized GitHub commit record.

    The seven fields are kept stable because downstream report generation only
    needs commit identity, source repository, authorship, timestamp, and diff size.
    """

    sha: str
    repository: str
    message: str
    author: str
    committed_at: datetime
    additions: int
    deletions: int


def collect(params: Mapping[str, Any]) -> list[CommitRecord]:
    """Collect GitHub commits in the requested repositories and time range."""
    repositories = _normalize_repositories(params.get("repositories", []))
    if not repositories:
        raise CollectorError("At least one GitHub repository is required")

    token = params.get("token") or os.getenv(str(params.get("token_env", "GITHUB_TOKEN")))
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    api_base = str(params.get("base_url", "https://api.github.com")).rstrip("/")
    client = params.get("client")
    owns_client = client is None
    http_client = client or httpx.Client(timeout=float(params.get("timeout", 30)), headers=headers)
    sleep_seconds = float(params.get("retry_interval", 5))

    try:
        records: list[CommitRecord] = []
        for owner, repository in repositories:
            try:
                records.extend(
                    _collect_repository(
                        http_client,
                        api_base,
                        owner,
                        repository,
                        params,
                        sleep_seconds,
                    )
                )
            except (CollectorError, httpx.HTTPError, ValueError, KeyError) as exc:
                notify_collector_error(params, exc)
                configure_logging().error(
                    "GitHub repository collection failed",
                    exc_info=True,
                    extra=log_extra(source="github", repository=f"{owner}/{repository}", error=str(exc)),
                )
        return records
    finally:
        if owns_client and hasattr(http_client, "close"):
            http_client.close()


def _collect_repository(
    client: httpx.Client,
    api_base: str,
    owner: str,
    repository: str,
    params: Mapping[str, Any],
    sleep_seconds: float,
) -> list[CommitRecord]:
    records: list[CommitRecord] = []
    page = 1

    while True:
        query: dict[str, Any] = {"per_page": 100, "page": page}
        since = params.get("since") or params.get("start")
        until = params.get("until") or params.get("end")
        if since:
            query["since"] = _format_api_datetime(since)
        if until:
            query["until"] = _format_api_datetime(until)

        response = _request_with_retry(
            client,
            "GET",
            f"{api_base}/repos/{owner}/{repository}/commits",
            params=query,
            sleep_seconds=sleep_seconds,
        )
        payload = response.json()
        if not isinstance(payload, list):
            raise CollectorError(f"Unexpected GitHub commits response for {owner}/{repository}")
        if not payload:
            break

        for commit in payload:
            sha = str(commit["sha"])
            detail = _request_with_retry(
                client,
                "GET",
                f"{api_base}/repos/{owner}/{repository}/commits/{sha}",
                sleep_seconds=sleep_seconds,
            ).json()
            records.append(_to_commit_record(detail, f"{owner}/{repository}"))

        if not _has_next_page(response):
            break
        if len(payload) == 0:
            break
        page += 1

    return records


def _request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    sleep_seconds: float = 5,
) -> httpx.Response:
    for attempt in range(3):
        try:
            response = client.request(method, url, params=params)
        except httpx.TimeoutException as exc:
            if attempt == 2:
                raise CollectorError(f"GitHub request timed out: {url}") from exc
            configure_logging().warning(
                "GitHub request timeout; retrying",
                extra=log_extra(source="github", url=url, attempt=attempt + 1),
            )
            time.sleep(sleep_seconds)
            continue
        except httpx.HTTPError as exc:
            if attempt == 2:
                raise CollectorError(f"GitHub request failed: {url}") from exc
            configure_logging().warning(
                "GitHub request failed; retrying",
                extra=log_extra(source="github", url=url, attempt=attempt + 1),
            )
            time.sleep(sleep_seconds)
            continue

        if response.status_code == 403 and _is_rate_limited(response):
            if attempt == 2:
                response.raise_for_status()
                raise CollectorError(f"GitHub rate limit remained exceeded: {url}")
            wait_seconds = _rate_limit_wait_seconds(response)
            configure_logging().warning(
                "GitHub rate limit reached; waiting before retry",
                extra=log_extra(source="github", url=url, wait_seconds=wait_seconds),
            )
            time.sleep(wait_seconds)
            continue

        if response.status_code >= 400:
            response.raise_for_status()

        return response

    raise CollectorError(f"GitHub request failed: {url}")


def _to_commit_record(payload: Mapping[str, Any], repository: str) -> CommitRecord:
    commit = payload.get("commit")
    if not isinstance(commit, Mapping):
        raise ValueError("GitHub commit detail is missing commit metadata")

    author_data = commit.get("author") if isinstance(commit.get("author"), Mapping) else {}
    account_data = payload.get("author") if isinstance(payload.get("author"), Mapping) else {}
    message = str(commit.get("message", "")).strip()
    sha = str(payload.get("sha", ""))
    # The GitHub login is preferred because members are mapped by login; a Git
    # author name alone is free-form and cannot be matched reliably.
    author = str(account_data.get("login") or author_data.get("name") or "")
    committer_data = commit.get("committer") if isinstance(commit.get("committer"), Mapping) else {}
    committed_at = _parse_datetime(author_data.get("date") or committer_data.get("date"))
    stats = payload.get("stats") if isinstance(payload.get("stats"), Mapping) else {}

    return CommitRecord(
        sha=sha,
        repository=repository,
        message=message,
        author=author,
        committed_at=committed_at,
        additions=int(stats.get("additions", 0)),
        deletions=int(stats.get("deletions", 0)),
    )


def _normalize_repositories(repositories: Any) -> list[tuple[str, str]]:
    if isinstance(repositories, Mapping):
        repositories = [repositories]
    if not isinstance(repositories, Sequence) or isinstance(repositories, (str, bytes)):
        raise CollectorError("github.repositories must be a list")

    normalized: list[tuple[str, str]] = []
    for repository in repositories:
        if isinstance(repository, Mapping):
            owner = repository.get("owner")
            name = repository.get("name")
        elif isinstance(repository, str) and "/" in repository:
            owner, name = repository.split("/", 1)
        else:
            continue
        if owner and name:
            normalized.append((str(owner), str(name)))
    return normalized


def _format_api_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid GitHub commit timestamp: {text}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_rate_limited(response: httpx.Response) -> bool:
    remaining = response.headers.get("X-RateLimit-Remaining")
    return remaining == "0" or response.status_code == 403


def _rate_limit_wait_seconds(response: httpx.Response) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1, float(retry_after))
        except ValueError:
            parsed = parsedate_to_datetime(retry_after)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(1, parsed.timestamp() - time.time())

    reset = response.headers.get("X-RateLimit-Reset")
    if reset:
        try:
            return max(1, float(reset) - time.time())
    # Kept separate to avoid accidentally using a negative reset value.
        except ValueError:
            pass
    return 60


def _has_next_page(response: httpx.Response) -> bool:
    link = response.headers.get("Link", "")
    return 'rel="next"' in link
