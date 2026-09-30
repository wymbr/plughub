"""
retention_job.py — entrada de mailing VENCIDA deixa de guardar os contatos (AUD-08).

`mailing_entries.expires_at` (vem do `entry_ttl_seconds` do mailing, ou do `ttl_seconds` da
entrada) era só FILTRO: o drain parava de ver a entrada, e telefone, e-mail e metadado
continuavam no banco para sempre. Aqui o vencimento passa a ter efeito: contatos e
metadado viram `{}` e a entrada `active` vira `expired`. Ficam a linha, o `customer_id`
(pseudônimo que liga as entregas) e as entregas — o fato da campanha, sem a pessoa.

Não há chave no namespace `retention`: o prazo desta classe JÁ é declarado, por mailing, no
`entry_ttl_seconds` — que é config do mailing, editável na tela. Criar outro prazo aqui
seriam duas casas para o mesmo fato.

Reimportar a mesma entrada (dedup) regrava contatos e volta a `active`, com novo prazo:
é o comportamento que o `ON CONFLICT` já tinha.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("plughub.mailing.retention")

INTERVAL_S = 3600

PURGE_SQL = """
UPDATE outbound.mailing_entries
   SET contacts = '{}'::jsonb,
       metadata = '{}'::jsonb,
       status   = CASE WHEN status = 'active' THEN 'expired' ELSE status END,
       updated_at = now()
 WHERE expires_at IS NOT NULL
   AND expires_at < now()
   AND (contacts <> '{}'::jsonb OR metadata <> '{}'::jsonb)
"""


def _count(status: str | None) -> int:
    last = (status or "").split()[-1:] or ["0"]
    return int(last[0]) if last[0].isdigit() else 0


async def expire_once(pool: Any) -> int:
    n = _count(await pool.execute(PURGE_SQL))
    if n:
        logger.info("retention: %d entrada(s) de mailing vencida(s) perderam contatos e metadado", n)
    return n


async def run_forever(pool: Any, interval_s: int = INTERVAL_S) -> None:
    logger.info("retention: expurgo de entradas de mailing vencidas ativo (a cada %ds)", interval_s)
    while True:
        try:
            await expire_once(pool)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("retention: rodada FALHOU — tenta de novo em %ds", interval_s)
        await asyncio.sleep(interval_s)


def supervise(task: "asyncio.Task") -> "asyncio.Task":
    """A morte da task tem de APARECER (RET-13): sem `py-tasks` neste serviço, o
    supervisor é o callback que o `probe_background_task_supervision` aceita."""
    def _done(t: "asyncio.Task") -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("retention: a task MORREU — entradas vencidas param de ser limpas",
                         exc_info=exc)
        else:
            logger.error("retention: a task TERMINOU sozinha — entradas vencidas param de ser limpas")
    task.add_done_callback(_done)
    return task
