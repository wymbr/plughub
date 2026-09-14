"""
_webrtc_pool_media_policy_bridge.py — metade PRODUTORA do `probe_webrtc_pool_media_policy.sh` (VOZ-10).

Roda DENTRO da imagem do orchestrator-bridge, na rede do compose, com o `main` DA IMAGEM
contra o agent-registry e o Redis reais. UM processo só, como o bridge de verdade — é o
que dá sentido à mutação de cache: um cache que morre com o processo não seria medido.

Sequência (a mesma sessão webrtc de fixture):
  1. PUT política P1 no pool de fixture            (via API do registry, com o JWT do admin)
  2. routing.assigned human  h1                    → deve levar P1
  3. routing.assigned native ia1                   → deve levar P1
  4. PUT política P2 no MESMO pool
  5. routing.assigned human  h2                    → deve levar P2 (leitura FRESCA)
  6. registry inalcançável; routing.assigned h3    → `registry_unavailable`, sem política

Não julga nada: escreve no stream e imprime `PASSO <n> <status http>`. Quem julga é a
metade leitora, no gateway — o contrato se mede no LEITOR.

MODE=mut_cache : `get_pool_config` memorizado por pool → o passo 5 leva P1, e o C4 do
                 leitor TEM de reprovar.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp
import redis.asyncio as aioredis

from plughub_orchestrator_bridge import main as m

MODE   = os.environ.get("MODE", "full")
SID    = os.environ["SID"]
POOL   = os.environ["POOL"]
TENANT = os.environ["TENANT"]
TOKEN  = os.environ["ADMIN_TOKEN"]
P1     = json.loads(os.environ["P1"])
P2     = json.loads(os.environ["P2"])


async def main() -> None:
    if MODE == "mut_cache":
        _orig, _memo = m.get_pool_config, {}

        async def _cached(http, tenant_id, pool_id):
            if pool_id not in _memo:
                _memo[pool_id] = await _orig(http, tenant_id, pool_id)
            return _memo[pool_id]
        m.get_pool_config = _cached

    r = aioredis.from_url(m.REDIS_URL, decode_responses=True)
    await r.setex(f"session:{SID}:meta", 600, json.dumps({"channel": "webrtc", "tenant_id": TENANT}))
    headers = {"Authorization": f"Bearer {TOKEN}", "x-tenant-id": TENANT, "Content-Type": "application/json"}

    async with aiohttp.ClientSession() as http:
        async def put(policy: dict, n: int) -> None:
            async with http.put(f"{m.AGENT_REGISTRY_URL}/v1/pools/{POOL}", headers=headers,
                                data=json.dumps({"media_policy": policy})) as resp:
                print(f"PASSO {n} {resp.status}", flush=True)

        async def assign(framework: str, iid: str, n: int) -> None:
            field = await m._routing_assigned_pool_field(http, r, SID, TENANT, POOL)
            await m._write_routing_assigned_to_stream(
                redis_client=r, session_id=SID, framework=framework,
                pool_config=field, segment_id="", instance_id=iid,
            )
            print(f"PASSO {n} escrito", flush=True)

        await put(P1, 1)
        await assign("human", "h1", 2)
        await assign("native", "ia1", 3)
        await put(P2, 4)
        await assign("human", "h2", 5)
        m.AGENT_REGISTRY_URL = "http://registry-inalcancavel.invalid:1"
        await assign("human", "h3", 6)
    await r.aclose()


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
