"""Data-dir resolution: repo mode (cwd) vs home mode (~/.ysearch).

The contract: a clone keeps today's cwd-relative behavior byte-identical;
a no-clone run (`uvx ysearch ui`) gets a seeded $YSEARCH_HOME / ~/.ysearch.
"""

from __future__ import annotations

from pathlib import Path

from ysearch import config, paths, store

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_repo_mode_when_cwd_has_config(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    monkeypatch.chdir(tmp_path)
    assert paths.data_dir() == Path(".")


def test_repo_mode_when_cwd_has_existing_db(tmp_path, monkeypatch):
    """A folder that already owns a ysearch.db keeps owning its data, even
    without config/ — continuity for any pre-home-mode usage."""
    (tmp_path / "ysearch.db").touch()
    monkeypatch.chdir(tmp_path)
    assert paths.data_dir() == Path(".")


def test_repo_mode_wins_over_ysearch_home(tmp_path, monkeypatch):
    """A clone always uses its own files — the env var only redirects the
    no-clone fallback. This is what keeps every chdir-style test (and the
    verified clone workflow) on its own cwd."""
    (tmp_path / "config").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("YSEARCH_HOME", str(tmp_path / "elsewhere"))
    assert paths.data_dir() == Path(".")
    assert not (tmp_path / "elsewhere").exists()


def test_home_mode_uses_ysearch_home_and_seeds_examples(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("YSEARCH_HOME", str(home))
    monkeypatch.chdir(tmp_path)  # no ./config → home mode
    resolved = paths.data_dir()
    assert resolved == home
    for name in paths._SEED_FILES:
        assert (home / "config" / name).is_file(), f"{name} not seeded"


def test_home_mode_seed_never_overwrites_user_files(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("YSEARCH_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    paths.data_dir()  # first run seeds
    marker = home / "config" / "criteria.example.yaml"
    marker.write_text("queries: ['user edited']\n", encoding="utf-8")
    # config/ exists now, so re-resolution must not re-seed over the edit.
    paths.data_dir()
    assert "user edited" in marker.read_text(encoding="utf-8")


def test_seed_files_match_repo_examples():
    """The packaged seeds are copies of config/*.example.* — this test is the
    drift gate. If it fails, re-copy the repo example into src/ysearch/_seed/."""
    for name in paths._SEED_FILES:
        repo_file = REPO_ROOT / "config" / name
        seed_file = REPO_ROOT / "src" / "ysearch" / "_seed" / name
        assert seed_file.read_text(encoding="utf-8") == repo_file.read_text(encoding="utf-8"), (
            f"src/ysearch/_seed/{name} drifted from config/{name} — re-copy it"
        )


def test_home_mode_db_and_config_land_in_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("YSEARCH_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    conn = store.connect()
    store.init_db(conn)
    conn.close()
    assert (home / "ysearch.db").is_file()
    assert not (tmp_path / "ysearch.db").exists()
    assert config.config_dir() == home / "config"


def test_home_mode_env_round_trip(tmp_path, monkeypatch):
    """Settings saves land in $YSEARCH_HOME/.env and load_env reads them back."""
    home = tmp_path / "home"
    monkeypatch.setenv("YSEARCH_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HOMEMODE_TEST_KEY", raising=False)
    config.save_env_values({"HOMEMODE_TEST_KEY": "v1"})
    assert "HOMEMODE_TEST_KEY=v1" in (home / ".env").read_text(encoding="utf-8")
    monkeypatch.delenv("HOMEMODE_TEST_KEY")
    config.load_env()
    import os

    assert os.environ["HOMEMODE_TEST_KEY"] == "v1"


def test_readme_text_repo_mode(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    text = paths.readme_text()
    assert text and "ysearch" in text
