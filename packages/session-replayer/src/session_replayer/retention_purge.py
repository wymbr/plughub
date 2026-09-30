"""
retention_purge.py
Expurgo por CLASSE de dado no stream durável (`session_stream_events`) — AUD-07 e AUD-08.

Por que existe: o stream durável guardava tudo para sempre. A política decidida pelo dono
(2026-09-30) é POR CLASSE de dado, configurável por tenant no namespace `retention` do
config-api. Duas classes moram nesta tabela:

  · `original_content_days` (default 90, AUD-07) — o texto DESMASCARADO. Mora DENTRO de
    `payload`, não na coluna homônima: medido em 2026-09-30, 43 linhas com
    `payload.original_content` e ZERO com a coluna preenchida. Um expurgo que limpasse só a
    coluna passaria verde sem apagar nada. A linha fica, com o conteúdo mascarado;
  · `conversation_content_days` (default 365, AUD-08) — o CONTEÚDO da conversa, mascarado
    ou não. O `payload` INTEIRO vira `{"expired": true}`: o conteúdo não mora numa chave só
    (`message` tem o texto, `session_resumed` a resposta digitada num formulário,
    `interaction_request` o prompt e as opções), e uma lista de chaves "que têm dado
    pessoal" envelheceria calada no primeiro tipo novo de evento. Tipo, horário e autor da
    linha ficam — a linha do tempo da sessão continua existindo, sem o que foi dito.

Não há loja de transcrição a parte (medido): a transcrição É a conversa.

Degradação nunca silenciosa, e na direção SEGURA para o dado:
  · config-api fora, ou valor inválido → aquele tenant NÃO é expurgado naquela classe nesta
    rodada, com WARNING/ERROR nomeando o motivo. Expurgar com um prazo adivinhado poderia
    apagar o que o tenant configurou para guardar por mais tempo, e apagar não se desfaz;
  · chave ausente com config-api de pé → vale o default declarado, com WARNING (o seed a
    cria; ausência é deploy sem seed, e dizê-lo é o que a torna visível).
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
MIN_DAYS, MAX_DAYS = 1, 3650
INTERVAL_S = 3600

EXPIRED_PAYLOAD = '{"expired": true}'


@dataclass(frozen=True)
class DataClass:
    key: str
    default_days: int
    tenants_sql: str
    purge_sql: str      # $1 = tenant, $2 = corte


CLASSES: tuple[DataClass, ...] = (
    DataClass(
        key="original_content_days", default_days=90,
        tenants_sql="""
SELECT DISTINCT tenant_id FROM session_stream_events
 WHERE payload ? 'original_content' OR original_content IS NOT NULL
""",
        purge_sql="""
UPDATE session_stream_events
   SET payload = payload - 'original_content',
       original_content = NULL
 WHERE tenant_id = $1
   AND timestamp < $2
   AND (payload ? 'original_content' OR original_content IS NOT NULL)
""",
    ),
    DataClass(
        key="conversation_content_days", default_days=365,
        tenants_sql=f"""
SELECT DISTINCT tenant_id FROM session_stream_events
 WHERE payload <> '{EXPIRED_PAYLOAD}'::jsonb
""",
        purge_sql=f"""
UPDATE session_stream_events
   SET payload = '{EXPIRED_PAYLOAD}'::jsonb,
       original_content = NULL
 WHERE tenant_id = $1
   AND timestamp < $2
   AND payload <> '{EXPIRED_PAYLOAD}'::jsonb
""",
    ),
)

# Compatibilidade de leitura para quem importava as constantes da primeira classe.
KEY = CLASSES[0].key
DEFAULT_DAYS = CLASSES[0].default_days
_TENANTS_SQL = CLASSES[0].tenants_sql
_PURGE_SQL = CLASSES[0].purge_sql


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


async def fetch_retention(config_api_url: str, tenant_id: str,
                          key: str = KEY, default: int = DEFAULT_DAYS) -> Retention:
    """Lê `retention.<key>` do tenant (override → global)."""
    url = (f"{config_api_url.rstrip('/')}/config/{NAMESPACE}"
           f"?tenant_id={urllib.parse.quote(tenant_id)}")

    def _get():
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            return json.loads(resp.read())

    try:
        body = await asyncio.get_running_loop().run_in_executor(None, _get)
    except Exception as exc:  # noqa: BLE001
        logger.warning("retention: config-api indisponível para tenant=%s (%s) — "
                       "%s NÃO expurgado nesta rodada", tenant_id, exc, key)
        return Retention(None, f"skip: config-api indisponível ({exc})")

    entries = body.get("entries") if isinstance(body, dict) and "entries" in body else body
    entry = entries.get(key) if isinstance(entries, dict) else None
    if entry is None:
        logger.warning("retention: %s.%s ausente para tenant=%s — usando o default de %d dias "
                       "(o seed do config-api cria a chave; ausência é deploy sem seed)",
                       NAMESPACE, key, tenant_id, default)
        return Retention(default, "default")
    raw = entry.get("value") if isinstance(entry, dict) and "value" in entry else entry
    days = _parse_days(raw)
    if days is None:
        logger.error("retention: %s.%s=%r inválido para tenant=%s (inteiro de %d a %d) — "
                     "NÃO expurgado nesta rodada",
                     NAMESPACE, key, raw, tenant_id, MIN_DAYS, MAX_DAYS)
        return Retention(None, f"skip: valor inválido {raw!r}")
    return Retention(days, "config")


def _count(status: str | None) -> int:
    last = (status or "").split()[-1:] or ["0"]
    return int(last[0]) if last[0].isdigit() else 0


async def purge_once(pool: asyncpg.Pool, config_api_url: str,
                     now: datetime | None = None) -> dict[str, dict[str, int]]:
    """Uma rodada: para cada CLASSE, cada tenant com dado dela, expurga o que passou do
    prazo. Devolve {classe: {tenant: linhas}} (só dos tenants efetivamente expurgados)."""
    now = now or datetime.now(timezone.utc)
    result: dict[str, dict[str, int]] = {}
    for cls in CLASSES:
        per: dict[str, int] = {}
        for r in await pool.fetch(cls.tenants_sql):
            tenant = r["tenant_id"]
            ret = await fetch_retention(config_api_url, tenant, cls.key, cls.default_days)
            if ret.days is None:
                continue
            cutoff = now - timedelta(days=ret.days)
            n = _count(await pool.execute(cls.purge_sql, tenant, cutoff))
            per[tenant] = n
            logger.info("retention: tenant=%s classe=%s expurgada em %d linha(s) anteriores "
                        "a %s (%d dias, fonte=%s)",
                        tenant, cls.key, n, cutoff.isoformat(), ret.days, ret.source)
        result[cls.key] = per
    return result


async def run_forever(pool: asyncpg.Pool, config_api_url: str,
                      interval_s: int = INTERVAL_S) -> None:
    """Laço do serviço. Nunca levanta: uma rodada que falha loga ERROR e a próxima tenta
    de novo — derrubar o `gather` levaria junto o Persister e o Replayer."""
    logger.info("retention: expurgo por classe ativo (%s, a cada %ds)",
                ", ".join(c.key for c in CLASSES), interval_s)
    while True:
        try:
            await purge_once(pool, config_api_url)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("retention: rodada de expurgo FALHOU — tenta de novo em %ds", interval_s)
        await asyncio.sleep(interval_s)
