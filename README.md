# ysearch — how to use it

ysearch is a local, private AI job scout. Every day it pulls job postings from
several sources, scores each one against *your* criteria with a cheap AI call,
and shows you a ranked inbox. You shortlist the good ones, copy a tailored
cover note, click through to the real posting, and apply yourself. It also
tracks every application through screening, interviews, and offers — and draws
the classic job-hunt funnel from your history.

**The one rule: ysearch never applies for you.** You review and submit every
application yourself. Auto-apply bots get accounts banned, convert terribly,
and the consent checkbox on application forms legally attests a *human*
applied. This is a hard product rule, not a preference.

Everything runs on your machine: your keys, your resume, your criteria, and
your database stay local and are never committed to git.

## Quickstart (fresh machine)

One line — installs [uv](https://docs.astral.sh/uv/) if needed, then ysearch:

```bash
curl -fsSL https://raw.githubusercontent.com/AryanJ129/ysearch/main/install.sh | sh
```

(Already have uv? `uv tool install ysearch` — or run without installing:
`uvx ysearch ui`.)

Then:

1. ```bash
   ysearch ui
   ```
2. In the app, open **Settings** and add your two keys (next section).
3. Open **Settings → Criteria**, describe what you're looking for, click
   **Generate criteria with AI**, review, save. Paste your resume too.
4. Back in a terminal:
   ```bash
   ysearch scan && ysearch score
   ```
5. Refresh the app — your scored inbox is ready.

**Where your data lives:** installed this way, everything (keys, criteria,
resume, database) goes in `~/.ysearch/` — set `YSEARCH_HOME` to move it. If
you run from a cloned repo instead, the clone's folder is used, exactly as
before.

### Developer setup (clone)

```bash
git clone https://github.com/AryanJ129/ysearch.git && cd ysearch
uv sync
uv run ysearch ui   # data stays in the clone: ./config, ./ysearch.db, ./.env
```

## Get your keys (both have free tiers)

- **OpenRouter** (AI scoring + drafts): create a key at
  [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys).
  Scoring costs about $0.003 per job — a heavy day is a few cents.
- **OpenWeb Ninja / JSearch** (job discovery): free key at
  [openwebninja.com/api/jsearch](https://www.openwebninja.com/api/jsearch) —
  **200 requests/month**, no card needed. The default scan cadence uses ~4
  requests/day, which fits with room to spare.

Paste both into **Settings → API keys** and hit **Test key** on each. Keys are
saved to a local `.env` file that git is configured to never track; the app
only ever shows a masked fingerprint.

## Optional: Naukri alerts (recommended in India)

Naukri postings are under-represented in the search API, so ysearch reads your
own Naukri alert emails instead — read-only, from one Gmail label:

1. On [naukri.com](https://www.naukri.com), create job alerts for your roles.
2. In Gmail, make a filter: alerts from naukri.com → apply label `ysearch`.
3. Create a [Gmail app password](https://myaccount.google.com/apppasswords)
   (requires 2-step verification).
4. Enter all three in **Settings → Naukri / Email alerts** and hit
   **Test connection**.

Once a real alert email has landed, run `ysearch scan` and eyeball the parsed
rows — the parser ships against a synthetic fixture and may need a one-time
mapping to the real email format.

## Daily use

```bash
ysearch scan && ysearch score   # ~4 quota requests + a few cents
ysearch ui                      # browse
```

(In a cloned repo, prefix with `uv run`.)

- **Inbox** — scored jobs, best first. Filter by minimum score and location.
  *Shortlist* what looks good; *Apply ↗* opens the real posting.
- **Tracker** — each shortlisted job: generate a plain-text cover note
  (grounded in your resume — the AI is forbidden from inventing facts), keep
  notes, and move the application through states as things happen.
- **Funnel** — your pipeline as a Sankey diagram, plus response rate and
  time-in-stage.
- `ysearch digest` prints the top 10 as markdown if you prefer a terminal.

## What the flags mean

| Flag | Meaning |
|---|---|
| `salary_unknown` | The posting states no salary (very common in India) |
| `below_floor` | Stated salary is under your floor — flagged, never hidden |
| `seniority_mismatch` | Requires materially more experience than you have — score capped at 60 |
| `location_mismatch` | Not in your locations and not remote-eligible for you |
| `needs_review` | The scorer hedged — read this one yourself |

## Privacy & cost guardrails

- Local-first: no server, no telemetry, no shared data. Each user runs their
  own copy with their own keys.
- `.env`, your resume, criteria, database, and metrics are all gitignored.
- Scoring is capped at 100 jobs/day by default (`--daily-cap` to override
  deliberately); every AI call is logged with its cost to `metrics/`.

MIT licensed. Issues and PRs welcome.
