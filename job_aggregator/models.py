"""Normalized data structures shared across sources, pipeline, and email."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Job:
    """A single normalized job listing.

    ``flags`` records soft-quality notes (e.g. ``"no_salary"``, ``"no_date"``) so the
    ranker can de-prioritize them and the email can show them, without dropping the job.
    """

    title: str
    company: str
    location: str
    salary: Optional[str]  # display string, e.g. "$120k–$160k/yr"
    description: str
    description_snippet: str
    url: str
    source: str
    posted_date: Optional[datetime]
    job_id: str
    is_remote: Optional[bool] = None
    job_type: Optional[str] = None
    job_level: Optional[str] = None
    salary_min_amount: Optional[float] = None  # numeric, for salary_min filtering
    salary_max_amount: Optional[float] = None
    relevance: float = 0.0
    flags: list[str] = field(default_factory=list)
    # Optional LLM relevance scoring (set only when the llm stage runs; see job_aggregator.llm).
    llm_score: Optional[int] = None  # 0-100
    llm_verdict: Optional[str] = None  # "strong" | "maybe" | "weak"
    llm_reason: Optional[str] = None

    @property
    def salary_value(self) -> Optional[float]:
        """Upper bound of the listed pay (falls back to the lower bound)."""
        return self.salary_max_amount or self.salary_min_amount


@dataclass
class SourceResult:
    """Outcome of one source's fetch — the unit of partial-failure reporting.

    ``ok=False`` means the fetch raised/failed. ``ok=True`` with ``near_empty=True``
    means it succeeded but returned zero jobs (possibly a silent block, possibly just
    no matches) — surfaced in the email so a quietly-broken board is visible.
    """

    source: str
    jobs: list[Job] = field(default_factory=list)
    ok: bool = True
    error: Optional[str] = None
    near_empty: bool = False

    @property
    def count(self) -> int:
        return len(self.jobs)
