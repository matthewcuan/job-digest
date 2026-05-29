"""Configuration: committed YAML for search/sources/email + env-only secrets."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# JobSpy boards (scraped) vs ATS boards (public APIs). Used for validation + routing.
JOBSPY_SITES = ("linkedin", "indeed", "glassdoor", "google", "ziprecruiter")
ATS_SITES = ("greenhouse", "lever", "ashby")
ALL_SOURCES = JOBSPY_SITES + ATS_SITES


class WorkMode(str, Enum):
    any = "any"
    remote = "remote"
    hybrid = "hybrid"
    onsite = "onsite"


class SearchCriteria(BaseModel):
    """Search criteria. ``must_have`` is an AND filter; ``nice_to_have`` boosts rank."""

    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    location: Optional[str] = None
    work_mode: WorkMode = WorkMode.any
    experience_level: Optional[str] = None  # free-text hint, e.g. "senior", "entry"
    date_posted_hours: Optional[int] = None  # only jobs newer than this survive
    salary_min: Optional[int] = None  # annualized; only applied when a salary is present
    job_type: Optional[str] = None  # fulltime|parttime|contract|temporary|internship
    distance: int = 50  # miles, for JobSpy
    country: str = "usa"  # country_indeed (required by Indeed/Glassdoor)

    @property
    def search_term(self) -> str:
        """Query string sent to sources = the must-have terms joined."""
        return " ".join(t.strip() for t in self.must_have if t.strip())

    @property
    def is_remote(self) -> bool:
        return self.work_mode is WorkMode.remote


class SourceConfig(BaseModel):
    enabled: bool = False
    limit: int = 25  # results_wanted (JobSpy) / max kept per ATS board
    companies: list[str] = Field(default_factory=list)  # ATS board slugs only


class SourcesConfig(BaseModel):
    # JobSpy-backed boards
    linkedin: SourceConfig = Field(default_factory=SourceConfig)
    indeed: SourceConfig = Field(default_factory=SourceConfig)
    glassdoor: SourceConfig = Field(default_factory=SourceConfig)
    google: SourceConfig = Field(default_factory=SourceConfig)
    ziprecruiter: SourceConfig = Field(default_factory=SourceConfig)
    # ATS public APIs (most reliable from CI)
    greenhouse: SourceConfig = Field(default_factory=SourceConfig)
    lever: SourceConfig = Field(default_factory=SourceConfig)
    ashby: SourceConfig = Field(default_factory=SourceConfig)

    def enabled_items(self) -> list[tuple[str, SourceConfig]]:
        return [(name, getattr(self, name)) for name in ALL_SOURCES if getattr(self, name).enabled]


class DedupConfig(BaseModel):
    fuzzy_threshold: int = 88  # rapidfuzz score (0-100); >= keeps as duplicate


class EmailConfig(BaseModel):
    smtp_host: Optional[str] = None  # may also come from SMTP_HOST env (env wins)
    smtp_port: int = 587
    sender: Optional[str] = None
    recipient: Optional[str] = None  # may also come from EMAIL_TO env (env wins)
    use_tls: bool = True  # STARTTLS (port 587)
    use_ssl: bool = False  # implicit TLS / SMTPS (port 465); auto-on when port == 465
    subject_prefix: str = "[Job Digest]"
    send_empty_digest: bool = False  # email even when zero new jobs
    send_on_total_failure: bool = True  # email when every source failed


class AppConfig(BaseModel):
    search: SearchCriteria = Field(default_factory=SearchCriteria)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)


class Secrets(BaseSettings):
    """Credentials and runtime knobs from environment / .env only — never committed.

    Field names map case-insensitively to env vars (``smtp_host`` <- ``SMTP_HOST``).
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    email_to: Optional[str] = None
    proxy_url: Optional[str] = None
    storage_backend: str = "file"  # "file" (local) | "repo" (CI commits .state/)
    state_dir: str = ".state"

    @field_validator("smtp_port", mode="before")
    @classmethod
    def _blank_port_to_none(cls, value):
        # GitHub Actions injects an unset secret as "" (not absent); an empty string
        # would otherwise fail int coercion and crash the whole run at startup.
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        return value


@dataclass
class ResolvedEmail:
    """SMTP settings with env secrets layered over committed config (env wins)."""

    host: Optional[str]
    port: int
    user: Optional[str]
    password: Optional[str]
    sender: Optional[str]
    recipient: Optional[str]
    use_tls: bool
    use_ssl: bool
    subject_prefix: str

    @property
    def deliverable(self) -> bool:
        return bool(self.host and self.recipient and self.sender)

    @property
    def implicit_tls(self) -> bool:
        """Use SMTPS (connect over TLS) rather than STARTTLS."""
        return self.use_ssl or self.port == 465


def resolve_email(cfg: EmailConfig, secrets: Secrets) -> ResolvedEmail:
    return ResolvedEmail(
        host=secrets.smtp_host or cfg.smtp_host,
        port=secrets.smtp_port or cfg.smtp_port,
        user=secrets.smtp_user,
        password=secrets.smtp_password,
        sender=cfg.sender or secrets.smtp_user,
        recipient=secrets.email_to or cfg.recipient,
        use_tls=cfg.use_tls,
        use_ssl=cfg.use_ssl,
        subject_prefix=cfg.subject_prefix,
    )


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Config not found at {p}. Copy config.yaml.example to config.yaml and edit it."
        )
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


def load_secrets() -> Secrets:
    return Secrets()
