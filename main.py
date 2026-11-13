"""Entry point orchestrating collection, generation, storage, and delivery."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, time as dt_time, timezone
import os
import smtplib
import socket
import ssl
import sys
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from collector import github, lark_msg, lark_task
from generator.formatter import MemberReport, generate
from notifier import email, lark_bot
from shared.config import AppConfig, load_config
from shared.errors import ConfigurationError
from shared.logger import configure_logging, log_extra
from shared.storage import ReportStorage, export_report_files

SOURCE_GITHUB = "github"
SOURCE_LARK_TASK = "lark_task"
SOURCE_LARK_MSG = "lark_msg"
ALL_SOURCES = (SOURCE_GITHUB, SOURCE_LARK_TASK, SOURCE_LARK_MSG)

SOURCE_LABELS = {
    SOURCE_GITHUB: "GitHub",
    SOURCE_LARK_TASK: "飞书任务",
    SOURCE_LARK_MSG: "飞书消息",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="智能日报生成器")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--date", help="日报日期（YYYY-MM-DD），默认为今天")
    parser.add_argument("--check", action="store_true", help="仅校验配置与外部服务连通性")
    parser.add_argument("--dry-run", action="store_true", help="执行采集与生成但不推送")
    parser.add_argument("--force", action="store_true", help="非工作日也执行")
    args = parser.parse_args(argv)

    logger = configure_logging()
    try:
        config = load_config(args.config)
    except ConfigurationError as exc:
        logger.error("Configuration invalid", extra=log_extra(error=str(exc)))
        return 1

    if args.check:
        return run_checks(config)

    try:
        report_date = _resolve_date(args.date, config)
    except ConfigurationError as exc:
        logger.error("Invalid report date", exc_info=True, extra=log_extra(error=str(exc)))
        return 1

    return run(config, report_date, dry_run=args.dry_run, force=args.force)


def run(
    config: AppConfig,
    report_date: date,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    """Run the daily report pipeline for one date."""
    logger = configure_logging()
    started_at = datetime.now(timezone.utc)

    if not force and not _is_working_day(report_date, config):
        logger.info(
            "Skipped daily report for non-working day",
            extra=log_extra(report_date=report_date.isoformat()),
        )
        return 0

    team = str(config["team"]["name"])
    logger.info(
        "Daily report started",
        extra=log_extra(started_at=started_at.isoformat(), report_date=report_date.isoformat(), team=team),
    )

    since, until = _collection_window(report_date, config)
    collected, failures = _collect_all(config, since, until)

    counts = {source: len(collected.get(source, [])) for source in ALL_SOURCES}
    logger.info(
        "Collection finished",
        extra=log_extra(
            **{f"count_{source}": counts[source] for source in ALL_SOURCES},
            failed_sources=sorted(failures),
        ),
    )

    if len(failures) == len(ALL_SOURCES):
        logger.error(
            "All data sources failed; no daily report generated",
            extra=log_extra(failed_sources=sorted(failures), report_date=report_date.isoformat()),
        )
        return 1

    members = build_member_reports(config, collected, failures)
    report = generate(members, report_date, team)

    try:
        storage = ReportStorage(_storage_path(config))
        storage.save_report(report.date, report.team, report.markdown, report.html)
    except (OSError, ValueError) as exc:
        logger.error("Daily report could not be stored", exc_info=True, extra=log_extra(error=str(exc)))

    try:
        exported = export_report_files(
            _reports_dir(config), report.date, report.markdown, report.html
        )
        logger.info(
            "Daily report exported to files",
            extra=log_extra(files=[str(path) for path in exported]),
        )
    except OSError as exc:
        # The database copy already exists, so a file export failure must not
        # abort delivery.
        logger.error(
            "Daily report files could not be written",
            exc_info=True,
            extra=log_extra(error=str(exc)),
        )

    if dry_run:
        logger.info(
            "Dry run finished; report generated without delivery",
            extra=log_extra(report_date=report.date, members=len(report.members)),
        )
        _log_finished(started_at, counts, pushed={})
        return 0

    pushed = _push_report(config, report)
    _log_finished(started_at, counts, pushed=pushed)
    return 0 if all(pushed.values()) else 1


def run_checks(config: AppConfig) -> int:
    """Validate configuration and external service connectivity."""
    logger = configure_logging()
    results: list[tuple[str, bool, str]] = []
    results.append(("config", True, "configuration loaded"))

    missing = _missing_environment_variables(config)
    if missing:
        results.append(("environment", False, "missing: " + ", ".join(missing)))
    else:
        results.append(("environment", True, "all referenced variables are set"))

    results.append(_check_github(config))
    results.extend(_check_lark(config))
    results.append(_check_smtp(config))
    results.append(_check_webhook(config))

    for name, ok, detail in results:
        logger.info(
            "Check result",
            extra=log_extra(check=name, ok=ok, detail=detail),
        )

    return 0 if all(ok for _, ok, _ in results) else 1


def build_member_reports(
    config: AppConfig,
    collected: Mapping[str, Sequence[Any]],
    failures: set[str],
) -> list[MemberReport]:
    """Map collected records to the configured team members."""
    members: list[MemberReport] = []
    commits = collected.get(SOURCE_GITHUB, [])
    tasks = collected.get(SOURCE_LARK_TASK, [])
    messages = collected.get(SOURCE_LARK_MSG, [])

    for member in config["members"]:
        keys = _member_keys(member)
        members.append(
            MemberReport(
                name=str(member["name"]),
                commits=[item for item in commits if _matches(keys, getattr(item, "author", ""))],
                tasks=[item for item in tasks if _matches(keys, getattr(item, "assignee", ""))],
                messages=[item for item in messages if _matches(keys, getattr(item, "sender_id", ""))],
                failures=set(failures),
            )
        )
    return members


def _collect_all(
    config: AppConfig,
    since: datetime,
    until: datetime,
) -> tuple[dict[str, list[Any]], set[str]]:
    logger = configure_logging()
    collected: dict[str, list[Any]] = {}
    failures: set[str] = set()

    collectors = (
        (SOURCE_GITHUB, _collect_github, lambda: _github_params(config, since, until)),
        (SOURCE_LARK_TASK, _collect_lark_task, lambda: _lark_params(config, since, until, "tasklist_id", require_id=False)),
        (SOURCE_LARK_MSG, _collect_lark_msg, lambda: _lark_params(config, since, until, "chat_id")),
    )

    for source, collector, params_factory in collectors:
        error_holder: list[Exception] = []
        try:
            params = params_factory()
            params["on_error"] = error_holder.append
            records = list(collector(params))
        except Exception as exc:
            error_holder.append(exc)
            records = []
            logger.error(
                "Data source failed",
                exc_info=True,
                extra=log_extra(source=source, error=str(exc)),
            )
        collected[source] = records
        if error_holder:
            failures.add(source)
            logger.warning(
                "Data source will be marked as failed in the report",
                extra=log_extra(source=source, error=str(error_holder[0])),
            )

    return collected, failures


def _collect_github(params: Mapping[str, Any]) -> list[Any]:
    return github.collect(params)


def _collect_lark_task(params: Mapping[str, Any]) -> list[Any]:
    return lark_task.collect(params)


def _collect_lark_msg(params: Mapping[str, Any]) -> list[Any]:
    return lark_msg.collect(params)


def _push_report(config: AppConfig, report: Any) -> dict[str, bool]:
    logger = configure_logging()
    pushed: dict[str, bool] = {}

    email_target = _email_target(config)
    if email_target is None:
        pushed["email"] = False
        logger.error("Email push skipped due to missing configuration", extra=log_extra(channel="email"))
    else:
        pushed["email"] = email.send(report, email_target)

    bot_target = _lark_bot_target(config)
    if bot_target is None:
        pushed["lark_bot"] = False
        logger.error("Lark push skipped due to missing configuration", extra=log_extra(channel="lark_bot"))
    else:
        pushed["lark_bot"] = lark_bot.send(report, bot_target)

    logger.info("Push finished", extra=log_extra(**{f"push_{key}": value for key, value in pushed.items()}))
    return pushed


def _log_finished(started_at: datetime, counts: Mapping[str, int], pushed: Mapping[str, bool]) -> None:
    finished_at = datetime.now(timezone.utc)
    configure_logging().info(
        "Daily report finished",
        extra=log_extra(
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            duration_seconds=round((finished_at - started_at).total_seconds(), 2),
            **{f"count_{source}": counts.get(source, 0) for source in ALL_SOURCES},
            **{f"push_{key}": value for key, value in pushed.items()},
        ),
    )


def _github_params(config: AppConfig, since: datetime, until: datetime) -> dict[str, Any]:
    section = config["github"]
    token = _env(section, "token_env", "GITHUB_TOKEN", required=True)
    return {
        "repositories": section.get("repositories", []),
        "token": token,
        "since": _api_time(since),
        "until": _api_time(until),
        "base_url": str(section.get("base_url", "https://api.github.com")),
        "retry_interval": float(section.get("retry_interval", 5)),
    }


def _lark_params(
    config: AppConfig,
    since: datetime,
    until: datetime,
    id_field: str,
    *,
    require_id: bool = True,
) -> dict[str, Any]:
    section = config["lark"]
    identifier = section.get(id_field)
    if require_id and not identifier:
        raise ConfigurationError(f"lark.{id_field} is required")

    params: dict[str, Any] = {
        "app_id": _env(section, "app_id_env", "LARK_APP_ID", required=True),
        "app_secret": _env(section, "app_secret_env", "LARK_APP_SECRET", required=True),
        "keywords": list(section.get("keywords", [])),
        "excluded_keywords": list(section.get("excluded_keywords", [])),
        "since": _api_time(since),
        "until": _api_time(until),
        "base_url": str(section.get("base_url", "https://open.feishu.cn")),
        "retry_interval": float(section.get("retry_interval", 5)),
    }
    if identifier:
        params[id_field] = str(identifier)
    return params


def _email_target(config: AppConfig) -> dict[str, Any] | None:
    section = config["smtp"]
    recipients = section.get("recipients") or []
    try:
        host = _env(section, "host_env", "SMTP_HOST", required=True)
        from_address = _env(section, "from_env", "SMTP_FROM", required=True)
        port_text = _env(section, "port_env", "SMTP_PORT", required=False)
        port = int(port_text) if port_text else 465
    except (ConfigurationError, ValueError):
        return None

    if not recipients:
        return None

    return {
        "host": host,
        "port": port,
        "username": _env(section, "username_env", "SMTP_USERNAME", required=False),
        "password": _env(section, "password_env", "SMTP_PASSWORD", required=False),
        "from_address": from_address,
        "recipients": list(recipients),
        "use_ssl": bool(section.get("use_ssl", port == 465)),
        "use_starttls": bool(section.get("use_starttls", False)),
        "retry_interval": float(section.get("retry_interval", 5)),
    }


def _lark_bot_target(config: AppConfig) -> dict[str, Any] | None:
    section = config["lark"]
    try:
        webhook_url = _env(section, "webhook_url_env", "LARK_WEBHOOK_URL", required=True)
    except ConfigurationError:
        return None
    return {"webhook_url": webhook_url, "retry_interval": float(section.get("retry_interval", 5))}


def _env(section: Mapping[str, Any], key: str, default: str, *, required: bool) -> str | None:
    name = str(section.get(key) or default)
    value = os.getenv(name)
    if not value:
        if required:
            raise ConfigurationError(f"Environment variable {name} is not set")
        return None
    return value


def _missing_environment_variables(config: AppConfig) -> list[str]:
    missing: list[str] = []
    references = (
        (config["github"], "token_env", "GITHUB_TOKEN"),
        (config["lark"], "app_id_env", "LARK_APP_ID"),
        (config["lark"], "app_secret_env", "LARK_APP_SECRET"),
        (config["lark"], "webhook_url_env", "LARK_WEBHOOK_URL"),
        (config["smtp"], "host_env", "SMTP_HOST"),
        (config["smtp"], "from_env", "SMTP_FROM"),
    )
    for section, key, default in references:
        name = str(section.get(key) or default)
        if not os.getenv(name):
            missing.append(name)
    return missing


def _check_github(config: AppConfig) -> tuple[str, bool, str]:
    section = config["github"]
    token = os.getenv(str(section.get("token_env") or "GITHUB_TOKEN"))
    if not token:
        return ("github", False, "missing GitHub token")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    base_url = str(section.get("base_url", "https://api.github.com")).rstrip("/")
    try:
        with httpx.Client(timeout=15, headers=headers) as client:
            response = client.get(f"{base_url}/rate_limit")
    except httpx.HTTPError as exc:
        return ("github", False, f"connection failed: {exc}")
    return ("github", response.status_code < 400, f"HTTP {response.status_code}")


def _check_lark(config: AppConfig) -> list[tuple[str, bool, str]]:
    """Check the tenant token plus each scope the collectors actually need."""
    section = config["lark"]
    app_id = os.getenv(str(section.get("app_id_env") or "LARK_APP_ID"))
    app_secret = os.getenv(str(section.get("app_secret_env") or "LARK_APP_SECRET"))
    if not app_id or not app_secret:
        return [("lark", False, "missing Lark app credentials")]

    base_url = str(section.get("base_url", "https://open.feishu.cn")).rstrip("/")
    try:
        with httpx.Client(timeout=15) as client:
            response = client.post(
                f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
    except httpx.HTTPError as exc:
        return [("lark", False, f"connection failed: {exc}")]
    if response.status_code >= 400:
        return [("lark", False, f"HTTP {response.status_code}")]
    try:
        payload = response.json()
    except ValueError:
        return [("lark", False, "invalid JSON response")]

    token = payload.get("tenant_access_token")
    if payload.get("code", -1) != 0 or not token:
        return [("lark", False, str(payload.get("msg", "token missing")))]

    results: list[tuple[str, bool, str]] = [("lark", True, "tenant_access_token acquired")]
    headers = {"Authorization": f"Bearer {token}"}

    try:
        with httpx.Client(timeout=15, headers=headers) as client:
            results.append(
                _probe_lark_scope(
                    client,
                    "lark_scope_task",
                    f"{base_url}/open-apis/task/v2/tasks",
                    {"page_size": 1},
                    "task:task:read",
                )
            )

            chat_id = str(section.get("chat_id") or "").strip()
            if not chat_id:
                results.append(("lark_scope_msg", False, "lark.chat_id is not configured"))
            else:
                results.append(
                    _probe_lark_scope(
                        client,
                        "lark_scope_msg",
                        f"{base_url}/open-apis/im/v1/messages",
                        {"container_id_type": "chat", "container_id": chat_id, "page_size": 1},
                        "im:message:readonly",
                    )
                )

            # The chat-list scope is only needed to discover chat_id; when it is
            # already configured this permission is optional.
            if chat_id:
                results.append(
                    ("lark_scope_chat", True, "not required (lark.chat_id is configured)")
                )
            else:
                results.append(
                    _probe_lark_scope(
                        client,
                        "lark_scope_chat",
                        f"{base_url}/open-apis/im/v1/chats",
                        {"page_size": 1},
                        "im:chat:readonly",
                    )
                )
    except httpx.HTTPError as exc:
        results.append(("lark_scope", False, f"connection failed: {exc}"))

    return results


def _probe_lark_scope(
    client: httpx.Client,
    name: str,
    url: str,
    params: Mapping[str, Any],
    required_scope: str,
) -> tuple[str, bool, str]:
    """Turn a Feishu error code into an actionable hint about the missing scope."""
    try:
        response = client.get(url, params=params)
    except httpx.HTTPError as exc:
        return (name, False, f"connection failed: {exc}")

    try:
        payload = response.json()
    except ValueError:
        return (name, False, f"HTTP {response.status_code}, invalid JSON")

    code = payload.get("code", -1)
    if code == 0:
        return (name, True, "granted")

    message = str(payload.get("msg", ""))
    if code == 99991672:
        return (name, False, f"missing scope {required_scope} (app identity)")
    if code == 99992402:
        return (name, False, f"field validation failed: {message[:80]}")
    if code == 230002:
        return (name, False, "bot is not a member of this chat; add the app to the group")
    if code in (99991663, 99991664, 99991668):
        return (name, False, f"token invalid: {message[:80]}")
    return (name, False, f"code {code}: {message[:80]}")


def _check_smtp(config: AppConfig) -> tuple[str, bool, str]:
    target = _email_target(config)
    if target is None:
        return ("smtp", False, "missing SMTP configuration")

    host, port = target["host"], target["port"]
    try:
        if target.get("use_ssl"):
            client = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(host, port, timeout=15)
        try:
            if target.get("username") and target.get("password"):
                client.login(str(target["username"]), str(target["password"]))
        finally:
            client.quit()
    except (smtplib.SMTPException, OSError) as exc:
        return ("smtp", False, f"connection failed: {exc}")
    return ("smtp", True, f"{host}:{port} reachable")


def _check_webhook(config: AppConfig) -> tuple[str, bool, str]:
    section = config["lark"]
    webhook_url = os.getenv(str(section.get("webhook_url_env") or "LARK_WEBHOOK_URL"))
    if not webhook_url:
        return ("lark_webhook", False, "missing LARK_WEBHOOK_URL")
    parsed = urlparse(webhook_url)
    if not parsed.hostname:
        return ("lark_webhook", False, "invalid webhook URL")
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 443), timeout=10):
            pass
    except OSError as exc:
        return ("lark_webhook", False, f"connection failed: {exc}")
    return ("lark_webhook", True, f"{parsed.hostname} reachable")


def _resolve_date(value: str | None, config: AppConfig) -> date:
    if not value:
        return datetime.now(_timezone(config)).date()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigurationError(f"Invalid --date value: {value}") from exc


def _timezone(config: AppConfig) -> ZoneInfo:
    name = str(config["team"].get("timezone", "Asia/Shanghai"))
    try:
        return ZoneInfo(name)
    except Exception as exc:
        raise ConfigurationError(f"Invalid team.timezone: {name}") from exc


def _collection_window(report_date: date, config: AppConfig) -> tuple[datetime, datetime]:
    """Return a rolling 24 hour window ending at the report cut-off time."""
    tz = _timezone(config)
    now = datetime.now(tz)
    if report_date >= now.date():
        end = now
    else:
        end = datetime.combine(report_date, dt_time(23, 59, 59), tzinfo=tz)
    return end - timedelta(hours=24), end


def _is_working_day(report_date: date, config: AppConfig) -> bool:
    """Decide whether a report should run for the given date.

    ``schedule.holidays`` / ``schedule.workdays`` act as manual overrides so an
    organisation can correct the calendar; otherwise the Chinese holiday
    calendar library decides, falling back to weekday detection when the
    library has no data for that year.
    """
    schedule = config.get("schedule") or {}
    if not isinstance(schedule, Mapping):
        schedule = {}

    iso = report_date.isoformat()
    if iso in {str(item) for item in schedule.get("workdays", [])}:
        return True
    if iso in {str(item) for item in schedule.get("holidays", [])}:
        return False

    try:
        import chinese_calendar

        return bool(chinese_calendar.is_workday(report_date))
    except Exception:
        configure_logging().warning(
            "Holiday calendar unavailable; falling back to weekday detection",
            extra=log_extra(report_date=iso),
        )
        return not (bool(schedule.get("skip_weekends", True)) and report_date.weekday() >= 5)


def _member_keys(member: Mapping[str, Any]) -> set[str]:
    values = (
        member.get("github_login"),
        member.get("lark_user_id"),
        member.get("name"),
        member.get("email"),
    )
    return {str(value).strip().casefold() for value in values if value}


def _matches(keys: set[str], value: Any) -> bool:
    if not value:
        return False
    candidates = str(value).split(",") if "," in str(value) else [str(value)]
    return any(candidate.strip().casefold() in keys for candidate in candidates)


def _storage_path(config: AppConfig) -> str:
    return str(config["storage"]["sqlite_path"])


def _reports_dir(config: AppConfig) -> str:
    section = config["storage"]
    return str(section.get("reports_dir") or "reports")


def _api_time(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


if __name__ == "__main__":
    sys.exit(main())
