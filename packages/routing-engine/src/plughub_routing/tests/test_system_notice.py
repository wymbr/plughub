"""
ALW-18 — o aviso da plataforma ao cliente também é fato do stream canônico.

O histórico do Console é projeção do stream (`mcp-server/lib/console-history.ts`), e ela lê o
autor por `author_role`/`author`, o texto por `payload.content.text` e o tipo `system_notice`.
O contrato é conferido contra o que esse LEITOR exige — não contra o que este produtor acha que
escreve.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from plughub_routing.system_notice import record_system_notice


@pytest.mark.asyncio
async def test_grava_no_stream_da_sessao_no_formato_que_a_projecao_le():
    redis = AsyncMock()
    ok = await record_system_notice(redis, "s1", "5b9e1c1e-0000-4000-8000-000000000001",
                                    "Aguardando agente disponível.", "2026-09-28T10:00:00+00:00")
    assert ok is True
    key, f = redis.xadd.await_args.args
    assert key == "session:s1:stream"
    assert f["type"] == "system_notice" and f["author_role"] == "system"
    assert json.loads(f["visibility"]) == "all"
    payload = json.loads(f["payload"])
    assert payload["content"] == {"type": "text", "text": "Aguardando agente disponível."}
    assert payload["message_id"] == f["event_id"] == "5b9e1c1e-0000-4000-8000-000000000001"


@pytest.mark.asyncio
async def test_redis_fora_nao_impede_o_aviso_e_e_dito(caplog):
    redis = AsyncMock()
    redis.xadd.side_effect = RuntimeError("redis caiu")
    with caplog.at_level("WARNING"):
        ok = await record_system_notice(redis, "s1", "id-1", "texto", "ts")
    assert ok is False
    assert "historico do Console fica sem esta linha" in caplog.text


@pytest.mark.asyncio
async def test_sem_texto_nao_grava_nada():
    redis = AsyncMock()
    assert await record_system_notice(redis, "s1", "id-1", "", "ts") is False
    redis.xadd.assert_not_awaited()
