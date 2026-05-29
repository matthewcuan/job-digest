"""Regression tests for the review fixes."""
from __future__ import annotations

import pytest

from job_aggregator import email_renderer
from job_aggregator.config import AppConfig, EmailConfig, Secrets, resolve_email
from job_aggregator.email_renderer import _safe_url, _should_send
from job_aggregator.pipeline import RunResult
from job_aggregator.util import format_salary, make_snippet, strip_html


# --- Fix 1: empty SMTP_PORT must not crash Secrets() ---

def test_empty_smtp_port_coerced_to_none(monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "")  # how Actions injects an unset secret
    secrets = Secrets(_env_file=None)
    assert secrets.smtp_port is None


def test_valid_smtp_port_still_parsed(monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "465")
    assert Secrets(_env_file=None).smtp_port == 465


# --- Fix 2: Lever interval tokens get a period suffix ---

@pytest.mark.parametrize(
    "interval,expected_suffix",
    [
        ("per-year-salary", "/yr"),
        ("per-hour-wage", "/hr"),
        ("per-month-salary", "/mo"),
        ("yearly", "/yr"),  # JobSpy style still works
        ("hourly", "/hr"),
    ],
)
def test_lever_and_jobspy_interval_suffixes(interval, expected_suffix):
    out = format_salary(100, 100, interval, "USD")
    assert out is not None and out.endswith(expected_suffix)


# --- Fix 3: safe_url blocks dangerous schemes ---

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/jobs/1", "https://example.com/jobs/1"),
        ("http://example.com", "http://example.com"),
        ("javascript:alert(1)", "#"),
        ("data:text/html,<script>", "#"),
        ("", "#"),
        (None, "#"),
    ],
)
def test_safe_url_filter(url, expected):
    assert _safe_url(url) == expected


# --- Fix 4 / 5: strip_html escaped flag ---

def test_strip_html_escaped_greenhouse_style():
    # Entity-escaped HTML (Greenhouse): real tags are revealed and removed.
    assert strip_html("&lt;p&gt;Hello &amp; bye&lt;/p&gt;", escaped=True) == "Hello & bye"


def test_strip_html_real_html_preserves_visible_escaped_markup():
    # Real HTML (Ashby/Lever): visible escaped markup in the text survives.
    out = strip_html("<p>Use &lt;div&gt; here</p>", escaped=False)
    assert out == "Use <div> here"


# --- Fix 7: send decision precedence ---

def _all_failed_result() -> RunResult:
    from job_aggregator.models import SourceResult

    return RunResult(source_results=[SourceResult("linkedin", [], False, "boom")], new_jobs=[])


def test_total_failure_opt_out_not_overridden_by_empty_digest():
    cfg = AppConfig()
    cfg.email = EmailConfig(send_on_total_failure=False, send_empty_digest=True)
    # User opted out of failure emails; empty-digest must not force one on an all-failed run.
    assert _should_send(_all_failed_result(), cfg) is False


def test_total_failure_sends_when_enabled():
    cfg = AppConfig()
    cfg.email = EmailConfig(send_on_total_failure=True)
    assert _should_send(_all_failed_result(), cfg) is True


# --- Fix 6: implicit TLS detection ---

def test_resolve_email_implicit_tls_on_465(monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "465")
    resolved = resolve_email(EmailConfig(smtp_host="smtp.x.com", sender="a@x.com", recipient="b@x.com"), Secrets(_env_file=None))
    assert resolved.implicit_tls is True


def test_resolve_email_starttls_on_587(monkeypatch):
    monkeypatch.delenv("SMTP_PORT", raising=False)  # no env override -> use config 587
    resolved = resolve_email(
        EmailConfig(smtp_host="smtp.x.com", smtp_port=587, sender="a@x.com", recipient="b@x.com"),
        Secrets(_env_file=None),
    )
    assert resolved.implicit_tls is False


# --- Helpers: probe slug candidates, verify_login, retention parsing ---

def test_slug_candidates():
    from job_aggregator.probe import slug_candidates

    cands = slug_candidates("Acme Corp")
    assert "acmecorp" in cands and "acme-corp" in cands and "AcmeCorp" in cands
    ashby = slug_candidates("Ashby")
    assert "ashby" in ashby and "Ashby" in ashby  # case-sensitive variant kept


def test_verify_login_ok(monkeypatch):
    from job_aggregator import email_renderer as er
    from job_aggregator.config import ResolvedEmail

    events = {}

    class FakeServer:
        def __enter__(self):
            events["entered"] = True
            return self

        def __exit__(self, *a):
            events["exited"] = True
            return False

    monkeypatch.setattr(er, "_connect", lambda resolved: FakeServer())
    resolved = ResolvedEmail("h", 587, "u", "p", "s@x.com", "r@x.com", True, False, "[x]")
    er.verify_login(resolved)
    assert events == {"entered": True, "exited": True}


def test_verify_login_not_deliverable():
    from job_aggregator import email_renderer as er
    from job_aggregator.config import ResolvedEmail

    resolved = ResolvedEmail(None, 587, None, None, None, None, True, False, "[x]")
    with pytest.raises(ValueError):
        er.verify_login(resolved)


def test_empty_retention_days_to_none(monkeypatch):
    monkeypatch.setenv("STATE_RETENTION_DAYS", "")
    assert Secrets(_env_file=None).state_retention_days is None


def test_retention_days_parsed(monkeypatch):
    monkeypatch.setenv("STATE_RETENTION_DAYS", "90")
    assert Secrets(_env_file=None).state_retention_days == 90
