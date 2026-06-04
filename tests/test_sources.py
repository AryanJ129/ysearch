from whysearch.sources import ats, jsearch

JSEARCH_PAYLOAD = {
    "data": [
        {
            "job_title": "AI Product Manager",
            "employer_name": "Acme AI",
            "job_city": "Chennai",
            "job_country": "IN",
            "job_is_remote": True,
            "job_min_salary": 1500000,
            "job_max_salary": 2500000,
            "job_salary_currency": "INR",
            "job_apply_link": "https://example.com/apply",
            "job_posted_at_datetime_utc": "2026-06-01T00:00:00Z",
            "job_description": "Build AI products.",
        },
        {"job_title": None, "employer_name": None},  # degenerate row must not crash
    ]
}


def test_jsearch_parse():
    jobs = jsearch.parse_jobs(JSEARCH_PAYLOAD)
    assert len(jobs) == 2
    job = jobs[0]
    assert job.source == "jsearch"
    assert job.title == "AI Product Manager"
    assert job.location == "Chennai, IN"
    assert job.remote is True
    assert job.salary_min == 1500000
    assert jobs[1].title == "(untitled)"
    assert jobs[1].company == "(unknown)"


def test_jsearch_parse_empty():
    assert jsearch.parse_jobs({}) == []
    assert jsearch.parse_jobs({"data": None}) == []


GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "title": "Forward Deployed Engineer",
            "location": {"name": "Remote - India"},
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
            "updated_at": "2026-06-01T00:00:00Z",
            "content": "&lt;p&gt;Deploy &lt;b&gt;AI&lt;/b&gt; for customers&lt;/p&gt;",
        }
    ]
}


def test_greenhouse_parse_strips_html():
    jobs = ats.parse_greenhouse("acme", GREENHOUSE_PAYLOAD)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "greenhouse"
    assert job.company == "acme"
    assert job.location == "Remote - India"
    assert "<" not in job.description
    assert "Deploy" in job.description and "AI" in job.description


def test_greenhouse_parse_empty():
    assert ats.parse_greenhouse("acme", {}) == []
