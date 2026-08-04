"""
test_agent_released_lifecycle.py — Fase E (D8) do ADR
`adr-work-item-requeue-and-agent-affinity.md`.

`agent_released` é o evento que o bridge publica quando o transporte de um agente
humano cai num CONTATO DE CLIENTE. Ele existe porque `agent_done` significa "o
agente concluiu o atendimento", e uma queda não concluiu nada — mas a vaga tem de
voltar do mesmo jeito, senão a re-rota do contato encontra a capacidade presa
(o defeito "vaga presa sem item" da fix 2a).

Para efeito de CAPACIDADE os dois eventos dizem a mesma verdade, e este handler é
dono de uma coisa só. Por isso o efeito é deliberadamente idêntico — e é
exatamente isso que precisa de teste: um `elif` que esqueça o nome novo não
quebra nada visível, só deixa vagas presas que aparecem horas depois, como
"available" errado no Monitor.

O que faria cada caso reprovar está no docstring de cada um.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from plughub_routing.kafka_listener import LifecycleEventHandler

TENANT   = "tenant_test"
INSTANCE = "human-alice"
CONV     = "sid-contact-001"
POOL     = "retencao_humano"


def _handler():
    instances = MagicMock()
    instances.remove_conversation = AsyncMock()
    return LifecycleEventHandler(instances, pool_registry=None), instances


def _event(name: str, **extra) -> dict:
    ev = {
        "event":           name,
        "tenant_id":       TENANT,
        "instance_id":     INSTANCE,
        "agent_type_id":   f"human_agent_{POOL}",
        "pools":           [POOL],
        "conversation_id": CONV,
        "timestamp":       "2026-08-04T14:00:00+00:00",
    }
    ev.update(extra)
    return ev


# ── 1 · o nome novo devolve a vaga ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_released_removes_the_conversation():
    """
    Reprova se o `elif` do handler não reconhecer `agent_released`: o evento cai
    no `else` (log em debug), a vaga não volta, e nada fica vermelho — o sintoma
    é capacidade errada no Monitor, horas depois.
    """
    handler, instances = _handler()
    await handler.handle(_event("agent_released"))

    instances.remove_conversation.assert_awaited_once()
    args, kwargs = instances.remove_conversation.await_args
    assert args[:3] == (TENANT, INSTANCE, CONV)
    assert kwargs["fallback_pools"] == [POOL]


# ── 2 · queda NUNCA segura vaga para wrap-up ─────────────────────────────────

@pytest.mark.asyncio
async def test_agent_released_never_holds_the_slot_even_if_flagged():
    """
    O hand-off de vaga (Phase 2) existe para o wrap-up INLINE herdar a vaga de
    quem acabou de atender. Numa queda não há quem herde: o autor sumiu. Segurar
    a vaga aqui a deixaria presa até o TTL do hold, com o agente já fora.

    Este teste manda o flag TRUE de propósito — a defesa é do consumidor, para
    que um produtor futuro não consiga segurar vaga em nome de quem caiu.
    """
    handler, instances = _handler()
    await handler.handle(_event("agent_released", keep_slot_for_wrapup=True))

    _, kwargs = instances.remove_conversation.await_args
    assert kwargs["hold_for_wrapup"] is False, (
        "queda segurou a vaga para um wrap-up que não existe"
    )


# ── 3 · o caminho normal NÃO foi alterado ────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_done_still_honours_the_wrapup_hold():
    """
    Guarda de regressão da Fase E sobre a Phase 2 (hand-off da vaga). O `and
    event_type == "agent_done"` que blinda o caso 2 é uma linha que, escrita ao
    contrário, desliga o hand-off inteiro — e o sintoma seria a ocupação
    oscilando entre o fim do contato e o claim do wrap-up, que é justamente o
    que a Phase 2 fechou.
    """
    handler, instances = _handler()
    await handler.handle(_event("agent_done", keep_slot_for_wrapup=True))

    _, kwargs = instances.remove_conversation.await_args
    assert kwargs["hold_for_wrapup"] is True


# ── 4 · sem contato, não há vaga a devolver ──────────────────────────────────

@pytest.mark.asyncio
async def test_agent_released_without_conversation_id_is_a_logged_noop():
    """
    Mesmo tratamento do `agent_done`: sem `conversation_id` não há o que remover,
    e o handler LOGA em vez de chamar com string vazia (que removeria "" do
    conjunto de sessões e voltaria sucesso — um no-op que parece trabalho feito).
    """
    handler, instances = _handler()
    await handler.handle(_event("agent_released", conversation_id=""))

    instances.remove_conversation.assert_not_awaited()
