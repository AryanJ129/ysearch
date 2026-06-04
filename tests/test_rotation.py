from ysearch import scan, store
from ysearch.config import Criteria


def _criteria():
    return Criteria(
        queries=["daily-1", "daily-2"],
        rotating_queries=[
            "pool-a",
            "pool-b",
            {"q": "pool-c onsite", "remote": False},
        ],
        rotate_per_scan=2,
    )


def test_plan_is_daily_plus_rotating_slice_and_cursor_advances(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.init_db(conn)
    criteria = _criteria()

    plan1 = [s.q for s in scan.build_query_plan(criteria, conn)]
    assert plan1 == ["daily-1", "daily-2", "pool-a", "pool-b"]

    plan2 = [s.q for s in scan.build_query_plan(criteria, conn)]
    assert plan2 == ["daily-1", "daily-2", "pool-c onsite", "pool-a"]  # wraps round-robin

    plan3 = [s.q for s in scan.build_query_plan(criteria, conn)]
    assert plan3 == ["daily-1", "daily-2", "pool-b", "pool-c onsite"]


def test_onsite_pool_entry_resolves_remote_false():
    criteria = _criteria()
    onsite = criteria.rotating_queries[2]
    assert criteria.resolved_remote(onsite) is False
    assert criteria.resolved_remote(criteria.queries[0]) is True  # inherits remote_ok


def test_quota_math_stays_under_cap():
    """Cadence guard: daily plan size × 30 days must fit the 200/mo hard cap."""
    criteria = _criteria()
    per_scan = len(criteria.queries) + criteria.rotate_per_scan
    assert per_scan * 30 <= 200
