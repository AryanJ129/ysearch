"""Scan: ingest all sources → normalize → dedupe-upsert into SQLite.

Quota-aware: the JSearch plan = daily queries + a round-robin slice of the
rotation pool (cursor persisted in the meta table), so on-site city passes are
part of the regular cadence. ATS boards are free and unquota'd but ingest only
title-filtered roles — a 350-role US board would otherwise drown scoring in
irrelevant rows (counts are printed, nothing is silently dropped).
"""

from __future__ import annotations

import json
import re

import httpx

from ysearch import config, normalize, store
from ysearch.config import Criteria, QuerySpec
from ysearch.sources import ats, jsearch

# Same crude filter the spike used — broad on purpose; the scorer judges fit.
ATS_TITLE_FILTER = re.compile(
    r"product|forward.deployed|solutions|automation|\bai\b|gtm|operations", re.IGNORECASE
)

_ATS_FETCHERS = {
    "greenhouse": ats.fetch_greenhouse,
    "lever": ats.fetch_lever,
    "ashby": ats.fetch_ashby,
}


def build_query_plan(criteria: Criteria, conn) -> list[QuerySpec]:
    """Daily queries + rotate_per_scan pool entries, round-robin via meta cursor."""
    plan = list(criteria.queries)
    pool = criteria.rotating_queries
    if pool and criteria.rotate_per_scan > 0:
        cursor = int(store.get_meta(conn, "rotation_cursor") or 0)
        take = min(criteria.rotate_per_scan, len(pool))
        plan += [pool[(cursor + i) % len(pool)] for i in range(take)]
        store.set_meta(conn, "rotation_cursor", str((cursor + take) % len(pool)))
    return plan


def _ingest(conn, jobs, *, country_hint: str | None) -> tuple[int, int]:
    new = merged = 0
    for job in jobs:
        bucket = normalize.location_bucket(
            job.location, remote=job.remote, country_hint=country_hint
        )
        key = normalize.dedupe_key(job, bucket)
        _, was_new = store.upsert_job(
            conn, job, bucket=bucket, key=key, raw_json=json.dumps(job.model_dump())
        )
        new += was_new
        merged += not was_new
    return new, merged


def run() -> int:
    config.load_env()
    criteria = config.load_criteria()
    companies = config.load_companies()
    conn = store.connect()
    store.init_db(conn)
    total_new = total_merged = 0

    plan = build_query_plan(criteria, conn)
    print(f"JSearch plan ({len(plan)} requests): {[s.q for s in plan]}")
    for spec in plan:
        remote = criteria.resolved_remote(spec)
        try:
            result = jsearch.search(spec.q, country=criteria.country, remote=remote or None)
        except (httpx.HTTPError, RuntimeError) as exc:
            print(f"  [!!] {spec.q!r}: {exc}")
            continue
        new, merged = _ingest(conn, result.jobs, country_hint=criteria.country)
        total_new += new
        total_merged += merged
        print(f"  [ok] {spec.q!r}: {len(result.jobs)} jobs ({new} new, {merged} merged)")

    for kind, fetcher in _ATS_FETCHERS.items():
        for slug in getattr(companies, kind):
            try:
                jobs = fetcher(slug)
            except httpx.HTTPError as exc:
                print(f"  [!!] {kind}:{slug}: {exc}")
                continue
            kept = [j for j in jobs if ATS_TITLE_FILTER.search(j.title)]
            new, merged = _ingest(conn, kept, country_hint=None)
            total_new += new
            total_merged += merged
            print(
                f"  [ok] {kind}:{slug}: kept {len(kept)}/{len(jobs)} roles"
                f" (title filter) — {new} new, {merged} merged"
            )

    conn.commit()
    total_rows = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print(f"Scan done: {total_new} new, {total_merged} merged, {total_rows} jobs in db.")
    conn.close()
    return 0
