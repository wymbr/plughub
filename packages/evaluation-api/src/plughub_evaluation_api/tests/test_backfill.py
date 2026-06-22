"""
test_backfill.py — R12: ordered + quota-aware backfill.

Covers the two behaviours R12 adds to run_campaign_backfill:
  1. segments are processed in close order (ended_at) regardless of fetch order;
  2. mode="quota" drives the SAME stateful deficit counter as the forward path,
     so the deterministic floor+deficit selection is reproducible from any input
     ordering.
"""
from __future__ import annotations

import random

import pytest

from .. import backfill as bf
from ..backfill import _close_order_key, run_campaign_backfill


# ─── Fake async Redis (mirrors decode_responses=True) ─────────────────────────

class _FakeAsyncRedis:
    def __init__(self):
        self.hashes: dict[str, dict[str, int]] = {}
        self.sets: dict[str, set] = {}

    async def sadd(self, key, member):
        s = self.sets.setdefault(key, set())
        if member in s:
            return 0
        s.add(member)
        return 1

    async def expire(self, key, ttl):  # noqa: ARG002
        return True

    async def hincrby(self, key, field, amount=1):
        h = self.hashes.setdefault(key, {})
        h[field] = int(h.get(field, 0)) + amount
        return h[field]

    async def hget(self, key, field):
        v = self.hashes.get(key, {}).get(field)
        return None if v is None else str(v)


# ─── Ordering helper (pure) ───────────────────────────────────────────────────

class TestCloseOrderKey:
    def test_orders_by_ended_at(self):
        segs = [
            {"segment_id": "c", "ended_at": "2026-06-22T10:03:00Z"},
            {"segment_id": "a", "ended_at": "2026-06-22T10:01:00Z"},
            {"segment_id": "b", "ended_at": "2026-06-22T10:02:00Z"},
        ]
        ordered = [s["segment_id"] for s in sorted(segs, key=_close_order_key)]
        assert ordered == ["a", "b", "c"]

    def test_falls_back_to_started_at_then_segment_id(self):
        segs = [
            {"segment_id": "z", "started_at": "2026-06-22T10:00:00Z"},
            {"segment_id": "a", "started_at": "2026-06-22T10:00:00Z"},
        ]
        # same time → stable tiebreak by segment_id
        ordered = [s["segment_id"] for s in sorted(segs, key=_close_order_key)]
        assert ordered == ["a", "z"]


# ─── run_campaign_backfill — quota path, ordered ──────────────────────────────

def _make_segments(n: int) -> list[dict]:
    """n AI segments of ONE agent, ended_at strictly increasing with index."""
    return [
        {
            "segment_id":    f"seg_{i:02d}",
            "session_id":    f"sess_{i:02d}",
            "role":          "primary",
            "pool_id":       "sac_ia",
            "agent_type_id": "skill_atendimento_sac_v1",
            "flow_id":       "skill_atendimento_sac_v1",
            "deploy_version": "1.0",
            "outcome":       "resolved",
            "duration_ms":   60000,
            "ended_at":      f"2026-06-22T10:{i:02d}:00Z",
        }
        for i in range(1, n + 1)
    ]


@pytest.fixture
def _patched_db(monkeypatch):
    created: list[dict] = []

    async def _latest_published_version(*a, **k):
        return 1

    async def _get_form(*a, **k):
        return {"version": 1}

    async def _instance_exists_for_segment(*a, **k):
        return False

    async def _create_instance(_db_pool, **kw):
        created.append(kw)
        return {"id": f"inst_{kw['segment_id']}"}

    monkeypatch.setattr(bf._db, "latest_published_version", _latest_published_version)
    monkeypatch.setattr(bf._db, "get_form", _get_form)
    monkeypatch.setattr(bf._db, "instance_exists_for_segment", _instance_exists_for_segment)
    monkeypatch.setattr(bf._db, "create_instance", _create_instance)
    return created


async def _run(monkeypatch, segments, *, rate=0.3, redis=None):
    async def _fetch(*a, **k):
        return list(segments)
    monkeypatch.setattr(bf, "fetch_closed_segments", _fetch)
    campaign = {
        "id": "camp1", "tenant_id": "tenant_demo", "form_id": "form1",
        "evaluation_pool_id": "sac_ia", "total_instances": 0,
        "sampling_rules": {"mode": "quota", "quota_rate_ai": rate,
                           "min_duration_s": 30, "outcome_filter": ["resolved"]},
    }
    return await run_campaign_backfill(
        None, campaign, analytics_api_url="http://x", from_dt="a", to_dt="b",
        redis_client=redis,
    )


class TestQuotaBackfill:
    async def test_deficit_selection_is_deterministic_and_ordered(
        self, monkeypatch, _patched_db
    ):
        # 10 AI contacts @30% → floor + deficit selects ended_at t=1,4,7 (3 instances).
        segs = _make_segments(10)
        random.Random(42).shuffle(segs)  # fetch order is arbitrary
        redis = _FakeAsyncRedis()
        report = await _run(monkeypatch, segs, rate=0.3, redis=redis)

        assert report["created"] == 3
        chosen = [k["segment_id"] for k in _patched_db]
        assert chosen == ["seg_01", "seg_04", "seg_07"]  # by ended_at, deficit cadence
        # shared counter reflects all 10 eligible contacts.
        h = redis.hashes["tenant_demo:eval:quota:camp1:ai:sac_ia:skill_atendimento_sac_v1:1.0"]
        assert h == {"total": 10, "sampled": 3}

    async def test_reproducible_across_input_orderings(self, monkeypatch, _patched_db):
        # Different fetch order → identical selection (sort makes it order-independent).
        segs = _make_segments(10)
        random.Random(7).shuffle(segs)
        report = await _run(monkeypatch, segs, rate=0.3, redis=_FakeAsyncRedis())
        assert [k["segment_id"] for k in _patched_db] == ["seg_01", "seg_04", "seg_07"]
        assert report["created"] == 3

    async def test_rerun_is_idempotent_via_seen_set(self, monkeypatch, _patched_db):
        # Same Redis reused across two backfill runs → second run re-samples nothing
        # (the :seen: set blocks double counting), denominator unchanged.
        segs = _make_segments(10)
        redis = _FakeAsyncRedis()
        await _run(monkeypatch, segs, rate=0.3, redis=redis)
        first = len(_patched_db)
        await _run(monkeypatch, segs, rate=0.3, redis=redis)
        assert len(_patched_db) == first  # nothing new created on re-run
        h = redis.hashes["tenant_demo:eval:quota:camp1:ai:sac_ia:skill_atendimento_sac_v1:1.0"]
        assert h["total"] == 10  # denominator not inflated
