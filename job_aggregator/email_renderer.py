"""Render the digest (HTML + plain text) and send it over SMTP."""
from __future__ import annotations

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader
from loguru import logger

from .config import AppConfig, ResolvedEmail, Secrets, resolve_email
from .models import Job
from .pipeline import RunResult
from .util import now_utc

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _autoescape(template_name: Optional[str]) -> bool:
    return bool(template_name) and template_name.endswith((".html", ".html.j2"))


_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=_autoescape,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _safe_url(url) -> str:
    """Only allow http(s) URLs in links — block javascript:/data: from job sources.

    Autoescaping escapes the attribute value but does NOT validate the scheme, so a
    malicious posting could otherwise inject ``javascript:`` into the Apply href.
    """
    if isinstance(url, str) and url.strip().lower().startswith(("http://", "https://")):
        return url.strip()
    return "#"


_env.filters["safe_url"] = _safe_url


def _group_by_source(jobs: list[Job]) -> list[tuple[str, list[Job]]]:
    """Group jobs by source; within a group sort by posted_date desc (None last).
    Groups ordered by size desc, then name."""
    groups: dict[str, list[Job]] = {}
    for job in jobs:
        groups.setdefault(job.source, []).append(job)
    for source_jobs in groups.values():
        source_jobs.sort(
            key=lambda j: (j.posted_date is None, -(j.posted_date.timestamp() if j.posted_date else 0))
        )
    return sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def build_context(result: RunResult, config: AppConfig) -> dict:
    source_health = {
        sr.source: {"ok": sr.ok, "count": sr.count, "error": sr.error, "near_empty": sr.near_empty}
        for sr in result.source_results
    }
    return {
        "generated_at": now_utc().strftime("%Y-%m-%d %H:%M UTC"),
        "total_new": len(result.new_jobs),
        "source_health": source_health,
        "failed_sources": [sr.source for sr in result.failed_sources],
        "all_failed": result.all_failed,
        "groups": _group_by_source(result.new_jobs),
        "stats": {
            "fetched": result.total_fetched,
            "after_dedup": result.after_dedup,
            "after_filter": result.after_filter,
            "seen_skipped": result.seen_skipped,
        },
    }


def render_digest(result: RunResult, config: AppConfig) -> tuple[str, str]:
    ctx = build_context(result, config)
    html = _env.get_template("email.html.j2").render(**ctx)
    text = _env.get_template("email.txt.j2").render(**ctx)
    return html, text


def build_subject(result: RunResult, config: AppConfig) -> str:
    prefix = config.email.subject_prefix
    if result.all_failed:
        return f"{prefix} ⚠ all sources failed"
    count = len(result.new_jobs)
    return f"{prefix} {count} new job{'' if count == 1 else 's'}"


def _should_send(result: RunResult, config: AppConfig) -> bool:
    if result.new_jobs:
        return True
    # All-failed is authoritative: send_on_total_failure decides, so a user who opts
    # out isn't overridden by send_empty_digest.
    if result.all_failed:
        return config.email.send_on_total_failure
    return config.email.send_empty_digest


def send_email(resolved: ResolvedEmail, subject: str, html: str, text: str) -> None:
    if not resolved.deliverable:
        raise ValueError(
            "Email is not deliverable: need host, sender, and recipient "
            "(set SMTP_HOST/EMAIL_TO env or email.* in config.yaml)."
        )
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = resolved.sender
    message["To"] = resolved.recipient
    # Plain part first so clients prefer HTML when they can render it.
    message.attach(MIMEText(text, "plain", "utf-8"))
    message.attach(MIMEText(html, "html", "utf-8"))

    context = ssl.create_default_context()
    if resolved.implicit_tls:  # SMTPS (e.g. port 465): TLS from the first byte
        server = smtplib.SMTP_SSL(resolved.host, resolved.port, context=context, timeout=30)
    else:
        server = smtplib.SMTP(resolved.host, resolved.port, timeout=30)
    with server:
        if not resolved.implicit_tls and resolved.use_tls:  # STARTTLS (e.g. port 587)
            server.starttls(context=context)
        if resolved.user and resolved.password:
            server.login(resolved.user, resolved.password)
        server.send_message(message)
    logger.info("Sent '{}' to {}", subject, resolved.recipient)


def send_digest(result: RunResult, config: AppConfig, secrets: Secrets) -> bool:
    """Render and send if there's a reason to. Returns True iff an email was sent."""
    if not _should_send(result, config):
        logger.info("Nothing to send (no new jobs; send_empty_digest is off)")
        return False
    resolved = resolve_email(config.email, secrets)
    subject = build_subject(result, config)
    html, text = render_digest(result, config)
    send_email(resolved, subject, html, text)
    return True
