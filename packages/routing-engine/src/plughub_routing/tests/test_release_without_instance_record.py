"""
test_release_without_instance_record.py — AGH-03: a vaga sai do semáforo mesmo quando o
REGISTRO da instância já foi apagado.

O DEFEITO (medido em 2026-09-15, 3 órfãos em 3 no `probe_agent_ws_restart_ghost`)
==============================================================================
`remove_conversation` só chamava `release_instance` dentro de `if raw_inst:`. O agente
humano é desregistrado ANTES de o `agent_done` ser consumido — pelo `close` (publica
`agent_disconnect` e chama `unregisterHumanAgent` na sequência) e pelo varredor da AGH-02 —,
então o registro some e o SEMÁFORO, que é outra chave, fica com a vaga. O reap só a devolve
quando a sessão fecha; se o MESMO usuário relogar antes, volta com capacidade a menos.

O QUE ESTE ARQUIVO AFIRMA (em pares, para não passar por um release que apague tudo)
=================================================================================
  1. sem registro, a vaga DESTA sessão sai do semáforo;
  2. TESTEMUNHA: a vaga de OUTRA sessão na mesma instância fica;
  3. sem registro, `hold_for_wrapup` NÃO cria hold — não há wrap-up para quem saiu;
  4. o caminho é BARULHENTO: o warning nomeia instância e conversa.

Integração com Redis real (o Lua do semáforo roda no servidor). Pula sem Redis — e pular
aparece verde: rode com REDIS_URL/PLUGHUB_REDIS_URL apontando para o Redis do demo.
"""
from __future__ import annotations

import logging
import os
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing.registry import InstanceRegistry, _instance_sessions_key

REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)

INSTANCE = "human-agh03-retencao_humano"


@pytest.fixture
async def ctx():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    reg = InstanceRegistry(client)
    tenant = f"t_agh03_{uuid.uuid4().hex[:8]}"
    try:
        yield reg, client, tenant
    finally:
        # O remove_conversation recompõe snapshots do tenant: limpa tudo que ele criou.
        async for k in client.scan_iter(match=f"{tenant}:*", count=500):
            await client.delete(k)
        await client.aclose()


async def _ocupa(reg, tenant, sid):
    assert await reg.claim_instance(tenant, INSTANCE, sid, None, 3, pool_id="retencao_humano") >= 1


@pytest.mark.asyncio
async def test_sem_registro_a_vaga_desta_sessao_SAI_do_semaforo(ctx, caplog):
    reg, client, tenant = ctx
    key = _instance_sessions_key(tenant, INSTANCE)
    await _ocupa(reg, tenant, "ses-que-fecha")
    await _ocupa(reg, tenant, "ses-que-segue")
    # O registro da instância NÃO existe — foi o desregistro humano que o apagou.
    assert not await client.exists(f"{tenant}:instance:{INSTANCE}")

    with caplog.at_level(logging.WARNING, logger="plughub_routing.registry"):
        await reg.remove_conversation(tenant, INSTANCE, "ses-que-fecha",
                                      fallback_pools=["retencao_humano"])

    membros = await client.smembers(key)
    assert not any(m.startswith("ses-que-fecha::") for m in membros), membros
    # TESTEMUNHA: o release é por prefixo DESTA sessão — a outra continua ocupando.
    assert any(m.startswith("ses-que-segue::") for m in membros), membros
    assert await client.scard(key) == 1
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "registro AUSENTE" in log and INSTANCE in log and "ses-que-fecha" in log


@pytest.mark.asyncio
async def test_sem_registro_hold_de_wrapup_NAO_e_criado(ctx):
    """Um hold seguraria a vaga para um wrap-up que ninguém vai atender — é o mesmo
    vazamento com outro nome, até o hold expirar."""
    reg, client, tenant = ctx
    key = _instance_sessions_key(tenant, INSTANCE)
    await _ocupa(reg, tenant, "ses-com-wrapup")

    await reg.remove_conversation(tenant, INSTANCE, "ses-com-wrapup",
                                  fallback_pools=["retencao_humano"], hold_for_wrapup=True)

    assert await client.scard(key) == 0, await client.smembers(key)
