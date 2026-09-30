"""AUD-07 — expurgo do original_content. Cada ramo com o controle positivo ao lado: um
expurgo que nunca roda passaria em todo teste que só olha "não apagou"."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from session_replayer import retention_purge as rp

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def _pool(tenants, status="UPDATE 3"):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"tenant_id": t} for t in tenants])
    pool.execute = AsyncMock(return_value=status)
    return pool


def _urlopen_returning(body):
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return MagicMock(return_value=cm)


# ── leitura da política ────────────────────────────────────────────────────────

async def test_reads_the_tenant_value_from_config():
    body = {"entries": {"original_content_days": {"value": 30}}}
    with patch.object(rp.urllib.request, "urlopen", _urlopen_returning(body)):
        r = await rp.fetch_retention("http://cfg", "t1")
    assert (r.days, r.source) == (30, "config")


async def test_config_api_down_skips_never_guesses():
    with patch.object(rp.urllib.request, "urlopen", MagicMock(side_effect=OSError("down"))):
        r = await rp.fetch_retention("http://cfg", "t1")
    assert r.days is None and r.source.startswith("skip")


@pytest.mark.parametrize("bad", [0, -5, 3651, "abc", True, 12.5, None])
async def test_invalid_value_skips(bad):
    body = {"entries": {"original_content_days": {"value": bad}}}
    with patch.object(rp.urllib.request, "urlopen", _urlopen_returning(body)):
        r = await rp.fetch_retention("http://cfg", "t1")
    assert r.days is None


async def test_absent_key_uses_declared_default_and_says_so(caplog):
    with patch.object(rp.urllib.request, "urlopen", _urlopen_returning({"entries": {}})):
        r = await rp.fetch_retention("http://cfg", "t1")
    assert (r.days, r.source) == (rp.DEFAULT_DAYS, "default")
    assert "ausente" in caplog.text


# ── a rodada ───────────────────────────────────────────────────────────────────

async def test_purge_uses_the_event_cutoff_per_tenant_and_per_class():
    pool = _pool(["t1", "t2"])
    pol = {("t1", "original_content_days"): rp.Retention(90, "config"),
           ("t2", "original_content_days"): rp.Retention(7, "config"),
           ("t1", "conversation_content_days"): rp.Retention(365, "config"),
           ("t2", "conversation_content_days"): rp.Retention(30, "config")}
    with patch.object(rp, "fetch_retention",
                      AsyncMock(side_effect=lambda _u, t, k, _d: pol[(t, k)])):
        out = await rp.purge_once(pool, "http://cfg", now=NOW)
    assert out == {"original_content_days": {"t1": 3, "t2": 3},
                   "conversation_content_days": {"t1": 3, "t2": 3}}
    calls = [c.args for c in pool.execute.await_args_list]
    assert calls[0][1:] == ("t1", NOW - timedelta(days=90))
    assert calls[1][1:] == ("t2", NOW - timedelta(days=7))
    assert calls[2][1:] == ("t1", NOW - timedelta(days=365))
    assert calls[3][1:] == ("t2", NOW - timedelta(days=30))
    assert calls[2][0] == rp.CLASSES[1].purge_sql


async def test_skipped_tenant_is_not_purged_but_the_others_are():
    pool = _pool(["down", "ok"])
    pol = {"down": rp.Retention(None, "skip: x"), "ok": rp.Retention(90, "config")}
    with patch.object(rp, "fetch_retention", AsyncMock(side_effect=lambda _u, t, _k, _d: pol[t])):
        out = await rp.purge_once(pool, "http://cfg", now=NOW)
    assert out == {"original_content_days": {"ok": 3}, "conversation_content_days": {"ok": 3}}
    assert [c.args[1] for c in pool.execute.await_args_list] == ["ok", "ok"]


def test_purge_sql_removes_the_key_inside_payload_not_only_the_column():
    """Medido: o original mora em `payload`, a coluna está sempre vazia. Limpar só a
    coluna seria um expurgo verde que não apaga nada."""
    sql = " ".join(rp._PURGE_SQL.split())
    assert "payload = payload - 'original_content'" in sql
    assert "original_content = NULL" in sql
    assert "tenant_id = $1" in sql and "timestamp < $2" in sql
    assert "payload ? 'original_content'" in " ".join(rp._TENANTS_SQL.split())


async def test_run_forever_survives_a_failed_round():
    calls = {"n": 0}

    async def boom(*_a, **_k):
        calls["n"] += 1
        raise RuntimeError("pg caiu")

    async def stop_after_second(_s):
        if calls["n"] >= 2:
            raise rp.asyncio.CancelledError

    with patch.object(rp, "purge_once", boom), patch.object(rp.asyncio, "sleep", stop_after_second):
        with pytest.raises(rp.asyncio.CancelledError):
            await rp.run_forever(MagicMock(), "http://cfg", interval_s=1)
    assert calls["n"] == 2


async def test_class_default_is_used_when_the_key_is_absent():
    with patch.object(rp.urllib.request, "urlopen", _urlopen_returning({"entries": {}})):
        r = await rp.fetch_retention("http://cfg", "t1", "conversation_content_days", 365)
    assert (r.days, r.source) == (365, "default")


def test_conversation_class_wipes_the_whole_payload_not_a_list_of_keys():
    """O conteúdo não mora numa chave só (`message`, `session_resumed`,
    `interaction_request`); uma lista de chaves envelheceria no primeiro tipo novo."""
    cls = {c.key: c for c in rp.CLASSES}["conversation_content_days"]
    sql = " ".join(cls.purge_sql.split())
    assert """SET payload = '{"expired": true}'::jsonb""" in sql
    assert "original_content = NULL" in sql
    assert "tenant_id = $1" in sql and "timestamp < $2" in sql
    assert """payload <> '{"expired": true}'::jsonb""" in sql, "sem isto, reescreve a linha já expirada toda hora"
