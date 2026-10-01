"""
_att02_fixture.py — fixture dos probes da porta INTERNA do anexo (ATT-02, ATT-06). Roda DENTRO do
gateway, com o store REAL e o Redis do serviço.

MODE=create  SID, POOL, TENANT → grava um JPEG de verdade na sessão SID e dá à sessão o pool de
             entrada POOL (`session:{SID}:meta`, a fonte que `resolve_live_session_pools` lê).
             Opcionais:
               ROSTER  JSON da lista `session:{SID}:participants` (o roster do bridge, que diz
                       quem ATENDE — ATT-06). Ex.: [{"participant_id":"human-x","role":"primary"}]
               PDF=1   grava também um PDF (a não-imagem, que não tem prévia borrada)
               STREAM=1 grava no stream uma fala de cliente com o anexo no formato ANTIGO (com link)
             Imprime {"file": "<id>", "pdf": "<id>"} na última linha.
MODE=expire  SID, FILE[,…], TENANT → expira os anexos (o expurgo leva o blob) e apaga meta e roster.
"""
import asyncio
import io
import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import redis.asyncio as aioredis
from PIL import Image

from plughub_channel_gateway.attachment_store import configure_scanner
from plughub_channel_gateway.config import get_settings
from plughub_channel_gateway.main import _create_attachment_store

# ATT-05: o commit re-codifica a imagem e pergunta ao antivírus — a fixture grava uma de verdade.
# Com detalhe (xadrez), para a prévia borrada da ATT-06 ter o que perder.
_img = Image.new("L", (64, 64))
_img.putdata([255 * ((x + y) % 2) for y in range(64) for x in range(64)])
_buf = io.BytesIO()
_img.convert("RGB").save(_buf, format="JPEG", quality=95)
JPEG = _buf.getvalue()
PDF = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"


async def main() -> None:
    s = get_settings()
    configure_scanner(s.clamav_host, s.clamav_port)   # este processo não passa pelo boot
    mode, sid, tenant = os.environ["MODE"], os.environ["SID"], os.environ["TENANT"]
    pool = await asyncpg.create_pool(s.database_url, min_size=1, max_size=2)
    store = _create_attachment_store(s, pool)
    r = aioredis.from_url(s.redis_url)
    expira = datetime.now(timezone.utc) + timedelta(hours=1)
    try:
        if mode == "create":
            out = {}
            for chave, nome, mime, dados in (("file", "probe.jpg", "image/jpeg", JPEG),
                                             ("pdf", "probe.pdf", "application/pdf", PDF)):
                if chave == "pdf" and os.environ.get("PDF") != "1":
                    continue
                fid, _ = await store.reserve(tenant_id=tenant, session_id=sid, file_name=nome,
                                             mime_type=mime, size_bytes=len(dados), expires_at=expira)
                await store.commit(file_id=fid, tenant_id=tenant, data=dados)
                out[chave] = fid
            await r.set(f"session:{sid}:meta", json.dumps({"pool_id": os.environ["POOL"]}), ex=900)
            if os.environ.get("ROSTER"):
                await r.set(f"session:{sid}:participants", os.environ["ROSTER"], ex=900)
            if os.environ.get("STREAM") == "1":
                # ATT-06: uma fala de cliente com anexo NO FORMATO ANTIGO (com o link assinado da
                # porta pública), para medir que a transcrição a mostra sem o link
                att = {"media_type": "image", "file_id": out["file"], "mime_type": "image/jpeg",
                       "url": f"http://localhost:8010/webchat/v1/attachments/{out['file']}?exp=1&sig=LINKANTIGO"}
                await r.xadd(f"session:{sid}:stream", {
                    "event_id": "att06-probe", "type": "message", "timestamp": expira.isoformat(),
                    "author_id": "c-att06", "author_role": "customer", "visibility": '"all"',
                    "payload": json.dumps({"message_id": "att06-probe", "text": "[Anexo: foto.jpg]",
                                           "content": {"type": "text", "text": "[Anexo: foto.jpg]",
                                                       "attachment": att}})})
                await r.expire(f"session:{sid}:stream", 900)
            print(json.dumps(out))
        else:
            for fid in [x for x in os.environ["FILE"].split(",") if x]:
                await store.soft_expire(file_id=fid)
            await r.delete(f"session:{sid}:meta", f"session:{sid}:participants", f"session:{sid}:stream")
    finally:
        await pool.close()
        await r.aclose()


asyncio.run(main())
