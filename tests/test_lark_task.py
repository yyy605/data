from datetime import datetime

import httpx

from collector.lark_task import TaskRecord, collect
from shared.errors import CollectorError


def make_response(status=200, json_data=None, request_url="https://open.feishu.cn/test", headers=None):
    return httpx.Response(
        status,
        json=json_data,
        headers=headers or {},
        request=httpx.Request("GET", request_url),
    )


class TaskClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.closed = False

    def get(self, url, headers=None, params=None, **kwargs):
        self.requests.append((url, dict(headers or {}), params or {}))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url, headers=None, json=None, **kwargs):
        self.requests.append((url, dict(headers or {}), json or {}))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = False


def base_params(client, task_response=None):
    params = {
        "project_id": "project-1",
        "app_id": "cli_test",
        "app_secret": "secret",
        "tenant_access_token": "tenant-token",
        "client": client,
        "tasks_url": "https://open.feishu.cn/open-apis/project/v2/tasks",
        "retry_interval": 0,
    }
    if task_response is not None:
        params["task_response"] = task_response
    return params


def test_collect_returns_five_field_task_records_and_paginates():
    first = {
        "code": 0,
        "data": {
            "items": [{
                "task_id": "task-1",
                "title": "实现日报采集",
                "status": {"name": "进行中"},
                "assignee": {"name": "张三"},
                "updated_at": "2026-11-06T09:30:00+08:00",
            }],
            "page_token": "next-page",
            "has_more": True,
        },
    }
    second = {
        "code": 0,
        "data": {
            "items": [{
                "id": "task-2",
                "summary": "编写接口测试",
                "status_name": "已完成",
                "owners": [{"user_id": "ou_2"}],
                "update_time": 1791399000000,
            }],
            "has_more": False,
        },
    }
    client = TaskClient([make_response(json_data=first), make_response(json_data=second)])

    records = collect(base_params(client))

    assert len(records) == 2
    assert isinstance(records[0], TaskRecord)
    assert len(records[0].__dataclass_fields__) == 5
    assert isinstance(records[0].updated_at, datetime)
    assert records[0].task_id == "task-1"
    assert records[0].assignee == "张三"
    assert records[1].assignee == "ou_2"
    assert client.requests[1][2]["page_token"] == "next-page"


def test_collect_requires_project_id():
    client = TaskClient([])
    try:
        collect({
            "app_id": "cli_test",
            "app_secret": "secret",
            "tenant_access_token": "tenant-token",
            "client": client,
        })
    except CollectorError as exc:
        assert "project_id" in str(exc)


def test_collect_refreshes_expired_token_once():
    expired = {"code": 99991663, "msg": "token expired"}
    task_data = {
        "code": 0,
        "data": {"items": [{
            "task_id": "task-3",
            "title": "刷新令牌",
            "status": "进行中",
            "assignee": "李四",
            "updated_at": "2026-11-06T10:00:00+08:00",
        }]},
    }
    token_data = {"code": 0, "tenant_access_token": "fresh-token"}
    client = TaskClient([
        make_response(json_data=expired),
        make_response(json_data=token_data),
        make_response(json_data=task_data),
    ])

    records = collect({
        "project_id": "project-1",
        "app_id": "cli_test",
        "app_secret": "secret",
        "tenant_access_token": "old-token",
        "client": client,
        "tasks_url": "https://open.feishu.cn/open-apis/project/v2/tasks",
        "retry_interval": 0,
    })

    assert [record.task_id for record in records] == ["task-3"]
    assert client.requests[-1][1]["Authorization"] == "Bearer fresh-token"


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
