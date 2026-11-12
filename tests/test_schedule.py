"""Tests for schedule handling: rolling window and working-day detection."""

from datetime import date, datetime, timedelta

import pytest

import main
from shared.config import AppConfig


def make_config(**schedule) -> AppConfig:
    return AppConfig(data={
        "team": {"name": "研发团队", "timezone": "Asia/Shanghai"},
        "schedule": schedule,
        "members": [{"github_login": "u1", "lark_user_id": "ou1", "name": "成员", "email": "a@b.c"}],
        "github": {"repositories": ["o/r"]},
        "lark": {},
        "smtp": {},
        "storage": {"sqlite_path": "unused.db"},
    })


@pytest.mark.parametrize("report_date", [date(2026, 11, 6), date(2026, 3, 2)])
def test_collection_window_is_rolling_24_hours(report_date):
    since, until = main._collection_window(report_date, make_config())

    assert until - since == timedelta(hours=24)
    assert until.tzinfo is not None


def test_collection_window_for_today_ends_now():
    config = make_config()
    today = datetime.now(main._timezone(config)).date()

    since, until = main._collection_window(today, config)

    assert until - since == timedelta(hours=24)
    assert (datetime.now(main._timezone(config)) - until).total_seconds() < 60


def test_collection_window_for_past_date_ends_at_day_end():
    since, until = main._collection_window(date(2026, 11, 6), make_config())

    assert until.hour == 23 and until.minute == 59
    assert since == until - timedelta(hours=24)


def test_weekend_is_skipped():
    config = make_config()

    assert main._is_working_day(date(2026, 11, 7), config) is False  # Saturday
    assert main._is_working_day(date(2026, 11, 8), config) is False  # Sunday
    assert main._is_working_day(date(2026, 11, 9), config) is True   # Monday


def test_legal_holiday_is_skipped_via_calendar_library():
    config = make_config()

    # 2026-01-01 is New Year's Day, a legal holiday.
    assert main._is_working_day(date(2026, 1, 1), config) is False


def test_make_up_workday_is_treated_as_working_day():
    config = make_config()

    # 2026-10-10 is a make-up working Saturday for the National Day holiday.
    assert main._is_working_day(date(2026, 10, 10), config) is True


def test_manual_holidays_override_calendar():
    config = make_config(holidays=["2026-11-12"])

    assert main._is_working_day(date(2026, 11, 12), config) is False


def test_manual_workdays_override_calendar():
    config = make_config(workdays=["2026-11-07"])

    assert main._is_working_day(date(2026, 11, 7), config) is True


def test_falls_back_to_weekday_detection_when_calendar_missing(monkeypatch):
    config = make_config()

    def boom(_date):
        raise ImportError("no calendar data")

    monkeypatch.setattr("chinese_calendar.is_workday", boom, raising=False)

    assert main._is_working_day(date(2026, 11, 9), config) is True   # Monday
    assert main._is_working_day(date(2026, 11, 7), config) is False  # Saturday
