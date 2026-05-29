"""Seen-jobs persistence. JSONL store behind a Storage interface.

The file is diffable and human-readable so the `state` branch in CI doubles as an
audit log of everything the program has ever emailed. Python only reads/writes the
local path; in CI the GitHub Actions workflow checks the file out of (and commits it
back to) the `state` branch. The STORAGE_BACKEND env var documents that intent and is
logged, but the read/write path is identical either way.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from loguru import logger

from .config import Secrets
from .models import Job
from .util import now_utc


@runtime_checkable
class Storage(Protocol):
    def load_seen(self) -> set[str]: ...
    def record(self, jobs: Iterable[Job]) -> int: ...
    def reset(self) -> None: ...


class JsonlStorage:
    """Append-only newline-delimited JSON store keyed by ``job_id``."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load_seen(self) -> set[str]:
        if not self.path.exists():
            return set()
        seen: set[str] = set()
        with self.path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed line {} in {}", lineno, self.path)
                    continue
                job_id = record.get("job_id")
                if job_id:
                    seen.add(job_id)
        logger.debug("Loaded {} seen job ids from {}", len(seen), self.path)
        return seen

    def record(self, jobs: Iterable[Job]) -> int:
        jobs = list(jobs)
        if not jobs:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = now_utc().isoformat()
        with self.path.open("a", encoding="utf-8") as fh:
            for job in jobs:
                fh.write(
                    json.dumps(
                        {
                            "job_id": job.job_id,
                            "title": job.title,
                            "company": job.company,
                            "source": job.source,
                            "first_seen": timestamp,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        logger.info("Recorded {} new job ids to {}", len(jobs), self.path)
        return len(jobs)

    def reset(self) -> None:
        if self.path.exists():
            self.path.unlink()
            logger.info("Cleared seen-store at {}", self.path)
        else:
            logger.info("Nothing to clear; {} does not exist", self.path)


def get_storage(secrets: Secrets) -> JsonlStorage:
    """Build the storage backend. Path is ``{STATE_DIR}/seen_jobs.jsonl``."""
    path = Path(secrets.state_dir) / "seen_jobs.jsonl"
    logger.debug("Storage backend='{}' path={}", secrets.storage_backend, path)
    return JsonlStorage(path)
