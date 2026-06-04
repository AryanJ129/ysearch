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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ysearch",
        description="AI job scout — aggregate, score, draft; you click submit.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", help="check config, db (WAL), keys, and the OpenRouter model slug")
    spike_p = sub.add_parser("spike", help="Phase-1 discovery spike: dump raw results for review")
    spike_p.add_argument("--per-query", type=int, default=10, help="rows shown per query/board")
    sub.add_parser("scan", help="(Phase 2) ingest sources into the db")
    sub.add_parser("digest", help="(Phase 2) markdown top-N digest")
    sub.add_parser("ui", help="(Phase 2) launch the Streamlit app")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return cmd_doctor()
    if args.command == "spike":
        from ysearch import spike

        return spike.run(per_query=args.per_query)
    if args.command in {"scan", "digest", "ui"}:
        print(f"`{args.command}` lands in Phase 2 — the spike gate comes first.")
        return 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
