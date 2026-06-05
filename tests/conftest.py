"""Global guard: tests must never touch the real ~/.ysearch.

Path resolution is cwd-first (a test that chdirs and creates ./config keeps
using its tmp cwd, exactly like a clone), so pointing YSEARCH_HOME at a
per-test tmp dir only redirects the home-mode fallback — which is the case
where a test without ./config would otherwise reach the user's real home.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_ysearch_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("ysearch-home")
    monkeypatch.setenv("YSEARCH_HOME", str(home))
    return home
