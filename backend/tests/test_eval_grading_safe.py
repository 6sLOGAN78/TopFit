from ranker.evals.grading import render_job


def test_render_job_strips_terminal_escapes() -> None:
    row = {
        "title": "Dev\x1b[2J\x1b]52;c;evil\x07",
        "company": "Acme\x9b31m",
        "location_text": "Pune",
        "city": None,
        "remote_type": "onsite",
        "remote_eligibility": "india",
        "seniority": "mid",
        "employment_type": "full_time",
        "salary_min_inr_annual": None,
        "salary_max_inr_annual": None,
        "skills": ["python"],
        "posted_at": None,
        "description_clean": "line1\nline2\ttab\x00",
    }
    out = render_job(row)
    assert "\x1b" not in out and "\x9b" not in out and "\x07" not in out and "\x00" not in out
    assert "line1\nline2\ttab" in out
