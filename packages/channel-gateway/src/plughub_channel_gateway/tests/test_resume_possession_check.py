"""
test_resume_possession_check.py — Fase A do ADR
`adr-work-item-requeue-and-agent-affinity.md` (D6: o submit confere posse).

Cobre o veredicto de QUATRO ramos do bloco A5 em `handle_resume`. A distinção que
importa é entre os dois últimos: até a Fase A havia dois ramos ("detido por outro"
→ 403; "tudo o mais" → passa), e esse "tudo o mais" misturava *ninguém detém* com
*não sei*. É a mesma armadilha que o handoff de 2026-08-04 registrou nos probes —
campo AUSENTE caindo no `else` que afirmava um estado.

  1. detido por MIM              → passa
  2. detido por OUTRO            → PermissionError (403)
  3. ninguém detém + NA FILA     → PermissionError (403)   ← o que a Fase A fecha
  4. ninguém detém + fora da fila→ passa (ausência honesta: push/encerrado/legado)
  4'. árbitro sem resposta       → passa, com log (falha de rede não recusa submit)

Sem I/O: Redis e Kafka são AsyncMock; o árbitro é substituído por patch em
`_routing_work_task_holder`, que é a fronteira exata onde o veredicto entra.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from plughub_channel_gateway.adapters.webhook import WebhookAdapter
from plughub_channel_gateway.config import Settings


TENANT_ID    = "tenant_test"
SESSION_ID   = "sid-poss-001"
POOL_ID      = "wrapup_detached_ia-int"
RESUME_TOKEN = "b" * 43
STEP_ID      = "aguardar_wrapup"
TOKEN_VALUE  = f"{SESSION_ID}:{STEP_ID}:2026-09-01T12:00:00+00:00"

MINE   = "human-alice"
THEIRS = "human-bob"

APPROVER = {
    "principal_type":     "human",
    "decided_by":         "alice",
    "verification_class": "possessed",
}


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.hget  = AsyncMock(return_value=TOKEN_VALUE)
    redis.hdel  = AsyncMock(return_value=1)
    redis.get   = AsyncMock(return_value=None)
    redis.set   = AsyncMock(return_value=True)
    redis.xadd  = AsyncMock(return_value=b"1-0")
    return redis


@pytest.fixture
def adapter(mock_redis):
    settings = Settings(
        kafka_brokers            = "localhost:9092",
        kafka_group_id           = "test-group",
        kafka_topic_inbound      = "conversations.inbound",
        kafka_topic_outbound     = "conversations.outbound",
        kafka_topic_events       = "conversations.events",
        redis_url                = "redis://localhost:6379",
        tenant_id                = TENANT_ID,
        storage_root             = "/tmp/plughub_test",
        attachment_expiry_days   = 1,
        database_url             = "postgresql://plughub:plughub@localhost/plughub",
        webchat_serving_base_url = "http://localhost:8010/webchat/v1/attachments",
        webchat_upload_base_url  = "http://localhost:8010/webchat/v1/upload",
    )
    return WebhookAdapter(producer=AsyncMock(), redis=mock_redis, settings=settings)


async def _resume(adapter, holder):
    """Roda handle_resume com o árbitro respondendo `holder`."""
    adapter._routing_work_task_holder = AsyncMock(return_value=holder)
    return await adapter.handle_resume(
        resume_token      = RESUME_TOKEN,
        tenant_id         = TENANT_ID,
        payload           = {"answers": {"disposition": "resolved"}},
        approver          = APPROVER,
        claim_pool_id     = POOL_ID,
        claim_instance_id = MINE,
    )


# ── 1. detido por mim → passa ────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["lease", "record"])
async def test_holder_is_caller_allows_submit(adapter, via):
    """
    Passa pelas DUAS vias. `via="record"` é o caso novo: depois de a lease vencer,
    a posse continua provável — antes da Fase A este cenário chegava como
    `found=False` e só passava por fail-open, indistinguível do item devolvido.
    """
    sid = await _resume(adapter, {
        "found": True, "instance_id": MINE, "via": via, "in_queue": False,
    })
    assert sid == SESSION_ID


# ── 2. detido por outro → 403 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_holder_is_someone_else_refuses(adapter):
    with pytest.raises(PermissionError) as exc:
        await _resume(adapter, {
            "found": True, "instance_id": THEIRS, "via": "record", "in_queue": False,
        })
    assert "does not hold" in str(exc.value)


# ── 3. ninguém detém + na fila → 403 (o buraco que a Fase A fecha) ───────────

@pytest.mark.asyncio
async def test_unheld_and_queued_refuses(adapter):
    """
    O estado exato deixado por um F5 do Console: lease apagada, registro apagado
    pelo re-parque, item de volta ao ZSET — e o formulário ainda na aba velha.

    Antes da Fase A isto retornava o session_id (fail-open). Se este teste voltar
    a passar como sucesso, o defeito voltou.
    """
    with pytest.raises(PermissionError) as exc:
        await _resume(adapter, {"found": False, "via": "none", "in_queue": True})
    assert "queue" in str(exc.value).lower()


# ── 4. ninguém detém + fora da fila → passa (ausência honesta) ───────────────

@pytest.mark.asyncio
async def test_unheld_and_not_queued_allows_submit(adapter):
    """Pool push / item encerrado / claim anterior à Fase A: não há o que conferir."""
    sid = await _resume(adapter, {"found": False, "via": "none", "in_queue": False})
    assert sid == SESSION_ID


@pytest.mark.asyncio
async def test_arbiter_unreachable_allows_submit(adapter):
    """
    Árbitro sem resposta (`None`) = DESCONHECIDO, não "ninguém detém". Recusar
    submissão legítima por falha de rede é pior que o fail-open — mas a diferença
    entre este ramo e o (3) é justamente o que o `None` preserva.
    """
    sid = await _resume(adapter, None)
    assert sid == SESSION_ID


# ── Escopo: sem aprovador o bloco A5 não roda ───────────────────────────────

@pytest.mark.asyncio
async def test_no_approver_skips_possession_check(adapter):
    """
    Resume externo/sistema (sem Bearer) e o timeout scanner não passam por posse —
    ali a credencial é o próprio token. O árbitro NÃO deve sequer ser consultado.
    """
    adapter._routing_work_task_holder = AsyncMock(
        return_value={"found": False, "via": "none", "in_queue": True}
    )
    sid = await adapter.handle_resume(
        resume_token = RESUME_TOKEN,
        tenant_id    = TENANT_ID,
        payload      = {"decision": "timeout", "source": "timeout_scanner"},
    )
    assert sid == SESSION_ID
    adapter._routing_work_task_holder.assert_not_called()
