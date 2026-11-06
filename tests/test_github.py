from datetime import datetime
from unittest.mock import Mock, patch

import httpx
import pytest

from collector.github import CommitRecord, collect
from shared.errors import CollectorError


def commit_list_payload(page=1, sha="abc123"):
    return [{"sha": sha if page == 1 else f"{sha}-{page}"}]


def commit_detail_payload(sha="abc123"):
    return {
        "sha": sha,
        "commit": {
            "message": "Add report collector",
            "author": {"name": "Alice", "date": "2026-11-06T09:00:00Z"},
        },
        "author": {"login": "alice"},
        "stats": {"additions": 12, "deletions": 3},
    }


class SequenceClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.closed = False

    def request(self, method, url, params=None):
        self.requests.append((method, url, params))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = False


def response(status=200, json_data=None, headers=None):
    return httpx.Response(
        status,
        json=json_data,
        headers=headers or {},
        request=httpx.Request("GET", "https://api.github.com/test"),
    )


def test_collect_returns_seven_field_commit_record():
    client = SequenceClient([
        response(200, commit_list_payload()),
        response(200, commit_detail_payload()),
    ])

    records = collect({
        "repositories": [{"owner": "example-org", "name": "repo"}],
        "since": "2026-11-06T00:00:00Z",
        "until": "2026-11-06T23:59:59Z",
        "client": client,
    })

    assert len(records) == 1
    record = records[0]
    assert isinstance(record, CommitRecord)
    assert len(record.__dataclass_fields__) == 7
    assert record.repository == "example-org/repo"
    assert record.author == "alice"
    assert record.additions == 12
    assert record.deletions == 3
    assert isinstance(record.committed_at, datetime)
    assert client.requests[0][2]["since"] == "2026-11-06T00:00:00Z"


@pytest.mark.parametrize("delay", [0])
def test_collect_supports_pagination(delay):
    client = SequenceClient([
        response(200, commit_list_payload(), {"Link": '<https://api.github.com/next>; rel="next"'}),
        response(200, commit_detail_payload()),
        response(200, commit_list_payload(page=2, sha="def456"), {}),
        response(200, commit_detail_payload("def456")),
    ])

    records = collect({
        "repositories": ["example-org/repo"],
        "client": client,
        "retry_interval": delay,
    })

    assert [record.sha for record in records] == ["abc123", "def456"]
    assert sum(1 for _, _, params in client.requests if params and params.get("per_page") == 100) == 2


@patch("collector.github.time.sleep")
def test_collect_retries_timeout_three_times_and_returns_empty_list(mock_sleep):
    client = SequenceClient([
        httpx.ReadTimeout("timeout"),
        httpx.ReadTimeout("timeout"),
        httpx.ReadTimeout("timeout"),
    ])

    records = collect({
        "repositories": [{"owner": "example-org", "name": "repo"}],
        "client": client,
        "retry_interval": 5,
    })

    assert records == []
    assert mock_sleep.call_count == 2


def test_collect_requires_repository():
    with pytest.raises(CollectorError, match="repository"):
        collect({"repositories": []})


@patch("collector.github.time.sleep")
def test_collect_retries_rate_limit_using_reset(mock_sleep):
    future_reset = int(__import__("time").time() + 10)
    client = SequenceClient([
        response(403, {"message": "API rate limit exceeded"}, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(future_reset)}),
        response(200, []),
    ])

    records = collect({
        "repositories": [{"owner": "example-org", "name": "repo"}],
        "client": client,
    })

    assert records == []
    assert mock_sleep.call_count == 1
