"""SMTP email notifier for daily reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from email.message import EmailMessage
import smtplib
import ssl
import time
from typing import Any, Callable

from shared.logger import configure_logging, log_extra


MAX_ATTEMPTS = 3


def send(report: Any, target: Mapping[str, Any]) -> bool:
    """Send the report as an HTML email.

    ``target`` accepts the SMTP connection settings plus the recipient list.
    The optional ``smtp_factory`` entry allows tests to inject a fake client.
    """
    logger = configure_logging()
    try:
        recipients = _recipients(target)
        message = build_message(report, target, recipients)
        smtp_factory = target.get("smtp_factory") or _default_smtp_factory(target)
    except ValueError as exc:
        logger.error(
            "Email configuration invalid",
            exc_info=True,
            extra=log_extra(channel="email", error=str(exc)),
        )
        return False

    retry_interval = float(target.get("retry_interval", 5))

    for attempt in range(MAX_ATTEMPTS):
        try:
            client = smtp_factory()
            try:
                _login_if_needed(client, target)
                client.send_message(message, from_addr=message["From"], to_addrs=recipients)
            finally:
                _close_quietly(client)
            logger.info(
                "Daily report email sent",
                extra=log_extra(channel="email", recipients=len(recipients), attempts=attempt + 1),
            )
            return True
        except (smtplib.SMTPException, OSError, ValueError) as exc:
            if attempt == MAX_ATTEMPTS - 1:
                logger.error(
                    "Daily report email failed",
                    exc_info=True,
                    extra=log_extra(channel="email", attempts=attempt + 1, error=str(exc)),
                )
                return False
            logger.warning(
                "Daily report email failed; retrying",
                extra=log_extra(channel="email", attempts=attempt + 1, error=str(exc)),
            )
            time.sleep(retry_interval)

    return False


def build_message(report: Any, target: Mapping[str, Any], recipients: Sequence[str]) -> EmailMessage:
    sender = _required(target, "from_address", "sender", "from_addr")
    subject = str(target.get("subject") or f"{_field(report, 'team', '团队')} {_field(report, 'date', '')} 日报")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(str(_field(report, "markdown", "")) or "日报内容为空")
    html = str(_field(report, "html", ""))
    if html:
        message.add_alternative(html, subtype="html")
    return message


def _default_smtp_factory(target: Mapping[str, Any]) -> Callable[[], Any]:
    host = _required(target, "host")
    port = int(target.get("port", 465 if target.get("use_ssl") else 25))
    use_ssl = bool(target.get("use_ssl", port == 465))
    timeout = float(target.get("timeout", 30))
    use_starttls = bool(target.get("use_starttls", False))
    context = ssl.create_default_context()

    def factory() -> Any:
        if use_ssl:
            return smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        client = smtplib.SMTP(host, port, timeout=timeout)
        if use_starttls:
            client.starttls(context=context)
        return client

    return factory


def _login_if_needed(client: Any, target: Mapping[str, Any]) -> None:
    username = target.get("username")
    password = target.get("password")
    if username and password:
        client.login(str(username), str(password))


def _close_quietly(client: Any) -> None:
    close = getattr(client, "quit", None) or getattr(client, "close", None)
    if close is None:
        return
    try:
        close()
    except (smtplib.SMTPException, OSError):
        pass


def _recipients(target: Mapping[str, Any]) -> list[str]:
    value = target.get("recipients") or target.get("to")
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise ValueError("SMTP recipients are required")
    recipients = [str(item).strip() for item in value if str(item).strip()]
    if not recipients:
        raise ValueError("SMTP recipients are required")
    return recipients


def _required(target: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = target.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"Missing required SMTP setting: {keys[0]}")


def _field(report: Any, name: str, default: Any) -> Any:
    if isinstance(report, Mapping):
        return report.get(name, default)
    return getattr(report, name, default)
