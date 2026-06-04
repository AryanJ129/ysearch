# WhySearch *(name WIP)*

Open-source AI job scout: **aggregate → score → draft → you click submit.**

- Pulls postings from JSearch (Google for Jobs aggregation), public ATS job boards
  (Greenhouse / Lever / Ashby), and your own Naukri job-alert emails. **No scraping.**
- Scores every posting against *your* criteria with one cheap LLM call
  (Claude Haiku via OpenRouter) — fit reasons and flags, salary flagged but never hard-filtered.
- Daily digest, shortlist, tailored cover-note drafts, and an application tracker
  with the classic job-hunt Sankey funnel.
- **Never auto-applies.** A human reviews and clicks submit, always — auto-apply gets
  accounts banned and converts terribly anyway.

Local-first: your criteria, resume, inbox, and database never leave your machine.
Each user runs their own copy with their own keys.

**Status:** early build (scaffold + discovery spike). Full quickstart lands with the
OSS-polish phase.
