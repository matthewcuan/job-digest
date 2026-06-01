"""Optional LLM relevance-scoring stage.

Scores each candidate job against a prose ``ideal_role`` and folds the score into ranking
(and can hard-filter below ``min_score``). The stage is fully degradable: when disabled,
unconfigured, or erroring, the pipeline falls back to keyword ranking and the run never
fails on an LLM problem — mirroring the per-source failure isolation elsewhere.

Provider-agnostic by design. The Anthropic backend ships today; an OpenAI-compatible
backend (Ollama / vLLM / hosted) is a thin add behind the same ``Scorer`` protocol.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Protocol

from loguru import logger

from .config import LLMConfig, LLMProvider, Secrets
from .models import Job

_VERDICTS = ("strong", "maybe", "weak")

_SYSTEM_TEMPLATE = (
    "You score how well a job posting matches a candidate's ideal role.\n\n"
    "The candidate's ideal role:\n{ideal_role}\n\n"
    "Score each job 0-100 for fit with that ideal role (100 = perfect match, 0 = "
    "irrelevant). Judge the whole posting (title and description), not just keyword "
    "overlap. Be decisive and consistent across jobs. Always respond by calling the "
    "`score_job` tool."
)

# Forced-tool structured output: the model must call this tool, so the response always
# carries a parseable {score, verdict, reason}. `strict` validates the shape server-side.
_TOOL = {
    "name": "score_job",
    "description": "Record the relevance score for a single job posting.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "description": "Fit score from 0 to 100."},
            "verdict": {"type": "string", "enum": list(_VERDICTS)},
            "reason": {"type": "string", "description": "One short sentence (<140 chars) on the fit."},
        },
        "required": ["score", "verdict", "reason"],
        "additionalProperties": False,
    },
}


@dataclass
class JobScore:
    score: int  # 0-100
    verdict: str  # one of _VERDICTS
    reason: str


def _user_content(job: Job) -> str:
    parts = [f"Title: {job.title}", f"Company: {job.company}", f"Location: {job.location}"]
    if job.salary:
        parts.append(f"Salary: {job.salary}")
    desc = (job.description or "").strip()
    if desc:
        parts.append(f"\nDescription:\n{desc[:1500]}")
    return "\n".join(parts)


def _coerce(data: dict) -> JobScore:
    """Defensively normalize tool output into a valid JobScore (clamp, fill verdict)."""
    try:
        score = int(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in _VERDICTS:
        verdict = "strong" if score >= 67 else "maybe" if score >= 34 else "weak"
    return JobScore(score=score, verdict=verdict, reason=str(data.get("reason", "")).strip())


class Scorer(Protocol):
    def score(self, job: Job, ideal_role: str) -> JobScore: ...


class AnthropicScorer:
    """Forced-tool structured scoring via the Anthropic Messages API (Haiku by default)."""

    def __init__(self, model: str, api_key: str, timeout: int) -> None:
        import anthropic  # lazy: only this backend needs the SDK installed

        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self._model = model

    def score(self, job: Job, ideal_role: str) -> JobScore:
        system = [
            {
                "type": "text",
                "text": _SYSTEM_TEMPLATE.format(ideal_role=ideal_role.strip() or "(unspecified)"),
                # Stable across every job in a run → cache the instructions+ideal_role prefix.
                "cache_control": {"type": "ephemeral"},
            }
        ]
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=200,
            temperature=0,  # deterministic, reproducible scores
            system=system,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "score_job"},
            messages=[{"role": "user", "content": _user_content(job)}],
        )
        block = next((b for b in resp.content if b.type == "tool_use"), None)
        if block is None:
            raise ValueError("model did not return a score_job tool call")
        return _coerce(dict(block.input))


def build_scorer(cfg: LLMConfig, secrets: Secrets) -> Optional[Scorer]:
    """Return a Scorer, or ``None`` if scoring should be skipped (disabled, missing key,
    SDK not installed, or unimplemented provider). ``None`` means the pipeline keyword-ranks
    exactly as before — it is never an error."""
    if not cfg.enabled:
        return None
    if cfg.provider is LLMProvider.anthropic:
        if not secrets.anthropic_api_key:
            logger.warning("llm.enabled but ANTHROPIC_API_KEY is unset — skipping LLM scoring")
            return None
        try:
            return AnthropicScorer(cfg.model, secrets.anthropic_api_key, cfg.timeout)
        except ImportError:
            logger.warning("llm.enabled but the `anthropic` package is not installed — skipping LLM scoring")
            return None
    logger.warning("llm.provider={} is not implemented yet — skipping LLM scoring", cfg.provider.value)
    return None


def score_jobs(jobs: list[Job], cfg: LLMConfig, scorer: Scorer) -> int:
    """Score ``jobs`` in place (sets the ``llm_*`` fields). Per-job failures are isolated;
    a failed job is left unscored and keyword-ranked. Caps at ``cfg.max_jobs`` and logs what
    it skipped (no silent truncation). Returns the number of jobs successfully scored."""
    targets = jobs[: cfg.max_jobs]
    if len(jobs) > cfg.max_jobs:
        logger.warning(
            "LLM scoring capped at max_jobs={}: {} job(s) left unscored (keyword-ranked only)",
            cfg.max_jobs, len(jobs) - cfg.max_jobs,
        )

    def _one(job: Job) -> bool:
        try:
            result = scorer.score(job, cfg.ideal_role)
        except Exception as exc:  # noqa: BLE001 — isolate per job, like a source fetch
            logger.debug("LLM scoring failed for {!r}: {}: {}", job.title, type(exc).__name__, exc)
            return False
        job.llm_score = result.score
        job.llm_verdict = result.verdict
        job.llm_reason = result.reason
        return True

    workers = max(1, cfg.concurrency)
    if workers == 1 or len(targets) <= 1:
        return sum(_one(job) for job in targets)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return sum(pool.map(_one, targets))
