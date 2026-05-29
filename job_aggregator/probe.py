"""Discover ATS board slugs for a company name across Greenhouse/Lever/Ashby.

Used by the `probe` CLI command so building the `companies:` lists in config.yaml is
copy-paste instead of guesswork.
"""
from __future__ import annotations

import re

import requests

from .sources._http import USER_AGENT

_ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}


def slug_candidates(name: str) -> list[str]:
    """Plausible slugs for a company name. Ashby slugs are case-sensitive, so we keep
    a CamelCase/raw variant alongside the lowercase ones used by Greenhouse/Lever."""
    name = name.strip()
    candidates: list[str] = []

    def add(value: str) -> None:
        if value and value not in candidates:
            candidates.append(value)

    add(re.sub(r"[^a-z0-9]", "", name.lower()))          # "acmecorp"
    add(re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))  # "acme-corp"
    add(re.sub(r"\s+", "", name))                         # "AcmeCorp" (case preserved)
    add(name)                                             # raw, e.g. "Ashby"
    return candidates


def _job_count(ats: str, slug: str, timeout: int = 10):
    """Return the open-job count if this (ats, slug) resolves, else None."""
    url = _ENDPOINTS[ats].format(slug=slug)
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=timeout
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if ats == "lever":
        return len(data) if isinstance(data, list) else None
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return len(jobs) if isinstance(jobs, list) else None


def probe_company(name: str) -> list[tuple[str, str, int]]:
    """Return [(ats, slug, job_count), ...] — at most one board per ATS.

    Slug candidates are tried lowercase-first, so the canonical lowercase slug wins for
    case-insensitive ATSes; once an ATS resolves we stop trying its other case variants.
    """
    hits: list[tuple[str, str, int]] = []
    resolved: set[str] = set()
    for slug in slug_candidates(name):
        for ats in _ENDPOINTS:
            if ats in resolved:
                continue
            count = _job_count(ats, slug)
            if count is not None:
                hits.append((ats, slug, count))
                resolved.add(ats)
    return hits
