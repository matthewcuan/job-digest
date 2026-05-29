"""Workday adapter: URL parsing + field mapping (mocked, no network)."""
from __future__ import annotations

import pytest

from job_aggregator.config import SearchCriteria
from job_aggregator.sources.workday import WorkdaySource, parse_workday_url


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
            ("nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite"),
        ),
        (
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA/Foo_JR1",
            ("nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite"),
        ),
        (
            "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Careers/jobs",
            ("acme.wd1.myworkdayjobs.com", "acme", "Careers"),
        ),
    ],
)
def test_parse_workday_url(url, expected):
    assert parse_workday_url(url) == expected


def test_workday_fetch_board_maps_postings(monkeypatch):
    import job_aggregator.sources.workday as wd

    def fake_post(url, *, json=None, timeout=15):
        assert url.endswith("/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs")
        assert json["searchText"] == "engineer"  # search term forwarded server-side
        return {
            "total": 1,
            "jobPostings": [
                {
                    "title": "Senior Engineer",
                    "externalPath": "/job/US-CA-Santa-Clara/Senior-Engineer_JR1",
                    "locationsText": "US, CA, Santa Clara",
                    "postedOn": "Posted Today",
                    "bulletFields": ["JR1"],
                }
            ],
        }

    def fake_get(url, *, timeout=15, params=None):
        return {
            "jobPostingInfo": {
                "title": "Senior Engineer",
                "jobDescription": "<div><p>Build <b>GPUs</b>. Remote OK.</p></div>",
                "startDate": "2026-05-20",
                "location": "US, CA, Santa Clara",
                "timeType": "Full time",
                "externalUrl": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Senior-Engineer_JR1",
                "jobReqId": "JR1",
                "remoteType": "Remote",
            }
        }

    monkeypatch.setattr(wd, "post_json", fake_post)
    monkeypatch.setattr(wd, "get_json", fake_get)

    source = WorkdaySource(["https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"])
    result = source.fetch(SearchCriteria(must_have=["engineer"]), 10)

    assert result.ok and result.count == 1
    job = result.jobs[0]
    assert job.title == "Senior Engineer"
    assert job.company == "Nvidia"  # derived from the tenant
    assert job.source == "workday"
    assert job.job_type == "fulltime"  # normalized from "Full time"
    assert job.is_remote is True  # remoteType="Remote"
    assert job.posted_date is not None and job.posted_date.year == 2026
    assert "Build GPUs" in job.description and "<" not in job.description  # HTML stripped
    assert job.url.endswith("Senior-Engineer_JR1")


def test_workday_detail_failure_falls_back_to_list_fields(monkeypatch):
    import job_aggregator.sources.workday as wd

    monkeypatch.setattr(
        wd, "post_json",
        lambda url, *, json=None, timeout=15: {
            "jobPostings": [
                {"title": "Eng", "externalPath": "/job/x_JR9", "locationsText": "Remote",
                 "postedOn": "Posted Yesterday", "bulletFields": ["JR9"]}
            ]
        },
    )

    def boom(url, *, timeout=15, params=None):
        raise RuntimeError("detail 500")

    monkeypatch.setattr(wd, "get_json", boom)
    source = WorkdaySource(["https://x.wd1.myworkdayjobs.com/en-US/Careers"])
    result = source.fetch(SearchCriteria(), 5)
    assert result.ok and result.count == 1
    job = result.jobs[0]
    assert job.title == "Eng"
    assert job.posted_date is not None  # parsed from "Posted Yesterday"
    assert job.url.endswith("/job/x_JR9")  # constructed public URL fallback
