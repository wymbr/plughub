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

import json
import logging
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from jose import JWTError
from plughub_authz import abac_can

from . import db as db_mod
from . import grants
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
    CreateUserFromTemplateRequest,
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


async def _assert_pode_conceder(
    pool: Any,
    claims: dict[str, Any],
    acao: str,
    *,
    module_config: dict[str, Any] | None = None,
    roles: list[str] | None = None,
    accessible_pools: list[str] | None = None,
    atual: dict[str, Any] | None = None,
) -> None:
    """Guard de RANK (MOD-02 / E2): ninguem concede o que nao detem.

    Substitui o `_assert_may_grant`, que RECUSAVA em bloco qualquer campo de
    capacidade a quem nao fosse master — e por isso o supervisor criava usuario sem
    poder dar-lhe papel nem pool (a Costura 1 do ADR). Aquela funcao foi REMOVIDA em
    vez de ficar sem chamador: codigo de seguranca morto e pior que nenhum, porque
    parece proteger. O discriminador que ela trouxe fica no PATCH: `model_fields_set`,
    o que o chamador ENVIOU — nao enviar o campo e nao mexer nele.

    ⚠️ NA CRIACAO ele NAO serve, e isso foi medido (2026-09-08): `CreateUserRequest.roles`
    tem default `["operator"]` e o preset e aplicado em toda criacao, entao omitir o
    campo nao e "nao conceder" — e conceder o preset inteiro. O `POST /users` chama este
    guard incondicionalmente.

    O master (`config.permissions: read_write`) passa direto — `grants.violacoes` o
    reconhece e devolve lista vazia sem olhar mais nada.

    ⚠️ `roles` e validado nas DUAS rotas, inclusive no PATCH, e isso e deliberado
    embora trocar o papel NAO reescreva grants (decisao de 2026-08-27). Enquanto as
    17 rotas do `mcp-server-plughub` gatearem por `requireJwtRole` — papel literal,
    julgado por `roles[0]` —, o papel CONFERE acesso por si; validar so na criacao
    deixaria o PATCH como porta lateral. Quando a AUT-38 migrar aqueles portoes, esta
    validacao pode ser reavaliada; ate la ela e conservadora de proposito.
    """
    # ⚠️ O MASTER CURTO-CIRCUITA ANTES DE TOCAR NO CATALOGO, e isso e correcao, nao
    # otimizacao: a expansao de `roles` le o `module_registry`, e sem esta saida o
    # detentor de `config.permissions` passaria a depender do catalogo para uma
    # decisao que nao o consulta — um banco lento ou vazio derrubaria a criacao de
    # usuario com 503 para quem tem direito a tudo. Medido por teste vermelho.
    if grants.e_master(claims):
        return

    presets_por_papel: dict[str, dict[str, dict[str, Any]]] = {}
    if roles:
        try:
            mods = [presets_mod.normalize_module_row(r) for r in
                    await db_mod.list_modules(pool, tenant_id=None, active_only=True)]
            # ⚠️ CATALOGO VAZIO NAO E "o papel nao concede nada" — e "nao consegui
            # avaliar", e as duas se parecem. Sem esta guarda o `roles: ["admin"]`
            # expande para `{}`, nenhuma violacao e encontrada e o guard FALHA ABERTO
            # concedendo os 44 campos. Achado por um teste vermelho em 2026-09-08, onde
            # o `pool` mockado devolvia lista vazia: o valor plausivel escondendo o
            # buraco, no proprio predicado de seguranca.
            if not mods:
                logger.error(
                    "guard de concessao: catalogo de modulos VAZIO — recusando a "
                    "concessao de papel(is) %s em vez de aprova-la por ausencia",
                    ",".join(roles),
                )
                raise HTTPException(
                    status_code=503,
                    detail=("catalogo de modulos vazio; concessao de papel recusada — "
                            "nao da para avaliar o que o papel concede"),
                )
            for papel in roles:
                presets_por_papel[papel] = presets_mod.build_module_config([papel], mods)
        except HTTPException:
            # A recusa por catalogo VAZIO ja e a decisao final — deixa-la cair no
            # `except` generico abaixo trocaria a mensagem que nomeia a causa por um
            # "indisponivel" que manda depurar a conexao com o banco.
            raise
        except Exception as exc:  # noqa: BLE001
            # Falhar ABERTO aqui seria conceder por indisponibilidade do catalogo.
            logger.error("guard de concessao: catalogo indisponivel (%s) — recusando", exc)
            raise HTTPException(
                status_code=503,
                detail="catalogo de modulos indisponivel; concessao recusada por seguranca",
            ) from exc

    fora = grants.violacoes(
        claims,
        module_config=module_config,
        roles=roles,
        accessible_pools=accessible_pools,
        presets=presets_por_papel,
        atual=atual,
    )
    if fora:
        raise HTTPException(
            status_code=403,
            detail=(
                f"forbidden: {acao} — voce nao pode conceder o que nao detem. "
                + " · ".join(fora)
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


async def _registrar_trilha(
    pool, claims: dict[str, Any], alvo: dict[str, Any], acao: str, campos: list[str],
) -> None:
    """Trilha de ADMINISTRACAO (AUT-39) — dois canais, e eles nao se substituem.

    **A tabela** responde *"quem mexeu nesta pessoa, quando e em que campos"*, que e
    consultavel depois. **O log** responde AGORA, e existe porque o canal durável
    pode ser justamente o que falhou.

    ⚠️ A troca de senha por TERCEIRO sai em WARNING, sozinha. Ela e o vetor medido:
    o alvo perde o acesso e nada no produto lhe diz por que. As demais saem em INFO —
    editar nome ou desativar tambem e administracao, mas nao tira a conta de ninguem.
    """
    ator_id    = str(claims.get("sub") or "")
    ator_email = str(claims.get("email") or "")
    alvo_id    = str(alvo.get("id") or "")
    alvo_email = str(alvo.get("email") or "")
    proprio    = ator_id == alvo_id

    if "password" in campos and not proprio:
        logger.warning(
            "RESET DE SENHA POR TERCEIRO: %s (%s) redefiniu a senha de %s — o alvo "
            "perde o acesso e nao e avisado por caminho nenhum do produto",
            ator_email or ator_id, ",".join(claims.get("roles") or []), alvo_email or alvo_id,
        )
    else:
        logger.info(
            "administracao de usuario: %s -> %s (%s: %s)",
            ator_email or ator_id, alvo_email or alvo_id, acao, ",".join(campos) or "-",
        )

    await db_mod.registrar_admin_de_usuario(
        pool,
        tenant_id    = str(claims.get("tenant_id") or ""),
        actor_id     = ator_id,
        actor_email  = ator_email,
        target_id    = alvo_id,
        target_email = alvo_email,
        acao         = acao,
        campos       = campos,
    )


def _irrestrito_para_pessoas(claims: dict[str, Any]) -> bool:
    """O caminho UNIVERSAL, e ele e DECLARADO — nao um bypass silencioso.

    `admin` e o papel dono do tenant: ele administra todo mundo por definicao, e e
    o unico caminho que sobrevive a um parque com ZERO grupos (medido em
    2026-09-09: 0 grupos, 0 membros, 0 supervisores). Sem esta linha, ligar o
    organograma deixaria o tenant sem NINGUEM capaz de administrar ninguem — a
    tranca que prende o dono do lado de fora.
    """
    return "admin" in (claims.get("roles") or [])


async def _assert_pode_administrar(
    pool, claims: dict[str, Any], alvo: dict[str, Any], acao: str,
) -> None:
    """AUT-39 — o eixo de ADMINISTRACAO e o organograma (Arc 9), nao o pool.

    Medido ao vivo em 2026-09-09, antes desta regra: um supervisor com `config.users`
    e **zero pools** listou os 9 usuarios do tenant, trocou a senha de um usuario de
    outro time (HTTP 200), ENTROU na conta, e a senha original deixou de valer. Nao e
    escalacao — o alvo nao esta acima —, e sim **tomada lateral entre times**, e para
    ela nao existia eixo onde declarar *"administro estas pessoas"*.

    ⚠️ A alternativa por POOL (`pools(alvo) ⊆ pools(ator)`) foi RECUSADA por censo, e
    a razao nao e gosto: o `admin@` so administrava os outros porque ENUMERAVA os 41
    pools do registry, e **um pool novo o tirava dessa condicao sem erro em lugar
    nenhum**. Membership de grupo e explicita — quem nao esta em grupo nenhum e
    recusado, o que e uma NEGACAO visivel, nao um silencio.

    ⚠️ Isto NAO substitui `_assert_may_touch`: aquele protege o alvo PRIVILEGIADO
    (quem detem `config.permissions`) de quem so administra pessoas. Os dois valem,
    e valem por razoes diferentes — um e sobre QUEM e o alvo, o outro sobre se ele e
    MEU.
    """
    ator_id = str(claims.get("sub") or "")
    alvo_id = str(alvo.get("id") or "")
    if ator_id and ator_id == alvo_id:
        return
    if _irrestrito_para_pessoas(claims):
        return
    if await db_mod.supervisiona(pool, ator_id, alvo_id):
        return
    logger.warning(
        "administracao NEGADA: %s tentou %s de %s, que nao e membro de grupo algum "
        "que ele supervisione",
        claims.get("email") or ator_id, acao, alvo.get("email") or alvo_id,
    )
    raise HTTPException(
        status_code=403,
        detail=(
            f"forbidden: {acao} exige que o alvo seja membro de um grupo que voce "
            f"supervisione (Configuracao > Grupos)"
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
        created_from_template_id=(str(row["created_from_template_id"])
                                  if row.get("created_from_template_id") else None),
        created_from_template_hash=row.get("created_from_template_hash"),
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
    pool = _get_pool(request)
    # MOD-02/E2: nascer com papel/escopo e CONCEDER. O master passa direto; o
    # delegado passa pelo guard de RANK.
    #
    # ⚠️ AQUI O GUARD E INCONDICIONAL, e o `model_fields_set` do PATCH nao serve —
    # medido em 2026-09-08 (MOD-05). Ele valia para a EDICAO, onde nao enviar o campo
    # significa nao mexer nele; na CRIACAO a certidao de nascimento e emitida SEMPRE,
    # porque `CreateUserRequest.roles` tem default `["operator"]` e o preset e aplicado
    # logo abaixo. Com a condicao, um delegado que so detinha `config.users` criava um
    # operator COMPLETO — os 6 campos do preset, nenhum deles seu — bastando OMITIR o
    # campo. Fail-open pela porta do default, que e o "valor plausivel" da § Postura:
    # o corpo mais curto era o que passava.
    await _assert_pode_conceder(
        pool, claims, "criar usuario",
        roles=body.roles, accessible_pools=body.accessible_pools,
    )
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


# ─── Criacao POR TEMPLATE (MOD-09 / G2) ───────────────────────────────────────
#
# D3: **a capacidade vem do TEMPLATE, nunca do corpo.** O corpo desta rota nao aceita
# `roles` nem `module_config` — o servidor os le da linha armazenada. Isso preserva o
# discriminador `model_fields_set` das outras rotas (o que o chamador ENVIOU e que e
# conceder) e fecha o caminho que a copia-no-cliente tinha: ate aqui o formulario
# copiava o template e mandava a capacidade num `POST /users` comum, e o servidor nao
# tinha como saber que um template estava envolvido.
#
# E4: **o template nao carrega pools.** Pool e do APLICADOR — ele escolhe dentre os
# seus, e o guard de RANK confere a contencao. Um mesmo template "Operador" serve
# entao todos os supervisores, e a pergunta "qual template para qual time" deixa de
# existir. Se a linha do template trouxer `accessible_pools` (formato antigo da tela),
# ele e IGNORADO — e a resposta diz isso, em vez de aplicar em silencio.
#
# ⚠️ NAO existe `delegable`. Ele foi removido na emenda: existia para aprovar um
# pacote que ATRAVESSARIA o guard, e sem travessia nao ha o que aprovar. Quem protege
# aqui e o mesmo `_assert_pode_conceder` das outras portas — um template mais rico que
# o aplicador e recusado NOMEANDO o campo, independentemente de quem o escreveu.

def _hash_config(config: dict[str, Any]) -> str:
    """SHA-256 da forma canonica do que foi aplicado.

    Sem o hash, "veio do template X" nao distingue quem nasceu do X de ontem de quem
    nasceu do X de hoje — e o template e editavel. Canonico = chaves ordenadas e sem
    espacos, senao a mesma config gera hashes diferentes por ordem de insercao.
    """
    import hashlib
    bruto = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


@router.post("/users/from-template/{template_id}", response_model=UserResponse,
             status_code=201, dependencies=[Depends(_USUARIOS_WRITE)])
async def create_user_from_template(
    template_id: str,
    body: CreateUserFromTemplateRequest,
    request: Request,
    claims: dict[str, Any] = Depends(_USUARIOS_WRITE),
) -> UserResponse:
    pool = _get_pool(request)
    linha = await perms_mod.get_template(pool, template_id)
    if not linha:
        raise HTTPException(status_code=404, detail="Template not found")
    cfg = linha.get("config")
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    cfg = cfg if isinstance(cfg, dict) else {}

    papel = cfg.get("role")
    roles = [papel] if isinstance(papel, str) and papel else ["operator"]
    tpl_mc = cfg.get("module_config") if isinstance(cfg.get("module_config"), dict) else {}

    # O guard de RANK vale AQUI como em qualquer outra concessao. E ele roda sobre o
    # EFEITO (`preset(role) UNIAO module_config`), senao um template `{role: "admin"}`
    # com `module_config` vazio atravessaria concedendo tudo pela porta do preset.
    await _assert_pode_conceder(
        pool, claims, f"aplicar o template `{linha.get('name', template_id)}`",
        module_config=tpl_mc, roles=roles,
        accessible_pools=body.accessible_pools,
    )

    existing = await db_mod.get_user_by_email(pool, body.tenant_id, body.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered in this tenant")

    aplicado = {"role": papel, "module_config": tpl_mc}
    row = await db_mod.create_user(
        pool,
        tenant_id=body.tenant_id,
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        roles=roles,
        accessible_pools=body.accessible_pools,
        max_concurrent_sessions=int(cfg.get("max_concurrent_sessions") or 3),
        created_from_template_id=str(linha["id"]),
        created_from_template_hash=_hash_config(aplicado),
    )

    # Preset do papel PRIMEIRO, template por cima: o template e a intencao explicita de
    # quem aplicou, e o preset e o piso do papel. A ordem inversa deixaria o preset
    # sobrescrevendo uma escolha deliberada.
    base = await presets_mod.apply_role_preset(pool, str(row["id"]), roles, body.email)
    final = dict(base or {})
    for modulo, campos in tpl_mc.items():
        if isinstance(campos, dict):
            final.setdefault(modulo, {}).update(campos)
    if final:
        await db_mod.set_user_module_config(pool, str(row["id"]), final)
        row["module_config"] = final

    if cfg.get("accessible_pools"):
        # E4: pool e do aplicador. Ignorar em silencio faria o operador crer que o
        # template definiu o escopo — e o escopo real seria outro.
        logger.info(
            "template %s traz `accessible_pools` e ele foi IGNORADO (E4: pool e do "
            "aplicador); valeram os pools do corpo: %s",
            template_id, body.accessible_pools,
        )
    return _user_to_response(row)


@router.get("/users", response_model=list[UserResponse],
            dependencies=[Depends(_USUARIOS_READ)])
async def list_users(
    request: Request,
    tenant_id: str = "tenant_demo",
    limit: int = 100,
    offset: int = 0,
    claims: dict[str, Any] = Depends(_USUARIOS_READ),
) -> list[UserResponse]:
    pool = _get_pool(request)
    # `None` = sem recorte (admin). Caso contrario, o organograma decide — e ele
    # decide no SQL, nunca depois do LIMIT.
    quem = None if _irrestrito_para_pessoas(claims) else str(claims.get("sub") or "")
    rows = await db_mod.list_users(
        pool, tenant_id, limit=limit, offset=offset, administravel_por=quem)
    return [_user_to_response(r) for r in rows]


@router.get("/users/{user_id}", response_model=UserResponse,
            dependencies=[Depends(_USUARIOS_READ)])
async def get_user(
    user_id: str,
    request: Request,
    claims: dict[str, Any] = Depends(_USUARIOS_READ),
) -> UserResponse:
    pool = _get_pool(request)
    row = await db_mod.get_user_by_id(pool, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    await _assert_pode_administrar(pool, claims, row, "ver")
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
    if set(body.model_fields_set) & _CAPACITY_FIELDS:
        await _assert_pode_conceder(
            pool, claims, "editar usuario",
            roles=body.roles if "roles" in body.model_fields_set else None,
            accessible_pools=(body.accessible_pools
                              if "accessible_pools" in body.model_fields_set else None),
        )
    await _assert_pode_administrar(pool, claims, existing, "editar")
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
    # Depois da mutacao, de proposito: registrar antes gravaria uma alteracao que
    # pode nao ter acontecido. O `campos` sai do `model_fields_set` — o que foi
    # ENVIADO —, e nao dos valores, pela mesma razao do `_CAPACITY_FIELDS`.
    await _registrar_trilha(pool, claims, existing, "update", sorted(body.model_fields_set))
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
        await _assert_pode_administrar(pool, claims, alvo, "remover")
        _assert_may_touch(claims, alvo, "remover")
    deleted = await db_mod.delete_user(pool, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    # A trilha sobrevive ao alvo: `user_admin_log.target_id` nao tem FK, senao o
    # CASCADE apagaria o registro de quem apagou.
    if alvo:
        await _registrar_trilha(pool, claims, alvo, "delete", [])


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


# MOD-02/E2: passou de `_PERMS_READ` para `_USUARIOS_READ`. O delegado precisa LER
# antes de escrever — o PUT abaixo SUBSTITUI o config inteiro, e um formulario
# hidratado com o que o chamador nao pode ver salvaria por cima com o vazio.
@router.get("/users/{user_id}/module-config", response_model=dict,
            dependencies=[Depends(_USUARIOS_READ)])
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
            dependencies=[Depends(_USUARIOS_WRITE)])
async def set_user_module_config(
    user_id: str, body: dict, request: Request,
    claims: dict[str, Any] = Depends(_USUARIOS_WRITE),
) -> dict:
    """
    Substitui todo o module_config do usuário.
    Valida cada módulo presente contra o schema registrado em auth.module_registry.
    Retorna 422 se houver violações de schema.
    """
    pool = _get_pool(request)
    existing = await db_mod.get_user_by_id(pool, user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")

    # MOD-02/E2 — a SEGUNDA porta. Ate aqui esta rota exigia `config.permissions` e
    # NAO comparava nada com o config do chamador: quem a alcancasse concedia
    # qualquer campo a qualquer um. Guard so na rota de apply-template seria "duas
    # portas para o mesmo dado, e so uma trancada".
    _assert_may_touch(claims, existing, "escrever o module_config")
    await _assert_pode_conceder(
        pool, claims, "escrever o module_config",
        module_config=body,
        atual=await db_mod.get_user_module_config(pool, user_id),
    )

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
              dependencies=[Depends(_USUARIOS_WRITE)])
async def patch_user_module_config(
    user_id: str,
    module_id: str,
    body: dict,
    request: Request,
    claims: dict[str, Any] = Depends(_USUARIOS_WRITE),
) -> dict:
    """
    Atualiza a config de um módulo específico do usuário sem sobrescrever os outros módulos.
    Valida contra o schema do módulo antes de persistir.
    """
    pool = _get_pool(request)
    # MOD-02/E2: mesma porta, mesmo predicado. O corpo aqui e o config de UM modulo,
    # entao ele viaja embrulhado com a chave do modulo — o guard raciocina sobre
    # `modulo.campo`, e passar o corpo cru faria os campos virarem modulos.
    alvo = await db_mod.get_user_by_id(pool, user_id)
    if alvo:
        _assert_may_touch(claims, alvo, "escrever o module_config")
        await _assert_pode_conceder(
            pool, claims, f"escrever o module_config de `{module_id}`",
            module_config={module_id: body},
            atual=await db_mod.get_user_module_config(pool, user_id),
        )

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
