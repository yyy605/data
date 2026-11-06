import json
import logging
from pathlib import Path

import pytest

from shared.config import load_config
from shared.errors import ConfigurationError
from shared.logger import JsonLineFormatter
from shared.storage import ReportStorage


def test_load_config_reads_example():
    source = Path(__file__).parents[1] / "config.yaml.example"
    config = load_config(source)

    assert config["team"]["name"] == "研发团队"
    assert config["members"][0]["github_login"] == "github-user-1"


def test_load_config_rejects_missing_required_section(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("team:\n  name: Team\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Missing required configuration section"):
        load_config(config_path)


def test_load_config_rejects_missing_member_field(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
team:
  name: Team
github:
  repositories: [repo]
lark: {}
smtp: {}
storage:
  sqlite_path: reports.db
members:
  - github_login: octocat
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="members\\[0\\] missing required field"):
        load_config(config_path)


def test_json_log_formatter_outputs_valid_json():
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="collector completed",
        args=(),
        exc_info=None,
    )
    record.extra_source = "github"

    payload = json.loads(JsonLineFormatter().format(record))

    assert payload["message"] == "collector completed"
    assert payload["source"] == "github"
    assert payload["logger"] == "test_logger"


def test_storage_creates_saves_reads_and_lists_report(tmp_path):
    storage = ReportStorage(tmp_path / "data" / "reports.db")

    report_id = storage.save_report("2026-11-06", "研发团队", "# 日报", "<h1>日报</h1>")
    report = storage.get_report("2026-11-06")
    reports = storage.list_reports()

    assert report_id > 0
    assert report is not None
    assert report["team"] == "研发团队"
    assert report["markdown"] == "# 日报"
    assert reports[0]["report_date"] == "2026-11-06"


def test_storage_upserts_same_report_date(tmp_path):
    storage = ReportStorage(tmp_path / "reports.db")
    storage.save_report("2026-11-06", "研发团队", "old", "<p>old</p>")
    storage.save_report("2026-11-06", "研发团队", "new", "<p>new</p>")

    assert storage.list_reports()[-1]["markdown"] == "new"
