"""Base for ATS sources (Greenhouse/Lever/Ashby) that iterate company board slugs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from loguru import logger

from ..config import SearchCriteria
from ..models import Job
from .base import JobSource


class AtsSource(JobSource):
    """Iterates configured board slugs. One bad slug is skipped; only if *every* slug
    fails do we raise (so the source is reported failed rather than silently empty).

    Boards are fetched concurrently — they're independent public JSON APIs on different
    hosts, so parallel requests from one IP don't risk the blocking the scrapers do.
    """

    def __init__(self, companies: list[str], timeout: int = 15, concurrency: int = 8):
        self.companies = list(companies)
        self.timeout = timeout
        self.concurrency = concurrency

    def _fetch(self, criteria: SearchCriteria, limit: int) -> list[Job]:
        if not self.companies:
            return []
        jobs: list[Job] = []
        failures: list[str] = []
        workers = max(1, min(self.concurrency, len(self.companies)))

        def _one(slug: str):
            try:
                return slug, self._fetch_board(slug, criteria, limit), None
            except Exception as exc:  # noqa: BLE001 — isolate per board
                return slug, [], f"{type(exc).__name__}: {exc}"

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for slug, board_jobs, error in pool.map(_one, self.companies):
                if error is not None:
                    logger.warning("{} board '{}' failed: {}", self.name, slug, error)
                    failures.append(slug)
                else:
                    jobs.extend(board_jobs[:limit])

        if failures and len(failures) == len(self.companies):
            raise RuntimeError(
                f"all {len(self.companies)} {self.name} board(s) failed: {', '.join(failures)}"
            )
        return jobs

    def _fetch_board(self, slug: str, criteria: SearchCriteria, limit: int) -> list[Job]:
        raise NotImplementedError
