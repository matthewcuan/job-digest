"""Common source interface with built-in failure isolation."""
from __future__ import annotations

from loguru import logger

from ..config import SearchCriteria
from ..models import Job, SourceResult


class JobSource:
    """Base class for every source.

    Subclasses implement ``_fetch``; the public ``fetch`` wraps it so any exception
    becomes a ``SourceResult(ok=False, ...)`` instead of propagating. This is what lets
    one broken board (e.g. Glassdoor raising ``ValueError`` on a bad location) never
    abort the others.
    """

    name: str = "base"

    def fetch(self, criteria: SearchCriteria, limit: int) -> SourceResult:
        try:
            jobs = self._fetch(criteria, limit)
        except Exception as exc:  # noqa: BLE001 — deliberate catch-all for isolation
            logger.warning("source '{}' failed: {}: {}", self.name, type(exc).__name__, exc)
            return SourceResult(
                source=self.name,
                jobs=[],
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        near_empty = len(jobs) == 0
        if near_empty:
            logger.info("source '{}' returned 0 jobs (possible block or no matches)", self.name)
        else:
            logger.info("source '{}' returned {} jobs", self.name, len(jobs))
        return SourceResult(source=self.name, jobs=jobs, ok=True, near_empty=near_empty)

    def _fetch(self, criteria: SearchCriteria, limit: int) -> list[Job]:
        raise NotImplementedError
