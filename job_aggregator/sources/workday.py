"""Workday public jobs API (the CXS endpoints behind *.myworkdayjobs.com sites).

Each company runs its own Workday tenant, so a board is configured by its **career-site
URL** (copy it from the browser), e.g.
    https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite
from which we derive host / tenant / site and call:
    POST https://{host}/wday/cxs/{tenant}/{site}/jobs          (list)
    GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}  (per-job detail)
The list endpoint lacks descriptions and real dates, so we fetch each posting's detail
(capped at the configured limit) for the description, startDate, timeType, and apply URL.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional
from urllib.parse import urlparse

from loguru import logger

from ..config import SearchCriteria
from ..models import Job
from ..util import make_snippet, normalize_job_type, now_utc, parse_iso, stable_job_id, strip_html
from ._ats import AtsSource
from ._http import get_json, post_json, pretty_company

_LOCALE_RE = re.compile(r"^[a-z]{2}([-_][A-Za-z]{2})?$")  # "en", "en-US", "en_US"


def parse_workday_url(url: str) -> tuple[str, str, str]:
    """Return (host, tenant, site) from a Workday career-site or CXS URL."""
    parsed = urlparse(url if "//" in url else "https://" + url)
    host = parsed.netloc
    tenant = host.split(".")[0]
    parts = [p for p in parsed.path.split("/") if p]
    if "cxs" in parts:  # already a CXS URL: /wday/cxs/{tenant}/{site}/jobs
        i = parts.index("cxs")
        tenant = parts[i + 1] if i + 1 < len(parts) else tenant
        site = parts[i + 2] if i + 2 < len(parts) else ""
    else:  # career-site URL: /{locale?}/{site}/...  -> site is the first non-locale segment
        segments = [p for p in parts if not _LOCALE_RE.match(p)]
        site = segments[0] if segments else ""
    return host, tenant, site


def _parse_relative_posted(text: Optional[str]):
    """Fallback when the detail fetch fails: turn 'Posted 5 Days Ago' into a date."""
    if not text:
        return None
    lowered = text.lower()
    if "today" in lowered:
        return now_utc()
    if "yesterday" in lowered:
        return now_utc() - timedelta(days=1)
    days = re.search(r"(\d+)\+?\s*day", lowered)
    if days:
        return now_utc() - timedelta(days=int(days.group(1)))
    months = re.search(r"(\d+)\+?\s*month", lowered)
    if months:
        return now_utc() - timedelta(days=30 * int(months.group(1)))
    return None


class WorkdaySource(AtsSource):
    name = "workday"

    def _fetch_board(self, url: str, criteria: SearchCriteria, limit: int) -> list[Job]:
        host, tenant, site = parse_workday_url(url)
        if not (host and tenant and site):
            raise ValueError(f"could not parse Workday tenant/site from URL: {url}")
        cxs = f"https://{host}/wday/cxs/{tenant}/{site}"
        public_base = f"https://{host}/{site}"
        company = pretty_company(tenant)

        data = post_json(
            f"{cxs}/jobs",
            json={"appliedFacets": {}, "limit": limit, "offset": 0, "searchText": criteria.search_term},
            timeout=self.timeout,
        )
        postings = (data.get("jobPostings") or [])[:limit]
        return [self._map(cxs, public_base, posting, company) for posting in postings]

    def _map(self, cxs: str, public_base: str, posting: dict, company: str) -> Job:
        path = posting.get("externalPath") or ""
        title = posting.get("title") or "Untitled"
        location = posting.get("locationsText") or ""
        req_id = (posting.get("bulletFields") or [None])[0]
        url = f"{public_base}{path}" if path else public_base
        description = ""
        posted = _parse_relative_posted(posting.get("postedOn"))
        job_type = None
        is_remote = None

        # Enrich from the per-job detail endpoint (description, real date, apply URL).
        if path:
            try:
                info = get_json(f"{cxs}{path}", timeout=self.timeout).get("jobPostingInfo", {})
                title = info.get("title") or title
                location = info.get("location") or location
                url = info.get("externalUrl") or url
                description = strip_html(info.get("jobDescription") or "", escaped=False)
                posted = parse_iso(info.get("startDate")) or posted
                job_type = normalize_job_type(info.get("timeType"))
                req_id = info.get("jobReqId") or info.get("id") or req_id
                remote_type = (info.get("remoteType") or "").lower()
                is_remote = True if ("remote" in remote_type or "remote" in location.lower()) else None
            except Exception as exc:  # noqa: BLE001 — keep the list-level fields on detail failure
                logger.debug("workday detail fetch failed for {}: {}", path, exc)

        return Job(
            title=title,
            company=company,
            location=location,
            salary=None,  # Workday rarely exposes structured pay; sometimes only in the description
            description=description,
            description_snippet=make_snippet(description),
            url=url,
            source=self.name,
            posted_date=posted,
            job_id=stable_job_id(
                self.name,
                native_id=str(req_id) if req_id else None,
                url=url,
                company=company,
                title=title,
                location=location,
            ),
            is_remote=is_remote,
            job_type=job_type,
        )
