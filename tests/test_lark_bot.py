import httpx

from generator.formatter import generate
from notifier.lark_bot import send


class FakeWebhookClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.closed = False

    def post(self, url, json=None, **kwargs):
        self.requests.append((url, json))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


def ok_response(payload=None):
    return httpx.Response(
        200,
        json=payload if payload is not None else {"code": 0, "msg": "success"},
        request=httpx.Request("POST", "https://open.feishu.cn/webhook"),
    )


def make_report():
    return generate([{"name": "张三"}], "2026-11-06", "研发团队")


def base_target(client):
    return {
        "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
        "client": client,
        "retry_interval": 0,
    }


def test_send_pushes_markdown_card():
    client = FakeWebhookClient([ok_response()])

    assert send(make_report(), base_target(client)) is True

    url, payload = client.requests[0]
    assert url.endswith("/hook/test")
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["title"]["content"] == "研发团队 2026-11-06 日报"
    markdown_element = payload["card"]["elements"][0]
    assert markdown_element["text"]["tag"] == "lark_md"
    assert "### 代码提交" in markdown_element["text"]["content"]


def test_send_retries_twice_then_succeeds():
    client = FakeWebhookClient([
        httpx.ConnectError("boom"),
        ok_response({"code": 19021, "msg": "sign match fail"}),
        ok_response(),
    ])

    assert send(make_report(), base_target(client)) is True
    assert len(client.requests) == 3


def test_send_returns_false_after_three_failures():
    client = FakeWebhookClient([
        httpx.ConnectError("boom"),
        httpx.ConnectError("boom"),
        httpx.ConnectError("boom"),
    ])

    assert send(make_report(), base_target(client)) is False
    assert len(client.requests) == 3


def test_send_returns_false_when_webhook_url_missing():
    assert send(make_report(), {}) is False


def test_send_returns_false_on_http_error_status():
    client = FakeWebhookClient([
        httpx.Response(400, json={"code": 9499}, request=httpx.Request("POST", "https://open.feishu.cn/webhook")),
        httpx.Response(400, json={"code": 9499}, request=httpx.Request("POST", "https://open.feishu.cn/webhook")),
        httpx.Response(400, json={"code": 9499}, request=httpx.Request("POST", "https://open.feishu.cn/webhook")),
    ])

    assert send(make_report(), base_target(client)) is False
