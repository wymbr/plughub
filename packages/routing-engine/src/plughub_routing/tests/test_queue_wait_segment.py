"""
test_queue_wait_segment.py — produtor da janela de espera (D12).

Sob teste está UMA propriedade, e ela é sobre AUSÊNCIA: **contato que não esperou
não produz segmento de espera.** O caminho que a viola não fica vermelho em lugar
nenhum — ele produz uma linha `role='queue' outcome='handoff' duration_ms=0`, que
é um valor perfeitamente plausível e some no meio do relatório de Fila/SLA.

História (2026-08-21): o portão era `if raw is None`, e `_decode` devolve `""`
para chave ausente, nunca `None`. O teste nunca era verdadeiro, o `return False`
nunca disparava, e **todo contato roteado direto emitia uma espera fantasma**.
Medido em 3 contatos reais: 2 sem fila → 2 fantasmas (100%); a query canônica do
Problema 36.2 não via nenhuma delas, porque lá o predicado é `outcome='abandoned'`
e a fantasma é `handoff`.

Cada teste diz o que o faria REPROVAR:
  · test_no_stamp_emits_nothing      → o portão volta a aceitar carimbo ausente
  · test_stamp_emits_wait_segment    → o produtor para de registrar espera real
  · test_publish_is_keyed_by_session → some o `key=` do publish (P1)
  · test_segment_id_is_deterministic → o id volta a ser sorteado (uuid4)

⚠️ O `_FakeProducer` daqui aceita `key` **na forma exata** em que o código chama e
falha se ela não vier. Mock mais permissivo que o contrato foi o que já escondeu
bug neste repositório: fixture que aceita kwarg inexistente prova só que o teste é
tolerante.

Teste de INTEGRAÇÃO: precisa de um Redis real.
    PLUGHUB_REDIS_URL=redis://redis:6379 pytest test_queue_wait_segment.py

O skip por Redis ausente é EXPLÍCITO (nunca verde por ausência de ambiente) e lê
as DUAS variáveis — `REDIS_URL` não existe dentro do container.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing import mute_queue
from plughub_routing.mute_queue import (
    first_queued_key, queue_wait_segment_id, resolve_queue_exit,
)


REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)


class _FakeProducer:
    """
    Espelha o contrato REAL do publish: `send(topic, key=bytes, value=dict)`.

    `key` é obrigatória de propósito. `conversations.participants` tem 3
    partições e ordem no Kafka é por partição; com `segment_id` determinístico
    a MESMA sessão emite várias vezes a MESMA linha, e é a chave que garante que
    a última emissão (a que traz o desfecho real) chegue por último. Sem ela o
    vencedor é sorteio — a linha pode congelar em `handoff wait_ms=0`.
    """
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, dict]] = []

    async def send(self, topic: str, *, key: bytes, value: dict) -> None:
        assert isinstance(key, (bytes, bytearray)), (
            f"chave deve ir como bytes, veio {type(key).__name__}"
        )
        self.sent.append((topic, key, value))


@pytest.fixture
async def env():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    tenant = f"t_qw_{uuid.uuid4().hex[:8]}"
    try:
        yield client, _FakeProducer(), tenant
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


@pytest.mark.asyncio
async def test_no_stamp_emits_nothing(env):
    """
    O caso que o defeito de 2026-08-21 produzia: sessão SEM `first_queued_ms`.

    Reprova se o portão voltar a comparar com `None` (ou qualquer teste que
    trate `""` como presença). O sintoma no produto é uma espera fantasma de
    0 ms em todo contato atendido na hora.
    """
    client, producer, tenant = env
    sid = "ses-sem-fila"

    assert await client.get(first_queued_key(tenant, sid)) is None, (
        "pré-condição quebrada: a sessão do teste já tem carimbo de fila"
    )

    emitted = await resolve_queue_exit(
        client, producer, tenant, "sac_ia", sid, "handoff",
    )

    assert emitted is False, "declarou espera para contato que nunca esperou"
    assert producer.sent == [], (
        f"emitiu {len(producer.sent)} segmento(s) de espera fantasma: "
        f"{[v.get('duration_ms') for _, _, v in producer.sent]}"
    )


@pytest.mark.asyncio
async def test_stamp_emits_wait_segment(env):
    """
    TESTEMUNHA DE PRESENÇA — sem ela, o teste acima passaria com um produtor
    que nunca emite nada. Um contador de ausência precisa do par.

    Reprova se o produtor deixar de registrar espera real, ou se a duração
    deixar de derivar do carimbo (ex.: alguém trocar por `now - now`).
    """
    client, producer, tenant = env
    sid       = "ses-com-fila"
    waited_ms = 5_000
    first_ms  = int(time.time() * 1000) - waited_ms
    await client.set(first_queued_key(tenant, sid), str(first_ms))

    emitted = await resolve_queue_exit(
        client, producer, tenant, "retencao_humano", sid, "abandoned",
    )

    assert emitted is True
    assert len(producer.sent) == 1, f"esperado 1 segmento, veio {len(producer.sent)}"
    topic, _key, value = producer.sent[0]
    assert topic == "conversations.participants"
    assert value["role"] == "queue"
    assert value["outcome"] == "abandoned"
    assert value["pool_id"] == "retencao_humano", (
        "D10.1: o pool do segmento de espera é o DESTINO, não a fila que executou"
    )
    assert value["duration_ms"] >= waited_ms, (
        f"duração {value['duration_ms']}ms não reflete a espera de {waited_ms}ms — "
        "o carimbo deixou de ser a fonte"
    )

    assert await client.get(first_queued_key(tenant, sid)) is None, (
        "o carimbo sobreviveu à emissão — a segunda saída da mesma sessão "
        "emitiria de novo com duração recontada a partir do início original"
    )


@pytest.mark.asyncio
async def test_publish_is_keyed_by_session(env):
    """
    P1 — a chave do publish é `session_id`, e ela é LOAD-BEARING.

    A justificativa original ("este caminho emite um evento só") não se
    sustentou na medição: um contato que escala produziu TRÊS emissões para a
    mesma sessão, todas com o mesmo `segment_id`. Quem vence é quem chega por
    último, e isso só é determinístico dentro de uma partição.

    Reprova se alguém remover o `key=` (o `_FakeProducer` levanta TypeError) ou
    trocar a convenção para `segment_id` — duas convenções de chave no mesmo
    tópico reabrem o problema pelo lado da cardinalidade.
    """
    client, producer, tenant = env
    sid = "ses-chave"
    await client.set(first_queued_key(tenant, sid), str(int(time.time() * 1000)))

    await resolve_queue_exit(client, producer, tenant, "sac_ia", sid, "handoff")

    assert len(producer.sent) == 1
    _topic, key, _value = producer.sent[0]
    assert key == sid.encode("utf-8"), (
        f"chave={key!r}, esperado {sid.encode('utf-8')!r} — mesma convenção do "
        "bridge (main.py), de propósito"
    )


@pytest.mark.asyncio
async def test_segment_id_is_deterministic(env):
    """
    O id derivado é o que torna emissão repetida INÓCUA: em vez de garantir
    "emite uma vez só" em N saídas concorrentes (impossível), garante-se que as
    N emissões sejam a MESMA linha, e o ReplacingMergeTree deduplica.

    Reprova se alguém voltar a `uuid4()` — o sintoma seria uma linha de espera
    por saída, inflando o volume de fila do relatório.
    """
    client, producer, tenant = env
    sid = "ses-id"
    await client.set(first_queued_key(tenant, sid), str(int(time.time() * 1000)))

    await resolve_queue_exit(client, producer, tenant, "sac_ia", sid, "handoff")

    _topic, _key, value = producer.sent[0]
    assert value["segment_id"] == queue_wait_segment_id(tenant, sid)
    assert value["segment_id"] == queue_wait_segment_id(tenant, sid), "não é estável"
    assert value["segment_id"] != mute_queue.queue_wait_segment_id(tenant, "outra"), (
        "o id não discrimina sessão"
    )
