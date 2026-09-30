"""AUD-08 — o texto livre das pesquisas expira pelo prazo do tenant; a nota fica."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from plughub_evaluation_api import retention_job as rj

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def _pool(tenants):
    p = MagicMock()
    p.fetch = AsyncMock(return_value=[{"tenant_id": t} for t in tenants])
    p.execute = AsyncMock(return_value="UPDATE 2")
    return p


def test_cutoff_per_tenant_and_the_unreadable_tenant_is_skipped():
    pool = _pool(["down", "t1"])
    pol = {"down": rj.Retention(None, "skip: x"), "t1": rj.Retention(30, "config")}
    with patch.object(rj, "fetch_retention", AsyncMock(side_effect=lambda _u, t: pol[t])):
        out = asyncio.run(rj.expire_once(pool, "http://cfg", now=NOW))
    assert out == {"t1": 2}
    (call,) = pool.execute.await_args_list
    assert call.args[1:] == ("t1", NOW - timedelta(days=30))


def test_the_score_stays_and_already_expired_rows_are_not_rewritten():
    sql = " ".join(rj.PURGE_SQL.split())
    assert "open_text = NULL" in sql and "verbatims = '[]'::jsonb" in sql
    assert "audio_ref = NULL" in sql and "transcript_ref = NULL" in sql
    assert "signals" not in sql, "a nota é métrica — fica"
    assert "responded_at < $2" in sql and "tenant_id = $1" in sql
    assert "open_text IS NOT NULL" in sql


def test_empty_config_url_skips():
    r = asyncio.run(rj.fetch_retention("", "t1"))
    assert r.days is None
