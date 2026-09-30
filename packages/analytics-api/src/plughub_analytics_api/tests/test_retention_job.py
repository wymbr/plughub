"""AUD-08 — o conteúdo de conversa no ClickHouse expira pelo prazo do tenant.

Cada ramo com o controle ao lado: um expurgo que nunca roda passaria em todo teste que
só olha "não apagou", e um que apaga tudo passaria em todo teste que só olha "apagou"."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plughub_analytics_api import retention_job as rj

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


class _Resp:
    def __init__(self, body, code=200):
        self._b, self.status_code = body, code

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _client_returning(resp=None, exc=None):
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.get = AsyncMock(side_effect=exc) if exc else AsyncMock(return_value=resp)
    return MagicMock(return_value=c)


def test_empty_config_url_skips():
    r = asyncio.run(rj.fetch_retention("", "t1"))
    assert r.days is None and r.source.startswith("skip")


def test_config_api_down_skips_never_guesses():
    with patch.object(rj.httpx, "AsyncClient", _client_returning(exc=OSError("down"))):
        r = asyncio.run(rj.fetch_retention("http://cfg", "t1"))
    assert r.days is None


@pytest.mark.parametrize("bad", [0, 3651, "x", True])
def test_invalid_value_skips(bad):
    body = {"entries": {rj.KEY: {"value": bad}}}
    with patch.object(rj.httpx, "AsyncClient", _client_returning(_Resp(body))):
        r = asyncio.run(rj.fetch_retention("http://cfg", "t1"))
    assert r.days is None


def test_tenant_value_and_absent_default():
    with patch.object(rj.httpx, "AsyncClient", _client_returning(_Resp({"entries": {rj.KEY: {"value": 30}}}))):
        assert asyncio.run(rj.fetch_retention("http://cfg", "t1")) == rj.Retention(30, "config")
    with patch.object(rj.httpx, "AsyncClient", _client_returning(_Resp({"entries": {}}))):
        assert asyncio.run(rj.fetch_retention("http://cfg", "t1")) == rj.Retention(365, "default")


class _CH:
    def __init__(self, counts):
        self.counts, self.commands, self.queries = counts, [], []

    class _R:
        def __init__(self, rows):
            self.result_rows = rows

    def query(self, sql, parameters=None):
        self.queries.append(sql)
        for table, n in self.counts.items():
            if f".{table} WHERE" in sql:
                return self._R([(n,)])
        return self._R([("t1",), ("t2",)])

    def command(self, sql, settings=None):
        self.commands.append(sql)


def test_expire_tenant_is_scoped_and_only_mutates_tables_with_rows():
    ch = _CH({"messages": 4, "contact_insights": 0, "session_timeline": 2})
    out = rj.expire_tenant(ch, "db", "t'1", datetime(2025, 9, 30, 12, 0))
    assert out == {"messages": 4, "contact_insights": 0, "session_timeline": 2}
    assert len(ch.commands) == 2, "tabela sem linha vencida não ganha mutação"
    msg = next(c for c in ch.commands if ".messages UPDATE" in c)
    assert "content = '[expired]'" in msg
    assert "tenant_id = 't\\'1'" in msg, "tenant com aspa vai escapado"
    assert "timestamp < '2025-09-30 12:00:00'" in msg
    assert "content != '[expired]'" in msg, "sem isto, reescreve o já expirado todo dia"
    tl = next(c for c in ch.commands if ".session_timeline UPDATE" in c)
    assert "payload = '{}'" in tl


def test_expire_once_skips_the_tenant_it_cannot_read_and_expires_the_other():
    class Store:
        _database = "db"

        def __init__(self):
            self.ch = _CH({"messages": 1, "contact_insights": 1, "session_timeline": 1})

        def new_client(self):
            return self.ch

    store = Store()
    pol = {"t1": rj.Retention(None, "skip: x"), "t2": rj.Retention(365, "config")}
    with patch.object(rj, "fetch_retention", AsyncMock(side_effect=lambda _u, t: pol[t])):
        out = asyncio.run(rj.expire_once(store, "http://cfg", now=NOW))
    assert list(out) == ["t2"]
    assert all("tenant_id = 't2'" in c for c in store.ch.commands)
    assert all("2025-09-30 12:00:00" in c for c in store.ch.commands)
