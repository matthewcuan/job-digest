"""Source field-mapping and failure-isolation tests (no network)."""
from __future__ import annotations

from job_aggregator.config import SearchCriteria
from job_aggregator.sources.ashby import AshbySource
from job_aggregator.sources.base import JobSource
from job_aggregator.sources.greenhouse import GreenhouseSource
from job_aggregator.sources.lever import LeverSource


def test_greenhouse_mapping_unescapes_html_and_detects_remote():
    item = {
        "id": 123,
        "title": "Engineer",
        "location": {"name": "Remote - US"},
        "absolute_url": "https://job-boards.greenhouse.io/x/jobs/123?gh_jid=123",
        "content": "&lt;p&gt;Hello &amp; goodbye&lt;/p&gt;",  # entity-escaped HTML
        "first_published": "2026-05-01T10:00:00-04:00",
    }
    job = GreenhouseSource(["x"])._map(item, "X Corp")
    assert job.title == "Engineer"
    assert job.company == "X Corp"
    assert job.location == "Remote - US"
    assert "Hello & goodbye" in job.description and "<" not in job.description
    assert job.posted_date is not None and job.posted_date.year == 2026
    assert job.is_remote is True


def test_lever_mapping_uses_text_field_and_epoch_ms():
    item = {
        "id": "uuid-1",
        "text": "Backend Engineer",  # Lever's title field is `text`
        "categories": {"location": "New York", "commitment": "Full-time"},
        "hostedUrl": "https://jobs.lever.co/x/uuid-1",
        "createdAt": 1553186035299,  # epoch milliseconds -> 2019
        "descriptionPlain": "Build APIs.",
        "workplaceType": "remote",
        "salaryRange": {"min": 100000, "max": 150000, "interval": "per-year-salary", "currency": "USD"},
    }
    job = LeverSource(["x"])._map(item, "X")
    assert job.title == "Backend Engineer"
    assert job.posted_date is not None and job.posted_date.year == 2019
    assert job.salary_max_amount == 150000
    assert job.salary is not None
    assert job.is_remote is True
    assert job.job_type == "Full-time"


def test_ashby_mapping_compensation_and_publishedat():
    item = {
        "id": "u-1",
        "title": "Engineering Manager",
        "location": "Remote - EU",
        "jobUrl": "https://jobs.ashbyhq.com/X/u-1",
        "publishedAt": "2024-03-04T14:29:08.532+00:00",
        "descriptionHtml": "<p>Hi there</p>",
        "isRemote": True,
        "employmentType": "FullTime",
        "isListed": True,
        "compensation": {"compensationTierSummary": "€76K – €185K • Offers Equity"},
    }
    job = AshbySource(["x"])._map(item, "X")
    assert job.title == "Engineering Manager"
    assert job.salary == "€76K – €185K • Offers Equity"
    assert job.is_remote is True
    assert job.job_type == "FullTime"
    assert job.posted_date is not None and job.posted_date.year == 2024
    assert "Hi there" in job.description and "<p>" not in job.description


class _Boom(JobSource):
    name = "boom"

    def _fetch(self, criteria, limit):
        raise ValueError("kaboom")


def test_failure_isolation_returns_failed_result():
    result = _Boom().fetch(SearchCriteria(), 5)
    assert result.ok is False
    assert result.jobs == []
    assert "kaboom" in (result.error or "")


def test_ats_all_boards_failing_raises(monkeypatch):
    # When every configured board errors, the source should report failure (ok=False),
    # not a silent empty success.
    import job_aggregator.sources.greenhouse as gh

    def boom(*args, **kwargs):
        raise RuntimeError("404")

    monkeypatch.setattr(gh, "get_json", boom)
    result = GreenhouseSource(["bad1", "bad2"]).fetch(SearchCriteria(), 5)
    assert result.ok is False
    assert "all 2 greenhouse board(s) failed" in (result.error or "")


def test_ats_partial_board_failure_still_ok(monkeypatch):
    import job_aggregator.sources.greenhouse as gh

    def maybe(url, **kwargs):
        if "good" in url:
            return {"jobs": [{"id": 1, "title": "Eng", "location": {"name": "Remote"}, "absolute_url": "u"}]}
        raise RuntimeError("404")

    monkeypatch.setattr(gh, "get_json", maybe)
    result = GreenhouseSource(["bad", "good"]).fetch(SearchCriteria(), 5)
    assert result.ok is True
    assert result.count == 1
