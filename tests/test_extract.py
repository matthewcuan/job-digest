"""Skill + salary extraction from free-text descriptions."""
from __future__ import annotations

from job_aggregator.util import extract_salary, extract_skills


def test_extract_skills_languages_and_tools():
    s = extract_skills("Backend Engineer — Python and Go services with PostgreSQL on AWS and Kubernetes.")
    for tech in ("Python", "Go", "PostgreSQL", "AWS", "Kubernetes"):
        assert tech in s


def test_extract_skills_punctuation_terms():
    s = extract_skills("Build APIs in C# / .NET with ASP.NET and Node.js.")
    for tech in (".NET", "C#", "ASP.NET", "Node.js"):
        assert tech in s


def test_extract_skills_word_boundary_no_java_for_javascript():
    s = extract_skills("Frontend role using JavaScript and React.")
    assert "JavaScript" in s and "React" in s
    assert "Java" not in s   # must not false-match the "java" inside "javascript"


def test_extract_skills_dedupes_and_caps():
    s = extract_skills("python Python AWS GCP Azure Docker Kafka Spark Redis MySQL React Vue Angular")
    assert len(s) <= 8
    assert len(s) == len(set(s))


def test_extract_skills_empty():
    assert extract_skills("") == []
    assert extract_skills("A role with great culture and benefits.") == []


def test_extract_salary_range_formats():
    assert extract_salary("Base salary range is $120,000 - $160,000 per year.") == "$120k–$160k"
    assert extract_salary("Comp: $120k–$160k + equity") == "$120k–$160k"


def test_extract_salary_none_when_absent_or_ambiguous():
    assert extract_salary("Competitive pay and equity.") is None
    assert extract_salary("Snacks cost $5 - $8.") is None        # too small / not thousands
    assert extract_salary("Salary is $150,000.") is None         # single value, not a range
