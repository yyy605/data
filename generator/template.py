"""Markdown and HTML templates for daily reports."""

from __future__ import annotations

from datetime import datetime
from html import escape
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .formatter import DailyReport, MemberReport

SOURCE_LABELS = {
    "github": "GitHub",
    "lark_task": "飞书任务",
    "lark_msg": "飞书消息",
}
SECTION_LABELS = (
    ("代码提交", "commits"),
    ("任务进展", "tasks"),
    ("协作沟通", "messages"),
)


def render_markdown(report: DailyReport) -> str:
    lines = [f"# {report.team} {report.date} 日报", ""]
    if not report.members:
        lines.extend(["今日无记录", ""])
        return "\n".join(lines).rstrip() + "\n"

    for member in report.members:
        lines.extend([f"## {member.name}", ""])
        for section_title, source in SECTION_LABELS:
            lines.extend([f"### {section_title}", ""])
            source_name = _source_name(source)
            if source_name in member.failures:
                lines.extend([f"- **数据获取失败**（{SOURCE_LABELS.get(source_name, source_name)}）", ""])
                continue

            items = getattr(member, source)
            if not items:
                lines.extend(["- 今日无记录", ""])
                continue

            if source == "commits":
                lines.extend([_render_commit(item) for item in items])
            elif source == "tasks":
                lines.extend([_render_task(item) for item in items])
            else:
                lines.extend([_render_message(item) for item in items])
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_html(report: DailyReport) -> str:
    body = markdown_to_html(report.markdown)
    return (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(f'{report.team} {report.date} 日报')}</title>\n"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "max-width:900px;margin:2rem auto;padding:0 1rem;color:#24292f;line-height:1.6}"
        "h1{border-bottom:1px solid #d8dee4;padding-bottom:.3em}"
        "h2{margin-top:2rem}h3{color:#57606a}li{margin:.25rem 0}</style>\n"
        "</head>\n<body>\n" + body + "\n</body>\n</html>\n"
    )


def markdown_to_html(markdown: str) -> str:
    """Convert the subset of Markdown emitted by this report generator to HTML."""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    list_items: list[str] = []

    def flush_list() -> None:
        if list_items:
            output.append("<ul>")
            output.extend(f"<li>{item}</li>" for item in list_items)
            output.append("</ul>")
            list_items.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_list()
            continue
        match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if match:
            flush_list()
            level = len(match.group(1))
            output.append(f"<h{level}>{_inline_html(match.group(2))}</h{level}>")
            continue
        if stripped.startswith("- "):
            list_items.append(_inline_html(stripped[2:].strip()))
            continue
        flush_list()
        output.append(f"<p>{_inline_html(stripped)}</p>")
    flush_list()
    return "\n".join(output)


def _inline_html(text: str) -> str:
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


def _source_name(section_source: str) -> str:
    return {
        "commits": "github",
        "tasks": "lark_task",
        "messages": "lark_msg",
    }[section_source]


def _render_commit(commit: Any) -> str:
    if isinstance(commit, dict):
        repository = commit.get("repository", "")
        sha = str(commit.get("sha", ""))[:7]
        message = commit.get("message", "")
        author = commit.get("author", "")
        additions = commit.get("additions", 0)
        deletions = commit.get("deletions", 0)
    else:
        repository = getattr(commit, "repository", "")
        sha = str(getattr(commit, "sha", ""))[:7]
        message = getattr(commit, "message", "")
        author = getattr(commit, "author", "")
        additions = getattr(commit, "additions", 0)
        deletions = getattr(commit, "deletions", 0)
    identity = f"`{sha}` " if sha else ""
    author_suffix = f" — {author}" if author else ""
    repository_suffix = f"（{repository}）" if repository else ""
    return f"- {identity}{message}{author_suffix}{repository_suffix}；+{additions}/-{deletions}"


def _render_task(task: Any) -> str:
    if isinstance(task, dict):
        task_id = task.get("task_id") or task.get("id", "")
        title = task.get("title", "")
        status = task.get("status", "")
        assignee = task.get("assignee", "")
    else:
        task_id = getattr(task, "task_id", getattr(task, "id", ""))
        title = getattr(task, "title", "")
        status = getattr(task, "status", "")
        assignee = getattr(task, "assignee", "")
    identity = f"`{task_id}` " if task_id else ""
    assignee_suffix = f" — {assignee}" if assignee else ""
    return f"- {identity}{title}（{status}）{assignee_suffix}"


def _render_message(message: Any) -> str:
    if isinstance(message, dict):
        sender = message.get("sender_id", "")
        content = message.get("content", "")
        sent_at = message.get("sent_at", message.get("timestamp", ""))
    else:
        sender = getattr(message, "sender_id", "")
        content = getattr(message, "content", "")
        sent_at = getattr(message, "sent_at", "")
    if isinstance(sent_at, datetime):
        sent_text = sent_at.strftime("%H:%M")
    else:
        sent_text = str(sent_at)
        if len(sent_text) >= 16 and "T" in sent_text:
            sent_text = sent_text[11:16]
    sender_suffix = f" — {sender}" if sender else ""
    time_suffix = f"（{sent_text}）" if sent_text else ""
    return f"- {content}{sender_suffix}{time_suffix}"
