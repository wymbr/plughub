# -*- coding: utf-8 -*-
"""Fixture de cliente IMPORTADO — roda DENTRO do container do gateway.

Uso: `docker exec -i <gw> python - <system> <external_id> <cpf> <phone> < este_arquivo`

Existe desde a PID-10 (2026-09-13): o OTP só é emitido contra âncora de procedência
AUTORITATIVA, e a única porta que a carimba é a importação. Probe que precise de um
cliente capaz de passar pelo step-up de posse nasce por aqui — pela MESMA função da
rota `/identity/import` (`IdentityIndex.import_customers`), sem a credencial da rota,
porque o que está sob teste nesses probes é o fluxo, não o portão da importação
(esse é do `probe_identity_provenance.sh`).

Imprime uma linha JSON: {outcome, customer_id, reason, phone_hash}.
"""
import asyncio
import json
import os
import sys

import asyncpg
import redis.asyncio as aioredis

from plughub_channel_gateway.config import get_settings
from plughub_channel_gateway.identity.index import IdentityIndex
from plughub_channel_gateway.identity.normalize import hash_anchor


async def main():
    system, ext, cpf, phone = sys.argv[1:5]
    s = get_settings()
    salt = os.getenv("PLUGHUB_IDENTITY_SALT", "plughub_identity_demo_salt")
    db = await asyncpg.create_pool(s.database_url, min_size=1, max_size=2)
    r = aioredis.from_url(s.redis_url)
    try:
        idx = IdentityIndex(redis=r, salt=salt, db_pool=db)
        (res,) = await idx.import_customers(s.tenant_id, system, [{
            "external_id": ext,
            "anchors": [{"kind": "cpf", "value": cpf}, {"kind": "phone", "value": phone}],
        }], imported_by="probe_fixture")
        return {"outcome": res.outcome, "customer_id": res.customer_id, "reason": res.reason,
                "phone_hash": hash_anchor(salt, "phone", phone)}
    finally:
        await db.close()
        await r.aclose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), default=str))
