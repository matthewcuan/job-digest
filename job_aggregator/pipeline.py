"""Orchestration: fetch -> flag -> dedup -> filter -> seen-filter -> rank."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from .config import AppConfig, SearchCriteria, Secrets, WorkMode
from .dedup import deduplicate
from .models import Job, SourceResult
from .rank import rank
from .sources import JobSource, build_sources
from .storage import Storage
from .util import normalize_job_type, now_utc, term_present


@dataclass
class RunResult:
    source_results: list[SourceResult] = field(default_factory=list)
    new_jobs: list[Job] = field(default_factory=list)  # filtered, deduped, unseen, ranked
    total_fetched: int = 0
    after_dedup: int = 0
    after_filter: int = 0
    seen_skipped: int = 0

    @property
    def attempted(self) -> int:
        return len(self.source_results)

    @property
    def ok_sources(self) -> list[SourceResult]:
        return [r for r in self.source_results if r.ok]

    @property
    def failed_sources(self) -> list[SourceResult]:
        return [r for r in self.source_results if not r.ok]

    @property
    def all_failed(self) -> bool:
        return self.attempted > 0 and len(self.failed_sources) == self.attempted


# Experience-level heuristics. Filtering on these is lenient: we only exclude on a clear
# contradiction (e.g. asking for "entry" but the title shouts "Senior"), never on silence.
_EXPERIENCE_HINTS = {
    "entry": ["intern", "junior", "entry", "new grad", "graduate", "associate"],
    "junior": ["junior", "entry", "associate", "jr"],
    "mid": ["mid", "intermediate"],
    "senior": ["senior", "sr", "staff", "principal", "lead"],
    "lead": ["lead", "principal", "staff", "manager", "head", "director"],
}
_SENIOR_TOKENS = ["senior", "staff", "principal", "lead", "head", "director", "vp", "manager"]


def _flag_missing(job: Job) -> None:
    if job.salary is None and "no_salary" not in job.flags:
        job.flags.append("no_salary")
    if job.posted_date is None and "no_date" not in job.flags:
        job.flags.append("no_date")


def _experience_matches(job: Job, level: str) -> bool:
    level_key = level.strip().lower()
    title = (job.title or "").lower()
    text = f"{title} {(job.job_level or '').lower()}"
    hints = _EXPERIENCE_HINTS.get(level_key, [level_key])
    if any(hint in text for hint in hints):
        return True
    # Looking junior but the title is clearly senior -> contradiction, exclude.
    if level_key in ("entry", "junior", "mid") and any(tok in title for tok in _SENIOR_TOKENS):
        return False
    # No clear signal -> keep (don't over-filter on a heuristic).
    return True


def passes_filters(job: Job, criteria: SearchCriteria) -> bool:
    """Authoritative client-side filter. Hard on objective criteria; salary/date use
    keep-but-flag (a job missing that field is NOT excluded)."""
    # must_have: every term must appear (AND), per the configured match mode/fields.
    for term in criteria.must_have:
        if not term.strip():
            continue
        if not term_present(
            term, job.title, job.description,
            mode=criteria.match_mode.value, fields=criteria.match_fields.value,
        ):
            return False

    # exclude: drop unwanted title disciplines even if must_have passed. Always a
    # whole-word, title-only match so "engineer" keeps broad recall while e.g.
    # "Mechanical Engineer" is rejected (and "rf" won't trip "Performance Engineer").
    for term in criteria.exclude:
        if not term.strip():
            continue
        if term_present(term, job.title, "", mode="word", fields="title"):
            return False

    # work_mode.
    if criteria.work_mode is WorkMode.remote:
        if not (job.is_remote or "remote" in (job.location or "").lower()):
            return False
    elif criteria.work_mode is WorkMode.onsite:
        if job.is_remote is True or "remote" in (job.location or "").lower():
            return False
    # hybrid: no reliable signal across sources — don't exclude.

    # job_type: only exclude on a known mismatch (unknown type is kept).
    if criteria.job_type:
        want = normalize_job_type(criteria.job_type)
        have = normalize_job_type(job.job_type)
        if want and have and want != have:
            return False

    # experience_level (lenient heuristic).
    if criteria.experience_level and not _experience_matches(job, criteria.experience_level):
        return False

    # date window — missing posted_date passes (keep-but-flag).
    if criteria.date_posted_hours and job.posted_date is not None:
        age_hours = (now_utc() - job.posted_date).total_seconds() / 3600.0
        if age_hours > criteria.date_posted_hours:
            return False

    # salary floor — missing salary passes (keep-but-flag); only excludes a listed-but-low one.
    if criteria.salary_min and job.salary_value is not None:
        if job.salary_value < criteria.salary_min:
            return False

    return True


def run(
    config: AppConfig,
    secrets: Secrets,
    *,
    storage: Optional[Storage] = None,
    sources: Optional[list[tuple[JobSource, int]]] = None,
) -> RunResult:
    """Run the full pipeline. Does NOT record seen jobs — the caller persists after a
    successful send (so a send failure doesn't silently swallow those listings)."""
    criteria = config.search
    result = RunResult()

    if sources is None:
        sources = build_sources(config, proxy_url=secrets.proxy_url)
    if not sources:
        logger.warning("No sources enabled — check config.sources")

    # 1-2. Fetch each source sequentially (concurrency from one IP raises block risk).
    all_jobs: list[Job] = []
    for source, limit in sources:
        source_result = source.fetch(criteria, limit)
        result.source_results.append(source_result)
        all_jobs.extend(source_result.jobs)
    result.total_fetched = len(all_jobs)

    # 3. Flag soft-quality gaps (missing salary/date) so rank/email can reflect them.
    for job in all_jobs:
        _flag_missing(job)

    # 4. Dedup across sources.
    deduped = deduplicate(all_jobs, fuzzy_threshold=config.dedup.fuzzy_threshold)
    result.after_dedup = len(deduped)

    # 5. Authoritative client-side filter.
    filtered = [job for job in deduped if passes_filters(job, criteria)]
    result.after_filter = len(filtered)

    # 6. Drop already-seen jobs.
    if storage is not None:
        seen = storage.load_seen()
        kept = [job for job in filtered if job.job_id not in seen]
        result.seen_skipped = len(filtered) - len(kept)
        filtered = kept

    # 7. Rank survivors.
    result.new_jobs = rank(filtered, criteria)

    logger.info(
        "pipeline: fetched={} deduped={} filtered={} seen_skipped={} new={}",
        result.total_fetched,
        result.after_dedup,
        result.after_filter,
        result.seen_skipped,
        len(result.new_jobs),
    )
    return result
