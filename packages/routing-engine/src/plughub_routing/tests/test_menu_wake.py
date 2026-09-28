"""
DUR-01 F3 — o routing-engine avisa o bridge quando sinaliza o agente de fila.

Com o pool de fila em `park`, o `LPUSH __agent_available__` não desbloqueia ninguém: a
espera está estacionada, e só o `menu.wake` a acorda. O contrato é conferido contra o que
o LEITOR (bridge, `process_menu_wake`) exige: `event_type`, `session_id` e `field` — o
agente de fila roda sem instância, então o campo é `_default_`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from plughub_routing.menu_wake import publish_menu_wake


@pytest.mark.asyncio
async def test_aviso_no_topico_certo_com_a_sessao_como_chave():
    producer = AsyncMock()
    assert await publish_menu_wake(producer, "tenant_demo", "s1", "agent_available") is True
    topic = producer.send.await_args.args[0]
    kw = producer.send.await_args.kwargs
    assert topic == "menu.wake"
    assert kw["key"] == b"s1"
    v = kw["value"]
    assert v["event_type"] == "menu_wake" and v["session_id"] == "s1"
    assert v["field"] == "_default_" and v["reason"] == "agent_available"
    assert v["tenant_id"] == "tenant_demo" and v["timestamp"]


@pytest.mark.asyncio
async def test_kafka_fora_nao_derruba_o_drain():
    """O sinal já está na lista: a falha do aviso é dita e devolvida, nunca propagada."""
    producer = AsyncMock()
    producer.send.side_effect = RuntimeError("broker indisponível")
    assert await publish_menu_wake(producer, "t", "s1", "queue_timeout") is False
