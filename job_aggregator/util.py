"""Shared helpers: text cleaning, salary formatting, stable IDs, date parsing."""
from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from typing import Optional

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: Optional[str], *, escaped: bool = False) -> str:
    """Return plain text from HTML input.

    ``escaped=True`` (Greenhouse) means the input is *entity-escaped* HTML
    (``&lt;div&gt;``): unescape first to reveal the real tags, strip them, then
    unescape again to decode remaining entities like ``&amp;`` / ``&nbsp;``.

    ``escaped=False`` (Ashby/Lever real HTML, or plain text) strips tags first, so
    content that legitimately *displays* escaped markup (e.g. a code sample literally
    showing ``&lt;div&gt;``) survives instead of being unescaped-then-deleted.
    """
    if not text:
        return ""
    if escaped:
        text = html.unescape(text)
    no_tags = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", html.unescape(no_tags)).strip()


def make_snippet(text: Optional[str], length: int = 300, *, escaped: bool = False) -> str:
    """~``length``-char plain-text snippet, truncated on a word boundary."""
    clean = strip_html(text, escaped=escaped)
    if len(clean) <= length:
        return clean
    truncated = clean[:length].rsplit(" ", 1)[0].rstrip(".,;:—-")
    return truncated + "…"


_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "C$", "AUD": "A$"}
_INTERVAL_SUFFIX = {
    "yearly": "/yr",
    "annual": "/yr",
    "annually": "/yr",
    "monthly": "/mo",
    "weekly": "/wk",
    "daily": "/day",
    "hourly": "/hr",
}

# Lever uses tokens like "per-year-salary" / "per-hour-wage"; JobSpy uses "yearly" etc.
_INTERVAL_ALIASES = {
    "year": "yearly", "yr": "yearly", "annum": "yearly",
    "month": "monthly", "mo": "monthly",
    "week": "weekly", "wk": "weekly",
    "day": "daily",
    "hour": "hourly", "hr": "hourly",
}


def _interval_suffix(interval: Optional[str]) -> str:
    if not interval:
        return ""
    key = interval.strip().lower()
    if key in _INTERVAL_SUFFIX:
        return _INTERVAL_SUFFIX[key]
    # Normalize Lever-style tokens: "per-year-salary" -> "year" -> "yearly".
    key = key.replace("per-", "").replace("-salary", "").replace("-wage", "").replace("_", "-").strip("- ")
    key = _INTERVAL_ALIASES.get(key, key)
    return _INTERVAL_SUFFIX.get(key, "")


def coerce_amount(value) -> Optional[float]:
    """Parse a salary number; ``None`` for missing/NaN/non-positive values."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    # JobSpy fills missing salary columns with NaN, which is a float but != itself.
    if amount != amount or amount <= 0:  # NaN check + non-positive guard
        return None
    return amount


def format_salary(min_amount=None, max_amount=None, interval=None, currency="USD") -> Optional[str]:
    """Format a salary range into a compact string, or ``None`` if nothing usable."""
    low = coerce_amount(min_amount)
    high = coerce_amount(max_amount)
    if low is None and high is None:
        return None
    symbol = _CURRENCY_SYMBOLS.get((currency or "USD").upper(), f"{(currency or '').upper()} ".strip() + " ")

    def fmt(amount: float) -> str:
        if amount >= 1000:
            return f"{symbol}{amount / 1000:.0f}k"
        return f"{symbol}{amount:.0f}"

    if low and high and low != high:
        body = f"{fmt(low)}–{fmt(high)}"
    else:
        body = fmt(low or high)
    return body + _interval_suffix(interval)


def _canonical_url(url: str) -> str:
    """Normalize a URL for fallback dedup (drop fragment + trailing slash)."""
    cleaned = url.split("#", 1)[0].rstrip("/")
    return cleaned.lower()


def stable_job_id(
    source: str,
    *,
    native_id: Optional[str] = None,
    url: Optional[str] = None,
    company: str = "",
    title: str = "",
    location: str = "",
) -> str:
    """Deterministic 16-char id for cross-run dedup.

    Prefers the source's own stable id (always available from JobSpy's ``id`` column
    and from every ATS API). Falls back to the URL, then to company|title|location.
    Using ``native_id`` avoids the trap where stripping query strings would collapse
    Indeed URLs (whose job key lives in ``?jk=``) onto one another.
    """
    if native_id:
        basis = f"{source}:{native_id}"
    elif url:
        basis = f"{source}:{_canonical_url(url)}"
    else:
        basis = f"{source}:{company}|{title}|{location}".lower()
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def parse_epoch_ms(value) -> Optional[datetime]:
    """Parse a Unix epoch-milliseconds value (Lever's ``createdAt``) to aware UTC."""
    try:
        ms = float(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def parse_iso(value) -> Optional[datetime]:
    """Parse an ISO-8601 string (Greenhouse/Ashby) to an aware datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return _ensure_aware(value)
    text = str(value).strip()
    if not text:
        return None
    # datetime.fromisoformat handles offsets like -04:00 and fractional seconds on 3.11+.
    normalized = text.replace("Z", "+00:00")
    try:
        return _ensure_aware(datetime.fromisoformat(normalized))
    except ValueError:
        return None


def _ensure_aware(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC so comparisons never raise."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


_JOB_TYPE_ALIASES = {
    "full": "fulltime",
    "fulltime": "fulltime",
    "part": "parttime",
    "parttime": "parttime",
    "contract": "contract",
    "contractor": "contract",
    "temp": "temporary",
    "temporary": "temporary",
    "intern": "internship",
    "internship": "internship",
}


def normalize_job_type(job_type: Optional[str]) -> Optional[str]:
    """Canonicalize a job-type label (``"Full-time"`` -> ``"fulltime"``)."""
    if not job_type:
        return None
    key = job_type.strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    return _JOB_TYPE_ALIASES.get(key, key)

