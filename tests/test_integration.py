"""End-to-end integration tests for the daily report pipeline."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
import time

import pytest

import main
from collector.github import CommitRecord
from collector.lark_msg import MessageRecord
from collector.lark_task import TaskRecord
from shared.config import AppConfig, load_config
from shared.logger import configure_logging
from shared.storage import ReportStorage

REPORT_DATE = date(2026, 11, 6)

CONFIG_TEMPLATE = """
team:
  name: "研发团队"
  timezone: "Asia/Shanghai"
schedule:
  hour: 18
  minute: 0
members:
  - github_login: "github-user-1"
    lark_user_id: "ou_example_user_1"
    name: "成员一"
    email: "member1@example.com"
  - github_login: "github-user-2"
    lark_user_id: "ou_example_user_2"
    name: "成员二"
    email: "member2@example.com"
github:
  token_env: "GITHUB_TOKEN"
  repositories:
    - owner: "example-org"
      name: "example-repository"
lark:
  app_id_env: "LARK_APP_ID"
  app_secret_env: "LARK_APP_SECRET"
  project_id: "example-project-id"
  chat_id: "oc_example_chat_id"
  webhook_url_env: "LARK_WEBHOOK_URL"
  keywords:
    - "项目"
  excluded_keywords:
    - "薪资"
smtp:
  host_env: "SMTP_HOST"
  port_env: "SMTP_PORT"
  username_env: "SMTP_USERNAME"
  password_env: "SMTP_PASSWORD"
  from_env: "SMTP_FROM"
  recipients:
    - "leader@example.com"
storage:
  sqlite_path: "{sqlite_path}"
"""


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    environment = {
        "GITHUB_TOKEN": "test-token",
        "LARK_APP_ID": "cli_test",
        "LARK_APP_SECRET": "test-secret",
        "LARK_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
        "SMTP_HOST": "smtp.example.com",
        "SMTP_FROM": "bot@example.com",
        "SMTP_PORT": "465",
        "SMTP_USERNAME": "bot@example.com",
        "SMTP_PASSWORD": "test-password",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    sqlite_path = (tmp_path / "data" / "reports.db").as_posix()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_TEMPLATE.format(sqlite_path=sqlite_path), encoding="utf-8")
    return load_config(config_path)


@pytest.fixture
def captured_logs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    logger = configure_logging()
    original_handlers = list(logger.handlers)
    original_propagate = logger.propagate
    # configure_logging() is called inside the pipeline and would reset
    # propagation, so route main's logger through this captured instance.
    logger.handlers = []
    logger.propagate = True
    monkeypatch.setattr(main, "configure_logging", lambda level=logging.INFO: logger)
    caplog.set_level(logging.INFO, logger="smart_daily_report")
    try:
        yield caplog
    finally:
        logger.handlers = original_handlers
        logger.propagate = original_propagate


@pytest.fixture
def pushes(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    recorded: dict[str, list] = {"email": [], "lark_bot": []}

    def fake_send(channel: str):
        def _send(report, target):
            recorded[channel].append((report, target))
            return True

        return _send

    monkeypatch.setattr(main.email, "send", fake_send("email"))
    monkeypatch.setattr(main.lark_bot, "send", fake_send("lark_bot"))
    return recorded


def commit(author: str = "github-user-1") -> CommitRecord:
    return CommitRecord(
        sha="abcdef1234567890",
        repository="example-org/example-repository",
        message="实现日报采集",
        author=author,
        committed_at=datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc),
        additions=10,
        deletions=2,
    )


def task(assignee: str = "ou_example_user_1") -> TaskRecord:
    return TaskRecord(
        task_id="task-1",
        title="完成采集模块",
        status="进行中",
        assignee=assignee,
        updated_at=datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc),
    )


def message(sender: str = "ou_example_user_1") -> MessageRecord:
    return MessageRecord(
        message_id="msg-1",
        sender_id=sender,
        content="项目风险已同步",
        sent_at=datetime(2026, 11, 6, 11, 0, tzinfo=timezone.utc),
    )


def stub_collectors(
    monkeypatch: pytest.MonkeyPatch,
    *,
    github_records=(),
    task_records=(),
    message_records=(),
    github_error: Exception | None = None,
    task_error: Exception | None = None,
    message_error: Exception | None = None,
) -> None:
    def collector(records, error):
        def _collect(params):
            if error is not None:
                raise error
            return list(records)

        return _collect

    monkeypatch.setattr(main, "_collect_github", collector(github_records, github_error))
    monkeypatch.setattr(main, "_collect_lark_task", collector(task_records, task_error))
    monkeypatch.setattr(main, "_collect_lark_msg", collector(message_records, message_error))


def stored_report(config: AppConfig) -> dict | None:
    return ReportStorage(config["storage"]["sqlite_path"]).get_report(REPORT_DATE.isoformat())


def test_normal_flow_generates_and_pushes_report(config, pushes, monkeypatch):
    stub_collectors(
        monkeypatch,
        github_records=[commit()],
        task_records=[task()],
        message_records=[message()],
    )

    assert main.run(config, REPORT_DATE, force=True) == 0

    report = stored_report(config)
    assert report is not None
    assert report["team"] == "研发团队"
    for heading in ("### 代码提交", "### 任务进展", "### 协作沟通"):
        assert heading in report["markdown"]
    assert "实现日报采集" in report["markdown"]
    assert "完成采集模块" in report["markdown"]
    assert "项目风险已同步" in report["markdown"]
    assert report["html"].lower().startswith("<!doctype html>")

    assert len(pushes["email"]) == 1
    assert len(pushes["lark_bot"]) == 1
    assert pushes["email"][0][0].markdown == report["markdown"]
    assert "leader@example.com" in pushes["email"][0][1]["recipients"]


def test_degraded_source_marks_failure_but_keeps_other_sections(config, pushes, monkeypatch, captured_logs):
    stub_collectors(
        monkeypatch,
        github_error=TimeoutError("github timeout"),
        task_records=[task()],
        message_records=[message()],
    )

    assert main.run(config, REPORT_DATE, force=True) == 0

    markdown = stored_report(config)["markdown"]
    assert "数据获取失败" in markdown
    assert "GitHub" in markdown
    assert "完成采集模块" in markdown
    assert "项目风险已同步" in markdown
    assert any(
        record.getMessage() == "Data source failed" and getattr(record, "extra_source", "") == "github"
        for record in captured_logs.records
    )


def test_member_without_records_shows_no_records(config, pushes, monkeypatch):
    stub_collectors(
        monkeypatch,
        github_records=[commit("github-user-1")],
        task_records=[task("ou_example_user_1")],
        message_records=[message("ou_example_user_1")],
    )

    assert main.run(config, REPORT_DATE, force=True) == 0

    markdown = stored_report(config)["markdown"]
    second_member = markdown.split("## 成员二", 1)[1]
    assert "今日无记录" in second_member
    assert "实现日报采集" not in second_member


def test_all_sources_failing_skips_report_and_logs_error(config, pushes, monkeypatch, captured_logs):
    stub_collectors(
        monkeypatch,
        github_error=RuntimeError("github down"),
        task_error=RuntimeError("lark task down"),
        message_error=RuntimeError("lark message down"),
    )

    assert main.run(config, REPORT_DATE, force=True) == 1

    assert stored_report(config) is None
    assert pushes["email"] == []
    assert pushes["lark_bot"] == []
    assert any(
        record.getMessage() == "All data sources failed; no daily report generated"
        for record in captured_logs.records
    )


def test_dry_run_skips_delivery(config, pushes, monkeypatch):
    stub_collectors(monkeypatch, github_records=[commit()])

    assert main.run(config, REPORT_DATE, dry_run=True, force=True) == 0

    assert stored_report(config) is not None
    assert pushes["email"] == []
    assert pushes["lark_bot"] == []


def test_full_flow_completes_well_under_60_seconds(config, pushes, monkeypatch):
    stub_collectors(
        monkeypatch,
        github_records=[commit()],
        task_records=[task()],
        message_records=[message()],
    )

    started = time.monotonic()
    assert main.run(config, REPORT_DATE, force=True) == 0
    elapsed = time.monotonic() - started

    assert elapsed < 60


def test_non_working_day_is_skipped(config, pushes, monkeypatch):
    stub_collectors(monkeypatch, github_records=[commit()])

    assert main.run(config, date(2026, 11, 7)) == 0

    assert stored_report(config) is None
    assert pushes["email"] == []
