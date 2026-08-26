"""
context_persister.py
ContextStore Persister (F5) — snapshota o ContextStore no fechamento da sessão.

Responsabilidade única:
  conversations.session_closed → pede ao mcp-server o ctx MASCARADO da sessão
  (e o do PROCESSO, quando há raiz) → grava em session_context_snapshot
  (PostgreSQL), tornando-o durável além do TTL de 24h da chave no Redis.

Por quê (ADR `adr-historico-unificado-duas-visoes.md` §3 F5): irmão do
`PipelineStatePersister`, mesma justificativa — *"a trajetória real não vai ao
stream e o Redis tem TTL 24h"*. O ContextStore guarda o que a plataforma APRENDEU
durante o contato (`caller.*`, `session.*`, confiança, origem, quando). Sem este
snapshot, não há como responder depois *"o que a plataforma sabia quando decidiu
escalar?"*.

── As quatro decisões do ADR, e a propriedade que as amarra ──────────────────

  · **Mascarado, ponto.** O valor cru nunca sai do Redis vivo. Quem mascara é o
    mcp-server (ver `_fetch_masked`); aqui não há regra de masking nenhuma, de
    propósito — uma segunda implementação de regra de segurança divergiria em
    silêncio, e nos dois sentidos (de menos vaza; de mais é invisível).
  · **Estado FINAL, não trajetória.** Perde-se sobrescrita DENTRO do contato.
    Perda declarada, não descoberta.
  · **Foto inteira, nunca delta.** Leitor que reconstrói estado a partir de deltas
    falha em silêncio quando um delta se perde.
  · **Contexto de PROCESSO a cada close de sessão-membro.** N fotos por processo,
    uma por contato encerrado.

A propriedade emergente que torna "estado final" e "foto por close" coerentes:
*estado final* perde a sobrescrita dentro do contato, mas *uma foto por close*
**recupera a sobrescrita ENTRE contatos**. A granularidade de trajetória passa a
ser exatamente o CONTATO — a unidade da hierarquia. O valor de leitura está no
**diff entre fotos consecutivas**: literalmente *"o que este contato acrescentou
ao processo"*.

⚠️ **Grau OPERATOR, para sempre.** Masking é (tag × papel) e um snapshot não tem
papel. Este registro **não serve a auditoria que precise do valor real** — essa
continua sendo o `TokenVault` de mensagens.

⚠️ **`hidden_count` é CONTADO, não omitido.** A entrada oculta fica na linha com
`value: null`; o contador viaja à parte para a UI poder dizer *"3 entradas
ocultas"* em vez de mostrar um snapshot que parece incompleto sem dizer por quê.

Tabela PostgreSQL:
  session_context_snapshot (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    scope         TEXT NOT NULL,      -- 'session' | 'journey'
    scope_ref     TEXT NOT NULL,      -- session_id, ou a raiz canônica do processo
    entries       JSONB NOT NULL DEFAULT '{}',
    entry_count   INT  NOT NULL DEFAULT 0,
    hidden_count  INT  NOT NULL DEFAULT 0,
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, session_id, scope)
  );
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS session_context_snapshot (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    scope         TEXT NOT NULL,
    scope_ref     TEXT NOT NULL,
    entries       JSONB NOT NULL DEFAULT '{}',
    entry_count   INT  NOT NULL DEFAULT 0,
    hidden_count  INT  NOT NULL DEFAULT 0,
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, session_id, scope)
);
CREATE INDEX IF NOT EXISTS idx_scs_session ON session_context_snapshot (tenant_id, session_id);
-- O índice do PROCESSO é o que serve a leitura de valor (diff entre fotos
-- consecutivas do mesmo processo, em ordem de captura).
CREATE INDEX IF NOT EXISTS idx_scs_scope_ref ON session_context_snapshot (tenant_id, scope, scope_ref, captured_at);
"""


class ContextStorePersister:
    """
    Pede o ctx MASCARADO ao mcp-server e persiste no PostgreSQL.

    Best-effort por design, como o irmão: hash ausente (TTL vencido, sessão sem
    contexto) NÃO é erro — apenas não há foto a gravar. **Mas a recusa é sempre
    logada com o motivo**: snapshot ausente e sessão-sem-contexto são estados
    diferentes, e sem log ficam indistinguíveis na investigação.
    """

    def __init__(
        self,
        pg_pool:       asyncpg.Pool,
        mcp_url:       str,
        service_token: str,
    ) -> None:
        self._pg    = pg_pool
        self._mcp   = mcp_url.rstrip("/")
        self._token = service_token

    async def ensure_schema(self) -> None:
        async with self._pg.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)

    # ── Leitura mascarada (o masking mora no mcp-server, nunca aqui) ──────────

    async def _fetch_masked(self, session_id: str, tenant_id: str) -> dict[str, Any] | None:
        """
        `POST /internal/context-snapshot` → `{session: {...}, journey?: {root, ...}}`.

        **Uma chamada, os dois escopos.** A raiz canônica do processo é resolvida
        LÁ, não aqui: ela exige o union-find sobre `journey_aliases`, que é a
        definição de *"qual journey é esta"* — reimplementá-la em Python partiria o
        contexto compartilhado no merge, e o sintoma seria uma foto de processo
        pendurada na raiz errada, sem nada ficar vermelho.

        Devolve `None` em qualquer falha, sempre com log do MOTIVO. O 503 tem
        tratamento próprio: significa que o mcp-server está SEM credencial
        configurada, o que é erro de DEPLOY e não "sessão sem contexto" — e essa
        distinção é a única coisa que impede o arco inteiro de parecer entregue
        enquanto nada é gravado.
        """
        if not self._token:
            logger.error(
                "ContextStorePersister: MCP_INTERNAL_SERVICE_TOKEN ausente neste "
                "serviço — NENHUM snapshot será gravado. Não é 'sessão sem contexto'.",
            )
            return None
        # `urllib` em executor — o MESMO mecanismo que `_fetch_config_value` já usa
        # neste serviço. Não é preferência de estilo: o container do session-replayer
        # não tem `aiohttp`, e acrescentar dependência para uma chamada por sessão
        # fechada pagaria uma imagem nova por conveniência de sintaxe.
        url  = f"{self._mcp}/internal/context-snapshot"
        body = json.dumps({
            "tenant_id":       tenant_id,
            "session_id":      session_id,
            "include_journey": True,
        }).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "content-type":   "application/json",
                "x-service-token": self._token,
            },
        )

        def _post() -> dict[str, Any]:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                return json.loads(resp.read())

        try:
            return await asyncio.get_event_loop().run_in_executor(None, _post)
        except urllib.error.HTTPError as exc:
            # 503 tem tratamento próprio: é erro de DEPLOY (mcp-server sem
            # credencial), não "sessão sem contexto". Sem essa distinção o arco
            # inteiro pareceria entregue enquanto grava zero linhas.
            if exc.code == 503:
                logger.error(
                    "ContextStorePersister: mcp-server RECUSOU (503) — ele está sem "
                    "MCP_INTERNAL_SERVICE_TOKEN. Snapshot NÃO gravado para session=%s",
                    session_id,
                )
            else:
                logger.warning(
                    "ContextStorePersister: mcp-server devolveu %s para session=%s — "
                    "snapshot não gravado", exc.code, session_id,
                )
            return None
        except Exception as exc:
            logger.warning(
                "ContextStorePersister: falha ao pedir o ctx mascarado (session=%s): %s",
                session_id, exc,
            )
            return None

    # ── Escrita ──────────────────────────────────────────────────────────────

    async def _write(
        self, tenant_id: str, session_id: str, scope: str, scope_ref: str,
        snap: dict[str, Any],
    ) -> bool:
        entries = snap.get("entries") or {}
        if not entries:
            logger.info(
                "ContextStorePersister: ctx %s vazio para sessão %s — nada a gravar "
                "(TTL vencido, ou o contato não acumulou contexto)", scope, session_id,
            )
            return False
        async with self._pg.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO session_context_snapshot
                    (tenant_id, session_id, scope, scope_ref, entries,
                     entry_count, hidden_count)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (tenant_id, session_id, scope) DO UPDATE SET
                    scope_ref    = EXCLUDED.scope_ref,
                    entries      = EXCLUDED.entries,
                    entry_count  = EXCLUDED.entry_count,
                    hidden_count = EXCLUDED.hidden_count,
                    captured_at  = NOW()
                """,
                tenant_id, session_id, scope, scope_ref,
                json.dumps(entries),
                int(snap.get("total") or 0),
                int(snap.get("hidden_count") or 0),
            )
        logger.info(
            "ContextStorePersister: gravado scope=%s session=%s ref=%s "
            "(%d entradas, %d ocultas)",
            scope, session_id, scope_ref,
            int(snap.get("total") or 0), int(snap.get("hidden_count") or 0),
        )
        return True

    async def persist(self, session_id: str, tenant_id: str) -> bool:
        """
        Snapshot do ctx da SESSÃO **e** do ctx do PROCESSO, numa chamada só.

        Idempotente por `(tenant, session, scope)`: re-fechamento atualiza a linha.
        Devolve True se ao menos uma foto foi gravada.

        ⚠️ O snapshot do PROCESSO é gravado **por sessão-membro** (a chave única
        inclui `session_id`), não por processo. É isso que produz as N fotos cuja
        DIFERENÇA responde *"o que este contato acrescentou ao processo"* — uma
        linha por processo guardaria só a última e apagaria exatamente o sinal que
        justifica gravar o escopo journey.
        """
        resp = await self._fetch_masked(session_id, tenant_id)
        if resp is None:
            return False

        wrote = False
        sess = resp.get("session")
        if isinstance(sess, dict):
            wrote = await self._write(
                tenant_id, session_id, "session", session_id, sess,
            ) or wrote

        jour = resp.get("journey")
        if isinstance(jour, dict):
            root = str(jour.get("root") or "")
            if root:
                wrote = await self._write(
                    tenant_id, session_id, "journey", root, jour,
                ) or wrote

        return wrote
