# -*- coding: utf-8 -*-
"""Exercicio do expurgo DENTRO do container do channel-gateway (VOZ-07).

Roda o codigo da IMAGEM contra o Postgres e o blob store REAIS da instalacao
(no demo, MinIO). Lido por stdin: `docker exec -i ... python - [--sem-expurgo]`.

Quatro anexos de fixture, numa sessao propria do tenant da instalacao (o serving
resolve pelo `settings.tenant_id`, entao so assim o 410 e mensuravel), gravados pelo caminho de produto
(`reserve` + `commit`, com bytes reais no store) e depois envelhecidos por SQL:

  vencida      expires_at ha 1 h, sem deleted_at      -> estagio 1 TEM de marcar
  carencia     deleted_at ha 25 h, com blob           -> estagio 2 TEM de apagar
  vigente      expires_at daqui a 1 dia               -> NADA pode acontecer
  recente      deleted_at ha 1 h, com blob            -> blob TEM de ficar (carencia)

Os dois controles sao a metade que se esquece: um expurgo que apagasse TUDO
passaria nos dois primeiros casos.

`--sem-expurgo` pula os estagios: e a mutacao, e os dois primeiros casos TEM de
reprovar — senao o exercicio mede a fixture, nao o expurgo.

Imprime uma linha JSON com o veredicto de cada caso. Limpa o que criou sempre.
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx

from plughub_channel_gateway import attachment_expiry as ae
from plughub_channel_gateway.config import get_settings
from plughub_channel_gateway.main import _create_attachment_store

JPEG = b"\xff\xd8\xff\xe0" + b"probe-voz07" * 8
SEM_EXPURGO = "--sem-expurgo" in sys.argv


async def blob_existe(store, meta_path):
    """True/False medido no backend; nunca inferido do Postgres."""
    if hasattr(store, "_bucket"):
        async with store._session.client("s3", endpoint_url=store._endpoint_url) as s3:
            try:
                await s3.head_object(Bucket=store._bucket, Key=meta_path)
                return True
            except Exception as exc:
                code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
                if str(code) in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise
    return (store._root / meta_path).exists()


async def main():
    settings = get_settings()
    TENANT = settings.tenant_id
    db = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    store = _create_attachment_store(settings, db)
    out = {"backend": type(store).__name__, "sem_expurgo": SEM_EXPURGO}
    ids, paths = {}, {}
    try:
        for nome in ("vencida", "carencia", "vigente", "recente"):
            fid, _ = await store.reserve(
                tenant_id=TENANT, session_id="probe-voz07", file_name=nome + ".jpg",
                mime_type="image/jpeg", size_bytes=len(JPEG),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
            meta = await store.commit(file_id=fid, tenant_id=TENANT, data=JPEG)
            ids[nome], paths[nome] = fid, meta.file_path
            if not await blob_existe(store, meta.file_path):
                out["erro"] = "fixture sem blob apos commit: %s" % nome
                return out

        async with db.acquire() as c:
            await c.execute("UPDATE session_attachments SET expires_at = NOW() - interval '1 hour' "
                            "WHERE file_id = $1", __import__("uuid").UUID(ids["vencida"]))
            await c.execute("UPDATE session_attachments SET expires_at = NOW() - interval '26 hours', "
                            "deleted_at = NOW() - interval '25 hours' WHERE file_id = $1",
                            __import__("uuid").UUID(ids["carencia"]))
            await c.execute("UPDATE session_attachments SET expires_at = NOW() - interval '2 hours', "
                            "deleted_at = NOW() - interval '1 hour' WHERE file_id = $1",
                            __import__("uuid").UUID(ids["recente"]))

        if not SEM_EXPURGO:
            out["marcadas"] = await ae.expire_once(store)
            r = await ae.purge_once(store)
            out["apagadas"], out["falhas"] = r.purged, r.failed

        async with db.acquire() as c:
            rows = {str(r["file_id"]): r for r in await c.fetch(
                "SELECT file_id, deleted_at, file_path FROM session_attachments "
                "WHERE file_id = ANY($1::uuid[])", [__import__("uuid").UUID(i) for i in ids.values()])}

        def row(n):
            return rows[ids[n]]

        async with httpx.AsyncClient(timeout=10) as http:
            base = settings.webchat_serving_base_url.rstrip("/")
            st_vencida = (await http.get("%s/%s" % (base, ids["vencida"]))).status_code
            st_vigente = (await http.get("%s/%s" % (base, ids["vigente"]))).status_code
        out["http"] = {"vencida": st_vencida, "vigente": st_vigente}

        out["casos"] = {
            "vencida_serving_410": st_vencida == 410,
            "vigente_serving_200": st_vigente == 200,
            "vencida_marcada": row("vencida")["deleted_at"] is not None,
            "carencia_blob_apagado": (row("carencia")["file_path"] is None
                                      and not await blob_existe(store, paths["carencia"])),
            "vigente_intacta": (row("vigente")["deleted_at"] is None
                                and await blob_existe(store, paths["vigente"])),
            "recente_blob_mantido": (row("recente")["file_path"] is not None
                                     and await blob_existe(store, paths["recente"])),
        }
        return out
    finally:
        # limpeza: blobs que sobraram e as linhas de fixture, sempre por ID
        for n, p in paths.items():
            try:
                if hasattr(store, "_bucket"):
                    async with store._session.client("s3", endpoint_url=store._endpoint_url) as s3:
                        await s3.delete_object(Bucket=store._bucket, Key=p)
                else:
                    (store._root / p).unlink(missing_ok=True)
            except Exception as exc:
                out.setdefault("limpeza_falhou", []).append("%s: %s" % (n, exc))
        async with db.acquire() as c:
            # por ID, nunca por tenant: as fixtures moram no tenant REAL
            await c.execute("DELETE FROM session_attachments WHERE file_id = ANY($1::uuid[])",
                            [__import__("uuid").UUID(i) for i in ids.values()])
        await db.close()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), default=str))
