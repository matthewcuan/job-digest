"""Base for ATS sources (Greenhouse/Lever/Ashby) that iterate company board slugs."""
from __future__ import annotations

from loguru import logger

from ..config import SearchCriteria
from ..models import Job
from .base import JobSource


class AtsSource(JobSource):
    """Iterates configured board slugs. One bad slug is skipped; only if *every* slug
    fails do we raise (so the source is reported failed rather than silently empty).
    """

    def __init__(self, companies: list[str], timeout: int = 15):
        self.companies = list(companies)
        self.timeout = timeout

    def _fetch(self, criteria: SearchCriteria, limit: int) -> list[Job]:
        if not self.companies:
            return []
        jobs: list[Job] = []
        failures: list[str] = []
        for slug in self.companies:
            try:
                board_jobs = self._fetch_board(slug, criteria, limit)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "{} board '{}' failed: {}: {}", self.name, slug, type(exc).__name__, exc
                )
                failures.append(slug)
                continue
            jobs.extend(board_jobs[:limit])
        if failures and len(failures) == len(self.companies):
            raise RuntimeError(
                f"all {len(self.companies)} {self.name} board(s) failed: {', '.join(failures)}"
            )
        return jobs

    def _fetch_board(self, slug: str, criteria: SearchCriteria, limit: int) -> list[Job]:
        raise NotImplementedError
