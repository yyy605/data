"""Tests for the environment self-check helpers."""

from datetime import datetime, timezone

import httpx
import pytest

import main
from shared.config import AppConfig


class StubLarkClient:
    """Minimal stand-in exposing only what _probe_lark_scope uses."""

    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def get(self, url, params=None):
        return httpx.Response(
            self.status,
            json=self.payload,
            request=httpx.Request("GET", url, params=params),
        )


@pytest.mark.parametrize(
    ("payload", "expected_ok", "expected_fragment"),
    [
        ({"code": 0, "msg": "ok"}, True, "granted"),
        ({"code": 99991672, "msg": "Access denied"}, False, "missing scope task:task:read"),
        ({"code": 99992402, "msg": "field validation failed"}, False, "field validation failed"),
        ({"code": 230002, "msg": "Bot/User can NOT be out of the chat."}, False, "not a member of this chat"),
        ({"code": 99991663, "msg": "invalid token"}, False, "token invalid"),
        ({"code": 12345, "msg": "boom"}, False, "code 12345"),
    ],
)
def test_probe_lark_scope_maps_codes_to_hints(payload, expected_ok, expected_fragment):
    name, ok, detail = main._probe_lark_scope(
        StubLarkClient(payload),
        "lark_scope_task",
        "https://open.feishu.cn/open-apis/task/v2/tasks",
        {"page_size": 1},
        "task:task:read",
    )

    assert name == "lark_scope_task"
    assert ok is expected_ok
    assert expected_fragment in detail


def test_probe_lark_scope_reports_connection_failure():
    class Boom:
        def get(self, url, params=None):
            raise httpx.ConnectError("no route")

    name, ok, detail = main._probe_lark_scope(
        Boom(), "lark_scope_msg", "https://example.invalid", {}, "im:message:readonly"
    )

    assert (name, ok) == ("lark_scope_msg", False)
    assert "connection failed" in detail


def test_params_carry_the_configured_ca_bundle(monkeypatch, tmp_path):
    bundle = tmp_path / "ca-bundle.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("LARK_WEBHOOK_URL", "https://example.invalid/hook")

    def build(network):
        return AppConfig(
            data={
                "network": network,
                "github": {"repositories": [], "token_env": "GITHUB_TOKEN"},
                "lark": {"webhook_url_env": "LARK_WEBHOOK_URL"},
            }
        )

    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    custom = build({"ca_bundle": str(bundle)})
    default = build({})

    assert main._github_params(custom, now, now)["verify"] == str(bundle)
    assert main._lark_bot_target(custom)["verify"] == str(bundle)
    assert main._github_params(default, now, now)["verify"] is True
    assert main._lark_bot_target(default)["verify"] is True
