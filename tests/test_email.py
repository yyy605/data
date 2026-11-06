import smtplib

import pytest

from generator.formatter import generate
from notifier.email import send


class FakeSMTP:
    def __init__(self, failures=0):
        self.failures = failures
        self.sent_messages = []
        self.closed = False

    def login(self, username, password):
        self.username = username
        self.password = password

    def send_message(self, message, from_addr=None, to_addrs=None):
        if self.failures > 0:
            self.failures -= 1
            raise smtplib.SMTPServerDisconnected("server unavailable")
        self.sent_messages.append((message, from_addr, to_addrs))

    def quit(self):
        self.closed = True


def make_report():
    return generate([{"name": "张三"}], "2026-11-06", "研发团队")


def base_target(client):
    return {
        "host": "smtp.example.com",
        "port": 465,
        "username": "bot@example.com",
        "password": "secret",
        "from_address": "bot@example.com",
        "recipients": ["leader@example.com"],
        "retry_interval": 0,
        "smtp_factory": lambda: client,
    }


def test_send_uses_subject_with_date_and_team_and_html_body():
    client = FakeSMTP()

    assert send(make_report(), base_target(client)) is True

    assert len(client.sent_messages) == 1
    message, from_addr, recipients = client.sent_messages[0]
    assert "2026-11-06" in message["Subject"]
    assert "研发团队" in message["Subject"]
    assert from_addr == "bot@example.com"
    assert recipients == ["leader@example.com"]

    html_part = message.get_body(preferencelist=("html",))
    assert html_part is not None
    assert html_part.get_content_type() == "text/html"
    assert "<!doctype html>" in html_part.get_content().lower()

    plain_part = message.get_body(preferencelist=("plain",))
    assert plain_part is not None
    assert "### 代码提交" in plain_part.get_content()


def test_send_retries_twice_after_failure():
    client = FakeSMTP(failures=2)

    assert send(make_report(), base_target(client)) is True
    assert len(client.sent_messages) == 1


def test_send_returns_false_after_all_attempts_fail():
    client = FakeSMTP(failures=3)

    assert send(make_report(), base_target(client)) is False
    assert client.sent_messages == []


def test_send_returns_false_when_recipients_missing():
    target = base_target(FakeSMTP())
    target.pop("recipients")

    assert send(make_report(), target) is False


@pytest.mark.parametrize("field", ["host", "from_address"])
def test_send_returns_false_when_required_setting_missing(field):
    import notifier.email as email_module

    target = base_target(FakeSMTP())
    target.pop(field)
    # Skip the injected factory so the default one validates host/from settings.
    target.pop("smtp_factory")

    assert email_module.send(make_report(), target) is False
