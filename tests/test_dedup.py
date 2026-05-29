"""Deduplication: exact, fuzzy, field-merge, and non-merge of distinct postings."""
from __future__ import annotations

from job_aggregator.dedup import deduplicate
from tests.helpers import make_job


def test_exact_dedup_keeps_richer_description():
    a = make_job("1", description="short")
    b = make_job("1", description="a much longer and richer description")
    out = deduplicate([a, b])
    assert len(out) == 1
    assert out[0].description == "a much longer and richer description"


def test_merge_backfills_missing_salary_from_thinner_copy():
    rich = make_job("1", description="long detailed description text", salary=None, salary_max=None)
    thin = make_job("1", description="short", salary="$200k", salary_max=200000)
    out = deduplicate([rich, thin])
    assert len(out) == 1
    assert out[0].description == "long detailed description text"  # richer kept
    assert out[0].salary == "$200k"  # but salary grafted in
    assert out[0].salary_max_amount == 200000


def test_fuzzy_dedup_across_sources():
    a = make_job("1", title="Senior Python Engineer", company="Acme", source="indeed", description="aaaa")
    b = make_job("2", title="Senior Python Engineer", company="Acme Inc", source="linkedin", description="bb")
    out = deduplicate([a, b], fuzzy_threshold=88)
    assert len(out) == 1


def test_distinct_locations_not_merged():
    a = make_job("1", title="Data Engineer", location="Ontario")
    b = make_job("2", title="Data Engineer", location="British Columbia")
    out = deduplicate([a, b], fuzzy_threshold=88)
    assert len(out) == 2


def test_distinct_roles_not_merged():
    a = make_job("1", title="Frontend Engineer")
    b = make_job("2", title="Backend Data Platform Architect")
    out = deduplicate([a, b], fuzzy_threshold=88)
    assert len(out) == 2
