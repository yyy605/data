from datetime import datetime, timezone

import httpx
import pytest

from collector.lark_task import TaskRecord, collect
from shared.errors import CollectorError


def make_response(status=200, json_data=None):
    return httpx.Response(
        status,
        json=json_data,
        headers={},
        request=httpx.Request("GET", "https://open.feishu.cn/test"),
    )


class TaskClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def get(self, url, headers=None, params=None, **kwargs):
        self.requests.append(("GET", url, dict(headers or {}), params or {}))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url, headers=None, json=None, **kwargs):
        self.requests.append(("POST", url, dict(headers or {}), json or {}))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


def base_params(client):
    return {
        "app_id": "cli_test",
        "app_secret": "secret",
        "tenant_access_token": "tenant-token",
        "client": client,
        "retry_interval": 0,
    }


def test_collect_uses_task_v2_endpoint_and_maps_fields():
    payload = {
        "code": 0,
        "data": {
            "items": [{
                "id": "task-guid-1",
                "summary": "实现日报采集",
                "completed_at": "0",
                "updated_at": "1791399000000",
                "members": [
                    {"id": "ou_1", "name": "张三", "role": "assignee"},
                    {"id": "ou_2", "name": "李四", "role": "follower"},
                ],
            }],
            "has_more": False,
        },
    }
    client = TaskClient([make_response(json_data=payload)])

    records = collect(base_params(client))

    assert len(records) == 1
    record = records[0]
    assert isinstance(record, TaskRecord)
    assert len(record.__dataclass_fields__) == 5
    assert record.task_id == "task-guid-1"
    assert record.title == "实现日报采集"
    assert record.status == "进行中"
    assert record.assignee == "张三"
    assert isinstance(record.updated_at, datetime)

    method, url, _, params = client.requests[0]
    assert method == "GET"
    assert url.endswith("/open-apis/task/v2/tasks")
    assert params["page_size"] == 50
    assert params["user_id_type"] == "open_id"
    assert "project_id" not in params


def test_collect_marks_completed_task():
    payload = {
        "code": 0,
        "data": {
            "items": [{
                "id": "task-2",
                "summary": "已完成任务",
                "completed_at": "1791399000000",
                "updated_at": "1791399000000",
                "members": [{"id": "ou_1", "role": "assignee"}],
            }],
            "has_more": False,
        },
    }
    client = TaskClient([make_response(json_data=payload)])

    records = collect(base_params(client))

    assert records[0].status == "已完成"
    assert records[0].assignee == "ou_1"


def test_collect_filters_out_tasks_outside_window():
    inside_ts = int(datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    outside_ts = int(datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    payload = {
        "code": 0,
        "data": {
            "items": [
                {
                    "id": "inside",
                    "summary": "窗口内",
                    "updated_at": str(inside_ts),
                    "members": [{"id": "ou_1", "role": "assignee"}],
                },
                {
                    "id": "outside",
                    "summary": "窗口外",
                    "updated_at": str(outside_ts),
                    "members": [{"id": "ou_1", "role": "assignee"}],
                },
            ],
            "has_more": False,
        },
    }
    client = TaskClient([make_response(json_data=payload)])

    records = collect({
        **base_params(client),
        "since": datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc),
        "until": datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc),
    })

    assert [record.task_id for record in records] == ["inside"]


def test_collect_filters_by_tasklist_id():
    payload = {
        "code": 0,
        "data": {
            "items": [
                {
                    "id": "in-list",
                    "summary": "指定清单",
                    "updated_at": "1791399000000",
                    "tasklists": [{"tasklist_guid": "tl-1"}],
                    "members": [{"id": "ou_1", "role": "assignee"}],
                },
                {
                    "id": "other-list",
                    "summary": "其他清单",
                    "updated_at": "1791399000000",
                    "tasklists": [{"tasklist_guid": "tl-2"}],
                    "members": [{"id": "ou_1", "role": "assignee"}],
                },
            ],
            "has_more": False,
        },
    }
    client = TaskClient([make_response(json_data=payload)])

    records = collect({**base_params(client), "tasklist_id": "tl-1"})

    assert [record.task_id for record in records] == ["in-list"]


def test_collect_requires_app_credentials():
    with pytest.raises(CollectorError, match="app_id"):
        collect({"client": TaskClient([])})


def test_collect_refreshes_expired_token_once():
    client = TaskClient([
        make_response(json_data={"code": 99991663, "msg": "token expired"}),
        make_response(json_data={"code": 0, "tenant_access_token": "fresh-token"}),
        make_response(json_data={"code": 0, "data": {"items": [], "has_more": False}}),
    ])

    records = collect(base_params(client))

    assert records == []
    assert client.requests[-1][2]["Authorization"] == "Bearer fresh-token"


def test_collect_retries_timeout_three_times_and_returns_empty(monkeypatch):
    client = TaskClient([
        httpx.ReadTimeout("timeout"),
        httpx.ReadTimeout("timeout"),
        httpx.ReadTimeout("timeout"),
    ])
    monkeypatch.setattr("collector.lark_task.time.sleep", lambda _: None)

    records = collect(base_params(client))

    assert records == []
    assert len(client.requests) == 3
