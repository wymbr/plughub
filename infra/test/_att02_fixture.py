"""
_att02_fixture.py — fixture do `probe_att02_attachment_internal_door.sh` (ATT-02). Roda DENTRO do
gateway, com o store REAL e o Redis do serviço.

MODE=create  SID, POOL, TENANT → grava um JPEG de verdade na sessão SID e dá à sessão o pool de
             entrada POOL (`session:{SID}:meta`, a fonte que `resolve_live_session_pools` lê).
             Imprime {"file": "<file_id>"} na última linha.
MODE=expire  SID, FILE, TENANT  → expira o anexo (o expurgo leva o blob) e apaga o meta.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import redis.asyncio as aioredis

from plughub_channel_gateway.config import get_settings
from plughub_channel_gateway.main import _create_attachment_store

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


async def main() -> None:
    s = get_settings()
    mode, sid, tenant = os.environ["MODE"], os.environ["SID"], os.environ["TENANT"]
    pool = await asyncpg.create_pool(s.database_url, min_size=1, max_size=2)
    store = _create_attachment_store(s, pool)
    r = aioredis.from_url(s.redis_url)
    try:
        if mode == "create":
            fid, _ = await store.reserve(
                tenant_id=tenant, session_id=sid, file_name="probe.jpg", mime_type="image/jpeg",
                size_bytes=len(JPEG), expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
            await store.commit(file_id=fid, tenant_id=tenant, data=JPEG)
            await r.set(f"session:{sid}:meta", json.dumps({"pool_id": os.environ["POOL"]}), ex=900)
            print(json.dumps({"file": fid}))
        else:
            await store.soft_expire(file_id=os.environ["FILE"])
            await r.delete(f"session:{sid}:meta")
    finally:
        await pool.close()
        await r.aclose()


asyncio.run(main())
