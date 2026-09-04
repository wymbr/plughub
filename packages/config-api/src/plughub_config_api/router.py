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

  GET  /config/{namespace}/_provenance?tenant_id=xxx
       Por key: qual ESCOPO responde (`tenant`|`global`), se o outro existe, e se
       os dois divergem. Registrada ANTES da paramétrica acima — ver o comentário
       na rota, que conta como a `/raw` morreu por ordem de registro.

  PUT  /config/{namespace}/{key}
       Body: {"tenant_id": null|"...", "value": <any>, "description": "..."}
       Upsert. tenant_id=null sets the global platform default.
       A resposta traz `shadowed_by[]`: numa escrita GLOBAL, os tenants com override
       próprio desta key — para quem a escrita NÃO tem efeito. Nomeia, não recusa.

  DELETE /config/{namespace}/{key}?tenant_id=xxx
       Removes an explicit entry. Returns 404 if not found.
       tenant_id=null or omitted targets the global default.

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
    # O catálogo de FORMATOS é conteúdo de diálogo, não política de compliance:
    # quem autora a forma escolhe o formato do campo. Ele REFERENCIA `masking.types`
    # (herda a máscara) mas não pode mudar o que é mascarado nem a classe LGPD —
    # essa continua atrás de `config.masking`. Sem este override o namespace cairia
    # no catch-all `platform`, que é grant de admin, e o autor de forma (que já tem
    # `config.dialog_forms` para publicar a forma) não conseguiria editar o formato
    # que a própria forma usa.
    "dialog":       "dialog_forms",
}


# ⚠️ ALW-03 (2026-09-02) — UM caso resolve por (namespace, KEY), e a excecao e a
# decisao. "Cadastrar um campo do ContextStore" sao DOIS fatos com donos diferentes:
#
#   · o CATALOGO de tipos (o que `cpf_br` mascara, sua classe LGPD)  → compliance
#   · o MAPA (quais campos existem, e qual tipo cada um usa)          → quem AUTORA flow
#
# Os dois viviam no namespace `masking` e portanto no mesmo grant (`config.masking`,
# preset ADMIN-ONLY), enquanto o autor de flow e `developer`. O ADR nomeia essa friccao
# como o que faz gente CONTORNAR o cadastro. Medido em 2026-09-02: `skill_flows.operacao`
# e `.editar` nascem para admin+developer; `config.masking` so para admin.
#
# E o mesmo formato do split `config.users` x `config.permissions`: um rotulo com "e"
# costuma ser dois fatos, e quando um deles decide politica, ele merece grant proprio.
_NS_KEY_FIELD_OVERRIDES = {
    ("masking", "context_map"): "context_map",
}


def _ns_field(namespace: str, key: str | None = None) -> str:
    if key is not None:
        por_key = _NS_KEY_FIELD_OVERRIDES.get((namespace, key))
        if por_key is not None:
            return por_key
    return _NS_FIELD_OVERRIDES.get(namespace, "platform")


async def _require_config_write(namespace: str, key: str, request: Request) -> None:
    """Write guard: admin-token (seed/sistema) OU Bearer + ABAC `config.{ns_field}` (read_write).

    MIGRADO para `plughub_authz` em 2026-08-28 (passo 2 da consolidação dos seis
    verificadores). Saíram daqui `_verify_hs256` (HMAC em stdlib — uma das três
    bibliotecas que faziam a MESMA verificação no repo) e `_check_config_field` com
    `.get(min_access, 0)`, que fazia um `min_access` digitado errado virar rank 0 e,
    com isso, qualquer grant não-`none` passar. O canônico levanta `ValueError`.

    O campo continua sendo resolvido AQUI, por `_ns_field(namespace, key)`: ele depende da
    rota, então não cabe numa dependência estática — é fato do call site, não do
    verificador. Desde a ALW-03 a `key` entra na resolução, porque `masking.context_map`
    tem dono diferente de `masking.types` — ver o comentário de `_NS_KEY_FIELD_OVERRIDES`.

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
        field=_ns_field(namespace, key),
        what=f"escrita de config em `{namespace}.{key}`",
    )


# ─── request models ──────────────────────────────────────────────────────────

class PutConfigBody(BaseModel):
    value:       Any
    tenant_id:   Optional[str] = None   # None → global default
    description: str           = ""


# ─── GET /config/{namespace}/_provenance ─────────────────────────────────────
#
# ⚠️ REGISTRADA ANTES de `/{namespace}/{key}` — e a ordem é o mecanismo, não estilo.
# O FastAPI casa na ORDEM de registro, então uma rota literal declarada depois de uma
# paramétrica que a cobre nunca é alcançada. Foi assim que a `GET /{namespace}/raw`
# morreu: declarada 200 linhas abaixo de `/{namespace}/{key}`, ela respondia
# `404 No config found for masking.raw` — com docstring prometendo exatamente
# *"o que está sobrescrevendo o default global"*, zero chamadores e zero testes.
# Medida em 2026-09-02 e REMOVIDA no mesmo commit: rota inalcançável não é contrato.
#
# O prefixo `_` existe para que a colisão não volte por outro caminho: uma key de
# config chamada `provenance` é plausível, uma chamada `_provenance` não é.

@router.get("/{namespace}/_provenance")
async def key_provenance(
    namespace: str,
    request:   Request,
    tenant_id: str = Query(..., description="Tenant whose provenance to describe"),
) -> JSONResponse:
    """Por key do namespace: qual ESCOPO responde, e se o outro existe e diverge.

    ── Por que existe (ALW-06 / CNS-14, 2026-09-02) ─────────────────────────────

    A resolução é `LIMIT 1` com o tenant na frente: **o override de tenant vence o
    global POR INTEIRO**. A consequência é que um `PUT` global numa key sombreada
    responde `200` e não muda nada para aquele tenant — medido três vezes no mesmo
    dia, e nenhuma delas ficou vermelha em lugar nenhum.

    O que torna isso caro não é o `PUT` perdido, é a DERIVA. Medido em `tenant_demo`:
    `masking.types` tem override **byte-idêntico** ao global (mesmo md5) — um sombra
    que não carrega informação nenhuma e só serve para engolir toda edição futura; e
    `masking.context_rules` já divergiu, com um rótulo corrigido no global que nunca
    chegou ao tenant. Hoje custou um rótulo velho; da próxima vez custa uma regra de
    máscara.

    Não bloqueia nada e não conserta nada — **nomeia**. Escolher qual lado vence é
    política, e política é de gente; o mesmo desenho que `config_drift` já adotou
    para a divergência declarado × gravado.
    """
    store = request.app.state.store
    return JSONResponse(content={
        "tenant_id": tenant_id,
        "namespace": namespace,
        "keys":      await store.provenance(tenant_id, namespace),
    })


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


# ─── CNS-05/CNS-08 — o `masking.context_map` é da PLATAFORMA ─────────────────

#: Root do ContextStore que só a plataforma declara. Continua exportado porque a
#: reserva é um fato do modelo, citado por doc e por teste — e porque, quando a chave
#: de tenant existir (CNS-16), é ele que o mesclador vai excluir.
RESERVED_CONTEXT_ROOT = "core"


def _reject_tenant_context_map(namespace: str, key: str, tenant_id, value) -> None:
    """Recusa QUALQUER `masking.context_map` vindo de tenant.

    ── Por que a recusa é total, e não só do root `core` (CNS-08, 2026-09-01) ──

    A CNS-05 recusava o root `core` de tenant, porque um tenant declarando ali invadia
    o espaço da plataforma. Medindo o mecanismo por inteiro, o dano não dependia do
    root: **a resolução de config é `LIMIT 1`, tenant vence o global POR INTEIRO**
    (`db.py`, comentário do schema). Qualquer override de tenant nesta chave substitui
    as 94 folhas da plataforma por aquilo que o tenant mandou — foi medido em
    2026-09-01: um `PUT` com uma folha deixou o tenant com **1 no lugar de 94**.

    E não há uso legítimo do outro lado hoje: medido no mesmo dia, **zero** tenants
    sobrescrevem esta chave, e a instalação tem **um** tenant (`platform_config`:
    `__global__` 83 chaves, `tenant_demo` 4, nenhuma delas esta).

    ⚠️ **Isto NÃO é o desenho final — é a porta fechada enquanto não há quem entre.**
    Quando existir um segundo tenant que precise de vocabulário próprio, o desenho é
    chave SEPARADA (`masking.context_map_tenant`) mesclada na leitura: cada chave mantém
    a semântica uniforme de config, e o tenant não alcança `core` **por construção do
    mesclador**, não por um portão que alguém pode esquecer de chamar. Construir o merge
    agora seria política contra população zero. Ver CNS-16.

    ⚠️ E o buraco vizinho continua aberto e registrado: quem tem `config.masking` escreve
    o `__global__` mandando `tenant_id: null` (CNS-14). É outra fronteira.
    """
    if namespace != "masking" or key != "context_map":
        return
    if not tenant_id or tenant_id == "__global__":
        return
    raise HTTPException(
        status_code=422,
        detail=(
            f"`masking.context_map` não aceita override por tenant (tenant_id={tenant_id!r}). "
            f"A resolução de config é tenant-vence-global POR INTEIRO, então o override "
            f"substituiria as folhas declaradas pela plataforma em vez de acrescentar às "
            f"delas. O mapa é editado no escopo GLOBAL (`tenant_id: null`). Vocabulário "
            f"próprio por tenant é desenho registrado (chave separada + merge na leitura), "
            f"ainda não construído — ver docs/product/contextstore-core-namespace-spec.md."
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
    _reject_tenant_context_map(namespace, key, body.tenant_id, body.value)
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

    # ── Uma escrita global que não alcança ninguém não pode responder só `ok` ──
    #
    # `LIMIT 1`, tenant na frente: quem tem linha própria para esta key não vê nada
    # do que acabou de ser gravado. O `200` já era verdade sobre a LINHA e mentira
    # sobre o EFEITO, e a diferença só aparecia contando os dois escopos à mão.
    #
    # Nomeia, nunca recusa: um tenant sobrescrever config é uso legítimo, e recusar
    # a escrita global por causa dele quebraria o caminho de seed. O que não pode é
    # ser mudo — a mesma regra do resto da casa.
    shadowed_by: list[str] = []
    if body.tenant_id is None:
        try:
            shadowed_by = await store.tenants_overriding(namespace, key)
        except Exception:  # pragma: no cover - relatório nunca derruba a escrita
            logger.exception(
                "provenance_lookup_failed ns=%s key=%s — a escrita FOI feita; o que "
                "falhou foi o relatório de quem a sombreia", namespace, key,
            )
            shadowed_by = []
        if shadowed_by:
            logger.warning(
                "global_write_shadowed ns=%s key=%s tenants=%s — a escrita no "
                "`__global__` não tem efeito para estes tenants (override próprio)",
                namespace, key, shadowed_by,
            )

    return JSONResponse(
        status_code=200,
        content={
            "ok":           True,
            "tenant_id":    effective_tenant,
            "namespace":    namespace,
            "key":          key,
            "shadowed_by":  shadowed_by,
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


# ─── GET /config/{namespace}/raw — REMOVIDA em 2026-09-02 ────────────────────
#
# Estava declarada AQUI, depois de `/{namespace}/{key}`, e por isso era inalcançável:
# `GET /config/masking/raw` casava a paramétrica com `key="raw"` e respondia
# `404 No config found for masking.raw`. Medido ao vivo. Zero chamadores no
# repositório, zero testes — ou seja, nunca funcionou e ninguém percebeu, porque um
# 404 numa rota de leitura parece "não há dado" e não "não há rota".
#
# Não foi movida para cima: a pergunta dela (*"o que está sobrescrevendo o global?"*)
# é a mesma que `/_provenance` responde, e melhor — por key, com o veredicto de
# divergência. Manter as duas seria duas respostas para o mesmo fato, e a casa já
# sabe como isso termina. `store.list_namespace_raw` fica, sem chamador HTTP.
