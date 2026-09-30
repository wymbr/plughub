"""
retention_purge.py
Expurgo do `original_content` desmascarado do stream durável (AUD-07, fatia 1).

Por que existe: `session_stream_events` guardava o texto DESMASCARADO para sempre. A
política decidida pelo dono (2026-09-30) é POR CLASSE de dado, configurável por tenant,
e esta é a classe que começa: `retention.original_content_days` (default 90).

O que o expurgo faz — e o que NÃO faz:
  · tira `original_content` da linha (a chave dentro de `payload` e a coluna), depois
    de N dias contados do `timestamp` do EVENTO. A linha fica: o conteúdo MASCARADO é
    outra classe (1 ano) e ainda não tem expurgo;
  · onde mora o texto: dentro de `payload`, não na coluna. Medido em 2026-09-30: 43
    linhas com `payload.original_content` e ZERO com a coluna preenchida — o escritor
    (`writeStreamEntry`) põe o original dentro do payload. Um expurgo que limpasse só a
    coluna passaria verde sem apagar nada.

Degradação nunca silenciosa, e na direção SEGURA para o dado:
  · config-api fora, ou valor inválido → aquele tenant NÃO é expurgado nesta rodada, com
    WARNING/ERROR nomeando o motivo. Expurgar com um default adivinhado poderia apagar o
    que um tenant configurou para guardar por mais tempo, e apagar não se desfaz;
  · chave ausente com config-api de pé → vale o default declarado, com WARNING (o seed
    a cria; ausência é deploy sem seed, e dizê-lo é o que a torna visível).
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg

logger = logging.getLogger(__name__)

NAMESPACE = "retention"
KEY = "original_content_days"
DEFAULT_DAYS = 90
MIN_DAYS, MAX_DAYS = 1, 3650
INTERVAL_S = 3600

_TENANTS_SQL = """
SELECT DISTINCT tenant_id FROM session_stream_events
 WHERE payload ? 'original_content' OR original_content IS NOT NULL
"""

_PURGE_SQL = """
UPDATE session_stream_events
   SET payload = payload - 'original_content',
       original_content = NULL
 WHERE tenant_id = $1
   AND timestamp < $2
   AND (payload ? 'original_content' OR original_content IS NOT NULL)
"""


@dataclass(frozen=True)
class Retention:
    days: int | None          # None = não expurgar nesta rodada
    source: str               # config | default | "skip: <motivo>"


def _parse_days(raw) -> int | None:
    if isinstance(raw, bool):
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(raw, float) and raw != n:
        return None
    return n if MIN_DAYS <= n <= MAX_DAYS else None


async def fetch_retention(config_api_url: str, tenant_id: str) -> Retention:
    """Lê `retention.original_content_days` do tenant (override → global)."""
    url = (f"{config_api_url.rstrip('/')}/config/{NAMESPACE}"
           f"?tenant_id={urllib.parse.quote(tenant_id)}")

    def _get():
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            return json.loads(resp.read())

    try:
        body = await asyncio.get_running_loop().run_in_executor(None, _get)
    except Exception as exc:  # noqa: BLE001
        logger.warning("retention: config-api indisponível para tenant=%s (%s) — "
                       "original_content NÃO expurgado nesta rodada", tenant_id, exc)
        return Retention(None, f"skip: config-api indisponível ({exc})")

    entries = body.get("entries") if isinstance(body, dict) and "entries" in body else body
    entry = entries.get(KEY) if isinstance(entries, dict) else None
    if entry is None:
        logger.warning("retention: %s.%s ausente para tenant=%s — usando o default de %d dias "
                       "(o seed do config-api cria a chave; ausência é deploy sem seed)",
                       NAMESPACE, KEY, tenant_id, DEFAULT_DAYS)
        return Retention(DEFAULT_DAYS, "default")
    raw = entry.get("value") if isinstance(entry, dict) and "value" in entry else entry
    days = _parse_days(raw)
    if days is None:
        logger.error("retention: %s.%s=%r inválido para tenant=%s (inteiro de %d a %d) — "
                     "original_content NÃO expurgado nesta rodada",
                     NAMESPACE, KEY, raw, tenant_id, MIN_DAYS, MAX_DAYS)
        return Retention(None, f"skip: valor inválido {raw!r}")
    return Retention(days, "config")


async def purge_once(pool: asyncpg.Pool, config_api_url: str,
                     now: datetime | None = None) -> dict[str, int]:
    """Uma rodada: por tenant com original guardado, expurga o que passou do prazo.
    Devolve {tenant: linhas expurgadas} (só dos tenants efetivamente expurgados)."""
    now = now or datetime.now(timezone.utc)
    rows = await pool.fetch(_TENANTS_SQL)
    result: dict[str, int] = {}
    for r in rows:
        tenant = r["tenant_id"]
        ret = await fetch_retention(config_api_url, tenant)
        if ret.days is None:
            continue
        cutoff = now - timedelta(days=ret.days)
        status = await pool.execute(_PURGE_SQL, tenant, cutoff)
        n = int(status.split()[-1]) if status and status.split()[-1].isdigit() else 0
        result[tenant] = n
        logger.info("retention: tenant=%s original_content expurgado de %d linha(s) "
                    "anteriores a %s (%d dias, fonte=%s)",
                    tenant, n, cutoff.isoformat(), ret.days, ret.source)
    return result


async def run_forever(pool: asyncpg.Pool, config_api_url: str,
                      interval_s: int = INTERVAL_S) -> None:
    """Laço do serviço. Nunca levanta: uma rodada que falha loga ERROR e a próxima tenta
    de novo — derrubar o `gather` levaria junto o Persister e o Replayer."""
    logger.info("retention: expurgo de original_content ativo (a cada %ds)", interval_s)
    while True:
        try:
            await purge_once(pool, config_api_url)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("retention: rodada de expurgo FALHOU — tenta de novo em %ds", interval_s)
        await asyncio.sleep(interval_s)
