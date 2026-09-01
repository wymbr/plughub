"""
router.py
Endpoints REST da auth-api.

Autenticação de operações de gestão (usuários, permissões, templates, módulos):
    header Authorization: Bearer <access_token> + ABAC `config.usuarios`.
    **Não há mais X-Admin-Token aqui** — G-PROBE 2026-06-26, strict, sem fallback
    (ver o bloco "ABAC gate" abaixo). Docstring corrigida em 2026-08-03: ela ainda
    anunciava o header antigo, e os testes tinham sido escritos contra a promessa.
Autenticação de sessão (me/refresh/logout): header Authorization: Bearer <access_token>
                                            ou body refresh_token.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from jose import JWTError
from plughub_authz import abac_can

from . import db as db_mod
from . import presets as presets_mod
from . import permissions as perms_mod
from .config import Settings, get_settings
from .jwt_utils import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from .models import (
    CreateTemplateRequest,
    CreateUserRequest,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    TemplateResponse,
    TokenResponse,
    TokenUserInfo,
    UpdateTemplateRequest,
    UpdateUserRequest,
    UserResponse,
)
from .password import hash_password, verify_password

logger = logging.getLogger("plughub.auth_api.router")

router = APIRouter(prefix="/auth", tags=["auth"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def _settings() -> Settings:
    return get_settings()


# ─── ABAC gate (admin-token → Bearer+ABAC config.usuarios) ─────────────────────
# G-PROBE platform-wide (2026-06-26): gestão de usuários/permissões/grupos deixa de
# usar X-Admin-Token e passa a autorizar pelo JWT do operador + ABAC `config.usuarios`
# (read_only p/ GET, read_write p/ mutação). Strict: sem fallback de admin-token.
# O bootstrap (seed_auth) minta um Bearer próprio assinado com o jwt_secret.

# O verificador é o CANÔNICO desde 2026-08-28 (passo 5 da consolidação). Saíram daqui
# o `_ACCESS_RANK` (quarta cópia da mesma tabela de rank no repositório) e o
# `_check_config_field`, cujo `.get(min_access, 0)` era a divergência 4: um `min_access`
# digitado errado virava rank 0 e QUALQUER grant não-`none` passava. Inerte hoje — os
# três call sites passam literais —, e é por isso que valia fechar antes do quarto.
#
# ⚠️ O que NÃO migrou, e a razão: `jwt_utils.py` continua com `python-jose`. Este
# serviço é o EMISSOR do token; quem assina e quem confere têm de ser a mesma
# biblioteca, e o canônico verifica com PyJWT. Trocar o emissor por simetria seria
# mexer na assinatura de toda a plataforma para arrumar a estética de um import. (D4.)
# O `from jose import JWTError` no topo é só o tipo de exceção que `decode_access_token`
# levanta — não é um segundo decodificador.


def _require_config(field: str, write: bool):
    """Dependency factory: exige Bearer + ABAC `config.{field}` (read_only|read_write)."""
    async def _dep(request: Request, settings: Settings = Depends(_settings)) -> dict[str, Any]:
        claims = await _bearer_claims(request, settings)
        need = "read_write" if write else "read_only"
        if not abac_can(claims, "config", field, need):
            raise HTTPException(
                status_code=403,
                detail=f"forbidden: requires config.{field} ({need})",
            )
        return claims
    return _dep


# `users`       = administrar PESSOAS (criar, editar dados, ativar/desativar, grupos)
# `permissions` = conceder CAPACIDADE (papeis, modulos/campos, escopo de pools)
#
# Split de 2026-08-27. Ver o bloco de comentario em `infra/modules.yaml`: o campo
# unico era a chave-mestra, e toda fronteira ABAC colapsava nele.
_USUARIOS_READ = _require_config("users", write=False)
_USUARIOS_WRITE = _require_config("users", write=True)
_PERMS_READ = _require_config("permissions", write=False)
_PERMS_WRITE = _require_config("permissions", write=True)


# Campos de CAPACIDADE: muda-los e CONCEDER, nao administrar. Ficam sob
# `config.permissions` mesmo em rotas cuja porta e `config.users`.
#
# ⚠️ `password` NAO esta aqui de proposito — resetar senha e trabalho legitimo de
# quem administra pessoas. O vetor "resetar a senha do admin e entrar como admin" e
# fechado pela outra ponta: `_assert_may_touch`, que protege o ALVO privilegiado.
#
# `unrestricted` saiu do conjunto em 2026-08-31 (AUT-15) porque saiu do MODELO: um
# campo que o `UpdateUserRequest` não declara nunca aparece em `model_fields_set`, e
# guardá-lo aqui seria vigiar uma porta que não existe mais.
_CAPACITY_FIELDS = frozenset({"roles", "accessible_pools"})


def _is_privileged(row: dict[str, Any]) -> bool:
    """O alvo detem capacidade que o torna intocavel por quem so administra pessoas.

    ── Por que `unrestricted` saiu deste predicado (AUT-15, 2026-08-31) ──────────

    Este e um predicado de SEGURANCA, e tirar um disjunto dele o enfraquece — entao a
    remocao precisa de razao, nao de arrumacao.

    A razao e que o disjunto protegia um FANTASMA. Desde a AUT-12/AUT-13 o campo nao e
    emitido no token, nao e lido pelo `resolve_scope` e nao decide escopo nenhum;
    manter alguem intocavel por deter uma flag inerte deixava esse alguem mais dificil
    de administrar do que um par, sem que a flag lhe desse poder algum.

    Populacao contada antes de decidir (nunca depois): 8 usuarios, 2 com `true`, e
    **1** privilegiado SO por ela — `probe@plughub.local`, fixture de portao. Mesma
    forma da medicao que fechou o ramo legado da evaluation-api.

    O disjunto que FICA e o que sempre foi o real: deter `config.permissions`. E ele
    e load-bearing — sem esta funcao o split de 2026-08-27 nao entrega o que promete
    (o supervisor redefine a senha do admin, campo de PESSOA, e entra como admin).
    """
    mc = row.get("module_config") or {}
    acc = ((mc.get("config") or {}).get("permissions") or {}).get("access", "none")
    return acc != "none"


def _assert_may_grant(claims: dict[str, Any], sent: set[str], acao: str) -> None:
    """Recusa alto quando o CORPO carrega campo de capacidade sem `config.permissions`.

    O discriminador e `model_fields_set` (pydantic v2) — o que o chamador ENVIOU, nao
    o valor resultante. Omitir `roles` num POST e aceitar o default; envia-lo e
    conceder, mesmo que por acaso o valor coincida com o default.
    """
    tocados = sorted(sent & _CAPACITY_FIELDS)
    if not tocados:
        return
    if not abac_can(claims, "config", "permissions", "read_write"):
        raise HTTPException(
            status_code=403,
            detail=(
                f"forbidden: {acao} com campo de capacidade requer config.permissions "
                f"(read_write) — no corpo: {', '.join(tocados)}"
            ),
        )


def _assert_may_touch(claims: dict[str, Any], alvo: dict[str, Any], acao: str) -> None:
    """Protege o ALVO: quem detem `config.permissions` so e tocado por um par.

    Sem esta regra o split nao entrega o que promete — o supervisor redefine a senha
    do admin (campo de PESSOA, permitido) e entra como admin.
    """
    if _is_privileged(alvo) and not abac_can(claims, "config", "permissions", "read_write"):
        raise HTTPException(
            status_code=403,
            detail=(
                f"forbidden: {acao} de um usuario que detem config.permissions "
                f"requer config.permissions (read_write)"
            ),
        )


def _user_to_response(row: dict[str, Any]) -> UserResponse:
    return UserResponse(
        id=str(row["id"]),
        tenant_id=row["tenant_id"],
        email=row["email"],
        name=row["name"],
        roles=list(row["roles"]),
        accessible_pools=list(row["accessible_pools"]),
        max_concurrent_sessions=int(row.get("max_concurrent_sessions", 3)),
        active=row["active"],
        created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
        updated_at=row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"]),
    )


async def _make_token_response(
    pool: asyncpg.Pool,
    user: dict[str, Any],
    settings: Settings,
) -> tuple[TokenResponse, str]:
    """Gera access_token + refresh_token. Retorna (TokenResponse, plain_refresh_token)."""
    plain_refresh = generate_refresh_token()
    module_config: dict[str, Any] = user.get("module_config") or {}
    role: str = (list(user["roles"]) or ["operator"])[0]
    # Arc 9 — resolve supervisor scope at token generation time
    sup_groups, sup_user_ids = await db_mod.resolve_supervisor_scope(
        pool, str(user["id"]),
    )
    access = create_access_token(
        user_id=str(user["id"]),
        tenant_id=user["tenant_id"],
        email=user["email"],
        name=user["name"],
        roles=list(user["roles"]),
        accessible_pools=list(user["accessible_pools"]),
        settings=settings,
        module_config=module_config,
        supervised_groups=sup_groups,
        supervised_user_ids=sup_user_ids,
        max_concurrent_sessions=int(user.get("max_concurrent_sessions", 3)),
    )
    expires_in = settings.access_token_expire_minutes * 60
    return (
        TokenResponse(
            access_token=access,
            refresh_token=plain_refresh,
            expires_in=expires_in,
            user=TokenUserInfo(
                id=str(user["id"]),
                email=user["email"],
                name=user["name"],
                roles=list(user["roles"]),
                tenant_id=user["tenant_id"],
                accessible_pools=list(user["accessible_pools"]),
                max_concurrent_sessions=int(user.get("max_concurrent_sessions", 3)),
                module_config=module_config,
            ),
        ),
        plain_refresh,
    )


async def _bearer_claims(request: Request, settings: Settings) -> dict[str, Any]:
    """Extrai e valida o Bearer token do cabeçalho Authorization."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth[len("Bearer "):]
    try:
        return decode_access_token(token, settings)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc


# ─── Auth endpoints ────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    """
    Login com e-mail e senha.
    Retorna access_token (JWT) + refresh_token (opaque, rotacionado).
    """
    pool = _get_pool(request)
    settings = _settings()

    user = await db_mod.get_user_by_email(pool, body.tenant_id, body.email)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user["active"]:
        raise HTTPException(status_code=403, detail="User account is inactive")

    token_resp, plain_refresh = await _make_token_response(pool, user, settings)
    await db_mod.create_session(
        pool,
        user_id=str(user["id"]),
        tenant_id=user["tenant_id"],
        refresh_token_hash=hash_refresh_token(plain_refresh),
        expire_days=settings.refresh_token_expire_days,
    )
    logger.info("login ok: %s @ %s", user["email"], user["tenant_id"])
    return token_resp


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, request: Request) -> TokenResponse:
    """
    Troca o refresh_token por um novo par access+refresh (token rotation).
    O refresh_token antigo é invalidado imediatamente.
    """
    pool = _get_pool(request)
    settings = _settings()

    old_hash = hash_refresh_token(body.refresh_token)
    session = await db_mod.get_session_by_token_hash(pool, old_hash)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = await db_mod.get_user_by_id(pool, str(session["user_id"]))
    if not user or not user["active"]:
        raise HTTPException(status_code=403, detail="User account is inactive")

    token_resp, plain_refresh = await _make_token_response(pool, user, settings)
    rotated = await db_mod.rotate_session(
        pool,
        old_token_hash=old_hash,
        new_token_hash=hash_refresh_token(plain_refresh),
        expire_days=settings.refresh_token_expire_days,
    )
    if not rotated:
        raise HTTPException(status_code=409, detail="Token rotation conflict — try again")

    return token_resp


@router.post("/logout", status_code=204)
async def logout(body: LogoutRequest, request: Request) -> None:
    """Invalida o refresh_token. Idempotente (sem erro se não encontrado)."""
    pool = _get_pool(request)
    token_hash = hash_refresh_token(body.refresh_token)
    await db_mod.delete_session(pool, token_hash)


@router.get("/me", response_model=MeResponse)
async def me(request: Request) -> MeResponse:
    """Retorna as claims do access token Bearer presente no header."""
    settings = _settings()
    claims = await _bearer_claims(request, settings)
    return MeResponse(
        sub=claims["sub"],
        tenant_id=claims["tenant_id"],
        email=claims["email"],
        name=claims["name"],
        roles=claims["roles"],
        accessible_pools=claims["accessible_pools"],
        module_config=claims.get("module_config", {}),
        max_concurrent_sessions=int(claims.get("max_concurrent_sessions", 3)),
    )


# ─── User management (admin) ──────────────────────────────────────────────────

@router.post("/users", response_model=UserResponse, status_code=201,
             dependencies=[Depends(_USUARIOS_WRITE)])
async def create_user(
    body: CreateUserRequest,
    request: Request,
    claims: dict[str, Any] = Depends(_USUARIOS_WRITE),
) -> UserResponse:
    # Nascer com papel/escopo/irrestrito e CONCEDER na criacao. Omitir o campo aceita
    # o default (`operator`, [], False) e nao exige nada a mais.
    _assert_may_grant(claims, set(body.model_fields_set), "criar usuario")
    pool = _get_pool(request)
    # Verifica se e-mail já existe
    existing = await db_mod.get_user_by_email(pool, body.tenant_id, body.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered in this tenant")

    row = await db_mod.create_user(
        pool,
        tenant_id=body.tenant_id,
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        roles=body.roles,
        accessible_pools=body.accessible_pools,
        max_concurrent_sessions=body.max_concurrent_sessions,
    )

    # ── Preset de nascimento (passo 3, 2026-08-27) ────────────────────────────
    # Ate aqui o usuario nascia com `module_config` VAZIO, ou seja, dentro da
    # degradacao graciosa — o menu "funcionava" porque o buraco o sustentava.
    # Aplicar o preset do papel e o que permite inverter aquela degradacao sem que
    # todo usuario novo nasca cego.
    #
    # ⚠️ A aplicacao mudou de casa em 2026-08-31 (AUT-12) e agora vive em
    # `presets.apply_role_preset`. Ela morava AQUI, e por isso o caminho do SEED — que
    # chama `db.create_user` direto — nunca a executava: o admin de uma instalacao nova
    # nascia com `module_config = '{}'`, sem menu, e sem poder se corrigir (conceder
    # exige `config.permissions`). Dois chamadores, uma implementacao.
    cfg = await presets_mod.apply_role_preset(pool, str(row["id"]), body.roles, body.email)
    if cfg:
        row["module_config"] = cfg

    return _user_to_response(row)


@router.get("/users", response_model=list[UserResponse],
            dependencies=[Depends(_USUARIOS_READ)])
async def list_users(
    request: Request,
    tenant_id: str = "tenant_demo",
    limit: int = 100,
    offset: int = 0,
) -> list[UserResponse]:
    pool = _get_pool(request)
    rows = await db_mod.list_users(pool, tenant_id, limit=limit, offset=offset)
    return [_user_to_response(r) for r in rows]


@router.get("/users/{user_id}", response_model=UserResponse,
            dependencies=[Depends(_USUARIOS_READ)])
async def get_user(user_id: str, request: Request) -> UserResponse:
    pool = _get_pool(request)
    row = await db_mod.get_user_by_id(pool, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_response(row)


@router.patch("/users/{user_id}", response_model=UserResponse,
              dependencies=[Depends(_USUARIOS_WRITE)])
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    request: Request,
    claims: dict[str, Any] = Depends(_USUARIOS_WRITE),
) -> UserResponse:
    pool = _get_pool(request)
    # Garante que o usuário existe
    existing = await db_mod.get_user_by_id(pool, user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")
    _assert_may_grant(claims, set(body.model_fields_set), "editar usuario")
    _assert_may_touch(claims, existing, "editar")

    ph = hash_password(body.password) if body.password else None
    row = await db_mod.update_user(
        pool,
        user_id=user_id,
        name=body.name,
        password_hash=ph,
        roles=body.roles,
        accessible_pools=body.accessible_pools,
        active=body.active,
        max_concurrent_sessions=body.max_concurrent_sessions,
    )
    return _user_to_response(row)


@router.delete("/users/{user_id}", status_code=204,
               dependencies=[Depends(_USUARIOS_WRITE)])
async def delete_user(
    user_id: str,
    request: Request,
    claims: dict[str, Any] = Depends(_USUARIOS_WRITE),
) -> None:
    pool = _get_pool(request)
    alvo = await db_mod.get_user_by_id(pool, user_id)
    if alvo:
        _assert_may_touch(claims, alvo, "remover")
    deleted = await db_mod.delete_user(pool, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")


# ─── Platform permissions — REMOVIDO em 2026-08-30 ───────────────────────────
#
# Aqui viviam `POST/GET/DELETE /permissions` e `GET /permissions/resolve`, sobre a
# tabela `auth.platform_permissions`. Saíram inteiros: **zero linhas** na tabela e
# **zero consumidores de produção** (só os testes chamavam), enquanto quem de fato
# decide *"esta pessoa pode?"* é `auth.users.module_config`, lido pelo verificador
# canônico `plughub_authz`.
#
# O risco não era o custo de manter: era um endpoint que **parece conceder
# permissão** e escreve numa tabela que ninguém consulta. Duas respostas para a
# mesma pergunta significam que a mais permissiva vale — mesmo modo de falha que a
# V2b removeu do masking e o grant-first removeu do menu.
#
# Ver `permissions.py` (cabeçalho) para o resíduo físico deliberado.


# ─── Permission templates (admin) ─────────────────────────────────────────────

def _tmpl_to_response(row: dict[str, Any]) -> TemplateResponse:
    import json as _json
    cfg = row.get("config")
    if isinstance(cfg, str):
        cfg = _json.loads(cfg)
    return TemplateResponse(
        id=str(row["id"]),
        tenant_id=row["tenant_id"],
        name=row["name"],
        description=row.get("description", ""),
        config=cfg if isinstance(cfg, dict) else {},
        created_at=row["created_at"].isoformat() if hasattr(row.get("created_at"), "isoformat") else str(row.get("created_at", "")),
        updated_at=row["updated_at"].isoformat() if hasattr(row.get("updated_at"), "isoformat") else str(row.get("updated_at", "")),
    )


@router.post("/templates", response_model=TemplateResponse, status_code=201,
             dependencies=[Depends(_PERMS_WRITE)])
async def create_template(body: CreateTemplateRequest, request: Request) -> TemplateResponse:
    pool = _get_pool(request)
    row = await perms_mod.create_template(
        pool,
        tenant_id=body.tenant_id,
        name=body.name,
        description=body.description,
        config=body.config,
    )
    return _tmpl_to_response(row)


@router.get("/templates", response_model=list[TemplateResponse],
            dependencies=[Depends(_PERMS_READ)])
async def list_templates(
    request: Request,
    tenant_id: str = "tenant_demo",
) -> list[TemplateResponse]:
    pool = _get_pool(request)
    rows = await perms_mod.list_templates(pool, tenant_id)
    return [_tmpl_to_response(r) for r in rows]


@router.get("/templates/{template_id}", response_model=TemplateResponse,
            dependencies=[Depends(_PERMS_READ)])
async def get_template(template_id: str, request: Request) -> TemplateResponse:
    pool = _get_pool(request)
    row = await perms_mod.get_template(pool, template_id)
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    return _tmpl_to_response(row)


@router.patch("/templates/{template_id}", response_model=TemplateResponse,
              dependencies=[Depends(_PERMS_WRITE)])
async def update_template(
    template_id: str,
    body: UpdateTemplateRequest,
    request: Request,
) -> TemplateResponse:
    pool = _get_pool(request)
    existing = await perms_mod.get_template(pool, template_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Template not found")
    row = await perms_mod.update_template(
        pool, template_id,
        name=body.name, description=body.description, config=body.config,
    )
    return _tmpl_to_response(row)


@router.delete("/templates/{template_id}", status_code=204,
               dependencies=[Depends(_PERMS_WRITE)])
async def delete_template(template_id: str, request: Request) -> None:
    pool = _get_pool(request)
    deleted = await perms_mod.delete_template(pool, template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")


# `POST /templates/{id}/apply` foi REMOVIDO em 2026-08-30 junto de
# `platform_permissions`: ele materializava o template naquela tabela, e a tela de
# Acesso nunca o chamou — ela copia `template.config` no cliente para PRÉ-PREENCHER
# o formulário de usuário. O template continua sendo preset; o que sumiu foi a
# segunda semântica do mesmo objeto.


# ─── Module registry ──────────────────────────────────────────────────────────
#
# GET  /auth/modules                — lista módulos ativos (público, usado pela UI)
# GET  /auth/modules/{module_id}    — detalhe do módulo
# POST /auth/modules                — registra/atualiza módulo (admin — para plugins)
# PATCH /auth/modules/{module_id}/active — ativa/desativa módulo (admin)

def _module_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    import json as _json
    schema = row.get("schema") or row.get("permission_schema") or {}
    if isinstance(schema, str):
        schema = _json.loads(schema)
    return {
        "module_id": row["module_id"],
        "tenant_id": row.get("tenant_id"),
        "label": row["label"],
        "icon": row["icon"],
        "nav_path": row["nav_path"],
        "permission_schema": schema,
        "active": row["active"],
        "registered_at": (
            row["registered_at"].isoformat()
            if hasattr(row.get("registered_at"), "isoformat")
            else str(row.get("registered_at", ""))
        ),
        "updated_at": (
            row["updated_at"].isoformat()
            if hasattr(row.get("updated_at"), "isoformat")
            else str(row.get("updated_at", ""))
        ),
    }


@router.get("/modules", response_model=list[dict])
async def list_modules(
    request: Request,
    tenant_id: str | None = None,
    active_only: bool = True,
) -> list[dict]:
    """
    Lista módulos disponíveis.
    tenant_id=None → apenas módulos de plataforma (built-in).
    tenant_id=X    → módulos de plataforma + módulos específicos do tenant X.
    Público — não requer admin token (a UI precisa para renderizar formulários de permissão).
    """
    pool = _get_pool(request)
    rows = await db_mod.list_modules(pool, tenant_id=tenant_id, active_only=active_only)
    return [_module_to_dict(r) for r in rows]


@router.get("/modules/{module_id}", response_model=dict)
async def get_module(module_id: str, request: Request) -> dict:
    pool = _get_pool(request)
    row = await db_mod.get_module(pool, module_id)
    if not row:
        raise HTTPException(status_code=404, detail="Module not found")
    return _module_to_dict(row)


@router.post("/modules", response_model=dict, status_code=201,
             dependencies=[Depends(_PERMS_WRITE)])
async def register_module(body: dict, request: Request) -> dict:
    """
    Registra ou atualiza um módulo (upsert por module_id).
    Usado por plugins para declarar seus módulos e permission_schemas.
    Módulos de plataforma são registrados automaticamente no startup via modules.yaml.
    """
    pool = _get_pool(request)
    module_id: str = body.get("module_id", "")
    if not module_id:
        raise HTTPException(status_code=422, detail="module_id is required")
    row = await db_mod.upsert_module(
        pool,
        module_id=module_id,
        label=body.get("label", module_id),
        icon=body.get("icon", "📦"),
        nav_path=body.get("nav_path", ""),
        schema=body.get("permission_schema", {}),
        tenant_id=body.get("tenant_id"),  # None = platform-wide
        active=body.get("active", True),
    )
    return _module_to_dict(row)


@router.patch("/modules/{module_id}/active", response_model=dict,
              dependencies=[Depends(_PERMS_WRITE)])
async def set_module_active(module_id: str, request: Request, active: bool = True) -> dict:
    pool = _get_pool(request)
    ok = await db_mod.set_module_active(pool, module_id, active)
    if not ok:
        raise HTTPException(status_code=404, detail="Module not found")
    row = await db_mod.get_module(pool, module_id)
    return _module_to_dict(row)  # type: ignore[arg-type]


# ─── User module-config (permissões ABAC) ─────────────────────────────────────
#
# GET   /auth/users/{id}/module-config                 — config completa (admin)
# PUT   /auth/users/{id}/module-config                 — substitui config completa (admin)
# PATCH /auth/users/{id}/module-config/{module_id}     — atualiza config de um módulo (admin)
#
# Formato de module_config armazenado em auth.users:
#   {
#     "evaluation": {
#       "contestar": { "access": "read_write", "scope": ["pool:retencao_humano"] },
#       "revisar":   { "access": "read_only",  "scope": [] }
#     },
#     "contacts": {
#       "visualizar": { "access": "read_only", "scope": [] }
#     }
#   }


@router.get("/users/{user_id}/module-config", response_model=dict,
            dependencies=[Depends(_PERMS_READ)])
async def get_user_module_config(user_id: str, request: Request) -> dict:
    """Retorna o module_config completo do usuário."""
    pool = _get_pool(request)
    # Verifica existência
    existing = await db_mod.get_user_by_id(pool, user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")
    cfg = await db_mod.get_user_module_config(pool, user_id)
    return cfg


@router.put("/users/{user_id}/module-config", response_model=dict,
            dependencies=[Depends(_PERMS_WRITE)])
async def set_user_module_config(user_id: str, body: dict, request: Request) -> dict:
    """
    Substitui todo o module_config do usuário.
    Valida cada módulo presente contra o schema registrado em auth.module_registry.
    Retorna 422 se houver violações de schema.
    """
    pool = _get_pool(request)
    existing = await db_mod.get_user_by_id(pool, user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")

    # Valida cada módulo contra o schema registrado
    all_errors: list[str] = []
    for module_id, module_data in body.items():
        mod_row = await db_mod.get_module(pool, module_id)
        if not mod_row:
            all_errors.append(f"Módulo '{module_id}' não encontrado no registro")
            continue
        import json as _json
        schema = mod_row.get("schema") or {}
        if isinstance(schema, str):
            schema = _json.loads(schema)
        errs = db_mod.validate_module_config(
            {"permission_schema": schema},
            module_data if isinstance(module_data, dict) else {},
        )
        all_errors.extend([f"[{module_id}] {e}" for e in errs])

    if all_errors:
        raise HTTPException(
            status_code=422,
            detail={"errors": all_errors},
        )

    ok = await db_mod.set_user_module_config(pool, user_id, body)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    return await db_mod.get_user_module_config(pool, user_id)


@router.patch("/users/{user_id}/module-config/{module_id}", response_model=dict,
              dependencies=[Depends(_PERMS_WRITE)])
async def patch_user_module_config(
    user_id: str,
    module_id: str,
    body: dict,
    request: Request,
) -> dict:
    """
    Atualiza a config de um módulo específico do usuário sem sobrescrever os outros módulos.
    Valida contra o schema do módulo antes de persistir.
    """
    pool = _get_pool(request)

    # Verifica existência do usuário
    existing = await db_mod.get_user_by_id(pool, user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")

    # Verifica existência do módulo e valida
    mod_row = await db_mod.get_module(pool, module_id)
    if not mod_row:
        raise HTTPException(status_code=404, detail=f"Module '{module_id}' not found")

    import json as _json
    schema = mod_row.get("schema") or {}
    if isinstance(schema, str):
        schema = _json.loads(schema)

    errors = db_mod.validate_module_config(
        {"permission_schema": schema},
        body if isinstance(body, dict) else {},
    )
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    row = await db_mod.patch_user_module_config(pool, user_id, module_id, body)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    # Retorna apenas a config do módulo atualizado
    import json as _json2  # noqa: F811
    cfg = row.get("module_config") or {}
    if isinstance(cfg, str):
        cfg = _json2.loads(cfg)
    return cfg.get(module_id, {})
