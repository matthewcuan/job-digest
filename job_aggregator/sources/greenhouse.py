"""Greenhouse public board API: boards-api.greenhouse.io/v1/boards/{slug}/jobs."""
from __future__ import annotations

from ..config import SearchCriteria
from ..models import Job
from ..util import make_snippet, parse_iso, stable_job_id, strip_html
from ._ats import AtsSource
from ._http import get_json, pretty_company


class GreenhouseSource(AtsSource):
    name = "greenhouse"
    BASE = "https://boards-api.greenhouse.io/v1/boards/{slug}"

    def _fetch_board(self, slug: str, criteria: SearchCriteria, limit: int) -> list[Job]:
        data = get_json(
            f"{self.BASE.format(slug=slug)}/jobs",
            params={"content": "true"},
            timeout=self.timeout,
        )
        company = self._board_name(slug)
        return [self._map(item, company) for item in data.get("jobs", [])]

    def _board_name(self, slug: str) -> str:
        try:
            meta = get_json(self.BASE.format(slug=slug), timeout=self.timeout)
            return meta.get("name") or pretty_company(slug)
        except Exception:  # noqa: BLE001 — cosmetic only; fall back to the slug
            return pretty_company(slug)

    def _map(self, item: dict, company: str) -> Job:
        title = item.get("title") or "Untitled"
        location = (item.get("location") or {}).get("name") or ""
        url = item.get("absolute_url") or ""
        native_id = item.get("id")
        content = item.get("content") or ""  # entity-escaped HTML — strip_html unescapes
        posted = parse_iso(item.get("first_published") or item.get("updated_at"))
        return Job(
            title=title,
            company=company,
            location=location,
            salary=None,  # only on the single-job endpoint with pay_transparency=true
            description=strip_html(content),
            description_snippet=make_snippet(content),
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
            is_remote=True if "remote" in location.lower() else None,
        )
