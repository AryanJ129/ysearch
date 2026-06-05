"""Where ysearch keeps its data: the clone's folder, or ~/.ysearch.

Repo mode — the current directory already holds ysearch data (`./config/`
from a git clone or any folder set up like one, or an existing `./ysearch.db`):
everything stays cwd-relative, exactly the verified clone workflow. Home mode —
neither marker (e.g. `uvx ysearch ui` run from anywhere): data lives in
$YSEARCH_HOME or ~/.ysearch, seeded with the packaged example configs on
first run.

Repo mode WINS over $YSEARCH_HOME, so a folder that owns data always keeps it
and the env var only redirects the no-clone fallback (tests point it at a tmp
dir so they can never touch the real ~/.ysearch).
"""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path

# Packaged copies of config/*.example.* — kept byte-identical to the repo
# files by tests/test_paths.py::test_seed_files_match_repo_examples.
_SEED_FILES = ("criteria.example.yaml", "companies.example.yaml", "resume.example.md")


def data_dir() -> Path:
    """Resolve where config/, ysearch.db, metrics/ and .env live this run."""
    if Path("config").is_dir() or Path("ysearch.db").is_file():
        return Path(".")
    home = Path(os.environ.get("YSEARCH_HOME") or Path.home() / ".ysearch").expanduser()
    if not (home / "config").is_dir():
        _seed(home)
    return home


def _seed(home: Path) -> None:
    """First home-mode run: create the dir and drop in the example configs so
    the existing copy-the-example flow (and Settings editors) work no-clone."""
    (home / "config").mkdir(parents=True, exist_ok=True)
    pkg = resources.files("ysearch") / "_seed"
    for name in _SEED_FILES:
        src = pkg / name
        dest = home / "config" / name
        if src.is_file() and not dest.exists():
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def readme_text() -> str | None:
    """README for the Help tab: the clone's file in repo mode, the packaged
    copy (force-included at wheel build) in home mode. None if neither."""
    local = Path("README.md")
    if local.exists():
        return local.read_text(encoding="utf-8")
    packaged = resources.files("ysearch") / "_seed" / "README.md"
    return packaged.read_text(encoding="utf-8") if packaged.is_file() else None
