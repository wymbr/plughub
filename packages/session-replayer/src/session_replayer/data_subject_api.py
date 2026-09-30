"""
data_subject_api.py — o registro durável da sessão entra no dossiê e na eliminação do
titular (AUD-09, LGPD art. 18, II e VI).

O session-replayer é o DONO de três tabelas com dado da pessoa, no Postgres:
  - `session_stream_events` — o stream durável da conversa (conteúdo mascarado, e o
    desmascarado dentro do `payload` até a retenção tirar);
  - `session_context_snapshot` — a foto do ContextStore no fechamento (mascarada);
  - `session_pipeline_state` — a trajetória do fluxo (ids de passo) e o erro, se houve.

Até aqui ele não tinha porta: era só consumidor de Kafka, e a analytics-api — que monta
o dossiê — não tem Postgres. As três ficavam em `NOT_COVERED`. Esta é a porta mínima que
o dono precisa ter, e NADA mais: duas rotas, SÓ SERVIÇO (`X-Service-Token`), chaveadas
por SESSÃO. Quem decide se o chamador é um DPO e grava a trilha é a analytics-api.

Regras:
  - **O desmascarado nunca sai por aqui.** O export tira `original_content` do `payload`
    e não devolve a coluna: o dossiê entrega o conteúdo MASCARADO (o desmascarado é a
    AUD-01, com portão próprio).
  - **Anonimiza e mantém a linha**, como a AUD-06 nas outras lojas: tipo, momento e
    trajetória ficam; conteúdo sai. Repetir o pedido responde zero.
  - **Sem token configurado, a porta RECUSA** (503) em vez de abrir.
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("session_replayer.data_subject")

MAX_SESSIONS = 5000
DEFAULT_PORT = 3880

EXPORT_EVENTS_SQL = """
SELECT session_id, event_id, event_type, timestamp, author, visibility,
       payload - 'original_content' AS payload
  FROM session_stream_events
 WHERE tenant_id = $1 AND session_id = ANY($2::text[])
 ORDER BY session_id, timestamp
"""
EXPORT_CONTEXT_SQL = """
SELECT session_id, scope, scope_ref, entries, entry_count, hidden_count, captured_at
  FROM session_context_snapshot
 WHERE tenant_id = $1 AND session_id = ANY($2::text[])
 ORDER BY session_id, scope
"""
EXPORT_PIPELINE_SQL = """
SELECT session_id, flow_id, status, current_step_id, transitions, error_context,
       started_at, updated_at
  FROM session_pipeline_state
 WHERE tenant_id = $1 AND session_id = ANY($2::text[])
 ORDER BY session_id
"""

# O autor da fala do cliente carrega o identificador dele; o dos agentes, não.
ERASE_EVENTS_SQL = """
UPDATE session_stream_events
   SET payload = jsonb_build_object('erased', $3::text),
       original_content = NULL,
       author = CASE WHEN author->>'role' = 'customer'
                     THEN jsonb_build_object('role', 'customer', 'participant_id', $3::text)
                     ELSE author END
 WHERE tenant_id = $1 AND session_id = ANY($2::text[])
   AND payload IS DISTINCT FROM jsonb_build_object('erased', $3::text)
"""
ERASE_CONTEXT_SQL = """
UPDATE session_context_snapshot SET entries = '{}'::jsonb
 WHERE tenant_id = $1 AND session_id = ANY($2::text[]) AND entries <> '{}'::jsonb
"""
ERASE_PIPELINE_SQL = """
UPDATE session_pipeline_state SET error_context = NULL
 WHERE tenant_id = $1 AND session_id = ANY($2::text[]) AND error_context IS NOT NULL
"""


class SubjectRequest(BaseModel):
    tenant_id: str
    session_ids: list[str] = []


class SubjectEraseRequest(SubjectRequest):
    marker: str


def _count(status: str | None) -> int:
    last = (status or "").split()[-1:] or ["0"]
    return int(last[0]) if last[0].isdigit() else 0


def _plain(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, str) and v[:1] in "{[":
        import json
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _rows(records: list) -> list[dict]:
    return [{k: _plain(v) for k, v in dict(r).items()} for r in records]


def _sids(body: SubjectRequest) -> list[str]:
    return [s for s in body.session_ids if s][:MAX_SESSIONS]


async def export_sessions(pool: Any, tenant_id: str, session_ids: list[str]) -> dict:
    if not session_ids:
        return {"stream_events": [], "context_snapshots": [], "pipeline_states": []}
    async with pool.acquire() as conn:
        ev = await conn.fetch(EXPORT_EVENTS_SQL, tenant_id, session_ids)
        ctx = await conn.fetch(EXPORT_CONTEXT_SQL, tenant_id, session_ids)
        pip = await conn.fetch(EXPORT_PIPELINE_SQL, tenant_id, session_ids)
    return {"stream_events": _rows(ev), "context_snapshots": _rows(ctx),
            "pipeline_states": _rows(pip)}


async def erase_sessions(pool: Any, tenant_id: str, session_ids: list[str], marker: str) -> dict:
    if not session_ids:
        return {"stream_events": 0, "context_snapshots": 0, "pipeline_states": 0}
    async with pool.acquire() as conn:
        async with conn.transaction():
            ev = await conn.execute(ERASE_EVENTS_SQL, tenant_id, session_ids, marker)
            ctx = await conn.execute(ERASE_CONTEXT_SQL, tenant_id, session_ids)
            pip = await conn.execute(ERASE_PIPELINE_SQL, tenant_id, session_ids)
    return {"stream_events": _count(ev), "context_snapshots": _count(ctx),
            "pipeline_states": _count(pip)}


def build_app(pool_getter: Any, service_token: str) -> FastAPI:
    app = FastAPI(title="session-replayer data-subject", docs_url=None, redoc_url=None,
                  openapi_url="/openapi.json")

    def _require_service(x_service_token: str | None = Header(default=None)) -> None:
        if not service_token:
            raise HTTPException(503, "SESSION_REPLAYER_SERVICE_TOKEN nao configurado — porta fechada")
        if not x_service_token or not hmac.compare_digest(x_service_token, service_token):
            raise HTTPException(401, "X-Service-Token ausente ou invalido")

    def _pool() -> Any:
        pool = pool_getter()
        if pool is None:
            raise HTTPException(503, "postgres ainda nao conectado")
        return pool

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/data-subject/sessions/export", dependencies=[Depends(_require_service)])
    async def export(body: SubjectRequest, request: Request) -> dict:
        return await export_sessions(_pool(), body.tenant_id, _sids(body))

    @app.post("/v1/data-subject/sessions/erase", dependencies=[Depends(_require_service)])
    async def erase(body: SubjectEraseRequest, request: Request) -> dict:
        if not body.marker.startswith("erased:"):
            raise HTTPException(422, "marker deve comecar com 'erased:'")
        out = await erase_sessions(_pool(), body.tenant_id, _sids(body), body.marker)
        logger.info("data-subject ERASE tenant=%s sessoes=%d marker=%s %s",
                    body.tenant_id, len(_sids(body)), body.marker, out)
        return out

    return app


async def serve(pool_getter: Any) -> None:
    """Sobe a porta no MESMO laço do consumidor (roda no `gather` do `start`)."""
    import uvicorn

    token = os.getenv("SESSION_REPLAYER_SERVICE_TOKEN", "")
    port = int(os.getenv("DATA_SUBJECT_PORT", str(DEFAULT_PORT)))
    if not token:
        logger.error("data-subject: SESSION_REPLAYER_SERVICE_TOKEN vazio — a porta %d RECUSA "
                     "tudo (503), e o dossiê e a eliminação do titular saem sem o registro "
                     "durável da sessão", port)
    config = uvicorn.Config(build_app(pool_getter, token), host="0.0.0.0", port=port,
                            log_level="warning", lifespan="off")
    await uvicorn.Server(config).serve()
