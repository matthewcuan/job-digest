"""LLM scoring stage — fully offline (FakeScorer; no network)."""
from __future__ import annotations

from datetime import timedelta

from job_aggregator.config import AppConfig, LLMConfig, SearchCriteria, Secrets
from job_aggregator.llm import JobScore, _coerce, build_scorer, score_jobs
from job_aggregator.pipeline import run
from tests.helpers import NOW, make_job


class FakeScorer:
    """Returns canned scores keyed by a substring of the title; raises for "boom"."""

    def __init__(self, table: dict[str, JobScore], calls: list | None = None):
        self.table = table
        self.calls = calls if calls is not None else []

    def score(self, job, ideal_role):
        self.calls.append(job.title)
        for key, result in self.table.items():
            if key.lower() in job.title.lower():
                if result is None:
                    raise RuntimeError("boom")
                return result
        return JobScore(score=0, verdict="weak", reason="no match")


def _cfg(**llm) -> AppConfig:
    cfg = AppConfig()
    cfg.search = SearchCriteria(must_have=["engineer"], match_fields="title")
    cfg.llm = LLMConfig(enabled=True, **llm)
    return cfg


# --- score_jobs unit behavior -------------------------------------------------------

def test_score_jobs_sets_fields_and_counts():
    jobs = [make_job("a", title="Backend Engineer"), make_job("b", title="Data Engineer")]
    scorer = FakeScorer({
        "Backend": JobScore(90, "strong", "great fit"),
        "Data": JobScore(20, "weak", "off-target"),
    })
    n = score_jobs(jobs, LLMConfig(enabled=True, concurrency=1), scorer)
    assert n == 2
    assert (jobs[0].llm_score, jobs[0].llm_verdict) == (90, "strong")
    assert jobs[1].llm_score == 20


def test_score_jobs_isolates_failures():
    jobs = [make_job("a", title="Backend Engineer"), make_job("b", title="boom Engineer")]
    scorer = FakeScorer({"Backend": JobScore(80, "strong", "ok"), "boom": None})
    n = score_jobs(jobs, LLMConfig(enabled=True, concurrency=1), scorer)
    assert n == 1                       # only the good one scored
    assert jobs[0].llm_score == 80
    assert jobs[1].llm_score is None    # failed job left unscored, not crashed


def test_score_jobs_caps_at_max_jobs():
    jobs = [make_job(str(i), title=f"Engineer {i}") for i in range(5)]
    scorer = FakeScorer({"Engineer": JobScore(50, "maybe", "x")})
    n = score_jobs(jobs, LLMConfig(enabled=True, concurrency=1, max_jobs=3), scorer)
    assert n == 3
    assert sum(j.llm_score is not None for j in jobs) == 3


# --- _coerce defensive normalization -------------------------------------------------

def test_coerce_clamps_and_fills_verdict():
    assert _coerce({"score": 250, "verdict": "bogus", "reason": "x"}) == JobScore(100, "strong", "x")
    assert _coerce({"score": -5, "verdict": "", "reason": ""}) == JobScore(0, "weak", "")
    assert _coerce({"score": "50", "reason": "y"}).verdict == "maybe"  # 34-66 -> maybe


# --- build_scorer gating -------------------------------------------------------------

def test_build_scorer_none_when_disabled():
    assert build_scorer(LLMConfig(enabled=False), Secrets()) is None


def test_build_scorer_none_without_key():
    # Explicit None overrides any env/.env value (init kwargs win in pydantic-settings).
    assert build_scorer(LLMConfig(enabled=True), Secrets(anthropic_api_key=None)) is None


# --- end-to-end through run() (scorer injected) --------------------------------------

class _Src:
    name = "greenhouse"

    def __init__(self, jobs):
        self._jobs = jobs

    def fetch(self, criteria, limit):
        from job_aggregator.models import SourceResult
        return SourceResult(self.name, list(self._jobs), True, None, False)


def test_run_hard_filters_below_min_score():
    jobs = [make_job("hi", title="Backend Engineer"), make_job("lo", title="Junk Engineer")]
    scorer = FakeScorer({"Backend": JobScore(90, "strong", "fit"), "Junk": JobScore(10, "weak", "no")})
    cfg = _cfg(min_score=40, concurrency=1)
    res = run(cfg, Secrets(), sources=[(_Src(jobs), 10)], scorer=scorer)
    assert {j.job_id for j in res.new_jobs} == {"hi"}   # low score dropped
    assert res.llm_scored == 2
    assert res.after_llm == 1


def test_run_derank_only_keeps_all_and_reorders():
    # "lo" is fresher (would rank above on recency) but a low LLM score sinks it.
    jobs = [
        make_job("hi", title="Backend Engineer", posted_date=NOW - timedelta(hours=20)),
        make_job("lo", title="Other Engineer", posted_date=NOW),
    ]
    scorer = FakeScorer({"Backend": JobScore(100, "strong", "fit"), "Other": JobScore(5, "weak", "no")})
    cfg = _cfg(min_score=None, weight=1.0, concurrency=1)
    res = run(cfg, Secrets(), sources=[(_Src(jobs), 10)], scorer=scorer)
    assert {j.job_id for j in res.new_jobs} == {"hi", "lo"}   # nothing dropped
    assert res.new_jobs[0].job_id == "hi"                     # strong LLM fit ranks first
