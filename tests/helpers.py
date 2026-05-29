"""Shared test helpers."""
from __future__ import annotations

from datetime import datetime, timezone

from job_aggregator.models import Job

NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def make_job(
    job_id: str,
    *,
    title: str = "Engineer",
    company: str = "Acme",
    location: str = "Remote",
    salary: str | None = None,
    salary_max: float | None = None,
    description: str = "",
    url: str | None = None,
    source: str = "greenhouse",
    posted_date: datetime | None = NOW,
    is_remote: bool | None = True,
    job_type: str | None = None,
) -> Job:
    return Job(
        title=title,
        company=company,
        location=location,
        salary=salary,
        description=description,
        description_snippet=description[:60],
        url=url or f"https://example.com/jobs/{job_id}",
        source=source,
        posted_date=posted_date,
        job_id=job_id,
        is_remote=is_remote,
        job_type=job_type,
        salary_max_amount=salary_max,
    )
