"""
models.py
Pydantic schemas de entrada/saída da auth-api.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, EmailStr, Field


# ─── Roles ────────────────────────────────────────────────────────────────────

Role = Literal["operator", "supervisor", "admin", "devops", "business"]


# ─── Requests ─────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str
    tenant_id: str = "tenant_demo"


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


# ══════════════════════════════════════════════════════════════════════════════
# LÁPIDE — `unrestricted` (AUT-15, 2026-08-31)
# ══════════════════════════════════════════════════════════════════════════════
#
# O campo saiu do produto: escopo de pool é sempre ENUMERADO, e não há declaração de
# "sem recorte" para usuário (o claim caiu na AUT-13, o ramo do `resolve_scope` na
# AUT-12). Mas *remover um campo de entrada não é neutro* — pydantic ignora chave
# desconhecida por default, então quem continuasse mandando `{"unrestricted": true}`
# receberia **200 e nada aconteceria**: uma concessão que o chamador acredita ter feito.
#
# Medido em 2026-08-31, logo após a remoção: `PATCH {"unrestricted":true}` → **200**.
# E havia dois remetentes reais — o `infra/seed/seed_auth.py` e o `S4b` do
# `probe_config_permissions_split.sh`, que exigia 403 e passaria a ver 200.
#
# O precedente da casa é recusar nomeando, e está escrito em `/reports/resources/tokens`:
# *"Parâmetro desconhecido é ignorado sem aviso, e `pool_id` não é desconhecido: ele
# EXISTE em todas as rotas vizinhas."* Aqui é mais forte ainda — este campo existia
# NESTE endpoint até hoje.
#
# ⚠️ O tipo anotado é `Any`, NÃO `None`. Com `None` o pydantic reprova pelo TIPO antes
# de chamar o validador, e o cliente recebe `"Input should be None"` — recusa correta
# com a razão PERDIDA, que é a degradação muda desta seção em miniatura. Medido: foi
# exatamente o que saiu da primeira versão desta lápide.
#
# ⚠️ **422, não 403.** O 403 diria *"você poderia, com o grant certo"*, e isso é falso:
# ninguém pode, porque a coisa não existe mais. O código de status é parte da mensagem.
def _recusa_lapide(v: Any) -> Any:
    if v is not None:
        raise ValueError(
            "campo REMOVIDO em 2026-08-31 (AUT-15): escopo de pool é sempre enumerado em "
            "`accessible_pools`; não existe declaração de 'sem recorte' para usuário. "
            "Para dar alcance total, envie a lista completa de pools do tenant."
        )
    return v


class CreateUserRequest(BaseModel):
    tenant_id: str
    email: str
    password: str = Field(min_length=8)
    name: str = ""
    roles: list[Role] = ["operator"]
    accessible_pools: list[str] = []   # NENHUM pool (AUT-03); irrestrito não é declarável
    unrestricted: Annotated[Any, AfterValidator(_recusa_lapide)] = None   # ver LÁPIDE
    max_concurrent_sessions: int = Field(default=3, ge=1, le=50)
    # AUT-44 — o TIME em que a pessoa nasce. Vazio e legitimo para `admin` (ele
    # administra todo mundo por definicao); para delegado e RECUSADO, porque criar
    # alguem que voce nao pode ver nem editar nao e contratar, e produzir orfao.
    group_ids: list[str] = []


class UpdateUserRequest(BaseModel):
    name: str | None = None
    password: str | None = Field(default=None, min_length=8)
    roles: list[Role] | None = None
    accessible_pools: list[str] | None = None
    unrestricted: Annotated[Any, AfterValidator(_recusa_lapide)] = None   # ver LÁPIDE
    active: bool | None = None
    max_concurrent_sessions: int | None = Field(default=None, ge=1, le=50)


# ─── Responses ────────────────────────────────────────────────────────────────

class TokenUserInfo(BaseModel):
    id: str
    email: str
    name: str
    roles: list[str]
    tenant_id: str
    accessible_pools: list[str]
    max_concurrent_sessions: int = 3     # capacity shared across all pools
    module_config: dict[str, Any] = {}   # ABAC config por módulo (carregado do JWT)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int          # seconds
    user: TokenUserInfo


class UserResponse(BaseModel):
    id: str
    tenant_id: str
    email: str
    name: str
    roles: list[str]
    accessible_pools: list[str]
    max_concurrent_sessions: int = 3
    active: bool
    created_at: str
    updated_at: str
    # PROVENIENCIA (MOD-09 / E4): de qual template esta pessoa NASCEU, e com qual
    # conteudo. Carimbo IMUTAVEL, nunca consultado para autorizar — o template e
    # preset, e quem nasceu dele segue a propria vida. Serve a pergunta de auditoria
    # ("quem veio do template X?"), que a copia pura nao sabia responder.
    created_from_template_id: str | None = None
    created_from_template_hash: str | None = None


class CreateUserFromTemplateRequest(BaseModel):
    """Corpo da criacao POR TEMPLATE — sem NENHUM campo de capacidade de modulo.

    ⚠️ `roles` e `module_config` NAO estao aqui de proposito (D3): a capacidade vem da
    LINHA ARMAZENADA do template, nunca do corpo. Aceita-los reabriria o caminho que a
    copia-no-cliente tinha — o formulario mandava a capacidade e o servidor so via um
    `POST /users` comum, sem saber que um template estava envolvido.

    ⚠️ `accessible_pools` FICA, e e a excecao com razao: pelo E4 o template NAO carrega
    pools. Pool e do aplicador — ele escolhe dentre os seus, e o guard de RANK confere
    a contencao. Assim um mesmo template "Operador" serve todos os supervisores, e a
    pergunta "qual template para qual time" deixa de existir.
    """
    tenant_id: str
    email: str
    password: str = Field(min_length=8)
    name: str = ""
    group_ids: list[str] = []   # AUT-44 — ver CreateUserRequest
    accessible_pools: list[str] = []


class MeResponse(BaseModel):
    sub: str
    tenant_id: str
    email: str
    name: str
    roles: list[str]
    accessible_pools: list[str]
    module_config: dict[str, Any] = {}   # ABAC config por módulo
    max_concurrent_sessions: int = 3


# ─── Permissions & Templates ───────────────────────────────────────────────────

class CreateTemplateRequest(BaseModel):
    tenant_id: str
    name: str
    description: str = ""
    # Fase 1 (preset copy-on-create): snapshot rico do cadastro de usuário
    # {role, module_config, accessible_pools, max_concurrent_sessions}.
    config: dict = {}


class UpdateTemplateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    config: dict | None = None


class TemplateResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str
    config: dict
    created_at: str
    updated_at: str


