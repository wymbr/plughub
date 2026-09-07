"""
test_resume_meta_horizon.py — RSM-01: o prazo do meta cobre o prazo do token.

O QUE ESTA SUÍTE JULGA

Um `suspend` grava um resume_token que vive `timeout_hours*3600 + 3600` (48 h no
default) e depende, na retomada, de `session:{id}:meta` — a chave de onde saem
`tenant_id` e `agent_type_id`. O meta nasce com **24 h fixas**. Quando
`timeout_hours` passa de 23, o token sobrevive ao dado de que depende: a retomada
é ACEITA e morre em `tenant_unknown`.

⚠️ **A causa era uma LISTA.** O caminho do suspend já estendia quatro chaves da
sessão (`stream`, `ctx`, `pipeline`, `status`) e não esta. Uma lista parece
completa por ser uma lista, e o que falta nela não aparece em contagem nenhuma —
por isso o teste que importa aqui é o **T5**: ele não julga a função, julga se
alguém a CHAMA. Uma extensão perfeita que ninguém invoca é exatamente o estado
anterior.

O que faria cada teste ficar VERMELHO está no docstring de cada um.

Sem I/O: Redis é AsyncMock com `ttl` roteirizado. `expire` é asserido pelo par
(chave, prazo) — asserir só a chave deixaria passar um `expire` que ENCURTA, que
é o defeito irmão que esta função existe para proibir.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from plughub_channel_gateway.adapters.webhook import WebhookAdapter
from plughub_channel_gateway.config import Settings


TENANT_ID = "tenant_test"
SESSION_ID = "sid-rsm-001"
TOKEN = "a" * 43
META_KEY = f"session:{SESSION_ID}:meta"

DIA = 24 * 3600          # o que o meta ganha ao nascer
HORIZONTE = 48 * 3600 + 3600   # timeout_hours=48 + 1 h de folga


def _settings() -> Settings:
    return Settings(
        kafka_brokers="localhost:9092",
        kafka_group_id="test-group",
        kafka_topic_inbound="conversations.inbound",
        kafka_topic_outbound="conversations.outbound",
        kafka_topic_events="conversations.events",
        redis_url="redis://localhost:6379",
        tenant_id=TENANT_ID,
        storage_root="/tmp/plughub_test",
        attachment_expiry_days=1,
        database_url="postgresql://plughub:plughub@localhost/plughub",
        webchat_serving_base_url="http://localhost:8010/webchat/v1/attachments",
        webchat_upload_base_url="http://localhost:8010/webchat/v1/upload",
    )


def _adapter(ttl_do_meta: int) -> tuple[WebhookAdapter, AsyncMock]:
    redis = AsyncMock()
    redis.ttl = AsyncMock(return_value=ttl_do_meta)
    redis.expire = AsyncMock(return_value=True)
    redis.set = AsyncMock(return_value=True)
    return WebhookAdapter(producer=AsyncMock(), redis=redis, settings=_settings()), redis


@pytest.mark.asyncio
async def test_t1_meta_curto_e_estendido_ate_o_horizonte_do_token():
    """
    VERMELHO se: a extensão não acontecer, ou acontecer com prazo diferente do
    horizonte do token — que é o caso original (meta 24 h × token 49 h).
    """
    adapter, redis = _adapter(DIA)
    await adapter._extend_session_meta_ttl(SESSION_ID, HORIZONTE)
    redis.expire.assert_awaited_once_with(META_KEY, HORIZONTE)


@pytest.mark.asyncio
async def test_t2_meta_mais_longo_nunca_e_encurtado():
    """
    VERMELHO se: um `expire` cru substituir a comparação. É o controle NEGATIVO,
    e sem ele o T1 passaria com uma implementação que encurta — o defeito que o
    `session_meta_merge` do bridge já pagou caro (86 397 → 14 398 na alocação).
    """
    adapter, redis = _adapter(HORIZONTE + 7200)
    await adapter._extend_session_meta_ttl(SESSION_ID, HORIZONTE)
    redis.expire.assert_not_awaited()


@pytest.mark.asyncio
async def test_t3_meta_ausente_e_no_op():
    """
    VERMELHO se: a função criar prazo sobre chave inexistente. `-2` é ausência, e
    criar o meta daqui inventaria `tenant_id` — a ausência tem dono próprio (a
    escrita do trigger) e o `conversation_escalate` recusa em vez de adivinhar.
    """
    adapter, redis = _adapter(-2)
    await adapter._extend_session_meta_ttl(SESSION_ID, HORIZONTE)
    redis.expire.assert_not_awaited()


@pytest.mark.asyncio
async def test_t4_meta_sem_prazo_nao_e_bootstrap_aqui():
    """
    VERMELHO se: alguém "unificar" esta função com `_extend_hash_ttl`, onde `-1`
    DEFINE. A divergência é deliberada e as duas direções de erro são opostas: lá
    a chave nasce sem TTL no `HSET` e ficaria imortal; aqui todo escritor usa
    SETEX, então definir prazo sobre `-1` seria ENCURTAR — a única direção que
    esta função proíbe.
    """
    adapter, redis = _adapter(-1)
    await adapter._extend_session_meta_ttl(SESSION_ID, HORIZONTE)
    redis.expire.assert_not_awaited()


@pytest.mark.asyncio
async def test_t5_o_escritor_do_token_CHAMA_a_extensao():
    """
    O teste que a RSM-01 pede. VERMELHO se: a chamada sumir de
    `_write_resume_meta` — que é EXATAMENTE o estado anterior à correção, com a
    função existindo e ninguém a invocando.

    Passa por `_write_resume_meta` de propósito: é o funil por onde os dois
    escritores de token do adapter (collect e delegate_conference) já passam, e
    asserir sobre ele prova a ligação, não a intenção.
    """
    adapter, redis = _adapter(DIA)
    await adapter._write_resume_meta(
        TENANT_ID, TOKEN, SESSION_ID, "aguardar_resposta",
        "2026-09-09T12:00:00+00:00",
        suspend_reason="input", ttl_s=HORIZONTE,
    )
    redis.expire.assert_awaited_once_with(META_KEY, HORIZONTE)


@pytest.mark.asyncio
async def test_t6_falha_de_redis_nao_derruba_o_suspend():
    """
    VERMELHO se: a exceção escapar. Estender o meta é best-effort — falhar aqui
    não pode recusar um `suspend` legítimo. O log é a contrapartida obrigatória
    (degradação nunca silenciosa), e está no `except`.
    """
    adapter, redis = _adapter(DIA)
    redis.expire = AsyncMock(side_effect=RuntimeError("redis caiu"))
    await adapter._extend_session_meta_ttl(SESSION_ID, HORIZONTE)   # não levanta
