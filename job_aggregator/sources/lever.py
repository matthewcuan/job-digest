"""Lever public postings API: api.lever.co/v0/postings/{slug}?mode=json."""
from __future__ import annotations

from ..config import SearchCriteria
from ..models import Job
from ..util import (
    coerce_amount,
    format_salary,
    make_snippet,
    parse_epoch_ms,
    stable_job_id,
    strip_html,
)
from ._ats import AtsSource
from ._http import get_json, pretty_company


class LeverSource(AtsSource):
    name = "lever"
    BASE = "https://api.lever.co/v0/postings/{slug}"

    def _fetch_board(self, slug: str, criteria: SearchCriteria, limit: int) -> list[Job]:
        data = get_json(self.BASE.format(slug=slug), params={"mode": "json"}, timeout=self.timeout)
        if not isinstance(data, list):  # mode=json returns a top-level array
            return []
        company = pretty_company(slug)
        return [self._map(item, company) for item in data]

    def _map(self, item: dict, company: str) -> Job:
        categories = item.get("categories") or {}
        title = item.get("text") or "Untitled"  # Lever's title field is `text`
        location = categories.get("location") or ""
        url = item.get("hostedUrl") or item.get("applyUrl") or ""
        native_id = item.get("id")
        description_plain = item.get("descriptionPlain") or ""
        description_html = item.get("description") or ""
        posted = parse_epoch_ms(item.get("createdAt"))  # epoch MILLISECONDS
        workplace = (item.get("workplaceType") or "").lower()
        is_remote = workplace == "remote" or "remote" in location.lower()
        salary_range = item.get("salaryRange") or {}
        comp_min = coerce_amount(salary_range.get("min")) if salary_range else None
        comp_max = coerce_amount(salary_range.get("max")) if salary_range else None
        salary = (
            format_salary(
                comp_min,
                comp_max,
                salary_range.get("interval"),
                salary_range.get("currency") or "USD",
            )
            if salary_range
            else None
        )
        description = description_plain or strip_html(description_html)
        return Job(
            title=title,
            company=company,
            location=location,
            salary=salary,
            description=description,
            description_snippet=make_snippet(description_plain or description_html),
            url=url,
            source=self.name,
            posted_date=posted,
            job_id=stable_job_id(
                self.name,
                native_id=str(native_id) if native_id is not None else None,
                url=url,
                company=company,
                title=title,
                location=location,
            ),
            is_remote=is_remote or None,
            job_type=categories.get("commitment"),
            salary_min_amount=comp_min,
            salary_max_amount=comp_max,
        )
