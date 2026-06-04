"""load_env semantics: project .env wins over shell env, blanks never clobber."""

import os

from whysearch import config


def test_filled_dotenv_value_overrides_shell_env(tmp_path, monkeypatch):
    """Anti-regression: a ~/.zshrc-exported key must not silently shadow the
    project .env key (python-dotenv's default behavior)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-shell")
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=from-project-dotenv\n")
    config.load_env(env_file)
    assert os.environ["OPENROUTER_API_KEY"] == "from-project-dotenv"


def test_blank_dotenv_line_does_not_clobber_shell_env(tmp_path, monkeypatch):
    """Copying .env.example (blank values) must not wipe a valid shell export."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-shell")
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=\nOPENWEBNINJA_API_KEY=\n")
    config.load_env(env_file)
    assert os.environ["OPENROUTER_API_KEY"] == "from-shell"


def test_missing_dotenv_is_fine(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-shell")
    config.load_env(tmp_path / "no-such.env")
    assert os.environ["OPENROUTER_API_KEY"] == "from-shell"
