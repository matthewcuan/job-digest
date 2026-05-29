"""Deduplication: exact by job_id, then fuzzy on company+title+location."""
from __future__ import annotations

from loguru import logger
from rapidfuzz import fuzz

from .models import Job


def _key(job: Job) -> str:
    return f"{job.company} {job.title} {job.location}".lower().strip()


def _merge(a: Job, b: Job) -> Job:
    """Keep the richer-description copy, then backfill its gaps from the other.

    "Richest description wins" is the headline rule, but a thinner copy may carry a
    salary or posted date the richer one lacks — so we graft those in rather than lose
    them. Mutates and returns the primary job.
    """
    a_richer = len(a.description) > len(b.description) or (
        len(a.description) == len(b.description) and len(a.title) >= len(b.title)
    )
    primary, other = (a, b) if a_richer else (b, a)

    if primary.salary is None and other.salary is not None:
        primary.salary = other.salary
        primary.salary_min_amount = other.salary_min_amount
        primary.salary_max_amount = other.salary_max_amount
    if primary.posted_date is None and other.posted_date is not None:
        primary.posted_date = other.posted_date
    if primary.is_remote is None and other.is_remote is not None:
        primary.is_remote = other.is_remote
    if not primary.job_type and other.job_type:
        primary.job_type = other.job_type
    return primary


def deduplicate(jobs: list[Job], fuzzy_threshold: int = 88) -> list[Job]:
    """Collapse duplicates, preferring the richest-description copy on each collision."""
    # Stage 1 — exact dedup on the stable id.
    by_id: dict[str, Job] = {}
    for job in jobs:
        existing = by_id.get(job.job_id)
        by_id[job.job_id] = job if existing is None else _merge(existing, job)
    unique = list(by_id.values())

    # Stage 2 — fuzzy dedup on company+title+location (catches the same role posted to
    # multiple boards with different ids). O(n^2) but n is tens-to-low-hundreds.
    kept: list[Job] = []
    keys: list[str] = []
    collapsed = 0
    for job in unique:
        key = _key(job)
        match_idx = None
        for i, existing_key in enumerate(keys):
            if fuzz.token_sort_ratio(existing_key, key) >= fuzzy_threshold:
                match_idx = i
                break
        if match_idx is None:
            kept.append(job)
            keys.append(key)
        else:
            collapsed += 1
            kept[match_idx] = _merge(kept[match_idx], job)
            keys[match_idx] = _key(kept[match_idx])

    if collapsed:
        logger.debug("dedup: collapsed {} fuzzy duplicate(s)", collapsed)
    return kept
