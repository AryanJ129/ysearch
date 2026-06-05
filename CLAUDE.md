# ysearch — Claude Code context

Local, private AI job scout: aggregate postings → score against the owner's
criteria with one cheap Haiku call each → ranked inbox → grounded cover-note
drafts → full application tracker with funnel analytics. Streamlit all-in-one
app, SQLite (WAL), Python 3.11+/`uv`. Public repo, MIT; users bring their own
keys — nothing personal is ever committed (see `.gitignore`).

> **PROJECT STATUS: PAUSED (2026-06-05).** Feature-complete through "close the
> loop" and in daily use as-is. See **"Resume here"** at the bottom before
> building anything new.

## Hard rules (product identity — do not violate)

- **NO auto-apply, ever.** The human reviews and submits every application.
- **NO scraping.** Sources are APIs, public ATS JSON endpoints, and the
  user's OWN alert/status emails over read-only IMAP.
- **Suggestions never move state.** Only `tracker.transition()` — behind an
  explicit owner click — changes an application. The Sankey funnel is a
  record of human decisions.
- **Deterministic guarantees live in CODE, not prompts** (score caps, scam
  regex, staleness, repost counts). The model adds judgment on top.
- Salary is scored/flagged, **never hard-filtered**. Dedupe key =
  company|title|location_bucket, **never URL**.
- Keys are masked (prefix+last4) everywhere, never logged, never rendered in
  full. Email contents are never printed or logged.

## Architecture (one line per module, `src/ysearch/`)

- `cli.py` — `ysearch doctor|spike|scan|score|digest|ui`.
- `paths.py` — data-dir resolution: **repo mode** (cwd has `config/` or
  `ysearch.db`) wins, else `$YSEARCH_HOME` / `~/.ysearch` seeded from
  `_seed/` (kept byte-identical to `config/*.example.*` by a sync test).
- `config.py` — .env (filled-value-wins semantics), criteria/companies YAML
  (pydantic-validated), resume; all under `paths.data_dir()`.
- `store.py` — SQLite schema + **`_migrate()`** (PRAGMA-checked ALTERs — see
  migration discipline below), dedupe-merge `upsert_job` (union URLs, ATS
  primary, longest description, earliest posted_at, repost detection >14d).
- `scan.py` — sources → normalize → upsert; JSearch rotation plan (quota:
  ~4 req/day of 200/mo), source toggles in db meta; ends with statussync +
  stall-suggestion generation.
- `score.py` — Haiku scoring, injection-guarded; `apply_policy` caps in code
  (scam_risk ≤20, seniority_mismatch ≤60); 100/day cap; latest-row-per-job.
- `signals.py` — HIGH-PRECISION scam regex net, force-unioned after parse.
- `prompts.py` — all prompts + `ALLOWED_FLAGS` + the `<posting>`/`<email>`
  injection guards; render_posting includes deterministic posting age.
- `normalize.py` — location buckets, dedupe keys, `posting_age_days`.
- `tracker.py` — state machine (events table) + funnel stats + response
  intelligence (`response_by_source`, `median_days_to_response`).
- `statussync.py` — status-label emails → ONE Haiku classify each →
  Apply/Dismiss suggestions; `processed_emails` dedupe; LLM failure = retry
  next scan, malformed email = "other", never a crash.
- `nudges.py` — deterministic follow-up nudges (live-computed; notes reset
  the clock) + auto-ghost suggestions (`source='stalled'`, one per stall
  episode, dismissal holds until the application moves).
- `sources/` — jsearch (OpenWeb Ninja), ats (Greenhouse/Lever/Ashby JSON),
  email_naukri (IMAP alert parser, fixture-built).
- `drafts.py` / `digest.py` / `sankey.py` / `app.py` — grounded plain-text
  cover notes, markdown digest, funnel figure, the Streamlit UI.
- `llm.py` — OpenRouter; model slug is `anthropic/claude-haiku-4.5`
  (**dot, not dash** — validated against live /models); cost telemetry to
  `metrics/llm_costs.jsonl`.

## Conventions & discipline

- `ruff format` + `ruff check` clean; tests via `uv run pytest`
  (**check the exit code directly, never through a pipe**). 160 tests green
  at pause; AppTest covers the real app script.
- **Never fake a test to get green** — fix code, not assertions; explain
  before changing any test's contract.
- **Hermetic tests**: `tests/conftest.py` pins `YSEARCH_HOME` to a tmp dir;
  chdir-style tests rely on repo-mode detection; keys stripped where spend
  could leak. Email fixtures are synthetic `.eml` files in `tests/fixtures/`.
- **Migration discipline**: new columns go in BOTH the `CREATE TABLE` schema
  and `store._migrate()`; verify against the real db only after backing it
  up (`cp ysearch.db ysearch.backup-<date>.db` — backups are gitignored).
- README **is** the in-app Help tab (single source — update both audiences
  in one edit). `_seed/` files must stay byte-identical to
  `config/*.example.*` (a test enforces it).
- Commits: small, one feature each, no co-author trailers.
- AppTest gotcha: after a button click that calls `st.rerun()`, the element
  list reflects the click-run — assert UI absence only after an explicit
  extra `at.run()`. DB side-effects can be asserted immediately.

## Resume here (when the pause ends)

State at pause (all pushed, `main`): v1 + v2 complete — ghost shields
(posting age, repost counts, hide-stale), scam flags (rubric + regex net +
≤20 cap), email status sync (label → Haiku → Apply/Dismiss suggestions),
close-the-loop (follow-up nudges, auto-ghost, response-by-source analytics),
home-dir data story + curl installer. 160 tests.

Pending, in rough order of value:

1. **PyPI publish** — the `ysearch` name was free (verified 2026-06-05) and
   `uv build` artifacts are ready; needs the owner's PyPI account/token
   (`uv publish`). Re-check name availability after the pause.
2. **Real-email validation** — two `TODO: validate against real` markers:
   `sources/email_naukri.py` (Naukri alert format) and `statussync.py`
   (real ATS status emails). Both parsers are fixture-built and defensive;
   eyeball the first real emails and map fields if needed. Gmail labels
   exist; the filter import file is generated at `config/gmail_filters.xml`
   (gitignored — regenerate if missing: two filters, naukri.com → `ysearch`,
   ATS senders → `ysearch-status`).
3. **Roadmap backlog (owner-ranked)**: LinkedIn/Indeed/Glassdoor alert-email
   parser family (spike on one real alert email first — formats vary);
   JD↔resume gap analysis; Workable/SmartRecruiters/Recruitee adapters +
   ATS auto-discovery for the watchlist; local market intelligence from the
   scanned corpus; per-job application pack; `.ics` from interview invites;
   Teal/Huntr CSV import; multi-profile criteria.

Known small debts: scam regex is intentionally narrow (model rubric is the
wide net); `jobs.source` backfill for pre-v2.1 rows was attributed by
primary-URL host (documented in `store._migrate`); statussync matcher skips
company names under 3 chars.
