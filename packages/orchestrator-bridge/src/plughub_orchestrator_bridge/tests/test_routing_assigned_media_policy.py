"""
test_routing_assigned_media_policy.py

A PROPOSIÇÃO (VOZ-10): o campo `pool` do `routing.assigned` carrega a política de mídia
DO POOL QUE ATENDE, lida FRESCA do agent-registry — e as três ausências chegam ao
gateway com nomes diferentes, porque ele as trata diferente.

  · sessão webrtc, registry respondeu    → `media_policy` do pool + `registry`
  · sessão webrtc, registry não respondeu → `registry_unavailable`, SEM `media_policy`
    (nunca `media_policy: null`, que o gateway leria como "o pool não declara")
  · sessão de outro canal                 → `not_webrtc`, e o registry NÃO é chamado

O valor distintivo (`["video"]` sozinho, sem áudio) não é enfeite: nenhum default de
plataforma produz vídeo sem áudio, então se ele aparecer veio do registry.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from plughub_orchestrator_bridge import main

SID = "sess-voz10"
DISTINCTIVE = {"customer_publish": ["video"], "agent_publish": []}


def _redis(channel: str | None):
    r = AsyncMock()
    r.get = AsyncMock(return_value=json.dumps({"channel": channel}) if channel else None)
    return r


@pytest.mark.asyncio
async def test_webrtc_leva_a_politica_do_registry():
    with patch.object(main, "get_pool_config", new=AsyncMock(return_value={"media_policy": DISTINCTIVE})) as g:
        field = await main._routing_assigned_pool_field(None, _redis("webrtc"), SID, "t", "video_humano")
    g.assert_awaited_once()
    assert g.await_args.args[1:] == ("t", "video_humano")
    assert field == {"pool_id": "video_humano", "media_policy": DISTINCTIVE, "media_policy_source": "registry"}


@pytest.mark.asyncio
async def test_voice_tambem_leva_a_politica():
    """VOZ-02: a chamada de TELEFONE entra numa sala pela perna SIP — sem a política do pool, o
    gateway ofereceria nada (sem bot leg, sem fala) e o chamador ficaria chamando."""
    with patch.object(main, "get_pool_config", new=AsyncMock(return_value={"media_policy": DISTINCTIVE})) as g:
        field = await main._routing_assigned_pool_field(None, _redis("voice"), SID, "t", "telefone")
    g.assert_awaited_once()
    assert field == {"pool_id": "telefone", "media_policy": DISTINCTIVE, "media_policy_source": "registry"}


@pytest.mark.asyncio
async def test_pool_sem_politica_e_null_declarado_nao_ausencia():
    with patch.object(main, "get_pool_config", new=AsyncMock(return_value={"pool_id": "p"})):
        field = await main._routing_assigned_pool_field(None, _redis("webrtc"), SID, "t", "p")
    assert field["media_policy_source"] == "registry" and field["media_policy"] is None


@pytest.mark.asyncio
async def test_registry_fora_nomeia_a_ausencia_e_loga_error(caplog):
    with patch.object(main, "get_pool_config", new=AsyncMock(return_value=None)), \
         caplog.at_level("ERROR"):
        field = await main._routing_assigned_pool_field(None, _redis("webrtc"), SID, "t", "p")
    assert field == {"pool_id": "p", "media_policy_source": "registry_unavailable"}
    assert "media_policy" not in field
    assert "NAO lida do registry" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["webchat", "webhook", None])
async def test_outro_canal_nao_paga_a_leitura(channel):
    with patch.object(main, "get_pool_config", new=AsyncMock(return_value={"media_policy": DISTINCTIVE})) as g:
        field = await main._routing_assigned_pool_field(None, _redis(channel), SID, "t", "p")
    g.assert_not_awaited()
    assert field == {"pool_id": "p", "media_policy_source": "not_webrtc"}


@pytest.mark.asyncio
async def test_escritor_do_stream_carrega_o_campo():
    """O helper tem de chegar ao XADD — um campo calculado e não escrito é o mesmo que nenhum."""
    r = _redis("webrtc")
    r.xadd = AsyncMock()
    r.expire = AsyncMock()
    with patch.object(main, "get_pool_config", new=AsyncMock(return_value={"media_policy": DISTINCTIVE})):
        pool_field = await main._routing_assigned_pool_field(None, r, SID, "t", "video_humano")
    await main._write_routing_assigned_to_stream(
        r, SID, framework="human", pool_config=pool_field, segment_id="seg1", instance_id="h1",
    )
    fields = r.xadd.await_args.args[1]
    assert json.loads(fields["pool"])["media_policy"] == DISTINCTIVE
    assert fields["framework"] == "human"
