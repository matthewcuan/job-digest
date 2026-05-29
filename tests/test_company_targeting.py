"""Indeed company targeting: company-name matching + per-company search behavior."""
from __future__ import annotations

import pandas as pd
import pytest

from job_aggregator.config import SearchCriteria
from job_aggregator.sources.jobspy_source import JobSpySource, _company_matches


@pytest.mark.parametrize(
    "job_company,target,expected",
    [
        ("Apple", "Apple", True),
        ("Apple Inc.", "Apple", True),
        ("Amazon.com", "Amazon", True),
        ("Google", "Google", True),
        ("Metabolic Inc", "Meta", False),  # token-level, not substring
        ("Microsoft Corporation", "Microsoft", True),
        ("", "Apple", False),
    ],
)
def test_company_matches(job_company, target, expected):
    assert _company_matches(job_company, target) is expected


def _fake_df(company: str) -> pd.DataFrame:
    return pd.DataFrame(
        [{"title": "Software Engineer", "company": company, "job_url": f"https://x/{company}", "id": company}]
    )


def test_per_company_search_and_filter(monkeypatch):
    import jobspy

    seen_terms: list[str] = []

    def fake_scrape(**kwargs):
        term = kwargs.get("search_term", "")
        seen_terms.append(term)
        # Emulate Indeed's company: operator returning that employer; otherwise junk.
        if 'company:"Apple"' in term:
            return _fake_df("Apple")
        if 'company:"Google"' in term:
            return _fake_df("Google")
        return _fake_df("Random Co")

    monkeypatch.setattr(jobspy, "scrape_jobs", fake_scrape)

    result = JobSpySource("indeed").fetch(
        SearchCriteria(must_have=["software"], target_companies=["Apple", "Google"]), 10
    )
    assert result.ok
    assert sorted(j.company for j in result.jobs) == ["Apple", "Google"]
    assert any('company:"Apple"' in t for t in seen_terms)
    assert any('company:"Google"' in t for t in seen_terms)
    assert all("software" in t for t in seen_terms)  # base query preserved


def test_no_targeting_runs_single_search(monkeypatch):
    import jobspy

    calls: list[str] = []

    def fake_scrape(**kwargs):
        calls.append(kwargs.get("search_term", ""))
        return _fake_df("Whatever Co")

    monkeypatch.setattr(jobspy, "scrape_jobs", fake_scrape)

    result = JobSpySource("indeed").fetch(SearchCriteria(must_have=["python"]), 5)
    assert len(calls) == 1
    assert "company:" not in calls[0]
    assert result.count == 1
