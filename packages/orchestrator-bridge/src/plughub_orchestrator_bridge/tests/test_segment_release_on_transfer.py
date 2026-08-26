"""
test_segment_release_on_transfer.py — release da TELA da origem no Transfer.

O DEFEITO (relatado 2026-08-24, consertado 2026-08-26): o Console não liberava a
tela depois de um `Transfer` — contato preso na lista, input desabilitado, toast
persistente *"Contato transferido. Aguardando encerramento…"* para sempre.

CAUSA: o front espera `session.closed reason="posatt_segment_complete"` com ele em
`recipients`. Esse evento era publicado por `process_routed` a partir do `hook_conf`
+ do SET `posatt:{conf}:participants` — artefatos que só o caminho de CONFERÊNCIA
cria. O wrap-up de agente saiu da conferência no wrap-up unificado (2026-07-27),
então `fire_pool_hooks` passou a fazer `continue` antes de gravá-los e ninguém mais
publicava o evento. **Um conserto de outra fatia quebrou este contrato em silêncio.**

O que estes testes protegem — e por que cada um pode REPROVAR:

  · **publica com o instance_id** — o mínimo: sem isso não há release nenhum.
  · **inclui o participant_id global quando existe** — o filtro do mcp-server
    compara `recipients` com `agentInstanceId`, que vem de `instance_id` OU, em
    fallback, de `participant_id`. Publicar só uma das formas faz o defeito voltar
    de modo INTERMITENTE (depende de qual identidade o socket capturou), que é a
    variante mais cara de diagnosticar.
  · **NUNCA broadcast** — o filtro do mcp-server só age quando `recipients` é
    array. Sem destinatário nenhum, publicar removeria o contato em TODO agente
    inscrito, inclusive o DESTINO que acabou de recebê-lo. Por isso a ausência
    ABORTA a publicação em vez de degradar para broadcast.
  · **ctx ilegível não impede o release** — a leitura do participant_id é
    conveniência; falhar nela não pode custar a tela do agente.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import plughub_orchestrator_bridge.main as bridge_mod


def _ctx_pid(value: str) -> str:
    return json.dumps({
        "value": value, "confidence": 1.0, "source": "bridge",
        "visibility": "agents_only", "updated_at": "2026-08-26T00:00:00Z",
    })


def _published(r) -> dict:
    """O payload do único publish, já decodificado."""
    assert r.publish.await_count == 1, (
        f"esperado 1 publish, houve {r.publish.await_count}"
    )
    channel, raw = r.publish.await_args.args
    assert channel == "agent:events:sess"
    return json.loads(raw)


@pytest.mark.asyncio
async def test_publishes_targeted_release_with_instance_id():
    r = AsyncMock()
    r.hget = AsyncMock(return_value=None)
    await bridge_mod._publish_segment_release(r, "sess", "t", "human-alice")
    ev = _published(r)
    assert ev["type"]       == "session.closed"
    assert ev["reason"]     == "posatt_segment_complete"
    assert ev["session_id"] == "sess"
    assert ev["recipients"] == ["human-alice"]


@pytest.mark.asyncio
async def test_includes_the_participant_id_as_well():
    """As DUAS formas de nomear o mesmo humano. O SET da conferência já mistura as
    duas fontes, e o filtro do mcp-server aceita qualquer uma — publicar só uma
    seria apostar em qual delas o `conversation.assigned` daquele socket carregou."""
    r = AsyncMock()
    r.hget = AsyncMock(return_value=_ctx_pid("part_abc123"))
    await bridge_mod._publish_segment_release(r, "sess", "t", "human-alice")
    ev = _published(r)
    assert set(ev["recipients"]) == {"human-alice", "part_abc123"}
    r.hget.assert_awaited_once_with("t:ctx:sess", "session.human_agent_participant_id")


@pytest.mark.asyncio
async def test_does_not_duplicate_when_both_names_are_equal():
    r = AsyncMock()
    r.hget = AsyncMock(return_value=_ctx_pid("human-alice"))
    await bridge_mod._publish_segment_release(r, "sess", "t", "human-alice")
    assert _published(r)["recipients"] == ["human-alice"]


@pytest.mark.asyncio
async def test_never_broadcasts_when_there_is_no_recipient():
    """TESTEMUNHA NEGATIVA. Publicar sem `recipients` desliga o filtro do
    mcp-server e remove o contato em todo agente inscrito — incluindo o destino,
    que acabou de recebê-lo. Um release sem destinatário é pior que nenhum."""
    r = AsyncMock()
    r.hget = AsyncMock(return_value=None)
    await bridge_mod._publish_segment_release(r, "sess", "t", "")
    r.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_ctx_failure_still_releases_the_screen():
    """A leitura do participant_id é conveniência; a tela não pode ficar presa
    porque um HGET falhou."""
    r = AsyncMock()
    r.hget = AsyncMock(side_effect=RuntimeError("redis down"))
    await bridge_mod._publish_segment_release(r, "sess", "t", "human-alice")
    assert _published(r)["recipients"] == ["human-alice"]


@pytest.mark.asyncio
async def test_malformed_ctx_entry_still_releases_the_screen():
    r = AsyncMock()
    r.hget = AsyncMock(return_value="not-json{")
    await bridge_mod._publish_segment_release(r, "sess", "t", "human-alice")
    assert _published(r)["recipients"] == ["human-alice"]
