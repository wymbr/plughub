"""
Fixture do probe_voz36_recording_access — roda DENTRO do channel-gateway (docker exec -i … python -).

MODE=create  guarda duas partes `call_recording` sintéticas numa sessão de probe (parte 1 atendida
             por POOL_A, parte 2 por POOL_B) e um anexo de webchat (controle de classe) — pelo
             MESMO store e o MESMO `reserve(attrs=…)` que o CallRecorder usa. Imprime JSON.
MODE=expire  soft_expire dos ids em IDS (vírgula) — o blob sai no ciclo diário da expiração.

O áudio é um cabeçalho Ogg mínimo: o gate mede o PORTÃO, não o conteúdo (conteúdo gravado de
verdade é o probe_voz06_recording).
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg

from plughub_channel_gateway.config import Settings
from plughub_channel_gateway.main import _create_attachment_store

TENANT = os.environ.get("TENANT", "tenant_demo")


async def main() -> None:
    s = Settings()
    pool = await asyncpg.create_pool(s.database_url, min_size=1, max_size=2)
    store = _create_attachment_store(s, pool)
    try:
        if os.environ["MODE"] == "expire":
            for fid in [x for x in os.environ.get("IDS", "").split(",") if x]:
                await store.soft_expire(file_id=fid)
            print(json.dumps({"expired": os.environ.get("IDS", "")}))
            return
        sid = os.environ["SID"]
        exp = datetime.now(timezone.utc) + timedelta(days=1)
        dados = b"OggS" + b"\x00" * 60
        out = {}
        for nome, parte, pool_id in (("a", 1, os.environ["POOL_A"]), ("b", 2, os.environ["POOL_B"])):
            fid, _ = await store.reserve(
                tenant_id=TENANT, session_id=sid, file_name=f"probe-voz36-p{parte}.ogg",
                mime_type="audio/ogg", size_bytes=len(dados), expires_at=exp,
                artifact_class="call_recording",
                attrs={"part": parte, "pools": [pool_id], "duration_ms": 1000,
                       "started_at": datetime.now(timezone.utc).isoformat(),
                       "ended_at": datetime.now(timezone.utc).isoformat()},
            )
            await store.commit(file_id=fid, tenant_id=TENANT, data=dados)
            out[nome] = fid
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60
        fid, _ = await store.reserve(tenant_id=TENANT, session_id=sid, file_name="probe-voz36.png",
                                     mime_type="image/png", size_bytes=len(png), expires_at=exp)
        await store.commit(file_id=fid, tenant_id=TENANT, data=png)
        out["web"] = fid
        print(json.dumps(out))
    finally:
        await pool.close()


asyncio.run(main())
