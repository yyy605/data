from datetime import datetime

import httpx
import pytest

from collector.lark_msg import MessageRecord, collect
from shared.errors import CollectorError


def make_response(status=200, json_data=None):
    return httpx.Response(
        status,
        json=json_data,
        headers={},
        request=httpx.Request("GET", "https://open.feishu.cn/test"),
    )


class MessageClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

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
        pass


def base_params(client):
    return {
        "chat_id": "oc_chat",
        "app_id": "cli_test",
        "app_secret": "secret",
        "tenant_access_token": "tenant-token",
        "client": client,
        "messages_url": "https://open.feishu.cn/open-apis/im/v1/messages",
        "retry_interval": 0,
    }


def test_collect_returns_four_field_records_and_filters_keywords():
    payload = {
        "code": 0,
        "data": {
            "items": [
                {
                    "message_id": "msg-1",
                    "sender": {"id": "ou_1"},
                    "body": {"content": '{"text":"项目需求已确认"}'},
                    "create_time": "1791399000000",
                },
                {
                    "message_id": "msg-2",
                    "sender": {"id": "ou_1"},
                    "body": {"content": '{"text":"今天聊了日常安排"}'},
                    "create_time": "1791399600000",
                },
                {
                    "message_id": "msg-3",
                    "sender": {"id": "ou_1"},
                    "body": {"content": '{"text":"项目绩效情况如下"}'},
                    "create_time": "1791400200000",
                },
            ],
            "has_more": False,
        },
    }
    client = MessageClient([make_response(json_data=payload)])

    records = collect({**base_params(client), "keywords": ["项目", "需求"]})

    assert len(records) == 1
    assert isinstance(records[0], MessageRecord)
    assert len(records[0].__dataclass_fields__) == 4
    assert isinstance(records[0].sent_at, datetime)
    assert records[0].message_id == "msg-1"
    assert records[0].sender_id == "ou_1"


def test_collect_without_keywords_returns_non_sensitive_messages():
    payload = {
        "code": 0,
        "data": {
            "items": [
                {"message_id": "msg-1", "sender_id": "ou_1", "content": "项目正常", "sent_at": "2026-11-06T09:00:00Z"},
                {"message_id": "msg-2", "sender_id": "ou_1", "content": "薪资调整讨论", "sent_at": "2026-11-06T09:01:00Z"},
            ],
            "has_more": False,
        },
    }
    client = MessageClient([make_response(json_data=payload)])

    records = collect(base_params(client))

    assert [record.message_id for record in records] == ["msg-1"]


def test_collect_requires_chat_id():
    with pytest.raises(CollectorError, match="chat_id"):
        collect({"app_id": "cli_test", "app_secret": "secret"})


def test_collect_refreshes_expired_token_once():
    client = MessageClient([
        make_response(json_data={"code": 99991663, "msg": "token expired"}),
        make_response(json_data={"code": 0, "tenant_access_token": "fresh-token"}),
        make_response(json_data={"code": 0, "data": {"items": [], "has_more": False}}),
    ])

    records = collect(base_params(client))

    assert records == []
    assert client.requests[-1][1]["Authorization"] == "Bearer fresh-token"


def test_collect_retries_timeout_three_times_and_returns_empty():
    client = MessageClient([
        httpx.ReadTimeout("timeout"),
        httpx.ReadTimeout("timeout"),
        httpx.ReadTimeout("timeout"),
    ])

    records = collect(base_params(client))

    assert records == []
    assert len(client.requests) == 3
