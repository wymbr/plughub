"""
tests/test_call_intervals.py — a chamada dentro do contato nos relatórios (WCH-02, 2026-09-22).

Proposições, cada uma com o controle ao lado:
  · o LEITOR do `media.calls` (parser) aceita início e fim no formato de
    `@plughub/schemas/media-calls.ts` e grava a LINHA INTEIRA; fim sem `ended_at` não apaga o início;
  · o consumidor assina o tópico e o store real tem o escritor (o mock o criaria);
  · a partição e a versão saem do EVENTO (início), nunca da inserção;
  · o filtro "com chamada" entra no predicado ÚNICO — lista, série e tokens (SQL EXECUTADO);
  · a lista traz contagem e duração, e a chamada em curso não soma zero à duração;
  · a transcrição de contato fechado recebe as chamadas como `media.call`, e a em curso só tem início.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from plughub_analytics_api import consumer
from plughub_analytics_api import reports_query as rq
from plughub_analytics_api.clickhouse import AnalyticsStore
from plughub_analytics_api.models import parse_media_call_event

INICIO = {
    "event_id": "5b0c3a5e-4a0b-4c2e-9d57-0d3c3f9a1e01", "event_type": "call_started",
    "tenant_id": "t", "session_id": "s-1", "call_id": "1726999999999-0", "channel": "webchat",
    "pool_id": "humano", "customer_publish": ["audio"], "started_at": "2026-09-22T12:00:00+00:00",
}
FIM = {**INICIO, "event_id": "5b0c3a5e-4a0b-4c2e-9d57-0d3c3f9a1e02", "event_type": "call_ended",
       "ended_at": "2026-09-22T12:03:10.500000+00:00", "duration_ms": 190500, "end_reason": "agent_hangup"}


class TestParser:
    def test_inicio_vira_linha_aberta(self):
        r = parse_media_call_event(INICIO)
        assert r["table"] == "call_intervals" and r["call_id"] == "1726999999999-0"
        assert r["ended_at"] is None and r["duration_ms"] is None and r["end_reason"] is None
        assert r["pool_id"] == "humano" and r["customer_publish"] == ["audio"]

    def test_fim_traz_a_linha_inteira(self):
        a, b = parse_media_call_event(INICIO), parse_media_call_event(FIM)
        for k in ("tenant_id", "session_id", "call_id", "channel", "pool_id", "customer_publish", "started_at"):
            assert a[k] == b[k], k
        assert b["duration_ms"] == 190500 and b["end_reason"] == "agent_hangup"

    def test_recusas(self):
        assert parse_media_call_event({**FIM, "ended_at": None}) is None   # não apaga o início
        assert parse_media_call_event({**INICIO, "call_id": ""}) is None
        assert parse_media_call_event({**INICIO, "started_at": ""}) is None
        assert parse_media_call_event({**INICIO, "event_type": "outro"}) is None
        assert parse_media_call_event({**INICIO, "pool_id": None})["pool_id"] is None

    def test_consumidor_assina_e_o_store_real_escreve(self):
        assert "media.calls" in consumer._TOPICS
        assert consumer._PARSERS["media.calls"] is parse_media_call_event
        assert hasattr(AnalyticsStore, "upsert_call_interval")      # o MagicMock abaixo o criaria
        store = MagicMock()
        store.upsert_call_interval = AsyncMock()
        asyncio.run(consumer._write_row(store, parse_media_call_event(FIM), "media.calls", 1))
        store.upsert_call_interval.assert_awaited_once()


class TestStore:
    def _store(self):
        st = AnalyticsStore.__new__(AnalyticsStore)
        st._insert = MagicMock()
        return st

    def test_particao_e_do_inicio_e_as_colunas_batem(self):
        st = self._store()
        asyncio.run(st.upsert_call_interval(parse_media_call_event(FIM)))
        tabela, linhas, cols = st._insert.call_args.args
        assert tabela == "call_intervals" and "row_version" not in cols
        (vals,) = linhas
        assert len(vals) == len(cols)
        row = dict(zip(cols, vals))
        assert row["date"] == row["started_at"] == datetime(2026, 9, 22, 12, 0)
        assert row["ended_at"] == datetime(2026, 9, 22, 12, 3, 10, 500000)

    def test_sem_inicio_nao_grava(self):
        st = self._store()
        asyncio.run(st.upsert_call_interval({**parse_media_call_event(INICIO), "started_at": None}))
        st._insert.assert_not_called()

    def test_transcricao_recebe_as_chamadas(self):
        st = self._store()
        st._database = "db"
        cli = MagicMock()
        cli.query.return_value = MagicMock(result_rows=[
            ("1-0", datetime(2026, 9, 22, 12, 0), datetime(2026, 9, 22, 12, 3), 180000, "agent_hangup"),
            ("2-0", datetime(2026, 9, 22, 12, 10), None, None, None),        # em curso: só início
        ])
        evs = st.query_session_calls(cli, "t", "s-1")
        assert [(e["type"], e["payload"]["state"]) for e in evs] == [
            ("media.call", "started"), ("media.call", "ended"), ("media.call", "started")]
        assert evs[1]["payload"] == {"state": "ended", "reason": "agent_hangup", "duration_ms": 180000}
        assert evs[0]["timestamp"].startswith("2026-09-22T12:00:00") and evs[0]["timestamp"].endswith("+00:00")
        assert len({e["entry_id"] for e in evs}) == 3


# ── SQL executado ─────────────────────────────────────────────────────────────

def _sql(c) -> str:
    return re.sub(r"\s+", " ", " ".join(call.args[0] for call in c.query.call_args_list))


def _client():
    c = MagicMock()
    c.query.return_value = MagicMock(result_rows=[], column_names=[])
    return c


MARCA = "FROM db.call_intervals FINAL WHERE tenant_id = {tenant_id:String})"


@pytest.fixture(autouse=True)
def _no_registry(monkeypatch):
    async def _fake(_tenant):
        return frozenset()
    monkeypatch.setattr(rq, "_internal_pools_for", _fake)


def _lista(has_call):
    c = _client()
    rq._fetch_sessions(c, "db", "t", "2026-09-01 00:00:00", "2026-09-30 00:00:00",
                       None, None, None, None, None, None, None, None, None, None, 1, 50,
                       has_call=has_call)
    return _sql(c)


def test_filtro_com_chamada_na_lista_na_serie_e_nos_tokens():
    assert MARCA in _lista(True)
    for fn in (rq.query_contacts_series_report, rq.query_token_breakdown_report):
        c = _client()
        asyncio.run(fn(c, "db", "t", from_dt="2026-09-01", to_dt="2026-09-30", has_call=True))
        assert MARCA in _sql(c), fn.__name__


def test_controle_sem_filtro_nao_recorta():
    sql = _lista(False)
    assert "s.session_id IN (SELECT session_id FROM db.call_intervals" not in sql
    # mas as colunas da chamada vêm sempre — é o JOIN, não o filtro
    assert "AS _calls ON _calls.session_id = s.session_id" in sql
    assert "AS call_count" in sql and "AS call_duration_ms" in sql


def test_em_curso_nao_soma_zero_a_duracao():
    sql = _lista(False)
    assert "sumIf(duration_ms, ended_at IS NOT NULL) AS call_ms" in sql
    assert "countIf(ended_at IS NULL) AS call_open" in sql
