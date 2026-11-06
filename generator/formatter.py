"""Daily report data models and generation entry point."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from shared.errors import GeneratorError

from .template import render_html, render_markdown


@dataclass
class MemberReport:
    name: str
    commits: list[Any] = field(default_factory=list)
    tasks: list[Any] = field(default_factory=list)
    messages: list[Any] = field(default_factory=list)
    failures: set[str] = field(default_factory=set)


@dataclass
class DailyReport:
    date: str
    team: str
    members: list[MemberReport] = field(default_factory=list)
    markdown: str = ""
    html: str = ""


def generate(members: Sequence[MemberReport] | Sequence[Mapping[str, Any]], date: date | datetime | str, team: str) -> DailyReport:
    """Generate a Markdown and HTML report for a team on one date."""
    if not isinstance(team, str) or not team.strip():
        raise GeneratorError("team is required")

    normalized_date = _normalize_date(date)
    normalized_members = [_normalize_member(member) for member in members]
    report = DailyReport(date=normalized_date, team=team.strip(), members=normalized_members)
    report.markdown = render_markdown(report)
    report.html = render_html(report)
    return report


def _normalize_date(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise GeneratorError(f"Invalid report date: {text}") from exc


def _normalize_member(member: MemberReport | Mapping[str, Any]) -> MemberReport:
    if isinstance(member, MemberReport):
        member.failures = _normalize_failures(member.failures)
        return member
    if not isinstance(member, Mapping):
        raise GeneratorError("members must contain MemberReport objects or mappings")

    name = member.get("name") or member.get("member") or member.get("github_login")
    if not name:
        raise GeneratorError("member.name is required")

    return MemberReport(
        name=str(name).strip(),
        commits=_normalize_items(member, "commits", "github_commits"),
        tasks=_normalize_items(member, "tasks", "lark_tasks", "task_records"),
        messages=_normalize_items(member, "messages", "lark_messages", "message_records"),
        failures=_normalize_failures(member.get("failures", member.get("source_failures", []))),
    )


def _normalize_items(member: Mapping[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = member.get(key)
        if value is None:
            continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return list(value)
        raise GeneratorError(f"member.{key} must be a list")
    return []


def _normalize_failures(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, Mapping):
        return {str(key) for key, failed in value.items() if failed}
    if isinstance(value, str):
        return {value.strip()} if value.strip() else set()
    if isinstance(value, (Sequence, Set)) and not isinstance(value, (str, bytes, bytearray)):
        return {str(item).strip() for item in value if str(item).strip()}
    raise GeneratorError("member failures must be a mapping, string, or list")
