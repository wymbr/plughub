"""
router.py
FastAPI routes for the Outbound Mailing API.

Endpoints — todos com portão desde a AUT-60 (2026-09-29). Antes, o header `X-Tenant-ID`
sozinho era a credencial inteira: pela borda pública (5174), com um header que qualquer um
escreve, `GET /v1/mailings` devolvia as listas — e as `entries` são contato de cliente.
O tenant vem do TOKEN de quem chama; o header só decide na porta de SERVIÇO.

  Mailings   — CRUD under /v1/mailings
  Entries    — POST /v1/mailings/{id}/entries (backing of mailing_add) + list
  Campaigns  — CRUD under /v1/campaigns
  Drain      — POST /v1/campaigns/{id}/drain  (atomic claim of a batch)
  Delivery   — POST /v1/deliveries/{id}/result + GET /v1/campaigns/{id}/deliveries

Pydantic models mirror the Zod contract in @plughub/schemas/outbound.ts.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

import hmac

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from plughub_authz import abac_can, bearer_from_header, verify_user_jwt
from pydantic import BaseModel, Field

from .config import get_settings
from .importer import (
    ImportParseError,
    RowCapExceeded,
    batch_ingest,
    parse_file,
)
from .db import (
    db_add_entry,
    db_contact_eligibility,
    db_create_campaign,
    db_create_mailing,
    db_create_policy,
    db_delete_mailing,
    db_delete_policy,
    db_drain_campaign,
    db_get_campaign,
    db_get_mailing,
    db_list_campaigns,
    db_list_deliveries,
    db_list_entries,
    db_list_mailings,
    db_list_policies,
    db_set_delivery_result,
    db_subject_erase,
    db_subject_export,
    db_unsubscribe,
    db_update_campaign,
    db_update_mailing,
    db_update_policy,
)

logger = logging.getLogger("plughub.mailing.router")


def _pool(request: Request):
    return request.app.state.pool


def _tenant(x_tenant_id: str | None) -> str:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header is required")
    return x_tenant_id


# ── Credencial e capacidade (AUT-60, 2026-09-29) ──────────────────────────────
#
# O catálogo (`infra/modules.yaml`, módulo `outbound`) decide o mapa, não a intuição:
#   `configurar` → "Criar e editar mailings e campanhas" (+ importar arquivo)
#   `operacao`   → "Monitorar entregas"
# Ler (listas, entradas, entregas) aceita QUALQUER um dos dois: quem monitora entregas
# precisa ver a campanha, e quem configura precisa ver o que configurou. Os dois são
# `scopable: false` — quem opera outbound, opera o tenant inteiro.
#
# As quatro ações de AGENTE — drenar, registrar resultado, elegibilidade, descadastro —
# são SÓ SERVIÇO: nenhuma tela as chama (as tools `campaign_drain`,
# `campaign_delivery_result`, `contact_eligibility_check` e `mailing_unsubscribe` do
# mcp-server são o único chamador). Dar a elas um campo de usuário abriria à tela um
# efeito que nenhuma tela produz — drenar é CONTATAR cliente.

CONFIGURAR = (("configurar", "read_write"),)
VER        = (("configurar", "read_write"), ("operacao", "read_only"))
SO_SERVICO = ()


def _principal(request: Request, grants: tuple, what: str, x_tenant_id: str | None) -> str:
    """Decide QUEM chama e se pode — e devolve o tenant que vale para a chamada.

    Mesmo desenho da SCH-01 (scheduler-api), com uma diferença deliberada: o header
    `X-Tenant-ID` de um USUÁRIO que divergir do token é 403 `tenant_mismatch`, não é
    ignorado em silêncio (regra da TNT-01) — header divergente é sintoma, não detalhe.

      1. `X-Service-Token` (ADITIVO): mcp-server e seeds. Token não configurado no serviço
         FECHA esta porta; nunca a abre. O tenant vem do header — não há token de onde tirá-lo.
      2. `Bearer` do auth-api + algum dos `grants` de `outbound`. Tenant = o do TOKEN.

    Sem nenhuma: 401. `grants` vazio (SO_SERVICO) com Bearer válido: 403. Sem
    `PLUGHUB_MAILING_JWT_SECRET`: 503 nomeando a env.
    """
    s = get_settings()
    svc = request.headers.get("x-service-token")
    if svc and s.service_token and hmac.compare_digest(svc, s.service_token):
        tenant = _tenant(x_tenant_id)
        logger.info("mailing: %s por servico (tenant=%s)", what, tenant)
        return tenant
    if svc and not s.service_token:
        logger.error("mailing: X-Service-Token recebido, mas PLUGHUB_MAILING_SERVICE_TOKEN "
                     "nao esta configurado — %s recusado", what)

    token = bearer_from_header(request.headers.get("authorization"))
    if not token:
        raise HTTPException(status_code=401, detail=f"{what} exige credencial")
    if not s.jwt_secret:
        raise HTTPException(
            status_code=503,
            detail="mailing-api sem PLUGHUB_MAILING_JWT_SECRET — nao consigo verificar credencial",
        )
    claims = verify_user_jwt(token, s.jwt_secret)
    if not claims:
        raise HTTPException(status_code=401, detail="credencial invalida ou expirada")
    if not grants:
        logger.warning("mailing NEGADO: sub=%s %s — acao so de servico", claims.get("sub"), what)
        raise HTTPException(status_code=403, detail=f"{what} e acao de servico, nao de usuario")
    if not any(abac_can(claims, "outbound", campo, minimo) for campo, minimo in grants):
        pedido = " ou ".join(f"outbound.{c} ({m})" for c, m in grants)
        logger.warning("mailing NEGADO: sub=%s %s — sem %s", claims.get("sub"), what, pedido)
        raise HTTPException(status_code=403, detail=f"{what} exige {pedido}")
    tenant = str(claims.get("tenant_id") or "")
    if not tenant:
        raise HTTPException(status_code=401, detail="credencial sem tenant")
    if x_tenant_id and x_tenant_id != tenant:
        logger.warning("mailing RECUSA: sub=%s pediu tenant=%s com credencial de %s (%s)",
                       claims.get("sub"), x_tenant_id, tenant, what)
        raise HTTPException(status_code=403, detail="tenant_mismatch")
    return tenant


def _exige_credencial(request: Request) -> None:
    """Dependência do ROUTER: sem credencial VÁLIDA, 401 antes de qualquer coisa.

    Existe porque o `_principal` decide DENTRO do handler — depois da validação do corpo —,
    e então um anônimo numa escrita recebia 422, que se lê como "rota aberta com corpo
    errado". Aqui decide só "há credencial?"; capacidade e tenant continuam por rota, no
    `_principal`. E é também o que torna impossível esquecer: rota nova herda a exigência.
    """
    s = get_settings()
    svc = request.headers.get("x-service-token")
    if svc and s.service_token and hmac.compare_digest(svc, s.service_token):
        return
    token = bearer_from_header(request.headers.get("authorization"))
    if not token:
        raise HTTPException(status_code=401, detail="credencial exigida")
    if not s.jwt_secret:
        raise HTTPException(
            status_code=503,
            detail="mailing-api sem PLUGHUB_MAILING_JWT_SECRET — nao consigo verificar credencial",
        )
    if not verify_user_jwt(token, s.jwt_secret):
        raise HTTPException(status_code=401, detail="credencial invalida ou expirada")


router = APIRouter(dependencies=[Depends(_exige_credencial)])


# ── Contract models (mirror @plughub/schemas/outbound.ts) ─────────────────────

class CreateMailingBody(BaseModel):
    name:              str
    description:       str | None = None
    dedup_policy:      str | None = None   # customer | customer_context | none
    metadata_contract: str | None = None
    entry_ttl_seconds: int | None = None
    column_map:        dict[str, Any] | None = None   # Fase 4 — import parsing config


class UpdateMailingBody(BaseModel):
    name:              str | None = None
    description:       str | None = None
    dedup_policy:      str | None = None
    metadata_contract: str | None = None
    entry_ttl_seconds: int | None = None
    column_map:        dict[str, Any] | None = None


class AddEntryBody(BaseModel):
    customer_id: str | None = None
    contacts:    dict[str, str] = Field(default_factory=dict)
    metadata:    dict[str, Any]
    dedup_key:   str | None = None
    source:      str | None = None
    ttl_seconds: int | None = None


class OrderField(BaseModel):
    path: str
    dir:  str = "asc"    # asc | desc
    type: str = "text"   # text | number


class CreateCampaignBody(BaseModel):
    name:                str
    mailing_id:          str
    pool_id:             str
    selection:           dict[str, Any] | None = None
    ordering:            list[OrderField] = Field(default_factory=list)
    channel_policy:      dict[str, Any] = Field(default_factory=dict)
    contact_calendar_id: str | None = None
    transactional:       bool = False
    batch_size:          int = 50
    retry:               dict[str, Any] = Field(default_factory=dict)
    agenda_id:           str | None = None


class UpdateCampaignBody(BaseModel):
    name:                str | None = None
    pool_id:             str | None = None
    selection:           dict[str, Any] | None = None
    ordering:            list[OrderField] | None = None
    channel_policy:      dict[str, Any] | None = None
    contact_calendar_id: str | None = None
    transactional:       bool | None = None
    batch_size:          int | None = None
    retry:               dict[str, Any] | None = None
    agenda_id:           str | None = None
    status:              str | None = None   # active | paused | completed | archived


class DrainBody(BaseModel):
    limit: int | None = None


class DeliveryResultBody(BaseModel):
    result:          str   # claimed|pending|contacted|responded|failed|skipped_ineligible|suppressed
    session_id:      str | None = None
    root_session_id: str | None = None
    error:           str | None = None


# ── Mailings — CRUD ───────────────────────────────────────────────────────────

@router.post("/v1/mailings", status_code=201)
async def create_mailing(
    body: CreateMailingBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, CONFIGURAR, "criar mailing", x_tenant_id)
    return await db_create_mailing(_pool(request), tenant, body.model_dump(exclude_none=True))


@router.get("/v1/mailings")
async def list_mailings(
    request: Request, x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, VER, "listar mailings", x_tenant_id)
    items = await db_list_mailings(_pool(request), tenant)
    return {"mailings": items, "total": len(items)}


@router.get("/v1/mailings/{mailing_id}")
async def get_mailing(
    mailing_id: str, request: Request, x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, VER, "ler mailing", x_tenant_id)
    m = await db_get_mailing(_pool(request), tenant, mailing_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mailing not found")
    return m


@router.patch("/v1/mailings/{mailing_id}")
async def update_mailing(
    mailing_id: str, body: UpdateMailingBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, CONFIGURAR, "editar mailing", x_tenant_id)
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    m = await db_update_mailing(_pool(request), tenant, mailing_id, data)
    if not m:
        raise HTTPException(status_code=404, detail="Mailing not found")
    return m


@router.delete("/v1/mailings/{mailing_id}", status_code=204)
async def delete_mailing(
    mailing_id: str, request: Request, x_tenant_id: str | None = Header(default=None),
) -> None:
    tenant = _principal(request, CONFIGURAR, "apagar mailing", x_tenant_id)
    ok = await db_delete_mailing(_pool(request), tenant, mailing_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Mailing not found")


# ── Entries — mailing_add + list ──────────────────────────────────────────────

@router.post("/v1/mailings/{mailing_id}/entries", status_code=201)
async def add_entry(
    mailing_id: str, body: AddEntryBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, CONFIGURAR, "adicionar entrada", x_tenant_id)
    mailing = await db_get_mailing(_pool(request), tenant, mailing_id)
    if not mailing:
        raise HTTPException(status_code=404, detail="Mailing not found")
    return await db_add_entry(_pool(request), tenant, mailing, body.model_dump(exclude_none=True))


@router.get("/v1/mailings/{mailing_id}/entries")
async def list_entries(
    mailing_id: str, request: Request,
    x_tenant_id: str | None = Header(default=None), status: str | None = None,
) -> dict:
    tenant = _principal(request, VER, "listar entradas", x_tenant_id)
    items = await db_list_entries(_pool(request), tenant, mailing_id, status)
    return {"entries": items, "total": len(items)}


# ── Fase 4 — Camada A: batch ingest (format-agnostic) ─────────────────────────

class IngestRowBody(BaseModel):
    customer_id: str | None = None
    anchors:     list[dict[str, str]] = Field(default_factory=list)   # [{kind, value}]
    contacts:    dict[str, str] = Field(default_factory=dict)
    metadata:    dict[str, Any] = Field(default_factory=dict)
    dedup_key:   str | None = None


class BatchIngestBody(BaseModel):
    rows:    list[IngestRowBody]
    resolve: bool = True
    source:  str | None = None


@router.post("/v1/mailings/{mailing_id}/entries/batch")
async def batch_ingest_entries(
    mailing_id: str, body: BatchIngestBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    """Camada A — ingest normalized rows (resolve + validate + upsert + report).
    Format-agnostic: the file importer is one producer; any future source posts here."""
    tenant = _principal(request, CONFIGURAR, "ingerir entradas", x_tenant_id)
    mailing = await db_get_mailing(_pool(request), tenant, mailing_id)
    if not mailing:
        raise HTTPException(status_code=404, detail="Mailing not found")
    identity = getattr(request.app.state, "identity", None)
    rows = [r.model_dump() for r in body.rows]
    return await batch_ingest(
        _pool(request), tenant, mailing, rows, identity, body.resolve, body.source,
    )


# ── Fase 4 — Camada B: file import (CSV/xlsx → column_map → Camada A) ──────────

@router.post("/v1/mailings/{mailing_id}/import")
async def import_file(
    mailing_id: str, request: Request,
    file: UploadFile = File(...),
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    """Camada B — parse a CSV/xlsx via the mailing's column_map into ingest rows, then
    hand off to Camada A. Synchronous with a row cap. Rejected rows are reported with
    their source line number (never abort the whole file)."""
    tenant = _principal(request, CONFIGURAR, "importar arquivo", x_tenant_id)
    mailing = await db_get_mailing(_pool(request), tenant, mailing_id)
    if not mailing:
        raise HTTPException(status_code=404, detail="Mailing not found")
    column_map = mailing.get("column_map")
    if not column_map:
        raise HTTPException(
            status_code=400,
            detail="mailing has no column_map configured — set it before importing",
        )
    content = await file.read()
    settings = request.app.state.settings
    max_rows = getattr(settings, "import_max_rows", 5000)
    try:
        rows, lines = parse_file(file.filename or "", content, column_map, max_rows)
    except RowCapExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except ImportParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    import_id = uuid4().hex[:16]
    identity = getattr(request.app.state, "identity", None)
    report = await batch_ingest(
        _pool(request), tenant, mailing, rows, identity, True, f"import:{import_id}",
    )
    # Remap batch rejections (row index) → source line numbers for the file report.
    report["rejected"] = [
        {"row": lines[r["index"]], "reason": r["reason"]} for r in report["rejected"]
    ]
    report["import_id"] = import_id
    return report


# ── Campaigns — CRUD ──────────────────────────────────────────────────────────

@router.post("/v1/campaigns", status_code=201)
async def create_campaign(
    body: CreateCampaignBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, CONFIGURAR, "criar campanha", x_tenant_id)
    # Validate the referenced mailing exists (tenant-scoped).
    if not await db_get_mailing(_pool(request), tenant, body.mailing_id):
        raise HTTPException(status_code=400, detail="mailing_id not found for tenant")
    return await db_create_campaign(_pool(request), tenant, body.model_dump(exclude_none=True))


@router.get("/v1/campaigns")
async def list_campaigns(
    request: Request, x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, VER, "listar campanhas", x_tenant_id)
    items = await db_list_campaigns(_pool(request), tenant)
    return {"campaigns": items, "total": len(items)}


@router.get("/v1/campaigns/{campaign_id}")
async def get_campaign(
    campaign_id: str, request: Request, x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, VER, "ler campanha", x_tenant_id)
    c = await db_get_campaign(_pool(request), tenant, campaign_id)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


@router.patch("/v1/campaigns/{campaign_id}")
async def update_campaign(
    campaign_id: str, body: UpdateCampaignBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, CONFIGURAR, "editar campanha", x_tenant_id)
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    c = await db_update_campaign(_pool(request), tenant, campaign_id, data)
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


# ── Drain + delivery result ───────────────────────────────────────────────────

@router.post("/v1/campaigns/{campaign_id}/drain")
async def drain_campaign(
    campaign_id: str, body: DrainBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, SO_SERVICO, "drenar campanha", x_tenant_id)
    campaign = await db_get_campaign(_pool(request), tenant, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign["status"] != "active":
        # Draining a non-active campaign is a no-op batch, not an error (the skill
        # sees an empty batch and completes) — but be explicit in the log.
        logger.info("drain of non-active campaign %s (status=%s) → empty",
                    campaign_id, campaign["status"])
        return {"campaign_id": campaign_id, "drained": []}
    drained = await db_drain_campaign(_pool(request), tenant, campaign, body.limit)
    return {"campaign_id": campaign_id, "drained": drained}


@router.post("/v1/deliveries/{delivery_id}/result")
async def set_delivery_result(
    delivery_id: str, body: DeliveryResultBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, SO_SERVICO, "registrar resultado de entrega", x_tenant_id)
    d = await db_set_delivery_result(
        _pool(request), tenant, delivery_id, body.model_dump(exclude_none=True),
    )
    if not d:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return d


@router.get("/v1/campaigns/{campaign_id}/deliveries")
async def list_deliveries(
    campaign_id: str, request: Request,
    x_tenant_id: str | None = Header(default=None), limit: int = 200,
) -> dict:
    tenant = _principal(request, VER, "listar entregas", x_tenant_id)
    items = await db_list_deliveries(_pool(request), tenant, campaign_id, limit)
    return {"deliveries": items, "total": len(items)}


# ── Fase 2 — contact governance ───────────────────────────────────────────────

Window = str | int   # "24h" | "7d" | 86400


class FrequencyCap(BaseModel):
    window:      Window
    max:         int
    per_channel: bool = False


class ChannelCap(BaseModel):
    window: Window
    max:    int


class CreateContactPolicyBody(BaseModel):
    scope:            str                    # tenant | campaign
    scope_id:         str | None = None
    frequency_caps:   list[FrequencyCap] = Field(default_factory=list)
    quarantine_after: Window | None = None
    channel_caps:     dict[str, ChannelCap] = Field(default_factory=dict)


class UpdateContactPolicyBody(BaseModel):
    frequency_caps:   list[FrequencyCap] | None = None
    quarantine_after: Window | None = None
    channel_caps:     dict[str, ChannelCap] | None = None


class EligibilityBody(BaseModel):
    customer_id: str
    channel:     str
    campaign_id: str | None = None
    claim:       bool = True
    at:          datetime | None = None


class UnsubscribeBody(BaseModel):
    customer_id: str
    mailing_id:  str | None = None
    channel:     str | None = None
    scope:       str = "mailing"   # mailing | global (global → cadastro do_not_contact)


@router.post("/v1/contact-policies", status_code=201)
async def create_policy(
    body: CreateContactPolicyBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, CONFIGURAR, "criar politica de contato", x_tenant_id)
    if body.scope not in ("tenant", "campaign"):
        raise HTTPException(status_code=400, detail="scope must be 'tenant' or 'campaign'")
    if body.scope == "campaign" and not body.scope_id:
        raise HTTPException(status_code=400, detail="scope_id required for campaign scope")
    return await db_create_policy(_pool(request), tenant, body.model_dump(mode="json", exclude_none=True))


@router.get("/v1/contact-policies")
async def list_policies(
    request: Request, x_tenant_id: str | None = Header(default=None), scope: str | None = None,
) -> dict:
    tenant = _principal(request, VER, "listar politicas de contato", x_tenant_id)
    items = await db_list_policies(_pool(request), tenant, scope)
    return {"policies": items, "total": len(items)}


@router.patch("/v1/contact-policies/{policy_id}")
async def update_policy(
    policy_id: str, body: UpdateContactPolicyBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, CONFIGURAR, "editar politica de contato", x_tenant_id)
    data = body.model_dump(mode="json", exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    p = await db_update_policy(_pool(request), tenant, policy_id, data)
    if not p:
        raise HTTPException(status_code=404, detail="Policy not found")
    return p


@router.delete("/v1/contact-policies/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: str, request: Request, x_tenant_id: str | None = Header(default=None),
) -> None:
    tenant = _principal(request, CONFIGURAR, "apagar politica de contato", x_tenant_id)
    ok = await db_delete_policy(_pool(request), tenant, policy_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Policy not found")


@router.post("/v1/contact/eligibility")
async def contact_eligibility(
    body: EligibilityBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, SO_SERVICO, "checar elegibilidade", x_tenant_id)
    req = body.model_dump(exclude_none=True)   # keep `at` as datetime (not json mode)
    calendar = getattr(request.app.state, "calendar", None)
    identity = getattr(request.app.state, "identity", None)
    return await db_contact_eligibility(_pool(request), tenant, req, calendar, identity)


class SubjectExportBody(BaseModel):
    customer_ids:   list[str] = []
    contact_values: list[str] = []


@router.post("/v1/data-subject/export")
async def data_subject_export(
    body: SubjectExportBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    """AUD-03 — dossiê de acesso do titular (LGPD art. 18, II). SÓ SERVIÇO: quem o
    monta é a analytics-api, que confere o DPO e grava a trilha; nenhuma tela chama."""
    tenant = _principal(request, SO_SERVICO, "exportar dados do titular", x_tenant_id)
    return await db_subject_export(_pool(request), tenant, body.customer_ids, body.contact_values)


class SubjectEraseBody(BaseModel):
    customer_ids:   list[str] = []
    contact_values: list[str] = []
    marker:         str


@router.post("/v1/data-subject/erase")
async def data_subject_erase(
    body: SubjectEraseBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    """AUD-06 — eliminação do titular (LGPD art. 18, VI). SÓ SERVIÇO, como o export: a
    analytics-api confere o DPO (`audit.data_requests` em escrita) e grava a trilha."""
    tenant = _principal(request, SO_SERVICO, "eliminar dados do titular", x_tenant_id)
    if not body.marker.startswith("erased:"):
        raise HTTPException(422, "marker deve comecar com 'erased:'")
    return await db_subject_erase(_pool(request), tenant, body.customer_ids,
                                  body.contact_values, body.marker)


@router.post("/v1/unsubscribe")
async def unsubscribe(
    body: UnsubscribeBody, request: Request,
    x_tenant_id: str | None = Header(default=None),
) -> dict:
    tenant = _principal(request, SO_SERVICO, "descadastrar", x_tenant_id)
    identity = getattr(request.app.state, "identity", None)
    return await db_unsubscribe(_pool(request), tenant, body.model_dump(exclude_none=True), identity)
