"""
router.py
FastAPI router for evaluation-api.

Endpoints:
  Forms:
    GET    /v1/evaluation/forms                   list forms
    POST   /v1/evaluation/forms                   create form
    GET    /v1/evaluation/forms/{id}              get form
    PUT    /v1/evaluation/forms/{id}              update form
    DELETE /v1/evaluation/forms/{id}              delete form (→ archived)

  Campaigns:
    GET    /v1/evaluation/campaigns               list campaigns
    POST   /v1/evaluation/campaigns               create campaign
    GET    /v1/evaluation/campaigns/{id}          get campaign
    PUT    /v1/evaluation/campaigns/{id}          update campaign
    POST   /v1/evaluation/campaigns/{id}/pause    pause
    POST   /v1/evaluation/campaigns/{id}/resume   resume
    POST   /v1/evaluation/campaigns/{id}/dispatch manual dispatch ("Rodar agora")
    POST   /v1/evaluation/campaigns/{id}/backfill T17 — backfill da janela de dados
    POST   /v1/evaluation/dispatch/scan           T15 — uma passada do dispatcher windowed

  Instances:
    GET    /v1/evaluation/instances               list instances
    POST   /v1/evaluation/instances               manual trigger
    GET    /v1/evaluation/instances/{id}          get instance
    POST   /v1/evaluation/instances/claim         evaluator claims next
    POST   /v1/evaluation/instances/{id}/expire   admin: force expire

  Results:
    GET    /v1/evaluation/results                 list results
    GET    /v1/evaluation/results/{id}            get result
    GET    /v1/evaluation/results/{id}/criteria   get criterion responses
    POST   /v1/evaluation/results/{id}/review     reviewer submits review
    POST   /v1/evaluation/results/{id}/lock       admin lock

  Contestations:
    GET    /v1/evaluation/contestations           list contestations
    POST   /v1/evaluation/contestations           file contestation
    GET    /v1/evaluation/contestations/{id}      get contestation
    POST   /v1/evaluation/contestations/{id}/adjudicate  supervisor adjudicates

  Sampling:
    POST   /v1/evaluation/sample                  check if session should be sampled

  Reports:
    GET    /v1/evaluation/reports/campaign/{id}   campaign report
    GET    /v1/evaluation/reports/agent           agent performance report

  Internal (called by evaluation_submit MCP tool):
    POST   /v1/evaluation/ingest                  ingest EvaluationResult from agent

  Health:
    GET    /health
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import httpx
import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from .config import settings
from . import db as _db
from . import kafka_emitter as _kafka
from . import scoring as _scoring
from .sampling import (
    should_sample, compute_expires_at, compute_priority, compute_deadline_at,
    campaign_dispatch_open,
)
from .sampling_engine import run_curation_sampling
from .backfill import run_campaign_backfill
from .prompt_composer import compose_rubric_prompt, DEFAULT_RUBRIC_BODY

logger = logging.getLogger("plughub.evaluation.router")

router = APIRouter()


# ─── Auth helpers ─────────────────────────────────────────────────────────────

def _require_admin(request: Request) -> None:
    if not settings.admin_token:
        return
    token = request.headers.get("x-admin-token", "")
    if token != settings.admin_token:
        raise HTTPException(status_code=401, detail="unauthorized")


def _decode_jwt(request: Request) -> dict[str, Any]:
    """Decode HS256 Bearer JWT; return payload with at least 'sub' and 'roles'."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    token = auth[7:]
    try:
        payload = pyjwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except pyjwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}")
    if "sub" not in payload:
        raise HTTPException(status_code=401, detail="token missing 'sub' claim")
    return payload


def _decode_jwt_optional(request: Request) -> dict[str, Any] | None:
    """Decode Bearer JWT if present; return None if absent (no error)."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        payload = pyjwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return payload
    except pyjwt.PyJWTError:
        return None


def _check_abac_permission(jwt_payload: dict[str, Any], field: str, pool_id: str | None = None) -> bool:
    """
    Check ABAC permission from module_config JWT claim.

    field:   'revisar'   → can the caller perform human review?
             'contestar' → can the caller file a contestation?
    pool_id: if the campaign is scoped to a pool, pass it to enforce scope.

    Graceful degradation: if module_config is absent (legacy token) → allow.
    Scope: if scope list is empty → global access → allow.
            if scope list is non-empty → pool_id must be in the list.
    """
    module_config = jwt_payload.get("module_config", {})
    if not module_config:
        # Legacy token with no module_config → no ABAC restriction
        return True
    field_config = module_config.get("evaluation", {}).get(field, {})
    access = field_config.get("access", "none")
    if access == "none":
        return False
    scope: list[str] = field_config.get("scope", [])
    if not scope:
        # Empty scope = global access
        return True
    if pool_id:
        # Scope list contains entries like "pool:retencao_humano"
        return f"pool:{pool_id}" in scope or pool_id in scope
    # No pool_id to check against → accept if any scope is present
    return True


# ─── DB / infra accessors ─────────────────────────────────────────────────────

def _pool(request: Request) -> asyncpg.Pool:
    return request.app.state.db_pool


def _kafka_producer(request: Request) -> Any:
    return request.app.state.kafka_producer


def _redis(request: Request) -> Any:
    return request.app.state.redis


# ─── ContextStore / Workflow helpers ──────────────────────────────────────────

async def _write_ctx(redis_client: Any, tenant_id: str, session_id: str, fields: dict[str, Any]) -> None:
    """Fire-and-forget write to ContextStore hash {tenant}:ctx:{session_id}."""
    try:
        key = f"{tenant_id}:ctx:{session_id}"
        now = datetime.now(timezone.utc).isoformat()
        pipe = redis_client.pipeline()
        for tag, value in fields.items():
            entry = json.dumps({
                "value": value,
                "confidence": 1.0,
                "source": "evaluation-api",
                "visibility": "agents_only",
                "updated_at": now,
            })
            pipe.hset(key, tag, entry)
        pipe.expire(key, settings.workflow_context_ttl_s)
        await pipe.execute()
    except Exception as exc:
        logger.warning("ContextStore write failed (non-fatal): %s", exc)


async def _resume_workflow(resume_token: str, tenant_id: str) -> None:
    """POST /v1/workflow/resume to workflow-api (fire-and-forget)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.workflow_api_url}/v1/workflow/resume",
                json={"token": resume_token, "decision": "input", "tenant_id": tenant_id},
            )
            if resp.status_code >= 400:
                logger.warning("workflow resume returned %s: %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.warning("workflow resume HTTP call failed (non-fatal): %s", exc)


def _compute_available_actions(
    result: dict[str, Any],
    jwt_payload: dict[str, Any] | None,
    pool_id: str | None,
) -> list[str]:
    """Compute available_actions server-side using ABAC — never trust the client."""
    if result.get("eval_status") == "locked":
        return []
    action_required = result.get("action_required")
    if not action_required or not jwt_payload:
        return []

    if action_required == "review" and _check_abac_permission(jwt_payload, "revisar", pool_id):
        return ["review"]
    if action_required == "contestation" and _check_abac_permission(jwt_payload, "contestar", pool_id):
        return ["contest"]
    return []


# ─── Health ───────────────────────────────────────────────────────────────────

@router.get("/health")
async def health(request: Request) -> dict:
    return {"status": "ok", "service": "evaluation-api"}


# ─── Forms ────────────────────────────────────────────────────────────────────

class FormCreate(BaseModel):
    tenant_id:        str
    name:             str
    description:      str = ""
    dimensions:       list[dict] = Field(default_factory=list)
    total_weight:     float = 1.0
    passing_score:    float | None = None
    allow_na:         bool = True
    knowledge_domains: list[str] = Field(default_factory=list)
    created_by:       str = "operator"


class FormUpdate(BaseModel):
    name:             str | None = None
    description:      str | None = None
    dimensions:       list[dict] | None = None
    total_weight:     float | None = None
    passing_score:    float | None = None
    allow_na:         bool | None = None
    knowledge_domains: list[str] | None = None
    status:           str | None = None


def _expose_form_id(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """O UI espera `form_id`; o DB usa `id` como PK (evform_*). Expõe ambos na
    resposta — sem isso o <select> cai no fallback HTML e envia o NOME do form."""
    if row is not None and "form_id" not in row and "id" in row:
        row["form_id"] = row["id"]
    return row


def _expose_campaign_id(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """O UI espera `campaign_id`; o DB usa `id` como PK (evcampaign_*). Expõe ambos
    — sem isso o front envia campaign_id=undefined (pause/resume/seed quebram com 422)."""
    if row is not None and "campaign_id" not in row and "id" in row:
        row["campaign_id"] = row["id"]
    return row


def _expose_result_id(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """O UI espera `result_id`; o DB usa `id` como PK (evresult_*) e já carrega
    `campaign_id` (FK). Expõe `result_id` = `id` — sem isso a seleção de linha do
    drill-down compara undefined===undefined (toda linha aparece selecionada). T9-B."""
    if row is not None and "result_id" not in row and "id" in row:
        row["result_id"] = row["id"]
    return row


@router.get("/v1/evaluation/forms")
async def list_forms(
    request: Request,
    tenant_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    pool = _pool(request)
    rows = await _db.list_forms(pool, tenant_id, status=status, limit=limit, offset=offset)
    rows = [_expose_form_id(r) for r in rows]
    return {"tenant_id": tenant_id, "forms": rows, "count": len(rows)}


@router.post("/v1/evaluation/forms", status_code=201)
async def create_form(body: FormCreate, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.create_form(pool, **body.model_dump())
    return _expose_form_id(row)


@router.get("/v1/evaluation/forms/{form_id}")
async def get_form(form_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.get_form(pool, form_id, tenant_id)
    if not row:
        raise HTTPException(404, detail="form not found")
    return _expose_form_id(row)


@router.put("/v1/evaluation/forms/{form_id}")
async def update_form(form_id: str, tenant_id: str, body: FormUpdate, request: Request) -> dict:
    pool = _pool(request)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    row = await _db.update_form(pool, form_id, tenant_id, **updates)
    if not row:
        raise HTTPException(404, detail="form not found")
    return _expose_form_id(row)


@router.delete("/v1/evaluation/forms/{form_id}", status_code=204)
async def delete_form(form_id: str, tenant_id: str, request: Request) -> None:
    pool = _pool(request)
    # Soft-delete via archive
    row = await _db.update_form(pool, form_id, tenant_id, status="archived")
    if not row:
        raise HTTPException(404, detail="form not found")


# ─── T6b — Form deploy lifecycle (publish + version snapshots) ──────────────────

class FormPublish(BaseModel):
    published_by: str = "operator"


@router.post("/v1/evaluation/forms/{form_id}/publish")
async def publish_form(form_id: str, tenant_id: str, body: FormPublish, request: Request) -> dict:
    """Publica a versão corrente do form: snapshot imutável em form_versions + deploy_status=published."""
    pool = _pool(request)
    row = await _db.publish_form(pool, form_id, tenant_id, published_by=body.published_by)
    if not row:
        raise HTTPException(404, detail="form not found")
    return _expose_form_id(row)


@router.get("/v1/evaluation/forms/{form_id}/versions")
async def list_form_versions(form_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    versions = await _db.list_form_versions(pool, form_id, tenant_id)
    return {"form_id": form_id, "versions": versions, "count": len(versions)}


@router.get("/v1/evaluation/forms/{form_id}/versions/{version}")
async def get_form_version(form_id: str, version: int, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.get_form_version(pool, form_id, tenant_id, version)
    if not row:
        raise HTTPException(404, detail="form version not found")
    return _expose_form_id(row)


# ─── T8-A — Rubric templates (spec §16.3) ──────────────────────────────────────
# Rubrica-template: instruções gerais de avaliação, default por tenant + override por
# campanha, versionada (snapshot imutável), espelhando o lifecycle de forms (T6b).
# Endpoints abertos (tenant_id), como forms; ABAC `gerir_rubrica` entra com a UI (chunk D).

class RubricCreate(BaseModel):
    tenant_id:   str
    scope:       str = "tenant"          # "tenant" | "campaign"
    campaign_id: str | None = None
    name:        str = "Rubric template"
    body:        str = ""
    created_by:  str = "operator"


class RubricUpdate(BaseModel):
    name:          str | None = None
    body:          str | None = None
    deploy_status: str | None = None     # normalmente não setado à mão (publish faz isso)


class RubricPublish(BaseModel):
    published_by: str = "operator"


@router.get("/v1/evaluation/rubric-templates")
async def list_rubric_templates(
    request: Request, tenant_id: str, campaign_id: str | None = None,
) -> dict:
    pool = _pool(request)
    rows = await _db.list_rubric_templates(pool, tenant_id, campaign_id=campaign_id)
    return {"tenant_id": tenant_id, "rubric_templates": rows, "count": len(rows)}


@router.post("/v1/evaluation/rubric-templates", status_code=201)
async def create_rubric_template(body: RubricCreate, request: Request) -> dict:
    pool = _pool(request)
    try:
        row = await _db.create_rubric_template(pool, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, detail="rubric template already exists for this scope")
    return row


@router.get("/v1/evaluation/rubric-templates/resolve")
async def resolve_rubric_template(
    request: Request, tenant_id: str, campaign_id: str | None = None,
) -> dict:
    """Rubrica EFETIVA (override publicado da campanha → default publicado do tenant →
    null). Base do compositor do prompt (chunk B) e do preview da UI."""
    pool = _pool(request)
    eff = await _db.resolve_rubric(pool, tenant_id, campaign_id=campaign_id)
    return {"tenant_id": tenant_id, "campaign_id": campaign_id, "resolved": eff}


@router.get("/v1/evaluation/rubric-templates/effective")
async def effective_rubric_template(
    request: Request, tenant_id: str, campaign_id: str | None = None,
) -> dict:
    """T8-B2 — body EFETIVO da rubrica COM fallback built-in (resolve → body/source; null
    → DEFAULT_RUBRIC_BODY/builtin_default). Consumido pelo `evaluation_context_get`
    (mcp-server) p/ expor `rubric_instructions` ao avaliador em runtime. Sempre devolve um
    body (nunca null) — o avaliador nunca fica sem instruções gerais."""
    pool = _pool(request)
    eff = await _db.resolve_rubric(pool, tenant_id, campaign_id=campaign_id)
    if eff:
        return {"body": eff.get("body") or DEFAULT_RUBRIC_BODY,
                "source": eff.get("source"), "scope": eff.get("scope"),
                "version": eff.get("version"), "rubric_id": eff.get("rubric_id")}
    return {"body": DEFAULT_RUBRIC_BODY, "source": "builtin_default",
            "scope": None, "version": None, "rubric_id": None}


@router.get("/v1/evaluation/rubric-templates/{rubric_id}")
async def get_rubric_template(rubric_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.get_rubric_template(pool, rubric_id, tenant_id)
    if not row:
        raise HTTPException(404, detail="rubric template not found")
    return row


@router.put("/v1/evaluation/rubric-templates/{rubric_id}")
async def update_rubric_template(
    rubric_id: str, tenant_id: str, body: RubricUpdate, request: Request,
) -> dict:
    pool = _pool(request)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    row = await _db.update_rubric_template(pool, rubric_id, tenant_id, updates)
    if not row:
        raise HTTPException(404, detail="rubric template not found")
    return row


@router.post("/v1/evaluation/rubric-templates/{rubric_id}/publish")
async def publish_rubric_template(
    rubric_id: str, tenant_id: str, body: RubricPublish, request: Request,
) -> dict:
    """Publica a rubrica corrente: snapshot imutável em rubric_template_versions +
    deploy_status=published. (Deploy epoch p/ comparação antes/depois entra no chunk D.)"""
    pool = _pool(request)
    row = await _db.publish_rubric_template(pool, rubric_id, tenant_id, published_by=body.published_by)
    if not row:
        raise HTTPException(404, detail="rubric template not found")
    return row


@router.get("/v1/evaluation/rubric-templates/{rubric_id}/versions")
async def list_rubric_template_versions(rubric_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    versions = await _db.list_rubric_template_versions(pool, rubric_id, tenant_id)
    return {"rubric_id": rubric_id, "versions": versions, "count": len(versions)}


@router.get("/v1/evaluation/rubric-templates/{rubric_id}/versions/{version}")
async def get_rubric_template_version(
    rubric_id: str, version: int, tenant_id: str, request: Request,
) -> dict:
    pool = _pool(request)
    row = await _db.get_rubric_template_version(pool, rubric_id, tenant_id, version)
    if not row:
        raise HTTPException(404, detail="rubric template version not found")
    return row


# ─── T8-B — Composição + preview do prompt (spec §5.1/§16.3) ───────────────────

class RubricPreview(BaseModel):
    tenant_id:           str
    form_id:             str | None = None
    campaign_id:         str | None = None
    rubric_body:         str | None = None   # preview de um DRAFT em edição (vence resolve)
    rubric_id:           str | None = None   # ou: body vivo de uma rubrica específica
    include_calibration: bool = True


@router.post("/v1/evaluation/rubric-templates/preview")
async def preview_rubric_prompt(body: RubricPreview, request: Request) -> dict:
    """Compõe e devolve o prompt do avaliador (instruções gerais + critérios do form +
    notas de calibração + placeholder de transcript), p/ o preview da UI Rubrica/Prompt.
    Precedência da rubrica: `rubric_body` explícito → body vivo de `rubric_id` →
    `resolve_rubric` (override pub. campanha → default pub. tenant) → built-in default."""
    pool = _pool(request)

    # 1) resolve a rubrica a usar
    rubric_body: str | None = body.rubric_body
    rubric_source = "explicit_body"
    if rubric_body is None and body.rubric_id:
        rt = await _db.get_rubric_template(pool, body.rubric_id, body.tenant_id)
        if rt:
            rubric_body, rubric_source = rt.get("body") or "", "rubric_id_live"
    if rubric_body is None:
        eff = await _db.resolve_rubric(pool, body.tenant_id, campaign_id=body.campaign_id)
        if eff:
            rubric_body, rubric_source = eff.get("body") or "", eff.get("source") or "resolved"
    if rubric_body is None:
        rubric_body, rubric_source = DEFAULT_RUBRIC_BODY, "builtin_default"

    # 2) form (critérios)
    form = await _db.get_form(pool, body.form_id, body.tenant_id) if body.form_id else None
    if body.form_id and not form:
        raise HTTPException(404, detail="form not found")

    # 3) notas de calibração publicadas da campanha (RAG)
    notes: list[dict] = []
    if body.include_calibration and body.campaign_id:
        notes = await _db.list_calibration_notes(
            pool, body.tenant_id, campaign_id=body.campaign_id,
            published_to_kb=True, limit=20,
        )

    result = compose_rubric_prompt(
        rubric_body=rubric_body, rubric_source=rubric_source,
        form=form, calibration_notes=notes,
    )
    result["form_id"] = body.form_id
    result["campaign_id"] = body.campaign_id
    return result


# ─── Campaigns ────────────────────────────────────────────────────────────────

class CampaignCreate(BaseModel):
    tenant_id:                  str
    name:                       str
    description:                str = ""
    form_id:                    str
    # pool_id (legado: escopo ABAC + índice, NOT NULL no DB) e evaluation_pool_id
    # (pool avaliado) representam o MESMO pool para campanhas de pool único — o UI
    # expõe um seletor só ("Evaluation Pool"). Aceitamos qualquer um e espelhamos.
    pool_id:                    str | None = None
    sampling_rules:             dict = Field(default_factory=dict)
    reviewer_rules:             dict = Field(default_factory=dict)
    schedule:                   dict = Field(default_factory=dict)
    # Arc 6 v2 — workflow motor for contestation/review cycle
    review_workflow_skill_id:   str | None = None   # e.g. "skill_revisao_treplica_v1"
    contestation_policy:        dict = Field(default_factory=dict)
    created_by:                 str = "operator"
    # Task #74/#75 — evaluation scope + infra
    evaluation_pool_id:         str | None = None   # pool being evaluated (sampling filter)
    evaluation_calendar_id:     str | None = None   # calendar for SLA + scheduling windows
    gateway_config_ids:         list[str] = Field(default_factory=list)  # model configs for evaluators
    evaluator_pool:             str | None = None   # S2.2: pool do agente AVALIADOR (null = default global)
    # T17 — janela de dados (quais sessões entram, por closed_at; ISO; NULL = aberto)
    period_start:               str | None = None
    period_end:                 str | None = None

    @model_validator(mode="after")
    def _mirror_pools(self) -> "CampaignCreate":
        # Espelha pool_id ↔ evaluation_pool_id (mesmo pool). Pelo menos um exigido.
        if not self.pool_id and self.evaluation_pool_id:
            self.pool_id = self.evaluation_pool_id
        if not self.evaluation_pool_id and self.pool_id:
            self.evaluation_pool_id = self.pool_id
        if not self.pool_id:
            raise ValueError("pool_id ou evaluation_pool_id é obrigatório")
        return self


class CampaignUpdate(BaseModel):
    name:                       str | None = None
    description:                str | None = None
    status:                     str | None = None
    sampling_rules:             dict | None = None
    reviewer_rules:             dict | None = None
    schedule:                   dict | None = None
    review_workflow_skill_id:   str | None = None
    contestation_policy:        dict | None = None
    # Task #74/#75
    evaluation_pool_id:         str | None = None
    evaluation_calendar_id:     str | None = None
    gateway_config_ids:         list[str] | None = None
    evaluator_pool:             str | None = None   # S2.2
    period_start:               str | None = None   # T17 — janela de dados (ISO)
    period_end:                 str | None = None


@router.get("/v1/evaluation/campaigns")
async def list_campaigns(
    request: Request,
    tenant_id: str,
    pool_id: str | None = None,
    evaluation_pool_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    pool = _pool(request)
    rows = await _db.list_campaigns(
        pool, tenant_id,
        pool_id=pool_id,
        evaluation_pool_id=evaluation_pool_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    rows = [_expose_campaign_id(r) for r in rows]
    return {"tenant_id": tenant_id, "campaigns": rows, "count": len(rows)}


@router.post("/v1/evaluation/campaigns", status_code=201)
async def create_campaign(body: CampaignCreate, request: Request) -> dict:
    pool = _pool(request)
    # Validate form exists
    form = await _db.get_form(pool, body.form_id, body.tenant_id)
    if not form:
        raise HTTPException(400, detail=f"form {body.form_id} not found for tenant")
    data = body.model_dump()
    row = await _db.create_campaign(
        pool,
        tenant_id=data["tenant_id"],
        name=data["name"],
        description=data.get("description", ""),
        form_id=data["form_id"],
        pool_id=data["pool_id"],
        sampling_rules=data.get("sampling_rules"),
        reviewer_rules=data.get("reviewer_rules"),
        schedule=data.get("schedule"),
        created_by=data.get("created_by", "operator"),
        evaluation_pool_id=data.get("evaluation_pool_id"),
        evaluation_calendar_id=data.get("evaluation_calendar_id"),
        gateway_config_ids=data.get("gateway_config_ids") or [],
        evaluator_pool=data.get("evaluator_pool"),
        period_start=data.get("period_start"),
        period_end=data.get("period_end"),
    )
    # Patch v2 scalar fields not handled by create (jsonb fields via update)
    v2_updates: dict[str, Any] = {}
    if data.get("review_workflow_skill_id"):
        v2_updates["review_workflow_skill_id"] = data["review_workflow_skill_id"]
    if data.get("contestation_policy"):
        v2_updates["contestation_policy"] = data["contestation_policy"]
    if v2_updates:
        row = await _db.update_campaign(pool, row["id"], body.tenant_id, **v2_updates) or row
    return _expose_campaign_id(row)


@router.get("/v1/evaluation/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.get_campaign(pool, campaign_id, tenant_id)
    if not row:
        raise HTTPException(404, detail="campaign not found")
    return _expose_campaign_id(row)


@router.put("/v1/evaluation/campaigns/{campaign_id}")
async def update_campaign(campaign_id: str, tenant_id: str, body: CampaignUpdate, request: Request) -> dict:
    pool = _pool(request)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    row = await _db.update_campaign(pool, campaign_id, tenant_id, **updates)
    if not row:
        raise HTTPException(404, detail="campaign not found")
    return _expose_campaign_id(row)


@router.post("/v1/evaluation/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.update_campaign(pool, campaign_id, tenant_id, status="paused")
    if not row:
        raise HTTPException(404, detail="campaign not found")
    return _expose_campaign_id(row)


@router.post("/v1/evaluation/campaigns/{campaign_id}/resume")
async def resume_campaign(campaign_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.update_campaign(pool, campaign_id, tenant_id, status="active")
    if not row:
        raise HTTPException(404, detail="campaign not found")
    return _expose_campaign_id(row)


@router.delete("/v1/evaluation/campaigns/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: str, tenant_id: str, request: Request) -> None:
    pool = _pool(request)
    ok = await _db.delete_campaign(pool, campaign_id, tenant_id)
    if not ok:
        raise HTTPException(404, detail="campaign not found")


# ─── Instances ────────────────────────────────────────────────────────────────

class InstanceCreate(BaseModel):
    tenant_id:   str
    campaign_id: str
    session_id:  str
    segment_id:  str | None = None
    priority:    int = 5


class InstanceClaim(BaseModel):
    tenant_id:          str
    campaign_id:        str | None = None
    evaluator_agent_id: str | None = None


@router.get("/v1/evaluation/instances")
async def list_instances(
    request: Request,
    tenant_id: str,
    campaign_id: str | None = None,
    status: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    pool = _pool(request)
    rows = await _db.list_instances(pool, tenant_id, campaign_id=campaign_id,
                                     status=status, session_id=session_id,
                                     limit=limit, offset=offset)
    return {"tenant_id": tenant_id, "instances": rows, "count": len(rows)}


@router.post("/v1/evaluation/instances", status_code=201)
async def create_instance(body: InstanceCreate, request: Request) -> dict:
    pool = _pool(request)
    producer = _kafka_producer(request)

    campaign = await _db.get_campaign(pool, body.campaign_id, body.tenant_id)
    if not campaign:
        raise HTTPException(400, detail="campaign not found")
    if campaign["status"] not in ("active", "draft"):
        raise HTTPException(400, detail=f"campaign status={campaign['status']}, cannot create instances")

    expires_at = await compute_expires_at(campaign, settings.calendar_api_url,
                                           default_ttl_hours=settings.default_instance_ttl_hours)
    row = await _db.create_instance(
        pool,
        tenant_id=body.tenant_id,
        campaign_id=body.campaign_id,
        form_id=campaign["form_id"],
        session_id=body.session_id,
        segment_id=body.segment_id,
        priority=body.priority,
        expires_at=expires_at,
    )
    await _kafka.emit_instance_created(
        producer, settings.evaluation_topic,
        instance_id=row["id"],
        tenant_id=row["tenant_id"],
        session_id=row["session_id"],
        campaign_id=row["campaign_id"],
        form_id=row["form_id"],
        priority=row["priority"],
        expires_at=expires_at.isoformat() if expires_at else None,
    )
    return row


@router.post("/v1/evaluation/campaigns/{campaign_id}/dispatch")
async def dispatch_campaign(
    campaign_id: str, tenant_id: str, request: Request, limit: int = 100,
) -> dict:
    """
    S2.2 dispatcher — emits `evaluation.requested` for every *scheduled* instance of the
    campaign. The session-replayer builds the ReplayContext (with the form) and the
    Routing Engine allocates an evaluator agent from the campaign's `evaluator_pool`
    (fallback: global default). Instances stay `scheduled` so the evaluator can claim them.
    Used by the "Rodar agora" button and (later) the windowed dispatcher.
    """
    _require_admin(request)
    pool = _pool(request)
    producer = _kafka_producer(request)

    campaign = await _db.get_campaign(pool, campaign_id, tenant_id)
    if not campaign:
        raise HTTPException(404, detail="campaign not found")

    evaluator_pool = (campaign.get("evaluator_pool") or "").strip() or settings.default_evaluator_pool

    rows = await _db.list_instances(
        pool, tenant_id, campaign_id=campaign_id, status="scheduled",
        limit=limit, offset=0,
    )
    dispatched = 0
    for row in rows:
        await _kafka.emit_evaluation_requested(
            producer, settings.evaluation_topic,
            instance_id=row["id"],
            tenant_id=row.get("tenant_id") or tenant_id,
            session_id=row["session_id"],
            campaign_id=row.get("campaign_id") or campaign_id,
            form_id=row.get("form_id") or campaign["form_id"],
            evaluator_pool=evaluator_pool,
        )
        dispatched += 1

    return {
        "campaign_id":    campaign_id,
        "dispatched":     dispatched,
        "evaluator_pool": evaluator_pool,
    }


# ─── T15 — dispatcher por janela de calendário (§18.4) ────────────────────────
# Despacho idempotente das instances `scheduled` de uma campanha, gated pela janela de
# calendário. Compartilhado pelo scanner de fundo (main._run_dispatch_scanner) e pelo
# endpoint de scan abaixo. Difere do `/campaigns/{id}/dispatch` manual ("Rodar agora"),
# que força o emit de TODAS as scheduled sem janela nem cooldown.

async def dispatch_campaign_scheduled(
    pool: asyncpg.Pool,
    producer: Any,
    campaign: dict,
    *,
    calendar_api_url: str,
    cooldown_s: int,
    batch_limit: int,
    respect_window: bool = True,
) -> dict:
    """Emite `evaluation.requested` para as instances despacháveis da campanha (idempotente
    via `dispatched_at` + cooldown). Gated pela janela de calendário quando
    `respect_window`. Retorna resumo {campaign_id, in_window, dispatched, evaluator_pool}."""
    campaign_id = campaign["id"]
    tenant_id   = campaign["tenant_id"]

    in_window = True
    if respect_window:
        in_window = await campaign_dispatch_open(campaign, calendar_api_url)
        if not in_window:
            return {"campaign_id": campaign_id, "in_window": False, "dispatched": 0}

    evaluator_pool = (campaign.get("evaluator_pool") or "").strip() or settings.default_evaluator_pool
    rows = await _db.claim_dispatchable_instances(
        pool, campaign_id, tenant_id,
        cooldown_s=cooldown_s, limit=batch_limit,
    )
    for row in rows:
        await _kafka.emit_evaluation_requested(
            producer, settings.evaluation_topic,
            instance_id=row["id"],
            tenant_id=row.get("tenant_id") or tenant_id,
            session_id=row["session_id"],
            campaign_id=row.get("campaign_id") or campaign_id,
            form_id=row.get("form_id") or campaign["form_id"],
            evaluator_pool=evaluator_pool,
        )
    return {
        "campaign_id":    campaign_id,
        "in_window":      in_window,
        "dispatched":     len(rows),
        "evaluator_pool": evaluator_pool,
    }


@router.post("/v1/evaluation/dispatch/scan")
async def dispatch_scan(
    tenant_id: str, request: Request,
    campaign_id: str | None = None,
) -> dict:
    """T15 — roda UMA passada do dispatcher windowed sob demanda (ops + testes), com a
    mesma lógica do scanner de fundo. Sem `campaign_id` → varre as campanhas ativas do
    tenant; com `campaign_id` → só ela. Idempotente (cooldown via `dispatched_at`)."""
    _require_admin(request)
    pool = _pool(request)
    producer = _kafka_producer(request)

    if campaign_id:
        c = await _db.get_campaign(pool, campaign_id, tenant_id)
        campaigns = [c] if c else []
    else:
        campaigns = await _db.list_campaigns(pool, tenant_id, status="active", limit=200)

    results = []
    for c in campaigns:
        if not c:
            continue
        results.append(await dispatch_campaign_scheduled(
            pool, producer, c,
            calendar_api_url=settings.calendar_api_url,
            cooldown_s=settings.dispatch_redispatch_cooldown_s,
            batch_limit=settings.dispatch_batch_limit,
        ))
    return {
        "scanned":    len(results),
        "dispatched": sum(r.get("dispatched", 0) for r in results),
        "campaigns":  results,
    }


@router.post("/v1/evaluation/campaigns/{campaign_id}/backfill")
async def backfill_campaign(
    campaign_id: str, tenant_id: str, request: Request,
) -> dict:
    """T17-backfill (§18.5) — reprocessa o PASSADO: enumera os segmentos fechados na janela
    de dados da campanha (`[period_start, period_end]`, por `analytics.segments`) e cria as
    instances por segmento (mesma amostragem do forward; idempotente por
    `(campaign_id, segment_id)`). As instances nascem `scheduled` → despachadas pelo T15.
    Exige `period_start` (a janela de dados); `period_end` nulo → até agora. Admin-token."""
    _require_admin(request)
    pool = _pool(request)

    campaign = await _db.get_campaign(pool, campaign_id, tenant_id)
    if not campaign:
        raise HTTPException(404, detail="campaign not found")

    period_start = campaign.get("period_start")
    if not period_start:
        raise HTTPException(
            400, detail="backfill requires period_start (campaign data window); set it first",
        )
    period_end = campaign.get("period_end")

    def _iso(v: Any) -> str:
        return v.isoformat() if isinstance(v, datetime) else str(v)

    from_dt = _iso(period_start)
    to_dt   = _iso(period_end) if period_end else datetime.now(tz=timezone.utc).isoformat()

    return await run_campaign_backfill(
        pool, campaign,
        analytics_api_url=settings.analytics_api_url,
        from_dt=from_dt, to_dt=to_dt,
        page_size=settings.backfill_page_size,
        max_segments=settings.backfill_max_segments,
    )


@router.get("/v1/evaluation/reports/campaign-summary")
async def campaign_summary(
    request: Request, tenant_id: str, campaign_id: str | None = None,
) -> dict:
    """T9-A2 — agregados por campanha p/ o nível 1 da lista de Avaliações (cards de
    campanha): contagens por instance status e por `result_state`, distribuição de
    `finalize_reason`, split humano/IA, tempo médio (`process_duration_ms`) e SLA vencido.
    Consolidado **global por campanha**; o frontend mescla com nome/período/pool da campanha."""
    pool = _pool(request)
    ids = [campaign_id] if campaign_id else None
    summaries = await _db.campaign_summaries(pool, tenant_id, campaign_ids=ids)
    return {"tenant_id": tenant_id, "summaries": summaries}


@router.get("/v1/evaluation/instances/{instance_id}")
async def get_instance(instance_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.get_instance(pool, instance_id, tenant_id)
    if not row:
        raise HTTPException(404, detail="instance not found")
    return row


@router.post("/v1/evaluation/instances/claim")
async def claim_instance(body: InstanceClaim, request: Request) -> dict:
    pool = _pool(request)
    producer = _kafka_producer(request)
    row = await _db.claim_next_instance(
        pool,
        body.tenant_id,
        campaign_id=body.campaign_id,
        evaluator_agent_id=body.evaluator_agent_id,
    )
    if not row:
        raise HTTPException(404, detail="no schedulable instance available")
    await _kafka.emit_instance_assigned(
        producer, settings.evaluation_topic,
        instance_id=row["id"],
        tenant_id=row["tenant_id"],
        session_id=row["session_id"],
        evaluator_agent_id=row.get("evaluator_agent_id"),
    )
    return row


@router.post("/v1/evaluation/instances/{instance_id}/expire", status_code=204)
async def expire_instance(instance_id: str, tenant_id: str, request: Request) -> None:
    _require_admin(request)
    pool = _pool(request)
    producer = _kafka_producer(request)
    row = await _db.update_instance_status(pool, instance_id, tenant_id, "expired")
    if not row:
        raise HTTPException(404, detail="instance not found")
    await _kafka.emit_instance_expired(
        producer, settings.evaluation_topic,
        instance_id=row["id"],
        tenant_id=row["tenant_id"],
        session_id=row["session_id"],
        campaign_id=row["campaign_id"],
    )


# ─── T13 — degradação: thin-session (skipped) e erro de avaliação (error) ──────
# skipped = não avaliável, sem culpa do avaliador (sessão sem dados) → SEM submit.
# error   = o avaliador falhou; classificação recuperável/irrecuperável (→ error_rejected)
#           é a T12 (ai_review). Ambos terminais p/ a camada de trabalho e FORA dos
#           relatórios de qualidade (que filtram evaluation_finalized, nunca emitido aqui).

_INSTANCE_TERMINAL = {"completed", "skipped", "error", "error_rejected", "expired"}


class InstanceSkipBody(BaseModel):
    reason: str = "thin_session"   # motivo (auditoria); ex.: thin_session, no_transcript


class InstanceErrorBody(BaseModel):
    reason: str = "evaluation_error"
    detail: str = ""


@router.post("/v1/evaluation/instances/{instance_id}/skip")
async def skip_instance(instance_id: str, tenant_id: str, body: InstanceSkipBody, request: Request) -> dict:
    """Marca a instance como `skipped` (thin-session). Não cria result, não submete.
    Guardado: só a partir de estado não-terminal."""
    _require_admin(request)
    pool = _pool(request)
    inst = await _db.get_instance(pool, instance_id, tenant_id)
    if not inst:
        raise HTTPException(404, detail="instance not found")
    if inst.get("status") in _INSTANCE_TERMINAL:
        raise HTTPException(409, detail=f"instance already terminal: {inst.get('status')}")
    row = await _db.update_instance_status(pool, instance_id, tenant_id, "skipped")
    logger.info("instance skipped (thin-session): %s reason=%s", instance_id, body.reason)
    return {"instance_id": instance_id, "status": "skipped", "reason": body.reason}


@router.post("/v1/evaluation/instances/{instance_id}/mark-error")
async def mark_instance_error(instance_id: str, tenant_id: str, body: InstanceErrorBody, request: Request) -> dict:
    """Marca a instance como `error` (falha do avaliador). A classificação
    recuperável→retry vs irrecuperável→error_rejected é a T12 (ai_review)."""
    _require_admin(request)
    pool = _pool(request)
    inst = await _db.get_instance(pool, instance_id, tenant_id)
    if not inst:
        raise HTTPException(404, detail="instance not found")
    if inst.get("status") in _INSTANCE_TERMINAL:
        raise HTTPException(409, detail=f"instance already terminal: {inst.get('status')}")
    row = await _db.update_instance_status(pool, instance_id, tenant_id, "error")
    logger.info("instance error: %s reason=%s detail=%s", instance_id, body.reason, body.detail[:200])
    return {"instance_id": instance_id, "status": "error", "reason": body.reason}


# ─── Ingest (from evaluation_submit MCP tool) ─────────────────────────────────

class IngestBody(BaseModel):
    """Called by evaluation_submit when instance_id is present."""
    tenant_id:          str
    instance_id:        str
    session_id:         str
    campaign_id:        str
    form_id:            str
    evaluator_agent_id: str
    overall_score:      float | None = None
    max_score:          float | None = None
    normalized_score:   float | None = None
    passed:             bool | None = None
    eval_status:        str = "submitted"
    evaluator_notes:    str = ""
    comparison_mode:    bool = False
    comparison_report:  dict | None = None
    knowledge_snippets: list[dict] = Field(default_factory=list)
    criterion_responses: list[dict] = Field(default_factory=list)
    # Arc 13 Fase B — per-dimension evidence and evaluation type
    dimension_threads: list[dict] = Field(default_factory=list)  # [{dimension_id, score, justification, evidence_entries[]}]
    evaluated_agent_type: str = "human_agent"  # "human_agent" | "ai_agent"
    evaluated_at: str | None = None            # backdating opcional (seeder sintético — séries temporais)


@router.post("/v1/evaluation/ingest", status_code=201)
async def ingest_result(body: IngestBody, request: Request) -> dict:
    pool = _pool(request)
    producer = _kafka_producer(request)
    return await _ingest_core(pool, producer, body)


async def finalize_evaluation(
    pool, producer, *,
    result_id: str,
    tenant_id: str,
    instance_id: str,
    session_id: str,
    campaign_id: str,
    contestation_state: str,
    final_score: float,
    finalize_reason: str | None = None,
    final_scores_by_dimension: list[dict] | None = None,
    process_duration_ms: int = 0,
    evaluated_agent_type: str | None = None,
    run_curation: bool = False,
    normalized_score: float | None = None,
) -> dict | None:
    """T3 — ÚNICO ponto que finaliza um resultado e emite `evaluation_finalized`.
    Todos os caminhos terminais (ingest IA, ai_review, submit_review, scanner) passam por
    aqui. Idempotente: se já finalizado, `finalize_result` retorna None e nenhum evento é
    emitido (seguro p/ race scanner × ação × redelivery)."""
    row = await _db.finalize_result(
        pool, result_id,
        contestation_state=contestation_state,
        final_score=final_score,
        process_duration_ms=process_duration_ms,
        finalize_reason=finalize_reason,
    )
    if row is None:
        return None  # já finalizado — idempotente, sem evento duplicado

    # Garante evaluated_agent_type no resultado (o ramo IA não o gravava).
    if evaluated_agent_type and not row.get("evaluated_agent_type"):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE evaluation.results SET evaluated_agent_type=$1 WHERE id=$2",
                evaluated_agent_type, result_id,
            )

    reason = finalize_reason or _db._FINALIZE_REASON_MAP.get(contestation_state)
    await _kafka.emit_evaluation_finalized(
        producer,
        instance_id=instance_id,
        session_id=session_id,
        campaign_id=campaign_id,
        tenant_id=tenant_id,
        final_score=final_score,
        final_scores_by_dimension=final_scores_by_dimension or [],
        contestation_state=contestation_state,
        process_duration_ms=process_duration_ms,
        finalize_reason=reason,
        segment_id=row.get("segment_id"),               # populado por T2
        round=row.get("round", 1),
        evaluated_agent_type=evaluated_agent_type or row.get("evaluated_agent_type"),
        form_version=row.get("form_version"),            # populado por T2
    )

    if run_curation:
        asyncio.create_task(
            run_curation_sampling(
                pool, instance_id=instance_id, tenant_id=tenant_id,
                campaign_id=campaign_id,
                normalized_score=float(normalized_score or final_score or 0),
            ),
            name=f"curation-sampling-{instance_id}",
        )
    return row


async def apply_state_deadline(pool, campaign: dict | None, result_id: str, kind: str) -> None:
    """T4 — computa e grava deadline_at na entrada de `open` (kind='contest') ou
    `under_review` (kind='review'). Best-effort: falha não interrompe a transição."""
    policy = (campaign or {}).get("contestation_policy") or {}
    hours = (policy.get("contest_deadline_hours", 48) if kind == "contest"
             else policy.get("review_deadline_hours", 24))
    try:
        dl = await compute_deadline_at(campaign or {}, settings.calendar_api_url, hours=int(hours))
        await _db.set_deadline_at(pool, result_id, dl)
    except Exception as exc:
        logger.warning("apply_state_deadline failed (non-fatal): %s", exc)


async def _is_flagged(pool, campaign_id: str, tenant_id: str, score: float | None) -> tuple[bool, str]:
    """T12 — sinaliza o resultado para o gate ai_review: score fora de faixa (regra
    `score_extremes` da campanha, params `min`/`max`) ou sem nota (erro). Sem regra
    configurada → não sinaliza (comportamento atual)."""
    if score is None:
        return True, "no_score"
    try:
        rules = await _db.list_sampling_rules(pool, tenant_id, campaign_id)
    except Exception:
        return False, ""
    for r in (rules or []):
        if not r.get("enabled", True) or r.get("rule_type") != "score_extremes":
            continue
        p = r.get("params") or {}
        lo = p.get("min", p.get("config_min"))
        hi = p.get("max", p.get("config_max"))
        if lo is not None and float(score) < float(lo):
            return True, f"below_min({lo})"
        if hi is not None and float(score) > float(hi):
            return True, f"above_max({hi})"
    return False, ""


async def _ingest_core(pool, producer, body: IngestBody, *, strict_validation: bool = True) -> dict:
    """Core ingest logic — callable both from the HTTP route and from the
    evaluation.events consumer (real-evaluator path, see _ingest_from_completed_event).
    Persists the EvaluationResult + criterion responses + ContestationThreads and
    advances the EvaluationInstance to completed.

    T7a — o formulário é a fonte única da nota: a `overall_score` recebida é DESCARTADA
    e recomputada de `criterion_responses` pelos pesos/tipos do snapshot pinado do form
    (`scoring.aggregate_scores`); `criterion_responses` é validado contra a definição do
    form (`scoring.validate_criterion_responses`). `strict_validation=True` (rota HTTP)
    rejeita com 422; o consumer real passa `False` (loga e segue — endurecer é T7b)."""
    # Verify instance exists
    instance = await _db.get_instance(pool, body.instance_id, body.tenant_id)
    if not instance:
        raise HTTPException(404, detail=f"instance {body.instance_id} not found")

    # ── T7a — agregação determinística + validação form-driven ────────────────────
    # Carrega o snapshot pinado da versão do form (T6b); fallback ao form vivo (T6a).
    overall_score    = body.overall_score
    normalized_score = body.normalized_score
    agg_by_dimension: list[dict] = []
    try:
        _form = await _db.get_form_version(
            pool, body.form_id, body.tenant_id, int(instance.get("form_version") or 1),
        )
    except Exception as exc:
        logger.warning("ingest: form snapshot load failed (non-fatal): %s", exc)
        _form = None
    if _form and body.criterion_responses:
        violations = _scoring.validate_criterion_responses(_form, body.criterion_responses)
        if violations:
            if strict_validation:
                raise HTTPException(
                    422, detail={"error": "invalid_criterion_responses", "violations": violations},
                )
            logger.warning("ingest: criterion_responses violations (non-strict): %s", violations)
        agg_overall, agg_by_dimension = _scoring.aggregate_scores(_form, body.criterion_responses)
        if agg_overall is not None:
            overall_score    = agg_overall          # descarta a nota do LLM (§5.2)
            normalized_score = round(agg_overall / 10.0, 3)

    # Create result
    result = await _db.create_result(
        pool,
        tenant_id=body.tenant_id,
        instance_id=body.instance_id,
        session_id=body.session_id,
        campaign_id=body.campaign_id,
        form_id=body.form_id,
        evaluator_agent_id=body.evaluator_agent_id,
        overall_score=overall_score,
        max_score=body.max_score,
        normalized_score=normalized_score,
        passed=body.passed,
        eval_status=body.eval_status,
        evaluator_notes=body.evaluator_notes,
        comparison_mode=body.comparison_mode,
        comparison_report=body.comparison_report,
        knowledge_snippets=body.knowledge_snippets,
    )

    # T2 — propaga segmento/identidade do avaliado + form_version da instance p/ o result
    # (a posse do 5a lê do result). Best-effort.
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE evaluation.results
                      SET segment_id=$1, evaluated_user_id=$2, form_version=$3, updated_at=now()
                    WHERE id=$4""",
                instance.get("segment_id"), instance.get("evaluated_user_id"),
                int(instance.get("form_version") or 1), result["id"],
            )
    except Exception as exc:
        logger.warning("propagate segment to result failed (non-fatal): %s", exc)

    # Create criterion responses
    criteria_rows: list[dict] = []
    if body.criterion_responses:
        criteria_rows = await _db.create_criterion_responses(
            pool,
            result["id"], body.instance_id, body.campaign_id, body.tenant_id,
            body.criterion_responses,
        )

    # ── T7a — ContestationThread round=1 nasce POR CRITÉRIO de criterion_responses ──
    # (chave canônica = criterion_id; §16.2). O trilho de auditoria imutável que as
    # contestações/revisões anexam vem direto das respostas do avaliador. Fallback ao
    # dimension_threads legado quando não há criterion_responses.
    if body.criterion_responses:
        _thread_src = [
            {
                "dimension_id":     r.get("criterion_id") or r.get("dimension_id", "unknown"),
                "justification":    r.get("notes") or r.get("justification", ""),
                "evidence_entries": r.get("evidence") or r.get("evidence_entries", []),
            }
            for r in body.criterion_responses
        ]
    else:
        _thread_src = [
            {
                "dimension_id":     d.get("dimension_id") or d.get("criterion_id", "unknown"),
                "justification":    d.get("justification", ""),
                "evidence_entries": d.get("evidence_entries", []),
            }
            for d in (body.dimension_threads or [])
        ]
    thread_count = 0
    for dim in _thread_src:
        dim_id = dim["dimension_id"]
        try:
            await _db.create_contestation_thread(
                pool,
                tenant_id=body.tenant_id,
                evaluation_instance_id=body.instance_id,
                dimension_id=dim_id,
                round=1,
                author_type="evaluator_ai",
                author_id=body.evaluator_agent_id,
                text=dim["justification"],
                evidence_entries=dim["evidence_entries"],
            )
            thread_count += 1
        except Exception as exc:
            logger.warning(
                "failed to create contestation thread for criterion=%s: %s",
                dim_id, exc,
            )

    # ── Arc 13 Fase B: set evaluated_agent_type + initial contestation_state ──
    # Fluxo 2 (AI agent): finalize immediately (no contestation).
    # Fluxo 1 (human agent): set contestation_open (or pre_review_pending if campaign has pre_review).
    try:
        campaign = await _db.get_campaign(pool, body.campaign_id, body.tenant_id)
        pre_review = campaign.get("pre_review_enabled", False) if campaign else False
    except Exception:
        pre_review = False

    # T12 — flagging: score fora de faixa (regra score_extremes) ∨ sem nota → gate ai_review
    # ANTES de publicar (vale p/ AI e humano). Resolve via POST /instances/{id}/ai-review.
    flagged, flag_reason = await _is_flagged(pool, body.campaign_id, body.tenant_id, overall_score)

    # Default p/ o ramo ai_agent (antes ficava indefinido → UnboundLocalError no return).
    initial_state = "auto_finalized"
    if flagged:
        initial_state = "pre_review_pending"   # _RESULT_STATE_MAP → result_state 'ai_review'
        # action_required só aceita review|contestation (CHECK); o gate é o result_state.
        await _db.set_contestation_state(
            pool, result["id"], "pre_review_pending", action_required=None,
        )
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE evaluation.results SET evaluated_agent_type=$1, updated_at=now() WHERE id=$2",
                body.evaluated_agent_type, result["id"],
            )
        logger.info("ingest: result %s FLAGGED (%s) → ai_review (gate)", result["id"], flag_reason)
    elif body.evaluated_agent_type == "ai_agent":
        # Fluxo 2: finalização imediata via finalize_evaluation (emissor único, T3).
        await finalize_evaluation(
            pool, producer,
            result_id=result["id"],
            tenant_id=body.tenant_id,
            instance_id=body.instance_id,
            session_id=body.session_id,
            campaign_id=body.campaign_id,
            contestation_state="auto_finalized",
            final_score=float(overall_score or 0),
            finalize_reason="auto_ai",
            final_scores_by_dimension=agg_by_dimension or [
                {"dimension_id": d.get("dimension_id", ""), "score": d.get("score", 0)}
                for d in body.dimension_threads
            ],
            process_duration_ms=0,
            evaluated_agent_type="ai_agent",
            run_curation=True,
            normalized_score=float(normalized_score or overall_score or 0),
        )
    else:
        # Fluxo 1: human agent — set contestation_state based on pre_review config
        initial_state = "pre_review_pending" if pre_review else "contestation_open"
        await _db.set_contestation_state(
            pool, result["id"], initial_state,
            action_required=None,
        )
        # Also set evaluated_agent_type on result
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE evaluation.results SET evaluated_agent_type=$1, updated_at=now() WHERE id=$2",
                body.evaluated_agent_type, result["id"],
            )
        # T4 — deadline de contestação ao entrar em open.
        if initial_state == "contestation_open":
            await apply_state_deadline(pool, campaign, result["id"], "contest")

    # Emit Kafka lifecycle event
    await _kafka.emit_instance_completed(
        producer, settings.evaluation_topic,
        instance_id=body.instance_id,
        result_id=result["id"],
        tenant_id=body.tenant_id,
        session_id=body.session_id,
        campaign_id=body.campaign_id,
        overall_score=overall_score,
        passed=body.passed,
        eval_status=body.eval_status,
        evaluated_at=body.evaluated_at,
    )

    return {
        "result_id":               result["id"],
        "instance_id":             body.instance_id,
        "criteria_rows_created":   len(criteria_rows),
        "contestation_threads_created": thread_count,
        "contestation_state":      initial_state,
        "evaluated_agent_type":    body.evaluated_agent_type,
        "eval_status":             body.eval_status,
        "overall_score":           overall_score,
        "final_scores_by_dimension": agg_by_dimension,
    }


async def _ingest_from_completed_event(pool, producer, ev: dict) -> None:
    """Bridge: `evaluation.completed` (published by the evaluation_submit MCP tool)
    → ingest. This is the Arc 13 designed-but-missing link that persists the REAL
    evaluator's result into Postgres and advances the EvaluationInstance to
    completed. Without it, only ClickHouse (analytics) received the event and the
    instance stayed `scheduled` (Avaliações empty). Idempotent: skips instances
    already completed (re-dispatch / Kafka redelivery)."""
    instance_id = ev.get("instance_id")
    if not instance_id:
        return  # ad-hoc evaluations without an instance are analytics-only
    tenant_id = ev.get("tenant_id") or ""
    inst = await _db.get_instance(pool, instance_id, tenant_id)
    if not inst:
        logger.warning("ingest-consumer: instance %s not found (tenant=%s)", instance_id, tenant_id)
        return
    if inst.get("status") == "completed":
        logger.info("ingest-consumer: instance %s already completed — skip", instance_id)
        return

    body = IngestBody(
        tenant_id=tenant_id,
        instance_id=instance_id,
        session_id=ev.get("session_id", "") or "",
        campaign_id=ev.get("campaign_id") or inst.get("campaign_id") or "",
        form_id=ev.get("form_id") or inst.get("form_id") or "",
        evaluator_agent_id=ev.get("evaluator_id", "") or "",
        overall_score=ev.get("composite_score"),
        eval_status=ev.get("eval_status", "submitted") or "submitted",
        evaluator_notes=ev.get("summary", "") or "",
        criterion_responses=ev.get("criterion_responses", []) or [],
        dimension_threads=ev.get("dimension_threads", []) or [],
        evaluated_agent_type=ev.get("evaluated_agent_type", "human_agent") or "human_agent",
    )
    # T7a — consumer real é lenient (loga violações; endurecer/forçar shape é T7b).
    res = await _ingest_core(pool, producer, body, strict_validation=False)
    logger.info(
        "ingest-consumer: persisted result %s for instance=%s session=%s score=%s",
        res.get("result_id"), instance_id, body.session_id, res.get("overall_score"),
    )


# ─── Synthetic seeder (avaliador fake — S2.Q1: valida o módulo em volume) ──────

class SeedSyntheticBody(BaseModel):
    tenant_id:      str
    campaign_id:    str
    count:          int = 50
    human_ratio:    float = 0.7          # fração evaluated_agent_type="human_agent"
    days_back:      int = 30             # espalha as datas nos últimos N dias (séries temporais)
    agent_type_ids: list[str] = Field(default_factory=list)  # variedade de agentes avaliados (NPS agent_key)
    seed_nps:       bool = True
    pool_id:        str = ""             # pool dos sinais de NPS (default = evaluation_pool da campanha)


def _rand_criterion_responses(form: dict) -> tuple[list[dict], list[dict], float]:
    """Gera criterion_responses + dimension_threads aleatórios a partir das
    dimensões/critérios do form (ignora type=auto_computed). create_criterion_responses
    lê `score` (não `value`). Retorna também overall_score (média 0–10)."""
    crit_resps: list[dict] = []
    dim_threads: list[dict] = []
    scores: list[float] = []
    for dim in (form.get("dimensions") or []):
        dim_id = dim.get("id") or dim.get("dimension_id") or "dim"
        dim_scores: list[float] = []
        for crit in (dim.get("criteria") or []):
            if crit.get("type") == "auto_computed":
                continue
            cid = crit.get("criterion_id") or crit.get("id") or "crit"
            cname = crit.get("label") or crit.get("name") or cid
            if random.random() < 0.08:
                crit_resps.append({"criterion_id": cid, "criterion_name": cname,
                                   "dimension_id": dim_id, "na": True, "score": None,
                                   "value": None, "na_reason": "sintético: não aplicável",
                                   "justification": "Avaliação sintética (N/A)."})
                continue
            val = round(random.uniform(4.0, 10.0), 1)
            dim_scores.append(val); scores.append(val)
            crit_resps.append({"criterion_id": cid, "criterion_name": cname,
                               "dimension_id": dim_id, "na": False,
                               "score": val, "value": val, "max_score": 10.0,
                               "justification": "Avaliação sintética para teste de volume.",
                               "evidence_refs": [random.randint(0, 5)]})
        if dim_scores:
            dscore = round(sum(dim_scores) / len(dim_scores), 2)
            dim_threads.append({
                "dimension_id": dim_id, "score": dscore,
                "justification": "Síntese sintética da dimensão para teste de volume do módulo de qualidade.",
                "evidence_entries": [{
                    "stream_entry_id": f"synthetic-{uuid.uuid4().hex[:8]}",
                    "excerpt": "trecho sintético de evidência",
                    "relevance_note": "evidência gerada artificialmente",
                }],
            })
    overall = round(sum(scores) / len(scores), 2) if scores else round(random.uniform(5, 9), 2)
    return crit_resps, dim_threads, overall


@router.post("/v1/evaluation/admin/seed-synthetic", status_code=201)
async def seed_synthetic(body: SeedSyntheticBody, request: Request) -> dict:
    """Avaliador FAKE: gera `count` avaliações sintéticas para uma campanha pelo
    MESMO caminho de uma avaliação real (cria instance + ingest_result), validando
    o módulo de qualidade em VOLUME sem depender do agente LLM nem de massa real.
    Opcionalmente injeta sinais de NPS sintéticos (grão session)."""
    pool = _pool(request)
    producer = _kafka_producer(request)

    campaign = await _db.get_campaign(pool, body.campaign_id, body.tenant_id)
    if not campaign:
        raise HTTPException(404, detail="campaign not found")
    form = await _db.get_form(pool, campaign["form_id"], body.tenant_id)
    if not form:
        raise HTTPException(400, detail="campaign form not found")

    eval_pool   = campaign.get("evaluation_pool_id") or campaign.get("pool_id") or ""
    nps_pool    = body.pool_id or eval_pool
    passing     = float(form.get("passing_score") or 7.0)
    # Agentes sintéticos p/ a atribuição do bench (agent_key = user_id | flow_id).
    synth_humans = [("agente_humano_demo", f"user_h{i}")  for i in range(1, 4)]
    synth_ais    = [(f"agente_ia_demo_{i}", f"flow_ia{i}") for i in range(1, 4)]

    created = 0
    nps_emitted = 0
    now = datetime.now(timezone.utc)
    span_days = max(0, body.days_back)
    for _ in range(max(1, min(body.count, 1000))):
        session_id = f"synthetic_{uuid.uuid4().hex}"
        is_human = random.random() < body.human_ratio
        # Agente avaliado (atribuição p/ o bench): humano usa user_id, IA usa flow_id.
        if is_human:
            atype, akey = random.choice(synth_humans)
            seg_user, seg_flow, seg_kind = akey, "", "human"
        else:
            atype, akey = random.choice(synth_ais)
            seg_user, seg_flow, seg_kind = "", akey, "ai"
        # Data espalhada nos últimos N dias (para Trend/Comparison terem série).
        evaluated_at = (now - timedelta(days=random.uniform(0, span_days),
                                        hours=random.uniform(0, 23))).isoformat()
        inst = await _db.create_instance(
            pool, tenant_id=body.tenant_id, campaign_id=body.campaign_id,
            form_id=campaign["form_id"], session_id=session_id,
            priority=random.randint(1, 10),
        )
        crit_resps, dim_threads, overall = _rand_criterion_responses(form)
        ingest = IngestBody(
            tenant_id=body.tenant_id, instance_id=inst["id"], session_id=session_id,
            campaign_id=body.campaign_id, form_id=campaign["form_id"],
            evaluator_agent_id="synthetic_evaluator",
            overall_score=overall, max_score=10.0, normalized_score=round(overall / 10.0, 3),
            passed=overall >= passing, eval_status="submitted",
            evaluator_notes="seed sintético (avaliador fake)",
            criterion_responses=crit_resps, dimension_threads=dim_threads,
            evaluated_agent_type="human_agent" if is_human else "ai_agent",
            evaluated_at=evaluated_at,
        )
        try:
            # T7a — seeder sintético é lenient (pode marcar na em critério sem na_allowed);
            # a nota é recomputada do form como em qualquer ingest.
            await _ingest_core(pool, producer, ingest, strict_validation=False)
            created += 1
        except Exception as exc:
            logger.warning("seed-synthetic: ingest failed for %s: %s", session_id, exc)
            continue
        # Segment sintético (conversations.participants → analytics.segments): dá ao
        # bench o AGENTE avaliado por join em session_id (lentes quality/quality_criteria).
        try:
            await producer.send_and_wait("conversations.participants", json.dumps({
                "type":           "participant_left",
                "event_id":       str(uuid.uuid4()),
                "session_id":     session_id,
                "tenant_id":      body.tenant_id,
                "participant_id": f"{atype}-synthetic",
                "pool_id":        eval_pool,
                "agent_type_id":  atype,
                "agent_type":     seg_kind,
                "user_id":        seg_user,
                "flow_id":        seg_flow,
                "user_login":     seg_user,
                "role":           "primary",
                "sequence_index": 0,
                "joined_at":      evaluated_at,
                "outcome":        "resolved",
                "duration_ms":    random.randint(60_000, 600_000),
                "timestamp":      evaluated_at,
            }).encode("utf-8"))
        except Exception as exc:
            logger.warning("seed-synthetic: participant emit failed: %s", exc)
        if body.seed_nps and random.random() < 0.6:
            try:
                await producer.send_and_wait("session.signals", json.dumps({
                    "event_id": str(uuid.uuid4()),
                    "tenant_id": body.tenant_id,
                    "origin_session_id": session_id,
                    "grain": "session",
                    "segment_id": None,
                    "agent_key": akey,
                    "survey_session_id": None,
                    "pool_id": nps_pool,
                    "signals": [{"metric": "nps", "value": float(random.randint(0, 10))}],
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                }).encode("utf-8"))
                nps_emitted += 1
            except Exception as exc:
                logger.warning("seed-synthetic: nps emit failed: %s", exc)

    return {
        "campaign_id": body.campaign_id, "requested": body.count,
        "results_created": created, "nps_signals_emitted": nps_emitted,
    }


@router.post("/v1/evaluation/admin/flush-synthetic")
async def flush_synthetic(tenant_id: str, request: Request) -> dict:
    """Apaga a massa sintética (session_id LIKE 'synthetic_%') do Postgres da
    evaluation-api. O ClickHouse é limpo pelo endpoint equivalente da analytics-api."""
    pool = _pool(request)
    deleted = await _db.flush_synthetic(pool, tenant_id)
    return {"tenant_id": tenant_id, "deleted": deleted}


# ─── Results ──────────────────────────────────────────────────────────────────

class ReviewBody(BaseModel):
    decision:      str    # "approved" | "rejected"
    round:         int    # anti-replay: must equal result.current_round
    review_note:   str = ""


class LockBody(BaseModel):
    locked_by:   str = "operator"
    lock_reason: str = "manual"


@router.get("/v1/evaluation/results")
async def list_results(
    request: Request,
    tenant_id: str,
    campaign_id: str | None = None,
    session_id: str | None = None,
    eval_status: str | None = None,
    action_required: str | None = None,   # "review" | "contestation" | "any"
    pool_id: str | None = None,
    evaluator_id: str | None = None,
    locked: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    List evaluation results with optional filters.
    Pass Authorization: Bearer <jwt> to get personalized `available_actions` per row.
    """
    db_pool = _pool(request)
    jwt_payload = _decode_jwt_optional(request)

    rows = await _db.list_results(
        db_pool, tenant_id,
        campaign_id=campaign_id,
        session_id=session_id,
        eval_status=eval_status,
        action_required=action_required,
        pool_id=pool_id,
        evaluator_id=evaluator_id,
        locked=locked,
        limit=limit,
        offset=offset,
    )

    # Compute available_actions for each row (O(1) — pure ABAC, no DB round-trip)
    # We need the campaign's pool_id for scope enforcement; cache per campaign_id
    campaign_pool_cache: dict[str, str | None] = {}
    for row in rows:
        c_id = row.get("campaign_id")
        if c_id not in campaign_pool_cache:
            if c_id:
                campaign = await _db.get_campaign(db_pool, c_id, tenant_id)
                campaign_pool_cache[c_id] = campaign.get("pool_id") if campaign else None
            else:
                campaign_pool_cache[c_id] = None
        row_pool_id = campaign_pool_cache.get(c_id)
        row["available_actions"] = _compute_available_actions(row, jwt_payload, row_pool_id)
        _expose_result_id(row)

    return {"tenant_id": tenant_id, "results": rows, "count": len(rows)}


@router.get("/v1/evaluation/results/{result_id}")
async def get_result(
    result_id: str,
    tenant_id: str,
    request: Request,
) -> dict:
    """
    Returns the result with server-computed `available_actions`.
    Pass Authorization: Bearer <jwt> to get personalized button state.
    The UI should never compute permissions locally — use this field only.
    """
    pool = _pool(request)
    row = await _db.get_result(pool, result_id, tenant_id)
    if not row:
        raise HTTPException(404, detail="result not found")

    # Resolve pool_id from campaign for scope checks
    pool_id: str | None = None
    if row.get("campaign_id"):
        campaign = await _db.get_campaign(pool, row["campaign_id"], tenant_id)
        pool_id = campaign.get("pool_id") if campaign else None

    # ABAC permission check from JWT (optional — anonymous callers see empty actions)
    jwt_payload = _decode_jwt_optional(request)
    available_actions = _compute_available_actions(row, jwt_payload, pool_id)

    result_with_actions = dict(row)
    _expose_result_id(result_with_actions)
    result_with_actions["available_actions"] = available_actions
    if available_actions and row.get("deadline_at"):
        result_with_actions["action_context"] = {
            "deadline_at":     row["deadline_at"].isoformat() if hasattr(row["deadline_at"], "isoformat") else row["deadline_at"],
            "round":           row.get("current_round", 0),
        }
    return result_with_actions


@router.get("/v1/evaluation/results/{result_id}/criteria")
async def get_criteria(result_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    rows = await _db.list_criterion_responses(pool, result_id, tenant_id)
    return {"result_id": result_id, "criterion_responses": rows, "count": len(rows)}


@router.post("/v1/evaluation/results/{result_id}/review")
def _deprecated_arc6(name: str) -> None:
    """5d — endpoints Arc 6 (result-level review + /contestations) substituídos pelo
    contrato único Arc 13 (/v1/evaluation/instances/{id}/contest|review). Mantidos
    funcionais até a migração de UI (T9/T10); então removidos."""
    logger.warning(
        "DEPRECATED Arc6 endpoint '%s' — use /v1/evaluation/instances/{id}/contest|review (T5).",
        name,
    )


async def review_result(result_id: str, tenant_id: str, body: ReviewBody, request: Request) -> dict:
    """
    DEPRECATED (5d) — use POST /v1/evaluation/instances/{id}/review (mantida/revisada).
    Human reviewer approves or rejects an evaluation result.
    Anti-replay: body.round must equal result.current_round (409 on mismatch).
    """
    _deprecated_arc6("results/{id}/review")
    pool = _pool(request)
    redis_client = _redis(request)

    # Identity from JWT
    jwt_payload = _decode_jwt(request)
    caller_user_id: str = jwt_payload["sub"]

    allowed_decisions = {"approved", "rejected"}
    if body.decision not in allowed_decisions:
        raise HTTPException(400, detail=f"decision must be one of {allowed_decisions}")

    # Load result and guard
    result = await _db.get_result(pool, result_id, tenant_id)
    if not result:
        raise HTTPException(404, detail="result not found")
    if result["eval_status"] == "locked":
        raise HTTPException(409, detail="result is locked, no further actions allowed")

    # Anti-replay: round must match current workflow round
    if body.round != result.get("current_round", 0):
        raise HTTPException(
            409,
            detail=f"round mismatch: expected {result.get('current_round', 0)}, got {body.round}",
        )

    # Verify ABAC permission (revisar field in module_config.evaluation)
    campaign = await _db.get_campaign(pool, result["campaign_id"], tenant_id) if result.get("campaign_id") else None
    pool_id = campaign.get("pool_id") if campaign else None
    if not _check_abac_permission(jwt_payload, "revisar", pool_id):
        raise HTTPException(403, detail="caller lacks 'revisar' permission for this campaign/pool")

    # Persist decision
    row = await _db.update_result(
        pool, result_id, tenant_id,
        eval_status="reviewed",
        reviewer_agent_id=caller_user_id,
        reviewer_outcome=body.decision,
        reviewer_notes=body.review_note,
        reviewed_at=datetime.now(tz=timezone.utc),
    )
    if not row:
        raise HTTPException(404, detail="result not found")

    # Write to ContextStore so the suspended workflow YAML choice step can branch on it
    if result.get("session_id"):
        await _write_ctx(redis_client, tenant_id, result["session_id"], {
            "session.review_decision": body.decision,
            "session.reviewer_id":     caller_user_id,
            "session.round_echoed":    body.round,
        })

    # Resume workflow (fire-and-forget)
    if result.get("resume_token"):
        await _resume_workflow(result["resume_token"], tenant_id)

    return row


@router.post("/v1/evaluation/results/{result_id}/lock")
async def lock_result_endpoint(result_id: str, body: LockBody, request: Request) -> dict:
    """
    Permanently lock a result. Called by:
    - Admin operators (X-Admin-Token) for manual locks
    - evaluation_lock MCP tool (called from congelar_resultado workflow step, no admin token)
    Returns 409 if result is already locked (idempotent for workflow retries).
    """
    pool = _pool(request)
    # Allow workflow calls without admin token; admin token gates manual/admin locks
    # (no hard auth requirement — the endpoint is internal, firewall-protected in production)
    row = await _db.lock_result(
        pool, result_id,
        lock_reason=body.lock_reason,
        locked_by=body.locked_by,
    )
    if row is None:
        # lock_result's WHERE includes eval_status != 'locked', so None means
        # either result doesn't exist, or it's already locked — check which:
        existing = await _db.get_result_by_id(pool, result_id)
        if existing is None:
            raise HTTPException(404, detail="result not found")
        raise HTTPException(409, detail="result is already locked")
    return row


# ─── Contestations ────────────────────────────────────────────────────────────

class ContestationCreate(BaseModel):
    tenant_id:            str
    result_id:            str
    instance_id:          str
    session_id:           str
    contestation_reason:  str = ""
    round:                int = 0   # anti-replay: must equal result.current_round


class AdjudicateBody(BaseModel):
    adjudicated_by:    str
    status:            str   # "accepted" | "rejected" | "withdrawn"
    adjudication_notes: str = ""
    adjusted_score:    float | None = None


@router.get("/v1/evaluation/contestations")
async def list_contestations(
    request: Request,
    tenant_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    pool = _pool(request)
    rows = await _db.list_contestations(pool, tenant_id, status=status, limit=limit, offset=offset)
    return {"tenant_id": tenant_id, "contestations": rows, "count": len(rows)}


@router.post("/v1/evaluation/contestations", status_code=201)
async def create_contestation(body: ContestationCreate, request: Request) -> dict:
    """
    DEPRECATED (5d) — use POST /v1/evaluation/instances/{id}/contest (por critério).
    File a contestation on an evaluation result.
    Anti-replay: body.round must equal result.current_round (409 on mismatch).
    """
    _deprecated_arc6("contestations")
    pool = _pool(request)
    producer = _kafka_producer(request)
    redis_client = _redis(request)

    # Identity from JWT
    jwt_payload = _decode_jwt(request)
    caller_user_id: str = jwt_payload["sub"]

    # Validate result
    result = await _db.get_result(pool, body.result_id, body.tenant_id)
    if not result:
        raise HTTPException(404, detail="result not found")
    if result["eval_status"] == "locked":
        raise HTTPException(409, detail="result is locked, cannot contest")

    # Anti-replay
    if body.round != result.get("current_round", 0):
        raise HTTPException(
            409,
            detail=f"round mismatch: expected {result.get('current_round', 0)}, got {body.round}",
        )

    # Verify ABAC permission (contestar field in module_config.evaluation)
    campaign = await _db.get_campaign(pool, result["campaign_id"], body.tenant_id) if result.get("campaign_id") else None
    pool_id = campaign.get("pool_id") if campaign else None
    if not _check_abac_permission(jwt_payload, "contestar", pool_id):
        raise HTTPException(403, detail="caller lacks 'contestar' permission for this campaign/pool")

    row = await _db.create_contestation(
        pool,
        tenant_id=body.tenant_id,
        result_id=body.result_id,
        instance_id=body.instance_id,
        session_id=body.session_id,
        contested_by=caller_user_id,
        contestation_reason=body.contestation_reason,
    )

    # Write to ContextStore so workflow choice step can see "contested"
    if result.get("session_id"):
        await _write_ctx(redis_client, body.tenant_id, result["session_id"], {
            "session.review_decision": "contested",
            "session.reviewer_id":     caller_user_id,
            "session.round_echoed":    body.round,
        })

    # Resume workflow (fire-and-forget)
    if result.get("resume_token"):
        await _resume_workflow(result["resume_token"], body.tenant_id)

    await _kafka.emit_contestation_opened(
        producer, settings.evaluation_topic,
        contestation_id=row["id"],
        result_id=body.result_id,
        instance_id=body.instance_id,
        tenant_id=body.tenant_id,
        session_id=body.session_id,
        contested_by=caller_user_id,
    )
    return row


@router.get("/v1/evaluation/contestations/{contestation_id}")
async def get_contestation(contestation_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.contestations WHERE id=$1 AND tenant_id=$2",
            contestation_id, tenant_id,
        )
    if not row:
        raise HTTPException(404, detail="contestation not found")
    return dict(row)


@router.post("/v1/evaluation/contestations/{contestation_id}/adjudicate")
async def adjudicate(contestation_id: str, tenant_id: str, body: AdjudicateBody, request: Request) -> dict:
    """DEPRECATED (5d) — adjudicação por X-Admin-Token substituída pela revisão humana
    ABAC em /v1/evaluation/instances/{id}/review."""
    _deprecated_arc6("contestations/{id}/adjudicate")
    pool = _pool(request)
    producer = _kafka_producer(request)
    allowed = {"accepted", "rejected", "withdrawn"}
    if body.status not in allowed:
        raise HTTPException(400, detail=f"status must be one of {allowed}")
    row = await _db.adjudicate_contestation(
        pool, contestation_id, tenant_id,
        status=body.status,
        adjudicated_by=body.adjudicated_by,
        adjudication_notes=body.adjudication_notes,
        adjusted_score=body.adjusted_score,
    )
    if not row:
        raise HTTPException(404, detail="contestation not found")
    await _kafka.emit_contestation_closed(
        producer, settings.evaluation_topic,
        contestation_id=contestation_id,
        result_id=row["result_id"],
        tenant_id=tenant_id,
        adjudicated_status=body.status,
        adjudicated_by=body.adjudicated_by,
    )
    return row


# ─── Sampling check ───────────────────────────────────────────────────────────

class SampleCheckBody(BaseModel):
    tenant_id:    str
    campaign_id:  str
    session_id:   str
    session_meta: dict = Field(default_factory=dict)
    counter:      int = 0


@router.post("/v1/evaluation/sample")
async def check_sample(body: SampleCheckBody, request: Request) -> dict:
    pool = _pool(request)
    campaign = await _db.get_campaign(pool, body.campaign_id, body.tenant_id)
    if not campaign:
        raise HTTPException(404, detail="campaign not found")
    if campaign["status"] != "active":
        return {"should_sample": False, "reason": f"campaign status={campaign['status']}"}

    # Hard filter: if campaign is scoped to a specific pool being evaluated,
    # only sessions from that pool are eligible.
    evaluation_pool_id = campaign.get("evaluation_pool_id")
    if evaluation_pool_id and body.session_meta.get("pool_id") != evaluation_pool_id:
        return {
            "should_sample": False,
            "reason":        f"session pool {body.session_meta.get('pool_id')!r} != evaluation_pool_id {evaluation_pool_id!r}",
        }

    sampling_rules = campaign.get("sampling_rules") or {}
    sampled = should_sample(
        body.session_id,
        body.session_meta,
        sampling_rules,
        counter=body.counter,
    )
    priority = compute_priority(body.session_meta, sampling_rules)
    return {
        "should_sample": sampled,
        "priority":      priority,
        "campaign_id":   body.campaign_id,
        "session_id":    body.session_id,
    }


# ─── Reports ──────────────────────────────────────────────────────────────────

@router.get("/v1/evaluation/reports/campaign/{campaign_id}")
async def campaign_report(
    campaign_id: str,
    tenant_id: str,
    request: Request,
) -> dict:
    pool = _pool(request)
    campaign = await _db.get_campaign(pool, campaign_id, tenant_id)
    if not campaign:
        raise HTTPException(404, detail="campaign not found")

    async with pool.acquire() as conn:
        # Summary by eval_status
        status_rows = await conn.fetch(
            """
            SELECT eval_status, COUNT(*) AS count,
                   AVG(overall_score) AS avg_score,
                   AVG(normalized_score) AS avg_normalized,
                   SUM(CASE WHEN passed THEN 1 ELSE 0 END) AS passed_count
              FROM evaluation.results
             WHERE campaign_id=$1 AND tenant_id=$2
             GROUP BY eval_status
            """,
            campaign_id, tenant_id,
        )
        # Per-criterion averages
        criterion_rows = await conn.fetch(
            """
            SELECT criterion_id, criterion_name, dimension_id,
                   COUNT(*) AS responses,
                   AVG(score) FILTER (WHERE NOT na) AS avg_score,
                   SUM(CASE WHEN na THEN 1 ELSE 0 END) AS na_count
              FROM evaluation.criterion_responses
             WHERE campaign_id=$1 AND tenant_id=$2
             GROUP BY criterion_id, criterion_name, dimension_id
             ORDER BY dimension_id, criterion_id
            """,
            campaign_id, tenant_id,
        )

    return {
        "campaign": campaign,
        "status_breakdown": [dict(r) for r in status_rows],
        "criteria_breakdown": [dict(r) for r in criterion_rows],
    }


@router.get("/v1/evaluation/reports/agent")
async def agent_report(
    request: Request,
    tenant_id: str,
    pool_id: str | None = None,
    campaign_id: str | None = None,
    from_dt: str | None = None,
    to_dt: str | None = None,
    limit: int = 50,
) -> dict:
    pool = _pool(request)
    cond = "WHERE r.tenant_id=$1"
    args: list[Any] = [tenant_id]
    if pool_id:
        args.append(pool_id)
        # Note: evaluator_agent_id is the agent instance; can't directly join pool here
        # Use campaign's pool_id as proxy via campaign table
        cond += f" AND c.pool_id=${len(args)}"
    if campaign_id:
        args.append(campaign_id)
        cond += f" AND r.campaign_id=${len(args)}"
    if from_dt:
        args.append(from_dt)
        cond += f" AND r.submitted_at >= ${len(args)}"
    if to_dt:
        args.append(to_dt)
        cond += f" AND r.submitted_at <= ${len(args)}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT r.evaluator_agent_id,
                   COUNT(*) AS total_evaluations,
                   AVG(r.overall_score) AS avg_score,
                   AVG(r.normalized_score) AS avg_normalized,
                   SUM(CASE WHEN r.passed THEN 1 ELSE 0 END) AS passed_count,
                   SUM(CASE WHEN r.eval_status='contested' THEN 1 ELSE 0 END) AS contestation_count,
                   SUM(CASE WHEN r.comparison_mode THEN 1 ELSE 0 END) AS comparison_count
              FROM evaluation.results r
              LEFT JOIN evaluation.campaigns c ON c.id = r.campaign_id
             {cond}
             GROUP BY r.evaluator_agent_id
             ORDER BY total_evaluations DESC
             LIMIT ${len(args)+1}
            """,
            *args, limit,
        )

    return {
        "tenant_id": tenant_id,
        "agents":    [dict(r) for r in rows],
        "count":     len(rows),
    }
