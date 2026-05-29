"""Ashby public posting API: api.ashbyhq.com/posting-api/job-board/{slug}."""
from __future__ import annotations

from ..config import SearchCriteria
from ..models import Job
from ..util import make_snippet, parse_iso, stable_job_id, strip_html
from ._ats import AtsSource
from ._http import get_json, pretty_company


class AshbySource(AtsSource):
    name = "ashby"
    BASE = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

    def _fetch_board(self, slug: str, criteria: SearchCriteria, limit: int) -> list[Job]:
        data = get_json(
            self.BASE.format(slug=slug),
            params={"includeCompensation": "true"},
            timeout=self.timeout,
        )
        company = pretty_company(slug)
        jobs = []
        for item in data.get("jobs", []):
            if item.get("isListed") is False:  # skip unlisted postings
                continue
            jobs.append(self._map(item, company))
        return jobs

    def _map(self, item: dict, company: str) -> Job:
        title = item.get("title") or "Untitled"
        location = item.get("location") or ""
        url = item.get("jobUrl") or item.get("applyUrl") or ""
        native_id = item.get("id")
        description_html = item.get("descriptionHtml") or ""  # real HTML (not escaped)
        description_plain = item.get("descriptionPlain") or ""
        posted = parse_iso(item.get("publishedAt"))
        workplace = (item.get("workplaceType") or "").lower()
        is_remote_flag = item.get("isRemote")
        is_remote = is_remote_flag if isinstance(is_remote_flag, bool) else (workplace == "remote")
        compensation = item.get("compensation") or {}
        salary = (
            compensation.get("compensationTierSummary")
            or compensation.get("scrapeableCompensationSalarySummary")
            or None
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
            is_remote=is_remote,
            job_type=item.get("employmentType"),
        )
