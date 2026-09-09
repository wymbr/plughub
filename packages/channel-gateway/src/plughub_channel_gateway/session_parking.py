"""
session_parking.py — o registro DURÁVEL de uma sessão parqueada.

RET-11 (2026-09-09). Uma sessão suspensa é endereçada por um token, e até aqui esse
token — junto com o prazo dele — vivia **apenas** no Redis, que neste deploy roda
`--save ""` com `appendonly no`. Medido antes de escrever uma linha:

    212 sessões `suspended` com `closed_at` NULL, a mais antiga de 10/08
      0 tokens vivos em `*:resume_tokens`
    160 dessas já com o prazo vencido, e ninguém agiu

A cadeia: o `suspend` grava token+prazo no Redis; o scanner de prazo varre
`*:resume_tokens` **no Redis**; sem token, ele não enxerga nada. O prazo durável até
existia (`analytics.session_transitions`, 680 linhas, todas com `resume_expires_at`),
mas é ClickHouse — analytics, `ReplacingMergeTree` alimentado por Kafka — e usá-lo
como fonte de decisão operacional tornaria a analytics load-bearing para operação,
sem precedente no repositório.

── O que este registro RESOLVE, e o que ele NÃO resolve ──────────────────────────

⚠️ **Ele não torna um processo retomável depois de uma perda total do Redis.** O
`pipeline_state` do flow é igualmente volátil: se o Redis inteiro se foi, não há a
que voltar. Prometer o contrário seria o "valor plausível" que este repositório caça.

São dois casos, e só o primeiro é recuperável:

  1. **Sessão viva, token perdido** — recuperável, e é caso REAL e documentado: o
     TTL de `{tenant}:resume_tokens` é do HASH, compartilhado por todas as sessões
     do tenant, então um `collect` de 1 h escrito depois **encurta** um suspend de
     48 h e os dois somem juntos (ver `_resume_meta_key` em `adapters/webhook.py`).
     Aqui a reidratação devolve o endereço e o caminho normal volta a funcionar.

  2. **Redis inteiro perdido** — irrecuperável. O que este registro dá é
     CONHECIMENTO: saber que aquela sessão foi parqueada, com que prazo, e que ele
     venceu. Sem isso a linha fica `suspended` para sempre, sem ninguém sequer poder
     contá-la. Encerrar essa população é trabalho à parte (a "varredura"), e é
     separado de propósito: limpeza não se mistura com mecanismo.

── Por que o token pode ser VAZIO, e por que isso é uma linha e não um silêncio ──

Há **dois** mecanismos de parque, e só um carrega `resume_token`. O step `collect`
(`skill-flow-engine/src/steps/collect.ts`) parqueia com `collect_token`, e o evento
`session_suspended` correspondente chega sem `resume_token` — por isso ele marca a
sessão como suspensa e **não** produz linha em `session_transitions`. Medido: 49 das
212 encalhadas são assim, 41 delas `limite_entrega`.

Guardamos a linha mesmo assim, com `token = ''`. Ela não é endereçável — nada pode
retomá-la —, mas passa a ser CONTÁVEL, que é a diferença entre uma dívida conhecida
e um buraco mudo. Ver `RET-12`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Sequence

import asyncpg

logger = logging.getLogger("plughub.channel.parking")

SCHEMA = "parking"

DDL = """
CREATE SCHEMA IF NOT EXISTS parking;

CREATE TABLE IF NOT EXISTS parking.session_parks (
    tenant_id   text        NOT NULL,
    session_id  text        NOT NULL,
    -- O ENDEREÇO de volta. Vazio quando o mecanismo de parque não o carrega
    -- (o `collect` usa `collect_token`); a linha existe assim mesmo, para a
    -- população ser contável em vez de invisível. Ver RET-12.
    token       text        NOT NULL DEFAULT '',
    step_id     text        NOT NULL DEFAULT '',
    reason      text        NOT NULL DEFAULT '',
    expires_at  timestamptz,
    parked_at   timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    resolved_by text        NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, session_id, parked_at)
);

-- Um token endereça UM parque. O índice é parcial porque `token = ''` é legítimo
-- e repetível (N sessões parqueadas por `collect` não colidem entre si).
CREATE UNIQUE INDEX IF NOT EXISTS session_parks_token_uniq
    ON parking.session_parks (tenant_id, token) WHERE token <> '';

-- A pergunta do scanner: quem venceu e ninguém resolveu.
CREATE INDEX IF NOT EXISTS session_parks_vencidos
    ON parking.session_parks (expires_at) WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS session_parks_por_sessao
    ON parking.session_parks (tenant_id, session_id) WHERE resolved_at IS NULL;
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    """Cria schema e tabela. Idempotente — roda a cada boot."""
    async with pool.acquire() as conn:
        await conn.execute(DDL)


async def registrar_parque(
    pool:       asyncpg.Pool,
    tenant_id:  str,
    session_id: str,
    token:      str,
    step_id:    str,
    reason:     str,
    expires_at: datetime | None,
    parked_at:  datetime,
) -> bool:
    """
    Grava o parque. Devolve True se gravou, False se já existia.

    ⚠️ IDEMPOTENTE por (tenant, sessão, momento) **e** por token: o consumidor é
    at-least-once, então o mesmo `session_suspended` chega mais de uma vez em
    rebalance de partição. `ON CONFLICT DO NOTHING` nas duas chaves — sem isso, o
    reprocesso viraria erro de unicidade e mataria o consumidor.
    """
    async with pool.acquire() as conn:
        r = await conn.execute(
            """
            INSERT INTO parking.session_parks
                (tenant_id, session_id, token, step_id, reason, expires_at, parked_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT DO NOTHING
            """,
            tenant_id, session_id, token, step_id, reason, expires_at, parked_at,
        )
    return r.endswith(" 1")


async def resolver_parque(
    pool:       asyncpg.Pool,
    tenant_id:  str,
    session_id: str,
    por:        str,
    token:      str = "",
) -> int:
    """
    Marca como resolvido. Devolve quantas linhas fechou.

    Fecha por TOKEN quando ele existe (endereço exato) e por SESSÃO quando não —
    o `collect` não tem token, e um resume de sessão sem token só pode significar
    "o que quer que estivesse parqueado aqui, acabou".
    """
    async with pool.acquire() as conn:
        if token:
            r = await conn.execute(
                """
                UPDATE parking.session_parks SET resolved_at = now(), resolved_by = $3
                WHERE tenant_id = $1 AND token = $2 AND resolved_at IS NULL
                """,
                tenant_id, token, por,
            )
        else:
            r = await conn.execute(
                """
                UPDATE parking.session_parks SET resolved_at = now(), resolved_by = $3
                WHERE tenant_id = $1 AND session_id = $2 AND resolved_at IS NULL
                """,
                tenant_id, session_id, por,
            )
    try:
        return int(r.split()[-1])
    except (IndexError, ValueError):
        return 0


async def parques_vencidos(
    pool:  asyncpg.Pool,
    limit: int = 200,
) -> Sequence[asyncpg.Record]:
    """
    Os parques ENDEREÇÁVEIS cujo prazo passou e ninguém resolveu.

    ⚠️ `token <> ''` é filtro, não descuido: parque sem endereço não pode ser
    retomado nem reidratado, e trazê-lo aqui faria o scanner tentar em laço algo
    que não tem caminho. Ele é contado por `contar_sem_endereco`, que é outra
    pergunta e tem outra resposta.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT tenant_id, session_id, token, step_id, reason, expires_at
            FROM parking.session_parks
            WHERE resolved_at IS NULL AND token <> '' AND expires_at < now()
            ORDER BY expires_at
            LIMIT $1
            """,
            limit,
        )


async def contar_sem_endereco(pool: asyncpg.Pool) -> int:
    """Quantos parques vivos NÃO têm endereço de volta (a população da RET-12)."""
    async with pool.acquire() as conn:
        v = await conn.fetchval(
            "SELECT count(*) FROM parking.session_parks "
            "WHERE resolved_at IS NULL AND token = ''"
        )
    return int(v or 0)


def _quando(payload: dict[str, Any], *chaves: str) -> datetime | None:
    """Lê o primeiro timestamp ISO presente. Ausência é `None`, nunca 'agora'."""
    for k in chaves:
        v = payload.get(k)
        if not v:
            continue
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            logger.warning("session_parking: timestamp ilegivel em '%s': %r", k, v)
    return None
