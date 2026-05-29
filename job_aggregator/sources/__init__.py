"""Source registry: build the enabled sources from config."""
from __future__ import annotations

from typing import Optional

from loguru import logger

from ..config import ATS_SITES, JOBSPY_SITES, AppConfig
from .ashby import AshbySource
from .base import JobSource
from .greenhouse import GreenhouseSource
from .lever import LeverSource

_ATS_CLASSES = {"greenhouse": GreenhouseSource, "lever": LeverSource, "ashby": AshbySource}


def build_sources(config: AppConfig, proxy_url: Optional[str] = None) -> list[tuple[JobSource, int]]:
    """Return ``(source, per-board limit)`` pairs for every enabled source."""
    built: list[tuple[JobSource, int]] = []
    for name, source_cfg in config.sources.enabled_items():
        if name in JOBSPY_SITES:
            from .jobspy_source import JobSpySource  # lazy: defer JobSpy+pandas import

            built.append((JobSpySource(name, proxy_url=proxy_url), source_cfg.limit))
        elif name in ATS_SITES:
            if not source_cfg.companies:
                logger.warning("source '{}' is enabled but has no companies configured; skipping", name)
                continue
            built.append((_ATS_CLASSES[name](source_cfg.companies), source_cfg.limit))
        else:  # pragma: no cover — config validation should prevent this
            logger.warning("unknown source '{}' in config; skipping", name)
    return built


__all__ = ["build_sources", "JobSource", "GreenhouseSource", "LeverSource", "AshbySource"]
