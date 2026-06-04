"""Settings-tab plumbing: env saving, criteria validation, AI onboarding."""

import os

import pytest

from ysearch import config, llm, onboard


def test_save_env_values_updates_appends_preserves(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# my comment\nOPENWEBNINJA_API_KEY=old\nJSEARCH_PROVIDER=openwebninja\n")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config.save_env_values(
        {"OPENWEBNINJA_API_KEY": "new-key", "OPENROUTER_API_KEY": "sk-new", "EMPTY": ""},
        path=env,
    )
    content = env.read_text()
    assert "# my comment" in content  # comments preserved
    assert "OPENWEBNINJA_API_KEY=new-key" in content  # updated in place
    assert "OPENWEBNINJA_API_KEY=old" not in content
    assert "JSEARCH_PROVIDER=openwebninja" in content  # untouched line preserved
    assert "OPENROUTER_API_KEY=sk-new" in content  # appended
    assert "EMPTY" not in content  # blanks never written
    assert os.environ["OPENROUTER_API_KEY"] == "sk-new"  # live for this process


def test_save_criteria_yaml_validates_before_writing(tmp_path):
    target = tmp_path / "criteria.yaml"
    with pytest.raises(Exception):
        config.save_criteria_yaml("rotate_per_scan: 2\n", path=target)  # queries missing
    assert not target.exists()  # invalid input never lands on disk
    criteria = config.save_criteria_yaml("queries: ['AI PM']\nyears_experience: 1.5\n", path=target)
    assert target.exists()
    assert criteria.years_experience == 1.5


def test_years_experience_reaches_the_scoring_prompt(tmp_path):
    criteria = config.save_criteria_yaml(
        "queries: ['AI PM']\nyears_experience: 1.5\n", path=tmp_path / "c.yaml"
    )
    text = criteria.as_prompt_text()
    assert "~1.5 years" in text
    assert "seniority" in text.lower()


def test_generate_criteria_strips_fences(monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda *a, **k: ("```yaml\nqueries: ['AI PM']\n```", 0.001))
    monkeypatch.setattr(llm, "log_cost", lambda *a, **k: None)
    text = onboard.generate_criteria_yaml("AI roles, remote, 1.5 years")
    assert "```" not in text
    import yaml

    from ysearch.config import Criteria

    assert Criteria(**yaml.safe_load(text)).queries[0].q == "AI PM"


def test_criteria_system_prompt_demands_yaml_only():
    from ysearch import prompts

    assert "ONLY the YAML" in prompts.CRITERIA_SYSTEM
    assert "years_experience" in prompts.CRITERIA_SYSTEM


def test_score_system_has_seniority_cap_rule():
    from ysearch import prompts

    assert "seniority_mismatch" in prompts.SCORE_SYSTEM
    assert "60 or below" in prompts.SCORE_SYSTEM


def test_mask_key_never_reveals_the_middle():
    assert llm.mask_key(None) == "not set"
    assert llm.mask_key("") == "not set"
    assert llm.mask_key("short") == "…rt"
    masked = llm.mask_key("sk-or-v1-aaaabbbbccccddddeeee4038")
    assert masked == "sk-or-…4038"
    assert "aaaabbbb" not in masked  # the body never appears


def test_save_companies_yaml_validates_before_writing(tmp_path):
    target = tmp_path / "companies.yaml"
    with pytest.raises(Exception):
        config.save_companies_yaml("- just\n- a list\n", path=target)  # not a mapping
    assert not target.exists()
    companies = config.save_companies_yaml("greenhouse: [anthropic]\n", path=target)
    assert companies.greenhouse == ["anthropic"]
    assert target.exists()


def test_source_toggles_default_on_and_persist(tmp_path):
    from ysearch import scan, store

    conn = store.connect(tmp_path / "t.db")
    store.init_db(conn)
    assert scan.enabled_sources(conn) == {"jsearch": True, "ats": True, "naukri": True}
    scan.set_source_enabled(conn, "jsearch", False)
    assert scan.enabled_sources(conn)["jsearch"] is False
    scan.set_source_enabled(conn, "jsearch", True)
    assert scan.enabled_sources(conn)["jsearch"] is True
    with pytest.raises(ValueError):
        scan.set_source_enabled(conn, "linkedin", True)  # never a source


def test_openrouter_check_key_no_key():
    import os

    saved = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        ok, detail = llm.check_key()
        assert ok is False and "no key" in detail
    finally:
        if saved:
            os.environ["OPENROUTER_API_KEY"] = saved
