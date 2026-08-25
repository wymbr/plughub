"""
test_queue_wait_sla_stamp.py — D14 (ii): o alvo de espera é carimbado no SEGMENTO.

O que está sob exame **não é** "o campo aparece no payload". É a proposição mais
estreita e mais cara: *o produtor sabe dizer AUSENTE, e nunca fabrica um alvo.*

── Por que a fatia existe ────────────────────────────────────────────────────────
SLA é fato do segmento de ESPERA, nunca da sessão. Uma sessão carrega UM alvo,
então contato que espera em duas filas perde a violação da segunda. Medido no
`retencao_humano` depois da (i): 48 esperas = 5 abertas + **10 SEM ALVO** + 33
julgáveis — e as 10 pertencem a sessões cujo `sessions.sla_target_ms` é 0/NULL
**enquanto o pool tem 300 000 ms configurado e a espera aconteceu naquele pool**.
23% das esperas concluídas daquele pool injulgáveis por o alvo estar guardado na
entidade errada.

── A linha que dá valor a este arquivo ───────────────────────────────────────────
É `test_absent_pool_config_stamps_null_never_zero_nor_fallback`. Sem ela o
arquivo inteiro passaria com um produtor que aplicasse `SLA_TARGET_MS_FALLBACK`
— e um alvo fabricado no LEDGER é pior que alvo nenhum: ele não é corrigível por
deploy (só por migração), e mente com a mesma cara de um alvo real.

O gatilho da ausência não é evento malformado, é **relógio**: `{t}:pool_config:{p}`
tem TTL efetivo de 1 h. É o mesmo gatilho que produziu a ETA de 0 ms publicada ao
cliente, achado em 2026-08-24.

── Testemunha de presença ────────────────────────────────────────────────────────
`test_wait_segment_carries_the_pool_target` está aqui porque um contador de
AUSÊNCIA sozinho passa com um produtor que nunca carimba nada.

Teste de INTEGRAÇÃO: precisa de um Redis real.
    PLUGHUB_REDIS_URL=redis://redis:6379 pytest test_queue_wait_sla_stamp.py

O skip por Redis ausente é EXPLÍCITO (nunca verde por ausência de ambiente) e lê
as DUAS variáveis — `REDIS_URL` não existe dentro do container.
"""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing.models import SLA_TARGET_MS_FALLBACK, pool_config_key
from plughub_routing.mute_queue import first_queued_key, resolve_queue_exit


REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)


class _FakeProducer:
    """Mesmo contrato do publish real — `send(topic, key=bytes, value=dict)`.

    Cópia deliberada do `_FakeProducer` de `test_queue_wait_segment.py` em vez de
    import: um fake compartilhado que ganhasse tolerância para servir a um teste
    a perde para todos. Fixture mais permissiva que o contrato é bug catalogado
    neste repositório.
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
    tenant = f"t_sla_{uuid.uuid4().hex[:8]}"
    try:
        yield client, _FakeProducer(), tenant
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


async def _queue_and_exit(client, producer, tenant, pool, *, cfg: dict | None) -> dict:
    """Enfileira, opcionalmente semeia o `pool_config`, sai da fila. Devolve o evento."""
    sid = f"ses-{uuid.uuid4().hex[:8]}"
    await client.set(first_queued_key(tenant, sid), str(int(time.time() * 1000) - 5_000))
    if cfg is not None:
        await client.set(pool_config_key(tenant, pool), json.dumps(cfg))

    emitted = await resolve_queue_exit(client, producer, tenant, pool, sid, "handoff")
    assert emitted is True, "pré-condição quebrada: a saída de fila não emitiu nada"
    assert len(producer.sent) == 1, f"esperado 1 segmento, veio {len(producer.sent)}"
    _topic, _key, value = producer.sent[-1]
    return value


@pytest.mark.asyncio
async def test_wait_segment_carries_the_pool_target(env):
    """
    TESTEMUNHA DE PRESENÇA. Reprova se o campo sumir do payload, ou se o produtor
    parar de resolvê-lo da config do pool onde a espera aconteceu.
    """
    client, producer, tenant = env
    value = await _queue_and_exit(
        client, producer, tenant, "retencao_humano",
        cfg={"sla_target_ms": 300_000, "pool_id": "retencao_humano"},
    )

    assert "sla_target_ms" in value, (
        "o campo não está no evento — o consumidor de analytics é uma ALLOWLIST, "
        "então a coluna nasceria NULL com todo o pipeline verde"
    )
    assert value["sla_target_ms"] == 300_000, (
        f"alvo carimbado={value['sla_target_ms']!r}, config do pool=300000"
    )
    assert value["role"] == "queue", "só o segmento de espera carrega alvo de espera"


@pytest.mark.asyncio
async def test_absent_pool_config_stamps_null_never_zero_nor_fallback(env):
    """
    ⚠️ **A LINHA QUE DÁ VALOR A ESTE ARQUIVO.**

    Config fora do cache (TTL de 1 h — gatilho de RELÓGIO, não evento malformado)
    ⇒ o segmento sai SEM alvo, honestamente. Três coisas erradas que este teste
    reprova, e as três já aconteceram neste repositório com outro nome:

      · `0`      — some no relatório e faz a espera parecer violada por construção;
      · `480000` — `SLA_TARGET_MS_FALLBACK` gravado no ledger como se fosse config
                   do tenant. **Não é corrigível por deploy**, só por migração;
      · o campo ausente do dict — indistinguível de `null` na tabela, mas o
                   consumidor é allowlist e o defeito reapareceria mais tarde.
    """
    client, producer, tenant = env
    value = await _queue_and_exit(
        client, producer, tenant, "pool_sem_cache", cfg=None,
    )

    assert value["sla_target_ms"] is None, (
        f"alvo FABRICADO ({value['sla_target_ms']!r}) para uma espera cujo pool "
        "não estava no cache. Ausência tem de ser dizível."
    )
    assert value["sla_target_ms"] != 0, "0 não é alvo — é ausência disfarçada"
    assert value["sla_target_ms"] != SLA_TARGET_MS_FALLBACK, (
        "o fallback vazou para o ledger: 480 s carimbados como se fossem a config "
        "do pool"
    )


@pytest.mark.asyncio
async def test_zero_in_config_is_refused_as_absence(env):
    """
    `0` na config não é "alvo instantâneo". O contrato do pool é `.positive()`,
    então zero só chega por evento malformado ou linha legada — e preservá-lo
    faria toda espera daquele pool contar como violação.

    Este ramo é o irmão do de cima e tem gatilho de natureza DIFERENTE (dado
    malformado × relógio). Por isso são dois testes, não um.
    """
    client, producer, tenant = env
    value = await _queue_and_exit(
        client, producer, tenant, "pool_zerado", cfg={"sla_target_ms": 0},
    )
    assert value["sla_target_ms"] is None, (
        f"0 preservado como alvo ({value['sla_target_ms']!r}) — o predicado "
        "`resolve_sla_target_ms` deixou de ser aplicado neste caminho"
    )


@pytest.mark.asyncio
async def test_boolean_in_config_is_refused(env):
    """
    `bool` é `int` em Python: `isinstance(True, int)` é `True`. Sem a guarda, um
    `true` no JSON viraria alvo de 1 ms — valor plausível produzido por um tipo
    errado, a forma mais barata de todas.
    """
    client, producer, tenant = env
    value = await _queue_and_exit(
        client, producer, tenant, "pool_bool", cfg={"sla_target_ms": True},
    )
    assert value["sla_target_ms"] is None, (
        f"booleano aceito como alvo ({value['sla_target_ms']!r}) — viraria 1 ms"
    )


@pytest.mark.asyncio
async def test_ai_pool_also_carries_a_target(env):
    """
    DECISÃO DO DONO (2026-08-24), gravada como teste para não ser reaberta por
    engano: **espera é espera**. A sub-pergunta da D14.1 era se pool de IA tem
    alvo — dos 63 segmentos `role='queue'` medidos, 19 estavam em pools de IA.
    Decidido que qualquer fila carrega o alvo do seu pool, e o rótulo perde o
    "humana".

    Reprova se alguém introduzir um ramo por `agent_kind` no produtor. Um pool de
    IA sem alvo configurado cai no ramo de ausência, como qualquer outro — o que
    o distingue é a CONFIG, não o tipo do agente.
    """
    client, producer, tenant = env
    value = await _queue_and_exit(
        client, producer, tenant, "sac_ia",
        cfg={"sla_target_ms": 30_000, "agent_kind": "ai"},
    )
    assert value["sla_target_ms"] == 30_000, (
        f"pool de IA saiu com alvo {value['sla_target_ms']!r} — alguém ramificou "
        "por agent_kind, contra a decisão registrada"
    )


@pytest.mark.asyncio
async def test_unreadable_config_degrades_without_losing_the_segment(env):
    """
    Config ilegível ⇒ alvo ausente, **mas o segmento continua sendo emitido**.

    A tentação é deixar a exceção subir: a leitura da config acontece a poucas
    linhas do publish, e uma falha ali levaria junto o registro da espera. Perder
    o alvo é perder uma dimensão; perder o segmento é perder o FATO — e a
    ausência do fato é invisível, porque ninguém conta a linha que não existe.
    """
    client, producer, tenant = env
    sid = "ses-cfg-ilegivel"
    await client.set(first_queued_key(tenant, sid), str(int(time.time() * 1000) - 5_000))
    await client.set(pool_config_key(tenant, "pool_quebrado"), "{isto nao e json")

    emitted = await resolve_queue_exit(
        client, producer, tenant, "pool_quebrado", sid, "abandoned",
    )

    assert emitted is True
    assert len(producer.sent) == 1, (
        "a config ilegível levou o SEGMENTO junto — a espera deixou de existir"
    )
    _topic, _key, value = producer.sent[0]
    assert value["sla_target_ms"] is None
    assert value["duration_ms"] >= 5_000, "a duração real sobreviveu à degradação"
