"""Relevance ranking: must_have density + nice_to_have bonus + recency, minus flags."""
from __future__ import annotations

from .config import SearchCriteria
from .models import Job
from .util import now_utc


def _count(haystack: str, term: str) -> int:
    if not term:
        return 0
    return haystack.count(term)


def score(job: Job, criteria: SearchCriteria) -> float:
    title = (job.title or "").lower()
    description = (job.description or "").lower()
    points = 0.0

    # must_have: reward title hits and (capped) description density.
    for term in criteria.must_have:
        term = term.lower().strip()
        if not term:
            continue
        if term in title:
            points += 3.0
        points += 0.5 * min(_count(description, term), 5)

    # nice_to_have: pure bonus, never required.
    for term in criteria.nice_to_have:
        term = term.lower().strip()
        if not term:
            continue
        if term in title:
            points += 1.5
        elif term in description:
            points += 0.75

    # recency: ~+5 for a fresh posting, decaying ~1/day; unknown date gets nothing.
    if job.posted_date:
        age_hours = max(0.0, (now_utc() - job.posted_date).total_seconds() / 3600.0)
        points += max(0.0, 5.0 - age_hours / 24.0)

    # de-prioritize flagged jobs (missing salary/date) without excluding them.
    points -= 0.5 * len(job.flags)
    return points


def rank(jobs: list[Job], criteria: SearchCriteria) -> list[Job]:
    """Score in place and return a new list sorted by relevance desc (stable)."""
    for job in jobs:
        job.relevance = score(job, criteria)
    return sorted(jobs, key=lambda j: j.relevance, reverse=True)
