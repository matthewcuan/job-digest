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


# --- keyword matching (shared by the filter and the ranker) -------------------------
# mode: "substring" (term anywhere) | "word" (whole-word/phrase, \bterm\b).
# fields: "title" | "title_and_description".

def _haystacks(title: str, description: str, fields: str) -> list[str]:
    hays = [(title or "").lower()]
    if fields != "title":
        hays.append((description or "").lower())
    return hays


def term_present(term: str, title: str, description: str, *, mode: str = "substring",
                 fields: str = "title_and_description") -> bool:
    """Whether ``term`` appears in the configured field(s) under the configured mode."""
    t = term.strip().lower()
    if not t:
        return False
    for hay in _haystacks(title, description, fields):
        if mode == "word":
            if re.search(r"\b" + re.escape(t) + r"\b", hay):
                return True
        elif t in hay:
            return True
    return False


def term_count(term: str, title: str, description: str, *, mode: str = "substring",
               fields: str = "title_and_description") -> int:
    """How many times ``term`` appears (for ranking density)."""
    t = term.strip().lower()
    if not t:
        return 0
    total = 0
    for hay in _haystacks(title, description, fields):
        if mode == "word":
            total += len(re.findall(r"\b" + re.escape(t) + r"\b", hay))
        else:
            total += hay.count(t)
    return total


# --- skill / salary extraction (best-effort, deterministic) -------------------------
# For the digest: pull the tech stack and a salary range out of free-text descriptions
# that the structured source fields don't expose. Approximate, not authoritative.

# Punctuation-bearing terms matched as substrings (word boundaries don't play well with #/+/.).
_SKILL_SUB = [
    (".NET", [".net", "dotnet"]), ("C#", ["c#", "c-sharp", "csharp"]),
    ("C++", ["c++"]), ("ASP.NET", ["asp.net"]), ("Node.js", ["node.js", "nodejs"]),
]
# Alphabetic terms matched on word boundaries (display, [lowercase aliases]).
_SKILL_WORD = [
    ("Python", ["python"]), ("JavaScript", ["javascript"]), ("TypeScript", ["typescript"]),
    ("Java", ["java"]), ("Go", ["golang"]), ("Rust", ["rust"]), ("Ruby", ["ruby"]),
    ("PHP", ["php"]), ("Scala", ["scala"]), ("Kotlin", ["kotlin"]), ("Swift", ["swift"]),
    ("Elixir", ["elixir"]),
    ("React", ["react"]), ("Angular", ["angular"]), ("Vue", ["vue"]),
    ("Django", ["django"]), ("Flask", ["flask"]), ("FastAPI", ["fastapi"]),
    ("Spring", ["spring"]), ("Rails", ["rails"]),
    ("AWS", ["aws"]), ("GCP", ["gcp"]), ("Azure", ["azure"]),
    ("Kubernetes", ["kubernetes", "k8s"]), ("Docker", ["docker"]), ("Terraform", ["terraform"]),
    ("Kafka", ["kafka"]), ("Spark", ["spark"]), ("Airflow", ["airflow"]),
    ("PostgreSQL", ["postgresql", "postgres"]), ("MySQL", ["mysql"]),
    ("MongoDB", ["mongodb"]), ("Redis", ["redis"]), ("Elasticsearch", ["elasticsearch"]),
    ("Snowflake", ["snowflake"]), ("GraphQL", ["graphql"]), ("gRPC", ["grpc"]), ("SQL", ["sql"]),
]


def extract_skills(text: str, limit: int = 8) -> list[str]:
    """Best-effort list of technologies/languages mentioned in ``text`` (title+description).
    Deterministic keyword match; punctuation terms first so .NET/C# rank ahead of languages."""
    if not text:
        return []
    low = text.lower()
    found: list[str] = []
    for display, subs in _SKILL_SUB:
        if any(s in low for s in subs):
            found.append(display)
    for display, words in _SKILL_WORD:
        if display == "Go":  # the language: "golang", or a capitalized standalone "Go"
            if "golang" in low or re.search(r"(?<![\w-])Go(?![\w-])", text):
                found.append(display)
            continue
        if any(re.search(r"\b" + re.escape(w) + r"\b", low) for w in words):
            found.append(display)
    out: list[str] = []
    for s in found:  # dedupe preserving order, then cap
        if s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


# Salary RANGE only (two numbers, each with a comma-group or a "k"), to avoid mistaking a
# stray "$5 - $8" or a single number for compensation.
_SALARY_NUM = r"(\d{1,3},\d{3}|\d{2,3}k)"
_SALARY_RANGE = re.compile(rf"\$\s?{_SALARY_NUM}\s?(?:[-–—]|to)\s?\$?\s?{_SALARY_NUM}", re.IGNORECASE)


def extract_salary(text: Optional[str]) -> Optional[str]:
    """Pull a salary range out of free text, or None. Conservative — requires an explicit
    range with thousands ($120,000–$160,000 or $120k–$160k). Display-only; does not feed the
    salary_min filter (we don't trust a fuzzily-parsed number to exclude jobs)."""
    if not text:
        return None
    m = _SALARY_RANGE.search(text)
    if not m:
        return None

    def _amt(s: str) -> float:
        s = s.lower().replace(",", "")
        return float(s[:-1]) * 1000 if s.endswith("k") else float(s)

    low, high = _amt(m.group(1)), _amt(m.group(2))
    if not (10_000 <= low <= high <= 2_000_000):
        return None
    return format_salary(low, high)

