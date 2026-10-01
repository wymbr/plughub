"""
attachment_rescan.py — a quarentena sai pela mesma porta por onde entrou: o antivírus (ATT-05).

Anexo que chegou com o antivírus fora do ar foi gravado em QUARENTENA e não é servido. Anexo
anterior à esteira (`scan_status` NULL) nunca foi verificado e também não é servido. Esta task de
boot pergunta de novo, a cada passada, e é só ela que libera:

    clean     → servível (o sha256 é preenchido se faltava)
    infected  → `infected` e expirado já (`deleted_at`): o expurgo leva o blob em 24 h
    quarentena → fica; o motivo vai ao log, com a contagem — nunca liberado por cansaço

A linha anterior à esteira é só VARRIDA, não re-codificada: re-codificar mudaria os bytes que o
cliente e o atendente já viram. O EXIF dela fica até ela expirar — dito, não escondido.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging

from .attachment_store import AttachmentStore, SCAN_REQUIRED_CLASSES, scan_status_of

logger = logging.getLogger("plughub.channel-gateway.attachment-rescan")

RESCAN_INTERVAL_S = 60
BATCH = 20


async def rescan_once(store: AttachmentStore, db, *, batch: int = BATCH) -> dict[str, int]:
    """Uma passada. Devolve a contagem por desfecho."""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT file_id, tenant_id
            FROM   session_attachments
            WHERE  status = 'committed' AND deleted_at IS NULL AND file_path IS NOT NULL
              AND  artifact_class = ANY($1::text[])
              AND  (scan_status IS NULL OR scan_status = 'quarantined')
            ORDER  BY created_at
            LIMIT  $2
            """,
            list(SCAN_REQUIRED_CLASSES), batch,
        )
    conta = {"clean": 0, "infected": 0, "quarantined": 0}
    ultimo = "ilegível"
    for r in rows:
        fid, tenant = str(r["file_id"]), r["tenant_id"]
        try:
            dados = b"".join([c async for c in await store.stream_bytes(file_id=fid, tenant_id=tenant)])
        except Exception as exc:  # noqa: BLE001 — arquivo ilegível fica em quarentena, dito
            logger.warning("rescan: %s ilegível (%s) — segue em quarentena", fid, exc)
            conta["quarantined"] += 1
            continue
        status, motivo = await scan_status_of(dados)
        conta[status] += 1
        async with db.acquire() as conn:
            if status == "infected":
                await conn.execute(
                    """
                    UPDATE session_attachments
                    SET    scan_status = 'infected', deleted_at = NOW(),
                           attrs = attrs || jsonb_build_object('scan_reason', $2::text)
                    WHERE  file_id = $1
                    """, r["file_id"], motivo)
                logger.warning("rescan: %s INFECTADO (%s) — expirado, o expurgo leva o arquivo", fid, motivo)
            elif status == "clean":
                await conn.execute(
                    """
                    UPDATE session_attachments
                    SET    scan_status = 'clean', sha256 = COALESCE(sha256, $2),
                           attrs = attrs || jsonb_build_object('scan_reason', '')
                    WHERE  file_id = $1
                    """, r["file_id"], hashlib.sha256(dados).hexdigest())
            else:
                await conn.execute(
                    """
                    UPDATE session_attachments
                    SET    scan_status = 'quarantined',
                           attrs = attrs || jsonb_build_object('scan_reason', $2::text)
                    WHERE  file_id = $1
                    """, r["file_id"], motivo)
                ultimo = motivo
    if conta["quarantined"]:
        logger.warning("rescan: %d anexo(s) seguem em QUARENTENA (último motivo: %s)",
                       conta["quarantined"], ultimo)
    return conta


async def run_attachment_rescan(store: AttachmentStore, db, *, interval_s: int = RESCAN_INTERVAL_S) -> None:
    """Task de BOOT. A primeira passada roda já na subida."""
    logger.info("attachment rescan started (intervalo=%ds, lote=%d)", interval_s, BATCH)
    while True:
        try:
            conta = await rescan_once(store, db)
            if any(conta.values()):
                logger.info("attachment rescan: %s", conta)
        except asyncio.CancelledError:
            logger.info("attachment rescan stopped")
            raise
        except Exception as exc:  # noqa: BLE001 — a passada falha, a task segue
            logger.error("attachment rescan: passada falhou, segue na próxima: %s", exc)
        await asyncio.sleep(interval_s)
