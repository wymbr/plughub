"""
test_work_item_release_on_drop.py — Fase B do ADR
`adr-work-item-requeue-and-agent-affinity.md` (D1: devolver pelo caminho de pull).

Cobre a DECISÃO que a Fase B introduziu no `agent_disconnect`: distinguir item de
fila pull de contato de cliente, e devolver o primeiro por `work_task_release` em
vez do re-publish de seis campos em `conversations.inbound`.

O discriminador é a POSSE no árbitro (`claim_record`, Fase A), não o
`dispatch_mode` do pool: só o caminho pull escreve registro de posse, então contato
de cliente nunca casa — e não sobra um ramo "pool_config ausente do cache", cujos
dois fallbacks possíveis erram para lados opostos (manter o defeito em silêncio,
ou largar um cliente esperando).

Casos, e o que faria cada um reprovar:
  · detido por quem caiu       → True   (some o casamento por instance_id)
  · detido por OUTRO           → False  (o bridge devolveria item alheio)
  · ninguém detém              → False  (contato de cliente vira release)
  · HTTP não-200 / exceção     → False  (fail-open para a re-rota, com log)
  · sem http / campos vazios   → False  (guarda de entrada)
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

import plughub_orchestrator_bridge.main as bridge_mod

TENANT  = "tenant_test"
POOL    = "wrapup_detached_ia-int"
SESSION = "sid-drop-001"
MINE    = "human-alice"
THEIRS  = "human-bob"


def _ctx(resp):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__  = AsyncMock(return_value=False)
    return cm


def _resp(status, body=None, text=""):
    r = MagicMock()
    r.status = status
    r.json   = AsyncMock(return_value=body or {})
    r.text   = AsyncMock(return_value=text)
    return r


def _http(resp):
    http = MagicMock()
    http.post = MagicMock(return_value=_ctx(resp))
    return http


# ── _routing_holds_item ──────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["lease", "record"])
async def test_holds_when_dropping_instance_is_the_holder(via):
    """
    `via="record"` é o caso que importa: a lease dura 180 s e o drop pode vir
    depois. Se só a lease respondesse, o bridge voltaria a tratar item de trabalho
    como contato de cliente — a Fase B sumiria justamente nos itens antigos.
    """
    http = _http(_resp(200, {
        "found": True, "instance_id": MINE, "via": via, "in_queue": False,
    }))
    assert await bridge_mod._routing_holds_item(
        http, TENANT, POOL, SESSION, MINE
    ) is True


@pytest.mark.asyncio
async def test_does_not_hold_when_someone_else_is_the_holder():
    """Outro agente reivindicou depois — quem caiu não tem o que devolver."""
    http = _http(_resp(200, {
        "found": True, "instance_id": THEIRS, "via": "record", "in_queue": False,
    }))
    assert await bridge_mod._routing_holds_item(
        http, TENANT, POOL, SESSION, MINE
    ) is False


@pytest.mark.asyncio
async def test_does_not_hold_for_customer_contact():
    """
    Contato de cliente: alocado por push, nunca teve claim → `found=false`.
    Este é o teste que protege o comportamento ANTIGO: se ele reprovar, a Fase B
    passou a devolver contatos de cliente à fila em vez de re-roteá-los, e o
    cliente fica esperando alguém que não vem.
    """
    http = _http(_resp(200, {"found": False, "via": "none", "in_queue": False}))
    assert await bridge_mod._routing_holds_item(
        http, TENANT, POOL, SESSION, MINE
    ) is False


@pytest.mark.asyncio
async def test_degrades_to_reroute_on_http_error():
    """Árbitro respondendo não-200 → False (re-rota, o comportamento vigente)."""
    http = _http(_resp(503))
    assert await bridge_mod._routing_holds_item(
        http, TENANT, POOL, SESSION, MINE
    ) is False


@pytest.mark.asyncio
async def test_degrades_to_reroute_on_exception():
    """Rede fora → False. Conservador: mantém o caminho antigo, com log."""
    http = MagicMock()
    http.post = MagicMock(side_effect=RuntimeError("connection refused"))
    assert await bridge_mod._routing_holds_item(
        http, TENANT, POOL, SESSION, MINE
    ) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "http_obj,tenant,pool,session,instance",
    [
        (None,        TENANT, POOL, SESSION, MINE),   # sem sessão HTTP
        (MagicMock(), "",     POOL, SESSION, MINE),   # sem tenant
        (MagicMock(), TENANT, "",   SESSION, MINE),   # sem pool
        (MagicMock(), TENANT, POOL, SESSION, ""),     # sem instância
    ],
)
async def test_guard_returns_false_on_missing_inputs(
    http_obj, tenant, pool, session, instance
):
    """Entrada incompleta nunca vira release — não há posse a afirmar."""
    assert await bridge_mod._routing_holds_item(
        http_obj, tenant, pool, session, instance
    ) is False


# ── _release_work_item ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_release_posts_the_four_fields_to_the_arbiter():
    """
    O release é do ÁRBITRO — o bridge solicita, não mexe no Redis do routing.
    Confere o endpoint e o corpo, que é o contrato.
    """
    http = _http(_resp(200, {"released": True, "requeued": True}))
    ok = await bridge_mod._release_work_item(http, TENANT, POOL, SESSION, MINE)
    assert ok is True
    args, kwargs = http.post.call_args
    assert args[0].endswith("/v1/work_queue/release")
    assert kwargs["json"] == {
        "tenant_id":   TENANT,
        "pool_id":     POOL,
        "session_id":  SESSION,
        "instance_id": MINE,
        # Fase C (D3): ESTE caminho é queda, não desistência — o item volta
        # reservado a quem caiu. O botão "Return to queue" chama o mesmo endpoint
        # SEM este campo, e é a única diferença entre os dois. Se ele sumir daqui,
        # a queda passa a devolver ao pool inteiro na hora, e a digitação parcial
        # do agente vira corrida com os colegas.
        "reserve_to_previous": True,
    }


@pytest.mark.asyncio
async def test_release_reports_when_nothing_was_requeued():
    """
    `requeued=false` = a vaga voltou, o item não (o JSON do contato já morreu).
    Continua devolvendo True — o release em si funcionou — mas o caso é registrado.
    Colapsá-lo em sucesso mudo esconderia um item que sumiu da fila.
    """
    http = _http(_resp(200, {"released": True, "requeued": False}))
    assert await bridge_mod._release_work_item(
        http, TENANT, POOL, SESSION, MINE
    ) is True


@pytest.mark.asyncio
async def test_release_returns_false_on_non_2xx():
    http = _http(_resp(500, text="boom"))
    assert await bridge_mod._release_work_item(
        http, TENANT, POOL, SESSION, MINE
    ) is False


@pytest.mark.asyncio
async def test_release_returns_false_on_exception():
    http = MagicMock()
    http.post = MagicMock(side_effect=RuntimeError("connection refused"))
    assert await bridge_mod._release_work_item(
        http, TENANT, POOL, SESSION, MINE
    ) is False
