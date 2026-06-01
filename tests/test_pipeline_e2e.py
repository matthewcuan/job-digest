"""End-to-end pipeline tests with mocked sources, plus email render."""
from __future__ import annotations

from datetime import timedelta

from job_aggregator import email_renderer
from job_aggregator.config import AppConfig, SearchCriteria, Secrets, WorkMode
from job_aggregator.pipeline import run
from job_aggregator.sources.base import JobSource
from job_aggregator.storage import JsonlStorage
from tests.helpers import NOW, make_job


class FakeSource(JobSource):
    def __init__(self, name, jobs):
        self.name = name
        self._jobs = jobs

    def _fetch(self, criteria, limit):
        return list(self._jobs)


class FailingSource(JobSource):
    def __init__(self, name):
        self.name = name

    def _fetch(self, criteria, limit):
        raise RuntimeError("blocked: 429")


def _config(**search) -> AppConfig:
    cfg = AppConfig()
    cfg.search = SearchCriteria(**search)
    return cfg


def test_match_fields_title_excludes_description_only_hit():
    # An AE role whose title lacks "engineer" but whose description mentions engineers.
    source = FakeSource(
        "greenhouse",
        [
            make_job("eng", title="Software Engineer", description="build things"),
            make_job("ae", title="Account Executive", description="work with our engineers daily"),
        ],
    )
    # Default (title_and_description) lets the AE through; title-only excludes it.
    default = run(_config(must_have=["engineer"]), Secrets(), sources=[(source, 10)])
    assert {j.job_id for j in default.new_jobs} == {"eng", "ae"}

    title_only = run(
        _config(must_have=["engineer"], match_fields="title"), Secrets(), sources=[(source, 10)]
    )
    assert {j.job_id for j in title_only.new_jobs} == {"eng"}


def test_match_mode_word_vs_substring():
    source = FakeSource("greenhouse", [make_job("em", title="Engineering Manager", description="")])
    # substring: "engineer" matches "engineering"
    substr = run(_config(must_have=["engineer"], match_fields="title"), Secrets(), sources=[(source, 10)])
    assert len(substr.new_jobs) == 1
    # word: "engineer" does NOT match "engineering"
    word = run(
        _config(must_have=["engineer"], match_mode="word", match_fields="title"),
        Secrets(), sources=[(source, 10)],
    )
    assert word.new_jobs == []


def test_exclude_drops_unwanted_title_disciplines():
    source = FakeSource(
        "greenhouse",
        [
            make_job("sw", title="Software Engineer"),
            make_job("be", title="Backend Engineer"),
            make_job("mech", title="Mechanical Engineer"),
            make_job("sales", title="Sales Engineer"),
        ],
    )
    res = run(
        _config(must_have=["engineer"], match_fields="title", exclude=["mechanical", "sales"]),
        Secrets(), sources=[(source, 10)],
    )
    assert {j.job_id for j in res.new_jobs} == {"sw", "be"}


def test_exclude_is_whole_word_not_substring():
    # "rf" must drop "RF Engineer" without tripping "Performance Engineer".
    source = FakeSource(
        "greenhouse",
        [make_job("perf", title="Performance Engineer"), make_job("rf", title="RF Engineer")],
    )
    res = run(
        _config(must_have=["engineer"], match_fields="title", exclude=["rf"]),
        Secrets(), sources=[(source, 10)],
    )
    assert {j.job_id for j in res.new_jobs} == {"perf"}


def test_must_have_filters_and_remote_required():
    source = FakeSource(
        "greenhouse",
        [
            make_job("1", title="Senior Python Engineer", description="python and django", is_remote=True),
            make_job("2", title="Java Developer", description="java only", is_remote=True),
            make_job("3", title="Python Engineer (Onsite)", description="python", is_remote=False, location="NYC"),
        ],
    )
    cfg = _config(must_have=["python"], nice_to_have=["django"], work_mode=WorkMode.remote)
    result = run(cfg, Secrets(), sources=[(source, 10)])
    ids = [j.job_id for j in result.new_jobs]
    assert ids == ["1"]  # 2 lacks python; 3 is onsite


def test_salary_and_date_keep_but_flag():
    source = FakeSource(
        "greenhouse",
        [
            make_job("hi", title="Python Eng", description="python", salary="$200k", salary_max=200000),
            make_job("lo", title="Python Eng", description="python", salary="$80k", salary_max=80000, company="LowCo"),
            make_job("none", title="Python Eng", description="python", salary=None, company="NoSalaryCo"),
            make_job("nodate", title="Python Eng", description="python", company="NoDateCo", posted_date=None),
        ],
    )
    cfg = _config(must_have=["python"], salary_min=100000, date_posted_hours=168)
    result = run(cfg, Secrets(), sources=[(source, 10)])
    ids = {j.job_id for j in result.new_jobs}
    # "lo" excluded (listed salary below floor); the rest kept (missing salary/date pass).
    assert ids == {"hi", "none", "nodate"}
    nodate = next(j for j in result.new_jobs if j.job_id == "nodate")
    assert "no_date" in nodate.flags


def test_seen_filter_across_runs(tmp_path):
    source = FakeSource("greenhouse", [make_job("1", title="Python Engineer", description="python")])
    storage = JsonlStorage(tmp_path / "seen.jsonl")
    cfg = _config(must_have=["python"])

    first = run(cfg, Secrets(), storage=storage, sources=[(source, 10)])
    assert len(first.new_jobs) == 1
    storage.record(first.new_jobs)

    second = run(cfg, Secrets(), storage=storage, sources=[(source, 10)])
    assert second.new_jobs == []
    assert second.seen_skipped == 1


def test_all_failed_flag():
    result = run(_config(), Secrets(), sources=[(FailingSource("linkedin"), 5), (FailingSource("indeed"), 5)])
    assert result.all_failed is True
    assert len(result.failed_sources) == 2


def test_partial_failure_is_not_all_failed():
    good = FakeSource("greenhouse", [make_job("1", title="Python Engineer", description="python")])
    result = run(_config(must_have=["python"]), Secrets(), sources=[(good, 10), (FailingSource("linkedin"), 5)])
    assert result.all_failed is False
    assert len(result.new_jobs) == 1
    assert result.ok_sources[0].source == "greenhouse"


def test_recency_ranking_orders_newest_higher():
    # Distinct company/location so dedup keeps both; identical scoring except recency.
    old = make_job(
        "old", title="Python Engineer", company="Alpha", location="Remote - US",
        description="python", posted_date=NOW - timedelta(days=20),
    )
    fresh = make_job(
        "fresh", title="Python Engineer", company="Omega", location="Remote - EU",
        description="python", posted_date=NOW,
    )
    result = run(_config(must_have=["python"]), Secrets(), sources=[(FakeSource("greenhouse", [old, fresh]), 10)])
    assert len(result.new_jobs) == 2
    assert [j.job_id for j in result.new_jobs][0] == "fresh"


def test_end_to_end_render_html_and_text():
    source = FakeSource(
        "greenhouse",
        [make_job("1", title="Python Engineer", description="great python role", salary="$150k")],
    )
    cfg = _config(must_have=["python"])
    result = run(cfg, Secrets(), sources=[(source, 10), (FailingSource("linkedin"), 5)])
    html, text = email_renderer.render_digest(result, cfg)
    assert "Python Engineer" in html and "Python Engineer" in text
    assert "linkedin" in text  # failed source surfaced in health summary
    assert email_renderer.build_subject(result, cfg) == "[Job Digest] 1 new job"
    assert email_renderer._should_send(result, cfg) is True
