"""
_config_put.py — escrita de config GLOBAL pelos probes, sem apagar a descrição. Roda DENTRO do
channel-gateway (tem `config_api_url` e o banco onde o config-api guarda `platform_config`).

Por que existe (ATT-07, 2026-10-01): o `PUT /config/{ns}/{key}` grava a `description` que receber,
e nenhuma rota de leitura a devolve. O probe da ATT-04 reenviava `""` achando que a tinha lido, e
apagou a descrição de `retention.attachment_days` a cada rodada (medido: vazia desde 2026-10-01).
Aqui a descrição atual é LIDA do store (só leitura) e reenviada; a ESCRITA é pela API.

  NS, KEY, VALUE (JSON), ADMIN (X-Admin-Token)   → imprime {"status": ..., "kept_description": n}
  DESCRIPTION (opcional)                          → usa este texto em vez do gravado
  TENANT (opcional)                               → escreve o override do tenant, não o global

Desde a CFG-01 o PUT com descrição vazia já mantém a gravada; o helper segue reenviando-a para
não depender disso, e é por ele que se TROCA uma descrição de propósito (DESCRIPTION).
"""
import asyncio
import json
import os

import asyncpg
import httpx

from plughub_channel_gateway.config import get_settings


async def main() -> None:
    s = get_settings()
    ns, key = os.environ["NS"], os.environ["KEY"]
    desc = os.environ.get("DESCRIPTION")
    tenant = os.environ.get("TENANT") or None
    if desc is None:
        conn = await asyncpg.connect(s.database_url)
        try:
            desc = await conn.fetchval(
                "SELECT description FROM public.platform_config "
                "WHERE tenant_id = $3 AND namespace = $1 AND key = $2", ns, key, tenant or "__global__") or ""
        finally:
            await conn.close()
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.put(f"{s.config_api_url.rstrip('/')}/config/{ns}/{key}",
                        json={"value": json.loads(os.environ["VALUE"]), "tenant_id": tenant,
                              "description": desc},
                        headers={"X-Admin-Token": os.environ["ADMIN"]})
    print(json.dumps({"status": r.status_code, "kept_description": len(desc)}))


asyncio.run(main())
