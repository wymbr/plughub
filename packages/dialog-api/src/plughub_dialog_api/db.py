"""
db.py
DDL and raw asyncpg operations for the dialog-api.

Single table in schema 'dialog':
  dialog.forms — versioned dialog-form JSON (draft/published), PK (tenant_id, form_id, version)

Versioning mirrors EvaluationForm + skill deploy lifecycle:
  - create → version = max(version)+1 (or 1), status='draft'
  - put    → if latest row is draft, replace its json; if published, create a new draft version
  - publish→ set a version's status='published' (the highest published version is "current")
  - get published → highest published version

Arquivamento (ADR adr-dialog-form-deletion, 2026-08-28). `deleted_at` marca o form como
ARQUIVADO — carimbado em TODAS as versões do `form_id` (o delete é do form, nunca da versão:
todo consumidor vincula por `form_id`). Duas regras que parecem detalhe e são a decisão:

  · o CATÁLOGO fecha (`db_list_forms` filtra), mas a RESOLUÇÃO por id NÃO (`db_get_form` serve
    arquivado, com `deleted_at` no corpo). São eixos distintos: esconder da lista responde
    "não use mais"; devolver 404 na resolução derrubaria contato em andamento, a composição de
    nota no fim do diálogo e a leitura de história já encerrada — e faria o `seed_dialog`
    RESSUSCITAR o form no boot seguinte (ele trata 404 como ausente);
  · form que nunca teve versão publicada é PURGADO de verdade — os seis leitores resolvem
    `status=published`, logo ele não pode estar vinculado a nada. É a única parte decidível de
    "recusar quando há referência viva".

Escrita sobre arquivado é recusada (`FormArchivedError` → 409 no router): restaurar é ato
próprio (`db_undelete_form`), nunca efeito colateral de salvar — senão conteúdo novo herdaria
um id ao qual um slot antigo ainda aponta.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger("plughub.dialog.db")

_DDL_SCHEMA = "CREATE SCHEMA IF NOT EXISTS dialog"

_DDL_FORMS = """
CREATE TABLE IF NOT EXISTS dialog.forms (
    tenant_id   TEXT        NOT NULL,
    form_id     TEXT        NOT NULL,
    version     INTEGER     NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft','published')),
    name        TEXT        NOT NULL DEFAULT '',
    tags        JSONB       NOT NULL DEFAULT '[]',
    json        JSONB       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, form_id, version)
)
"""

_DDL_FORMS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_dialog_forms_lookup "
    "ON dialog.forms (tenant_id, form_id, status, version DESC)"
)

# A coluna entra por ALTER porque `CREATE TABLE IF NOT EXISTS` não altera tabela que já
# existe — sem isto, base instalada subiria sem `deleted_at` e o filtro do catálogo falharia
# em runtime, não no boot.
_DDL_FORMS_DELETED_AT = (
    "ALTER TABLE dialog.forms ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ"
)


class FormArchivedError(Exception):
    """Escrita recusada porque o form está arquivado. O router mapeia para 409."""

    def __init__(self, form_id: str, deleted_at: Any) -> None:
        super().__init__(f"dialog form archived: {form_id}")
        self.form_id = form_id
        self.deleted_at = deleted_at


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_DDL_SCHEMA)
            await conn.execute(_DDL_FORMS)
            await conn.execute(_DDL_FORMS_IDX)
            await conn.execute(_DDL_FORMS_DELETED_AT)
    logger.info("dialog schema ensured")


def _row_to_form(row: asyncpg.Record) -> dict[str, Any]:
    """Return the stored DialogForm JSON, with authoritative row columns merged in."""
    doc = json.loads(row["json"])
    doc["tenant_id"] = row["tenant_id"]
    doc["form_id"]   = row["form_id"]
    doc["version"]   = row["version"]
    doc["status"]    = row["status"]
    doc["created_at"] = row["created_at"].isoformat()
    doc["updated_at"] = row["updated_at"].isoformat()
    # Arquivado ainda é SERVIDO (ADR D1) — quem resolve por id já tem vínculo. O campo vai
    # junto para que o chamador possa LOGAR que serviu um arquivado, em vez de descobrir
    # depois que a origem do conteúdo era um form fora do catálogo.
    doc["deleted_at"] = row["deleted_at"].isoformat() if row["deleted_at"] else None
    return doc


def _row_to_meta(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "tenant_id":  row["tenant_id"],
        "form_id":    row["form_id"],
        "version":    row["version"],
        "status":     row["status"],
        "name":       row["name"],
        "tags":       json.loads(row["tags"]),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "deleted_at": row["deleted_at"].isoformat() if row["deleted_at"] else None,
        # É o que decide se o DELETE arquiva ou PURGA — e a tela precisa dele ANTES de
        # perguntar, para avisar da irreversibilidade. NÃO é derivável do `status` desta
        # linha: a última versão pode ser rascunho e existir uma publicada mais antiga.
        "ever_published": bool(row["ever_published"]),
    }


async def db_list_forms(
    pool: asyncpg.Pool,
    tenant_id: str,
    *,
    include_deleted: bool = False,
) -> list[dict]:
    """List the latest version (metadata only) per form_id for a tenant.

    O catálogo esconde arquivados por DEFAULT — é ele que responde "o que posso escolher/
    vincular", e é por ele que o combo de deploy fica correto sem código próprio.
    `include_deleted=True` é a lixeira do editor.
    """
    rows = await pool.fetch(
        f"""
        SELECT DISTINCT ON (form_id)
               tenant_id, form_id, version, status, name, tags,
               created_at, updated_at, deleted_at,
               EXISTS (SELECT 1 FROM dialog.forms p
                       WHERE p.tenant_id = f.tenant_id AND p.form_id = f.form_id
                         AND p.status = 'published') AS ever_published
        FROM dialog.forms f
        WHERE tenant_id = $1
          {"" if include_deleted else "AND deleted_at IS NULL"}
        ORDER BY form_id, version DESC
        """,
        tenant_id,
    )
    return [_row_to_meta(r) for r in rows]


async def db_get_form(
    pool: asyncpg.Pool,
    tenant_id: str,
    form_id: str,
    *,
    status: str | None = None,
    version: int | None = None,
) -> dict | None:
    """
    Resolve a single dialog form.
      version given   → that exact (form_id, version)
      status='published' → highest published version (the "current")
      else            → highest version regardless of status

    NÃO filtra `deleted_at` — é DELIBERADO (ADR D1). Resolver por id explícito só acontece a
    partir de um vínculo que já existe (skill em execução, `config_json` do slot,
    `core.workflow.dialog_form_id` no ctx, segmento histórico); ninguém DESCOBRE form por id. Pôr o
    filtro aqui não impediria uso novo — impediria a continuação e a leitura do passado.
    """
    if version is not None:
        row = await pool.fetchrow(
            "SELECT * FROM dialog.forms WHERE tenant_id=$1 AND form_id=$2 AND version=$3",
            tenant_id, form_id, version,
        )
    elif status == "published":
        row = await pool.fetchrow(
            """
            SELECT * FROM dialog.forms
            WHERE tenant_id=$1 AND form_id=$2 AND status='published'
            ORDER BY version DESC LIMIT 1
            """,
            tenant_id, form_id,
        )
    else:
        row = await pool.fetchrow(
            """
            SELECT * FROM dialog.forms
            WHERE tenant_id=$1 AND form_id=$2
            ORDER BY version DESC LIMIT 1
            """,
            tenant_id, form_id,
        )
    return _row_to_form(row) if row else None


# Estado do form em UMA leitura — versões, se já publicou alguma vez, e se está arquivado.
# Fonte ÚNICA do veredicto "está arquivado?": ter duas respostas para essa pergunta é como se
# paga por escrever num form que o catálogo não mostra.
_SQL_FORM_STATE = """
SELECT count(*)::int                                    AS versions,
       count(*) FILTER (WHERE status='published')::int  AS published_versions,
       coalesce(max(version), 0)::int                   AS max_version,
       max(deleted_at)                                  AS deleted_at
FROM dialog.forms
WHERE tenant_id=$1 AND form_id=$2
"""


async def _form_state(conn: asyncpg.Connection, tenant_id: str, form_id: str) -> dict[str, Any]:
    """Agregado sobre zero linhas devolve UMA linha com contagens 0 — `versions == 0` é a
    resposta para "o form existe?", sem consulta extra."""
    row = await conn.fetchrow(_SQL_FORM_STATE, tenant_id, form_id)
    if row is None:                              # defensivo: agregado sempre devolve linha
        return {"versions": 0, "published_versions": 0, "max_version": 0, "deleted_at": None}
    return {
        "versions":           row["versions"],
        "published_versions": row["published_versions"],
        "max_version":        row["max_version"],
        "deleted_at":         row["deleted_at"],
    }


async def _guard_writable(conn: asyncpg.Connection, tenant_id: str, form_id: str) -> dict[str, Any]:
    """Recusa escrita sobre form arquivado, DENTRO da transação (não antes dela: checar fora
    deixaria a janela em que arquivar e publicar se cruzam e o form termina publicado e fora
    do catálogo ao mesmo tempo). Devolve o estado para quem já precisa dele."""
    state = await _form_state(conn, tenant_id, form_id)
    if state["deleted_at"] is not None:
        raise FormArchivedError(form_id, state["deleted_at"])
    return state


async def db_create_form(pool: asyncpg.Pool, tenant_id: str, doc: dict) -> dict:
    """Create a new draft version of a form (version = max+1). Recusa form arquivado."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await _guard_writable(conn, tenant_id, doc["form_id"])
            version = state["max_version"] + 1
            stored = dict(doc)
            stored.update({"tenant_id": tenant_id, "version": version, "status": "draft"})
            row = await conn.fetchrow(
                """
                INSERT INTO dialog.forms (tenant_id, form_id, version, status, name, tags, json)
                VALUES ($1,$2,$3,'draft',$4,$5::jsonb,$6::jsonb)
                RETURNING *
                """,
                tenant_id, doc["form_id"], version,
                doc.get("name", ""), json.dumps(doc.get("tags", [])),
                json.dumps(stored),
            )
    return _row_to_form(row)


async def db_put_form(pool: asyncpg.Pool, tenant_id: str, form_id: str, doc: dict) -> dict:
    """
    Edit a form. If the latest version is a draft, replace its json in place;
    if the latest is published (or none exists), create a new draft version.
    Recusa form arquivado.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _guard_writable(conn, tenant_id, form_id)
            latest = await conn.fetchrow(
                """
                SELECT version, status FROM dialog.forms
                WHERE tenant_id=$1 AND form_id=$2
                ORDER BY version DESC LIMIT 1
                """,
                tenant_id, form_id,
            )
            if latest is not None and latest["status"] == "draft":
                version = latest["version"]
                stored = dict(doc)
                stored.update({"tenant_id": tenant_id, "form_id": form_id,
                               "version": version, "status": "draft"})
                row = await conn.fetchrow(
                    """
                    UPDATE dialog.forms
                    SET name=$4, tags=$5::jsonb, json=$6::jsonb, updated_at=now()
                    WHERE tenant_id=$1 AND form_id=$2 AND version=$3
                    RETURNING *
                    """,
                    tenant_id, form_id, version,
                    doc.get("name", ""), json.dumps(doc.get("tags", [])),
                    json.dumps(stored),
                )
            else:
                version = (latest["version"] + 1) if latest is not None else 1
                stored = dict(doc)
                stored.update({"tenant_id": tenant_id, "form_id": form_id,
                               "version": version, "status": "draft"})
                row = await conn.fetchrow(
                    """
                    INSERT INTO dialog.forms (tenant_id, form_id, version, status, name, tags, json)
                    VALUES ($1,$2,$3,'draft',$4,$5::jsonb,$6::jsonb)
                    RETURNING *
                    """,
                    tenant_id, form_id, version,
                    doc.get("name", ""), json.dumps(doc.get("tags", [])),
                    json.dumps(stored),
                )
    return _row_to_form(row)


async def db_publish_form(
    pool: asyncpg.Pool,
    tenant_id: str,
    form_id: str,
    version: int | None = None,
) -> dict | None:
    """Publish a version (default = the latest draft). Idempotent snapshot.
    Recusa form arquivado — publicar é o ato mais claro de "quero que isto rode"."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _guard_writable(conn, tenant_id, form_id)
            if version is None:
                target = await conn.fetchval(
                    """
                    SELECT version FROM dialog.forms
                    WHERE tenant_id=$1 AND form_id=$2 AND status='draft'
                    ORDER BY version DESC LIMIT 1
                    """,
                    tenant_id, form_id,
                )
                if target is None:
                    return None
                version = target
            row = await conn.fetchrow(
                """
                UPDATE dialog.forms
                SET status='published', updated_at=now()
                WHERE tenant_id=$1 AND form_id=$2 AND version=$3
                RETURNING *
                """,
                tenant_id, form_id, version,
            )
    return _row_to_form(row) if row else None


async def db_delete_form(pool: asyncpg.Pool, tenant_id: str, form_id: str) -> dict | None:
    """
    Arquiva o form (todas as versões) — ou o PURGA, se ele nunca teve versão publicada.

    Os dois regimes vivem no mesmo verbo porque a diferença é demonstrável, não estimada:
    todos os leitores de runtime resolvem `status='published'`, logo um form sem nenhuma
    versão publicada **não pode estar vinculado a nada** e apagá-lo não derruba ninguém.
    Quem já publicou alguma vez só pode ser ARQUIVADO — pode haver vínculo, e a resposta
    diz qual dos dois aconteceu (`purged`) para a tela poder avisar ANTES no caso
    irreversível.

    Devolve None quando o form não existe (404 no router). Idempotente: arquivar de novo
    preserva o `deleted_at` ORIGINAL (o `WHERE deleted_at IS NULL` garante), porque a data
    do arquivamento é fato histórico e re-carimbar apagaria quando ele saiu de circulação.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await _form_state(conn, tenant_id, form_id)
            if state["versions"] == 0:
                return None

            if state["published_versions"] == 0:
                rows = await conn.fetch(
                    "DELETE FROM dialog.forms WHERE tenant_id=$1 AND form_id=$2 "
                    "RETURNING version",
                    tenant_id, form_id,
                )
                logger.info("dialog form PURGED tenant=%s form=%s versions=%d "
                            "(nunca publicado)", tenant_id, form_id, len(rows))
                return {"form_id": form_id, "purged": True, "deleted_at": None,
                        "versions": len(rows), "already_deleted": False}

            if state["deleted_at"] is not None:
                return {"form_id": form_id, "purged": False,
                        "deleted_at": state["deleted_at"].isoformat(),
                        "versions": state["versions"], "already_deleted": True}

            # `updated_at` NÃO é tocado: ele fala do CONTEÚDO, e arquivar não muda conteúdo.
            # A data do arquivamento tem coluna própria.
            rows = await conn.fetch(
                """
                UPDATE dialog.forms SET deleted_at = now()
                WHERE tenant_id=$1 AND form_id=$2 AND deleted_at IS NULL
                RETURNING deleted_at
                """,
                tenant_id, form_id,
            )
            stamped = rows[0]["deleted_at"] if rows else None
            logger.info("dialog form ARCHIVED tenant=%s form=%s versions=%d",
                        tenant_id, form_id, len(rows))
            return {"form_id": form_id, "purged": False,
                    "deleted_at": stamped.isoformat() if stamped else None,
                    "versions": len(rows), "already_deleted": False}


async def db_undelete_form(pool: asyncpg.Pool, tenant_id: str, form_id: str) -> dict | None:
    """
    Restaura um form arquivado (limpa `deleted_at` de todas as versões).

    É rota própria, e não efeito colateral de salvar: restaurar por escrita faria conteúdo
    novo herdar um id ao qual um slot antigo ainda aponta — o slot passaria a executar outra
    coisa sem ninguém ter tocado no deploy. Devolve None se o form não existe. Idempotente:
    restaurar form vivo é no-op com `was_deleted=False`.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            state = await _form_state(conn, tenant_id, form_id)
            if state["versions"] == 0:
                return None
            rows = await conn.fetch(
                """
                UPDATE dialog.forms SET deleted_at = NULL
                WHERE tenant_id=$1 AND form_id=$2 AND deleted_at IS NOT NULL
                RETURNING version
                """,
                tenant_id, form_id,
            )
            if rows:
                logger.info("dialog form RESTORED tenant=%s form=%s versions=%d",
                            tenant_id, form_id, len(rows))
            return {"form_id": form_id, "restored_versions": len(rows),
                    "was_deleted": state["deleted_at"] is not None}
