#!/bin/sh
# ysearch one-line installer — installs uv if missing, then ysearch as a uv tool.
#
#   curl -fsSL https://raw.githubusercontent.com/AryanJ129/ysearch/main/install.sh | sh
#
# Everything ysearch stores (keys, criteria, resume, database) stays local —
# in ~/.ysearch when installed this way. Nothing is sent anywhere.
set -eu

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv (Python package manager, from astral.sh)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Make uv visible to THIS run — its installer puts it in ~/.local/bin.
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "Installing ysearch..."
# PyPI is the fast path; fall back to the repo so the installer also works
# for pre-release commits.
if ! uv tool install --force ysearch 2>/dev/null; then
    echo "(PyPI unavailable — installing from GitHub instead)"
    uv tool install --force "git+https://github.com/AryanJ129/ysearch"
fi

if ! command -v ysearch >/dev/null 2>&1; then
    # uv's tool bin dir isn't on PATH yet — let uv add it to the shell rc.
    uv tool update-shell || true
    echo
    echo "Installed — open a NEW terminal so PATH updates, then run:  ysearch ui"
else
    echo
    echo "Done. Run:  ysearch ui"
fi
echo "Your data lives in ~/.ysearch (config, keys, database) — local only."
