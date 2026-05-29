"""JobSpy-backed source: one instance per board, called per-site for isolation."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from loguru import logger

from ..config import SearchCriteria
from ..models import Job
from ..util import coerce_amount, format_salary, make_snippet, normalize_job_type, parse_iso, stable_job_id
from .base import JobSource

# Our config name -> JobSpy's site_name token (only ziprecruiter differs).
_SITE_MAP = {"ziprecruiter": "zip_recruiter"}


def _clean(value):
    """Map pandas NaN/NaT/empty to None; otherwise return the value unchanged."""
    if value is None:
        return None
    try:
        if value != value:  # NaN / NaT are not equal to themselves
            return None
    except Exception:  # noqa: BLE001
        pass
    return value


class JobSpySource(JobSource):
    def __init__(self, name: str, proxy_url: Optional[str] = None):
        self.name = name
        self.jobspy_site = _SITE_MAP.get(name, name)
        self.proxy_url = proxy_url

    def _fetch(self, criteria: SearchCriteria, limit: int) -> list[Job]:
        # Imported lazily so tests/other sources don't pay JobSpy+pandas import cost.
        from jobspy import scrape_jobs

        kwargs: dict = dict(
            site_name=[self.jobspy_site],
            results_wanted=limit,
            distance=criteria.distance,
            country_indeed=criteria.country,
            description_format="html",
            verbose=0,
        )
        if criteria.search_term:
            kwargs["search_term"] = criteria.search_term
        if criteria.location:
            kwargs["location"] = criteria.location
        if criteria.is_remote:
            kwargs["is_remote"] = True
        normalized_type = normalize_job_type(criteria.job_type)
        if normalized_type:
            kwargs["job_type"] = normalized_type
        if criteria.date_posted_hours:
            kwargs["hours_old"] = criteria.date_posted_hours
        if self.proxy_url:
            kwargs["proxies"] = [self.proxy_url]
        # Google ignores structured params — it filters only via google_search_term.
        if self.jobspy_site == "google" and criteria.search_term:
            gterm = criteria.search_term
            if criteria.location:
                gterm = f"{gterm} {criteria.location}"
            kwargs["google_search_term"] = gterm

        df = scrape_jobs(**kwargs)
        if df is None or len(df) == 0:
            return []
        return [self._row_to_job(row) for _, row in df.iterrows()]

    def _row_to_job(self, row) -> Job:
        title = str(_clean(row.get("title")) or "Untitled")
        company = str(_clean(row.get("company")) or "Unknown")
        location = str(_clean(row.get("location")) or "")
        url = str(_clean(row.get("job_url_direct")) or _clean(row.get("job_url")) or "")
        native_id = _clean(row.get("id"))
        description = _clean(row.get("description")) or ""
        min_amount = coerce_amount(_clean(row.get("min_amount")))
        max_amount = coerce_amount(_clean(row.get("max_amount")))
        salary = format_salary(
            min_amount,
            max_amount,
            _clean(row.get("interval")),
            _clean(row.get("currency")) or "USD",
        )
        is_remote = _clean(row.get("is_remote"))
        job_type = _clean(row.get("job_type"))
        job_level = _clean(row.get("job_level"))
        return Job(
            title=title,
            company=company,
            location=location,
            salary=salary,
            description=str(description),
            description_snippet=make_snippet(description),
            url=url,
            source=self.name,
            posted_date=_parse_posted(_clean(row.get("date_posted"))),
            job_id=stable_job_id(
                self.name,
                native_id=str(native_id) if native_id is not None else None,
                url=url,
                company=company,
                title=title,
                location=location,
            ),
            is_remote=bool(is_remote) if is_remote is not None else None,
            job_type=str(job_type) if job_type else None,
            job_level=str(job_level) if job_level else None,
            salary_min_amount=min_amount,
            salary_max_amount=max_amount,
        )


def _parse_posted(value) -> Optional[datetime]:
    if value is None:
        return None
    # pandas Timestamp subclasses datetime, so this catches both.
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return parse_iso(str(value))
