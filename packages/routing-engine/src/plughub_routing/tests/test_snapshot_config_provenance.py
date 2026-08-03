"""
test_snapshot_config_provenance.py
Pré-requisito da F3 (2026-08-03): de onde `refresh_pool_snapshot` tira os campos de
CONFIG quando não há snapshot.

POR QUE ISTO É UM TESTE, E NÃO UMA NOTA. O fan-out (`refresh_snapshots_for_instance`)
pulava pool sem snapshot com a justificativa *"recomputar aqui inventaria
sla/channel_types"*. A justificativa estava certa: o método caía num
`sla_target_ms=480_000` e `channel_types=[]` fabricados. Mas a saída escolhida — pular —
criou uma **dependência silenciosa** no bootstrap, que é quem criava a primeira linha de
todo pool a cada 15 s. Enquanto ele escrevesse, a lacuna se fechava sozinha e ninguém
via; e a F3 mexe justamente nele.

A cadeia agora é `snapshot → pool_config → default (com aviso)`. O que estes testes
travam:

  · **`channel_types` não pode nascer vazio quando existe config** — é o campo que o
    `system_availability_check` lê para decidir a oferta de canal AO CLIENTE. Vazio faz
    o pool parecer que não atende canal nenhum, e o sintoma é ausência de oferta, não
    erro.
  · **o pool passa a ser reescrito mesmo sem snapshot prévio** — que é a razão de a
    heurística ter saído.

Contra o código anterior, `test_config_fields_come_from_pool_config` nasce VERMELHO:
`channel_types` vinha `[]` e `sla_target_ms` vinha 480000, independentemente da config.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing.models import AgentInstance
from plughub_routing.registry import (
    InstanceRegistry,
    _pool_config_key,
    _pool_snapshot_key,
)

REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)


@pytest.fixture
async def env():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    reg = InstanceRegistry(client)
    tenant = f"t_prov_{uuid.uuid4().hex[:8]}"
    try:
        yield reg, client, tenant
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


async def _write_pool_config(client, tenant, pool_id, **over):
    doc = {
        "pool_id":       pool_id,
        "tenant_id":     tenant,
        "channel_types": ["whatsapp", "webchat"],
        "sla_target_ms": 120_000,
        "agent_kind":    "human",
    }
    doc.update(over)
    await client.set(_pool_config_key(tenant, pool_id), json.dumps(doc))


class TestProvenance:
    async def test_config_fields_come_from_pool_config_when_no_snapshot(self, env):
        """Sem snapshot, a config vem do cache autoritativo — não de um default."""
        reg, client, tenant = env
        pool = "retencao_humano"
        await _write_pool_config(client, tenant, pool)

        await reg.refresh_pool_snapshot(tenant, pool)

        snap = json.loads(await client.get(_pool_snapshot_key(tenant, pool)))
        assert snap["channel_types"] == ["whatsapp", "webchat"]
        assert snap["sla_target_ms"] == 120_000

    async def test_existing_snapshot_still_wins(self, env):
        """Precedência: o snapshot vivo continua sendo a 1ª fonte (menor mudança)."""
        reg, client, tenant = env
        pool = "sac_ia"
        await _write_pool_config(client, tenant, pool, channel_types=["sms"],
                                 sla_target_ms=999_000)
        await client.set(_pool_snapshot_key(tenant, pool), json.dumps({
            "pool_id": pool, "channel_types": ["voice"], "sla_target_ms": 60_000,
        }))

        await reg.refresh_pool_snapshot(tenant, pool)

        snap = json.loads(await client.get(_pool_snapshot_key(tenant, pool)))
        assert snap["channel_types"] == ["voice"]
        assert snap["sla_target_ms"] == 60_000

    async def test_no_source_at_all_uses_defaults(self, env, caplog):
        """Sem snapshot E sem config, o default entra — mas BARULHENTO.

        É o único caso em que a linha publicada não descreve config nenhuma. O aviso é
        o que distingue "não havia de onde tirar" de "o pool não atende canal nenhum",
        que é como um `channel_types: []` se lê na tela.
        """
        reg, client, tenant = env
        pool = "pool_fantasma"

        with caplog.at_level("WARNING"):
            await reg.refresh_pool_snapshot(tenant, pool)

        snap = json.loads(await client.get(_pool_snapshot_key(tenant, pool)))
        assert snap["channel_types"] == []
        assert snap["sla_target_ms"] == 480_000
        # `getMessage()`, não `record.message % record.args`: o caplog já entrega a
        # mensagem interpolada, então reinterpolá-la estoura com
        # "not all arguments converted during string formatting" — que se lê como
        # defeito do código sob teste, e é do teste.
        assert any("SEM snapshot e SEM pool_config" in r.getMessage()
                   for r in caplog.records), "default silencioso — o aviso sumiu"


class TestFanOutNoLongerSkips:
    async def test_pool_without_snapshot_IS_written(self, env):
        """A heurística "só se já existe snapshot" saiu — este é o caso que ela pulava.

        Um humano logado em dois pools: só um tem snapshot. Antes, o irmão ficava sem
        linha até o bootstrap passar; agora o fan-out escreve os dois.
        """
        reg, client, tenant = env
        with_snap, without_snap = "retencao_humano", "retencao_humano-int"
        await _write_pool_config(client, tenant, with_snap)
        await _write_pool_config(client, tenant, without_snap)
        await client.set(_pool_snapshot_key(tenant, with_snap), json.dumps({
            "pool_id": with_snap, "channel_types": ["whatsapp"], "sla_target_ms": 120_000,
        }))

        inst = "human-u_prov"
        await reg.set_instance(AgentInstance(
            instance_id=inst, agent_type_id="human", tenant_id=tenant,
            pools=[with_snap, without_snap], status="ready",
            max_concurrent=3, current_sessions=0,
        ))

        occ = await reg.refresh_snapshots_for_instance(tenant, inst)

        assert set(occ) == {with_snap, without_snap}
        assert await client.exists(_pool_snapshot_key(tenant, without_snap)) == 1
        snap = json.loads(await client.get(_pool_snapshot_key(tenant, without_snap)))
        # e nasceu com a config CERTA, não com o default
        assert snap["channel_types"] == ["whatsapp", "webchat"]
