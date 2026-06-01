"""Relevance ranking: must_have density + nice_to_have bonus + recency, minus flags."""
from __future__ import annotations

from .config import SearchCriteria
from .models import Job
from .util import now_utc, term_count, term_present


def score(job: Job, criteria: SearchCriteria, *, llm_weight: float = 1.0) -> float:
    mode = criteria.match_mode.value
    title = job.title or ""
    description = job.description or ""
    points = 0.0

    # must_have: reward title hits and (capped) description density. Ranking always
    # considers both fields for richness, regardless of the filter's match_fields.
    for term in criteria.must_have:
        if not term.strip():
            continue
        if term_present(term, title, "", mode=mode, fields="title"):
            points += 3.0
        points += 0.5 * min(term_count(term, "", description, mode=mode, fields="title_and_description"), 5)

    # nice_to_have: pure bonus, never required.
    for term in criteria.nice_to_have:
        if not term.strip():
            continue
        if term_present(term, title, "", mode=mode, fields="title"):
            points += 1.5
        elif term_present(term, "", description, mode=mode, fields="title_and_description"):
            points += 0.75

    # recency: ~+5 for a fresh posting, decaying ~1/day; unknown date gets nothing.
    if job.posted_date:
        age_hours = max(0.0, (now_utc() - job.posted_date).total_seconds() / 3600.0)
        points += max(0.0, 5.0 - age_hours / 24.0)

    # de-prioritize flagged jobs (missing salary/date) without excluding them.
    points -= 0.5 * len(job.flags)

    # LLM relevance (additive blend): a 100 adds ~5 pts, on par with a fresh posting.
    # Only contributes when the optional LLM stage scored this job.
    if job.llm_score is not None:
        points += llm_weight * (job.llm_score / 20.0)
    return points


def rank(jobs: list[Job], criteria: SearchCriteria, *, llm_weight: float = 1.0) -> list[Job]:
    """Score in place and return a new list sorted by relevance desc (stable)."""
    for job in jobs:
        job.relevance = score(job, criteria, llm_weight=llm_weight)
    return sorted(jobs, key=lambda j: j.relevance, reverse=True)
