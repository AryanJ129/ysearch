import pytest

from whysearch import config


def test_load_criteria(tmp_path):
    p = tmp_path / "criteria.yaml"
    p.write_text("queries: ['AI PM']\nsalary_floor_lpa: 12\n")
    criteria = config.load_criteria(p)
    assert criteria.queries == ["AI PM"]
    assert criteria.salary_floor_lpa == 12
    assert criteria.country == "in"  # default


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
