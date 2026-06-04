"""ysearch CLI."""

from __future__ import annotations

import argparse
import os
import sys

from ysearch import config, llm, store


def cmd_doctor() -> int:
    config.load_env()
    ok = True
    for name, loader in (("criteria", config.load_criteria), ("companies", config.load_companies)):
        try:
            loader()
            print(f"[ok] config/{name}.yaml")
        except Exception as exc:  # surfaced, not swallowed — doctor reports everything
            ok = False
            print(f"[!!] config/{name}.yaml: {exc}")
    conn = store.connect()
    store.init_db(conn)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    if mode == "wal":
        print("[ok] db (WAL mode)")
    else:
        ok = False
        print(f"[!!] db journal_mode={mode} (expected wal)")
    for env in ("OPENWEBNINJA_API_KEY", "OPENROUTER_API_KEY"):
        print(f"[ok] {env} set" if os.environ.get(env) else f"[--] {env} not set")
    try:
        llm.validate_model_slug()
        print(f"[ok] OpenRouter model {llm.MODEL}")
    except Exception as exc:
        ok = False
        print(f"[!!] {exc}")
    return 0 if ok else 1


def cmd_score(limit: int | None, daily_cap: int) -> int:
    from ysearch import score as score_mod

    config.load_env()
    criteria = config.load_criteria()
    conn = store.connect()
    store.init_db(conn)
    summary = score_mod.score_unscored(conn, criteria, limit=limit, daily_cap=daily_cap)
    conn.close()
    print(
        f"Scored {summary['scored']} jobs (${summary['spent_usd']});"
        f" {summary['failed']} failed; {summary['cap_left']} left in today's cap."
    )
    return 0


def cmd_digest(n: int) -> int:
    from ysearch import digest

    conn = store.connect()
    store.init_db(conn)
    print(digest.render(conn, n))
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ysearch",
        description="AI job scout — aggregate, score, draft; you click submit.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", help="check config, db (WAL), keys, and the OpenRouter model slug")
    spike_p = sub.add_parser("spike", help="discovery spike: dump raw results for review")
    spike_p.add_argument("--per-query", type=int, default=10, help="rows shown per query/board")
    sub.add_parser("scan", help="ingest JSearch (rotating plan) + ATS boards into the db")
    score_p = sub.add_parser("score", help="LLM-score unscored jobs (daily cap applies)")
    score_p.add_argument("--limit", type=int, default=None, help="max jobs to score this run")
    score_p.add_argument(
        "--daily-cap",
        type=int,
        default=None,
        help="override the per-day score cap (default 100) — explicit budget call",
    )
    digest_p = sub.add_parser("digest", help="print the top-N scored jobs as markdown")
    digest_p.add_argument("-n", type=int, default=10)
    sub.add_parser("ui", help="launch the Streamlit app (inbox + shortlist + drafts)")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return cmd_doctor()
    if args.command == "spike":
        from ysearch import spike

        return spike.run(per_query=args.per_query)
    if args.command == "scan":
        from ysearch import scan

        config.load_env()
        return scan.run()
    if args.command == "score":
        from ysearch.score import DAILY_CAP

        return cmd_score(args.limit, args.daily_cap or DAILY_CAP)
    if args.command == "digest":
        return cmd_digest(args.n)
    if args.command == "ui":
        import importlib.util
        import subprocess

        spec = importlib.util.find_spec("ysearch.app")
        assert spec and spec.origin, "ysearch.app module not found"
        return subprocess.call([sys.executable, "-m", "streamlit", "run", spec.origin])
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
