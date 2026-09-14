# -*- coding: utf-8 -*-
"""Limpeza das fixtures da PID-09 DENTRO do container do gateway.

Lido por stdin: `docker exec -i <gw> python - '<json {fones:{…}, customers:[…]}>'`.
Apaga só o que o exercício criou — por hash de âncora, por id de cliente e pelo sistema de
importação `__probe_pid09__`, nunca por tenant. Imprime uma linha JSON com as contagens.
"""
import asyncio
import json
import os
import sys

import asyncpg
import redis.asyncio as aioredis

from plughub_channel_gateway.config import get_settings
from plughub_channel_gateway.identity.normalize import hash_anchor

SYSTEM = "__probe_pid09__"


async def main():
    arg = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    s = get_settings()
    tenant = s.tenant_id
    salt = os.getenv("PLUGHUB_IDENTITY_SALT", "plughub_identity_demo_salt")
    fones = list((arg.get("fones") or {}).values())
    cids = [c for c in (arg.get("customers") or []) if c]
    hashes = [hash_anchor(salt, "phone", f, None) for f in fones]
    db = await asyncpg.create_pool(s.database_url, min_size=1, max_size=2)
    rds = aioredis.from_url(s.redis_url)
    out = {}
    try:
        async with db.acquire() as conn:
            if cids:
                refs = await conn.fetch(
                    "SELECT customer_id FROM identity.customer_external_refs WHERE tenant_id=$1 AND system=$2",
                    tenant, SYSTEM)
                cids = sorted(set(cids) | {r["customer_id"] for r in refs})
            out["keys"] = await conn.execute(
                "DELETE FROM identity.customer_secondary_keys WHERE tenant_id=$1 AND "
                "(customer_id = ANY($2::text[]) OR (kind='phone' AND value_hash = ANY($3::text[])))",
                tenant, cids, hashes)
            out["refs"] = await conn.execute(
                "DELETE FROM identity.customer_external_refs WHERE tenant_id=$1 AND system=$2", tenant, SYSTEM)
            out["customers"] = await conn.execute(
                "DELETE FROM identity.customers WHERE customer_id = ANY($1::text[])", cids)
        chaves = ["%s:identity:phone:%s" % (tenant, h) for h in hashes]
        chaves += ["%s:pending_by_customer:%s" % (tenant, c) for c in cids]
        chaves += ["%s:customer:prospect:%s" % (tenant, c) for c in cids]
        out["redis"] = await rds.delete(*chaves) if chaves else 0
    finally:
        await rds.aclose()
        await db.close()
    return out


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), default=str))
