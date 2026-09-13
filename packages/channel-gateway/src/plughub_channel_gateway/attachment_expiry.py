"""
attachment_expiry.py — o expurgo de anexos, em dois estágios (VOZ-07)

⚠️ POR QUE ESTE ARQUIVO EXISTE. O cron de dois estágios estava descrito em TRÊS
casas — docstring de `attachment_store.py`, `adr-webchat-channel.md` e o
`CLAUDE.md` § WebChat — e em nenhum código. Medido em 2026-09-13: `soft_expire`
sem chamador, nenhum SQL aplicando `expires_at`, e o serving recusando só por
`deleted_at`, que ninguém escrevia. O `attachment_expiry_days` editável na tela
carimbava uma data que nada lia: **anexo nenhum expirava**, e a minimização
prometida pela política de retenção era só a promessa.

OS DOIS ESTÁGIOS
  1 · a cada passada (1 h): `expire_due` marca `deleted_at` nas linhas vencidas.
      Dali em diante o serving responde 410 — o dado some para quem pede.
  2 · a cada 24 h: `purge_deleted` apaga o BLOB de quem foi marcado há mais de
      `PURGE_GRACE` e zera `file_path`. A carência cobre download em curso no
      momento da marcação.

O que NÃO é config, e por quê: os três números abaixo são mecânica do expurgo,
não política de retenção — a política é `attachment_expiry_days`, que já mora na
config-api com tela. Pô-los em env violaria *"env só para segredo e topologia"*,
e pô-los na config-api daria ao tenant um botão para adiar a própria
minimização.

DEGRADAÇÃO NUNCA É MUDA
  * cada passada loga o que marcou e o que apagou, inclusive zero — um expurgo
    que não loga é indistinguível de um expurgo que não roda, que era o defeito;
  * blob que não sai é CONTADO e NOMEADO (`PurgeResult.failed_ids`), mantém
    `file_path` e é tentado de novo;
  * exceção numa passada é logada e o laço segue: morrer aqui levaria de volta
    ao estado em que nada expira, e o supervisor só alarmaria depois.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from .attachment_store import AttachmentStore, PurgeResult

logger = logging.getLogger("plughub.channel-gateway.attachment-expiry")

EXPIRE_INTERVAL_S = 3600
PURGE_INTERVAL_S = 86400
PURGE_GRACE = timedelta(hours=24)
# Lote por chamada. O laço drena lote a lote até vir um incompleto, com teto de
# lotes por passada para uma fila enorme não monopolizar o processo.
BATCH = 500
MAX_BATCHES = 40


async def expire_once(store: AttachmentStore, *, batch: int = BATCH,
                      max_batches: int = MAX_BATCHES) -> int:
    """Estágio 1, drenado. Devolve quantas linhas marcou nesta passada."""
    total = 0
    for _ in range(max_batches):
        n = await store.expire_due(limit=batch)
        total += n
        if n < batch:
            break
    else:
        logger.warning(
            "attachment expiry: teto de %d lotes atingido com %d marcadas — "
            "a fila de vencidos e maior que uma passada; o resto fica para a proxima",
            max_batches, total,
        )
    return total


async def purge_once(store: AttachmentStore, *, grace: timedelta = PURGE_GRACE,
                     batch: int = BATCH, max_batches: int = MAX_BATCHES) -> PurgeResult:
    """Estágio 2, drenado. Falha não interrompe o lote; é somada e nomeada."""
    purged, falhas = 0, {}
    for _ in range(max_batches):
        r = await store.purge_deleted(grace=grace, limit=batch)
        purged += r.purged
        # A linha que falhou mantém `file_path` e VOLTA no lote seguinte da mesma
        # passada; somar `r.failed` contaria o mesmo blob várias vezes. A falha é
        # por ARQUIVO, então conta-se por id.
        falhas.update(dict.fromkeys(r.failed_ids))
        # Lote sem nenhum apagado só repetiria as mesmas falhas: parar evita girar
        # em falso sobre um blob que não sai.
        if r.purged + r.failed < batch or r.purged == 0:
            break
    return PurgeResult(purged=purged, failed=len(falhas), failed_ids=list(falhas))


async def run_attachment_expiry(
    store: AttachmentStore,
    *,
    expire_interval_s: int = EXPIRE_INTERVAL_S,
    purge_interval_s: int = PURGE_INTERVAL_S,
) -> None:
    """Task de BOOT. Roda a primeira passada já na subida — reinício não pode
    empurrar o expurgo para uma hora depois — e depois a cada intervalo."""
    logger.info(
        "attachment expiry started (estagio1=%ds, estagio2=%ds, carencia=%s)",
        expire_interval_s, purge_interval_s, PURGE_GRACE,
    )
    ultimo_purge = float("-inf")
    while True:
        try:
            marcadas = await expire_once(store)
            logger.info("attachment expiry: estagio 1 marcou %d anexo(s) vencido(s)", marcadas)
            if time.monotonic() - ultimo_purge >= purge_interval_s:
                r = await purge_once(store)
                ultimo_purge = time.monotonic()
                if r.failed:
                    logger.warning(
                        "attachment expiry: estagio 2 apagou %d blob(s) e FALHOU em %d: %s",
                        r.purged, r.failed, ", ".join(r.failed_ids[:20]),
                    )
                else:
                    logger.info("attachment expiry: estagio 2 apagou %d blob(s)", r.purged)
        except asyncio.CancelledError:
            logger.info("attachment expiry stopped")
            raise
        except Exception as exc:
            logger.error("attachment expiry: passada falhou, segue na proxima: %s", exc)
        await asyncio.sleep(expire_interval_s)
