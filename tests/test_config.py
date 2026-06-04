import pytest

from ysearch import config


def test_load_criteria(tmp_path):
    # Schema v2 (rotation cadence): plain strings coerce to QuerySpec.
    p = tmp_path / "criteria.yaml"
    p.write_text("queries: ['AI PM']\nsalary_floor_lpa: 12\n")
    criteria = config.load_criteria(p)
    assert [s.q for s in criteria.queries] == ["AI PM"]
    assert criteria.queries[0].remote is None  # inherits remote_ok at resolve time
    assert criteria.salary_floor_lpa == 12
    assert criteria.country == "in"  # default
    assert criteria.rotating_queries == []  # optional


def test_criteria_mixed_query_forms(tmp_path):
    p = tmp_path / "criteria.yaml"
    p.write_text(
        "queries: ['AI PM']\n"
        "rotating_queries:\n"
        "  - 'AI solutions engineer'\n"
        "  - { q: 'AI PM in Chennai', remote: false }\n"
    )
    criteria = config.load_criteria(p)
    onsite = criteria.rotating_queries[1]
    assert onsite.q == "AI PM in Chennai"
    assert criteria.resolved_remote(onsite) is False
    assert criteria.resolved_remote(criteria.rotating_queries[0]) is True


def test_missing_criteria_points_at_example(tmp_path):
    with pytest.raises(FileNotFoundError, match="criteria.example.yaml"):
        config.load_criteria(tmp_path / "criteria.yaml")


def test_criteria_prompt_text_mentions_flag_not_filter(tmp_path):
    p = tmp_path / "criteria.yaml"
    p.write_text("queries: ['AI PM']\nsalary_floor_lpa: 12\n")
    text = config.load_criteria(p).as_prompt_text()
    assert "flag, don't filter" in text


def test_load_companies(tmp_path):
    p = tmp_path / "companies.yaml"
    p.write_text("greenhouse: [anthropic]\n")
    companies = config.load_companies(p)
    assert companies.greenhouse == ["anthropic"]
    assert companies.lever == []
