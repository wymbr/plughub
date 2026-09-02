"""
router.py
FastAPI router for the Config API.

Routes:
  GET  /config/{namespace}/{key}?tenant_id=xxx
       Resolved value (tenant override → global default). 404 if neither exists.

  GET  /config/{namespace}?tenant_id=xxx
       All resolved keys in namespace for the given tenant.

  GET  /config?tenant_id=xxx
       All resolved config for the tenant, grouped by namespace.

  PUT  /config/{namespace}/{key}
       Body: {"tenant_id": null|"...", "value": <any>, "description": "..."}
       Upsert. tenant_id=null sets the global platform default.

  DELETE /config/{namespace}/{key}?tenant_id=xxx
       Removes an explicit entry. Returns 404 if not found.
       tenant_id=null or omitted targets the global default.

  GET  /config/{namespace}/raw?tenant_id=xxx
       Raw (non-resolved) entries explicitly set for (tenant_id, namespace).
       Useful for seeing what overrides are active.

Escrita: portao DUAL — `X-Admin-Token` (= PLUGHUB_CONFIG_ADMIN_TOKEN, caminho de
seed/sistema) OU `Authorization: Bearer <jwt>` com ABAC `config.{campo do namespace}`
em read_write. Verificado por `plughub_authz.enforce_write` (verificador canonico).
O `?admin_token=` em query string foi REMOVIDO em 2026-08-28 — segredo em URL entra
em log de acesso e de proxy; tinha um unico chamador, e era teste.

Leitura NAO tem portao, e isso e decidido: os leitores sao chamadores de runtime sem
credencial. Um portao que fechasse a leitura passaria no teste de seguranca e quebraria
o produto em silencio.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from plughub_authz import enforce_write
from pydantic import BaseModel

logger = logging.getLogger("plughub.config.router")

router = APIRouter(prefix="/config")


# ─── auth dependency for mutations (admin-token OR Bearer+ABAC) ───────────────
# G-PROBE platform-wide: o endpoint de escrita é genérico (`/{namespace}/{key}`) e
# compartilhado por várias telas. Cada namespace mapeia a um campo ABAC `config.*`.
# Gate DUAL (transição): admin-token (telas ainda não migradas) OU Bearer + ABAC do
# campo do namespace (telas migradas: Platform→`plataforma`, Masking→`masking`).

# namespaces de canais (telas ainda em admin-token) → campo `canais` (path Bearer correto,
# inativo até a migração de Channels). masking → `masking`. Default (Platform é o editor
# catch-all de config de plataforma) → `plataforma`.
_NS_FIELD_OVERRIDES = {
    "masking":      "masking",
    "audit_policy": "masking",   # MaskingPage edita masking + audit_policy → mesmo campo
    "webchat":  "channels", "webhook": "channels", "sms": "channels", "whatsapp": "channels", "voice": "channels", "webrtc": "channels",
    # Passo 2 do arco de ABAC total (2026-08-27): Dashboards deixa de cair no
    # catch-all `platform`. O MENU passa a apontar para o mesmo campo — a divergência
    # menu×backend do Channels (menu em `platform`, backend em `channels`) é o defeito
    # que este passo fecha, e repeti-lo aqui seria criá-lo de novo.
    "dashboards":   "dashboards",
}


def _ns_field(namespace: str) -> str:
    return _NS_FIELD_OVERRIDES.get(namespace, "platform")


async def _require_config_write(namespace: str, request: Request) -> None:
    """Write guard: admin-token (seed/sistema) OU Bearer + ABAC `config.{ns_field}` (read_write).

    MIGRADO para `plughub_authz` em 2026-08-28 (passo 2 da consolidação dos seis
    verificadores). Saíram daqui `_verify_hs256` (HMAC em stdlib — uma das três
    bibliotecas que faziam a MESMA verificação no repo) e `_check_config_field` com
    `.get(min_access, 0)`, que fazia um `min_access` digitado errado virar rank 0 e,
    com isso, qualquer grant não-`none` passar. O canônico levanta `ValueError`.

    O campo continua sendo resolvido AQUI, por `_ns_field(namespace)`: ele depende da
    rota, então não cabe numa dependência estática — é fato do call site, não do
    verificador.

    ⚠️ **O `?admin_token=` SAIU** (D5, decidido 2026-08-28). Ele existia como parâmetro
    de query ao lado do header. Medido antes de remover: **um** chamador em todo o
    repositório, e era teste (`infra/test/smoke_config_write_auth.sh:79`) — zero uso em
    produção. E não era só código morto: segredo em query string entra em log de
    acesso, log de proxy e histórico de browser, ou seja, o parâmetro vazava a
    chave-mestra de config pelo caminho mais difícil de expurgar. Quem precisa do
    caminho de sistema manda `X-Admin-Token`.
    """
    from .config import get_settings
    settings = get_settings()
    enforce_write(
        request=request,
        admin_token=getattr(settings, "admin_token", None) or "",
        jwt_secret=getattr(settings, "jwt_secret", ""),
        module="config",
        field=_ns_field(namespace),
        what=f"escrita de config no namespace `{namespace}`",
    )


# ─── request models ──────────────────────────────────────────────────────────

class PutConfigBody(BaseModel):
    value:       Any
    tenant_id:   Optional[str] = None   # None → global default
    description: str           = ""


# ─── GET /config/{namespace}/{key} ───────────────────────────────────────────

@router.get("/{namespace}/{key}")
async def get_config(
    namespace: str,
    key:       str,
    request:   Request,
    tenant_id: str = Query(..., description="Tenant to resolve config for"),
) -> JSONResponse:
    """
    Returns the resolved value for (tenant_id, namespace, key).
    Applies two-level lookup: tenant-specific → global default.
    404 if no value exists at either level.
    """
    store = request.app.state.store
    value = await store.get(tenant_id, namespace, key)
    if value is None:
        raise HTTPException(
            status_code=404,
            detail=f"No config found for {namespace}.{key} "
                   f"(tenant={tenant_id}, no global default either)",
        )
    return JSONResponse(content={
        "tenant_id": tenant_id,
        "namespace": namespace,
        "key":       key,
        "value":     value,
    })


# ─── GET /config/{namespace} ─────────────────────────────────────────────────

@router.get("/{namespace}")
async def list_namespace(
    namespace: str,
    request:   Request,
    tenant_id: str = Query(..., description="Tenant to resolve config for"),
) -> JSONResponse:
    """
    All resolved keys in a namespace for the given tenant.
    Returns {key: resolved_value}. Empty dict if namespace has no entries.
    """
    store = request.app.state.store
    data  = await store.list_namespace(tenant_id, namespace)
    return JSONResponse(content={
        "tenant_id": tenant_id,
        "namespace": namespace,
        "entries":   data,
    })


# ─── GET /config ─────────────────────────────────────────────────────────────

@router.get("")
async def list_all(
    request:   Request,
    tenant_id: str = Query(..., description="Tenant to resolve config for"),
) -> JSONResponse:
    """
    All resolved config for a tenant, grouped by namespace.
    Not cached — for admin/diagnostic use.
    """
    store = request.app.state.store
    data  = await store.list_all(tenant_id)
    return JSONResponse(content={
        "tenant_id": tenant_id,
        "config":    data,
    })


# ─── CNS-05 — o root `core.*` é RESERVADO à plataforma ───────────────────────

#: Root do ContextStore que só a plataforma declara. A reserva inteira é este nome:
#: um predicado, não uma lista de pastas — é o que a torna uma REGRA (um nome se
#: explica sozinho) em vez de uma CONSULTA, e o que a impede de envelhecer quando
#: nascer a próxima pasta do core.
RESERVED_CONTEXT_ROOT = "core"


def _reject_tenant_core_root(namespace: str, key: str, tenant_id, value) -> None:
    """Recusa um `masking.context_map` de TENANT que declare o root `core`.

    Até 2026-09-01 a reserva era decisão sem mecanismo, e isso foi MEDIDO, não
    suposto: um `PUT` com `tenant_id` de tenant declarando `contexto.core.contact`
    voltava **HTTP 200**, e a leitura de volta mostrava o dano maior — o override
    SUBSTITUI a chave inteira, então aquele tenant passava a ter **1 folha no lugar
    das 94** da plataforma. A invasão do `core` e o apagamento do mapa eram o mesmo
    gesto.

    Escopo deliberado: só o par (`masking`, `context_map`) e só quando há tenant. O
    seed da plataforma escreve `core` no `__global__` e tem de continuar podendo —
    é ele o dono do root.

    ⚠️ **Isto NÃO fecha o buraco vizinho:** quem tem `config.masking` também pode
    escrever o `__global__` (mandando `tenant_id: null`) e de lá reescrever o `core`
    à vontade. É outra fronteira — a de quem pode editar o default da plataforma — e
    fechá-la aqui, de carona, misturaria duas decisões. Fica registrado, não fingido.
    """
    if namespace != "masking" or key != "context_map":
        return
    if not tenant_id or tenant_id == "__global__":
        return
    if not isinstance(value, dict):
        return
    contexto = value.get("contexto")
    if not isinstance(contexto, dict) or RESERVED_CONTEXT_ROOT not in contexto:
        return
    raise HTTPException(
        status_code=422,
        detail=(
            f"root `{RESERVED_CONTEXT_ROOT}` é RESERVADO à plataforma e não pode ser "
            f"declarado por tenant (tenant_id={tenant_id!r}). O que a plataforma escreve "
            f"vive sob `core.*`; tudo o mais do ContextStore é do tenant — use "
            f"`session.*` (4 h), `journey.*` (30 d) ou qualquer outro root, que cai no "
            f"hash da sessão. Ver docs/product/contextstore-core-namespace-spec.md §2."
        ),
    )


# ─── PUT /config/{namespace}/{key} ───────────────────────────────────────────

@router.put("/{namespace}/{key}", dependencies=[Depends(_require_config_write)])
async def put_config(
    namespace: str,
    key:       str,
    body:      PutConfigBody,
    request:   Request,
) -> JSONResponse:
    """
    Upsert a config value.
    body.tenant_id = null  → sets global platform default.
    body.tenant_id = "xyz" → sets tenant-specific override.
    Publishes config.changed to Kafka after a successful write (fire-and-forget).
    """
    _reject_tenant_core_root(namespace, key, body.tenant_id, body.value)
    store   = request.app.state.store
    emitter = request.app.state.emitter
    await store.set(
        tenant_id   = body.tenant_id,
        namespace   = namespace,
        key         = key,
        value       = body.value,
        description = body.description,
    )
    # Fire-and-forget — never let Kafka failure affect the HTTP response
    await emitter.emit_config_changed(
        tenant_id = body.tenant_id,
        namespace = namespace,
        key       = key,
        operation = "set",
    )
    effective_tenant = body.tenant_id or "__global__"
    return JSONResponse(
        status_code=200,
        content={
            "ok":        True,
            "tenant_id": effective_tenant,
            "namespace": namespace,
            "key":       key,
        },
    )


# ─── DELETE /config/{namespace}/{key} ────────────────────────────────────────

@router.delete("/{namespace}/{key}", dependencies=[Depends(_require_config_write)])
async def delete_config(
    namespace: str,
    key:       str,
    request:   Request,
    tenant_id: Optional[str] = Query(
        default=None,
        description="Tenant whose override to remove. Omit to delete the global default.",
    ),
) -> JSONResponse:
    """
    Removes an explicit config entry.
    - Deleting a tenant override restores the global default for that tenant.
    - Deleting the global entry leaves all tenants without a fallback (returns null).
    """
    store   = request.app.state.store
    emitter = request.app.state.emitter
    deleted = await store.delete(tenant_id, namespace, key)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Config entry {namespace}.{key} not found "
                   f"(tenant={tenant_id or '__global__'})",
        )
    await emitter.emit_config_changed(
        tenant_id = tenant_id,
        namespace = namespace,
        key       = key,
        operation = "delete",
    )
    return JSONResponse(content={"ok": True, "deleted": True})


# ─── GET /config/{namespace}/raw ─────────────────────────────────────────────

@router.get("/{namespace}/raw")
async def list_namespace_raw(
    namespace: str,
    request:   Request,
    tenant_id: str = Query(..., description="Which tenant's explicit entries to list"),
) -> JSONResponse:
    """
    Raw (non-resolved) entries explicitly set for (tenant_id, namespace).
    Shows what is overriding the global default for a specific tenant.
    Pass tenant_id='__global__' to see the global defaults themselves.
    """
    store   = request.app.state.store
    entries = await store.list_namespace_raw(tenant_id, namespace)
    return JSONResponse(content={
        "tenant_id": tenant_id,
        "namespace": namespace,
        "entries":   entries,
    })
