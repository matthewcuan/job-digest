"""Shared HTTP helper for the ATS sources: JSON GET with sane retries."""
from __future__ import annotations

from typing import Optional

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

USER_AGENT = "Mozilla/5.0 (compatible; job-aggregator/0.1)"


def _is_retryable(exc: BaseException) -> bool:
    """Retry transient failures only — connection/timeout, 429, and 5xx. Never 4xx."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        return code == 429 or 500 <= code < 600
    return False


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(_is_retryable),
)
def get_json(url: str, *, timeout: int = 15, params: Optional[dict] = None):
    resp = requests.get(
        url,
        timeout=timeout,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


def pretty_company(slug: str) -> str:
    """Best-effort human company name from a board slug (APIs rarely give one)."""
    cleaned = slug.replace("-", " ").replace("_", " ").strip()
    # Don't lowercase already-mixed-case slugs like "Ashby"; only title-case all-lower ones.
    if cleaned.islower():
        return cleaned.title()
    return cleaned or slug
