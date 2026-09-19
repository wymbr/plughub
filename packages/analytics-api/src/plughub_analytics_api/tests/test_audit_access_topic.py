"""
VOZ-36 — `audit.access` → `audit_access_log`: a trilha LGPD tem UMA escritora (a analytics-api),
e o acesso a dado pessoal servido noutro serviço (a gravação, no channel-gateway) chega por aqui.

Forma copiada de `channel-gateway/recording_router.py::_audit` — contrato se mede no LEITOR.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from plughub_analytics_api import consumer
from plughub_analytics_api.models import parse_audit_access_event

ESCUTA = {
    "event_id": "4f1b1b2e-8f55-4c61-9a37-7d3c2a6f0b11", "tenant_id": "tenant_demo",
    "actor_sub": "u-1", "actor_kind": "user", "endpoint": "channel-gateway:recording.listen",
    "target_kind": "recording", "target_id": "s-1/9d2c", "result": "ok", "row_count": 1,
    "accessed_at": "2026-09-18T12:00:00.123456+00:00",
}


def test_topico_registrado_com_o_parser():
    assert "audit.access" in consumer._TOPICS
    assert consumer._PARSERS["audit.access"] is parse_audit_access_event


def test_escuta_vira_linha_da_trilha_com_o_event_id_como_access_id():
    row = parse_audit_access_event(ESCUTA)
    assert row["table"] == "audit_access_log"
    assert row["access_id"] == ESCUTA["event_id"]
    assert row["accessed_at"] == datetime(2026, 9, 18, 12, 0, 0, 123456, tzinfo=timezone.utc)
    assert (row["endpoint"], row["target_kind"], row["result"], row["row_count"]) == \
        ("channel-gateway:recording.listen", "recording", "ok", 1)


def test_recusa_anonima_preserva_o_ator_vazio():
    row = parse_audit_access_event({**ESCUTA, "actor_sub": "", "actor_kind": "anonymous",
                                    "result": "denied", "row_count": 0})
    assert (row["actor_sub"], row["actor_kind"], row["result"]) == ("", "anonymous", "denied")


def test_evento_incompleto_e_recusado_nunca_inventado(caplog):
    with caplog.at_level("ERROR"):
        assert parse_audit_access_event({**ESCUTA, "target_id": ""}) is None
        assert parse_audit_access_event({**ESCUTA, "result": "talvez"}) is None
        assert parse_audit_access_event({**ESCUTA, "accessed_at": "ontem"}) is None
    assert sum("RECUSADO" in r.message for r in caplog.records) == 3


def test_write_row_despacha_para_insert_audit_access_log():
    store = MagicMock()
    store.insert_audit_access_log = AsyncMock()
    asyncio.run(consumer._write_row(store, parse_audit_access_event(ESCUTA), "audit.access", 1))
    store.insert_audit_access_log.assert_awaited_once()
    assert store.insert_audit_access_log.await_args.args[0]["access_id"] == ESCUTA["event_id"]
