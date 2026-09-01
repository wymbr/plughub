"""
test_router.py
Testes da auth-api — mock asyncpg, sem banco real.

Cobertura:
  TestHealth                 — GET /health
  TestLogin                  — login OK, senha errada, usuário inativo
  TestRefresh                — refresh token rotation, token inválido
  TestLogout                 — logout OK e idempotente
  TestMe                     — Bearer válido, sem token, token expirado
  TestCreateUser             — criação OK, e-mail duplicado, 401 sem Bearer, 403 sem grant
                               e 403 com grant `read_only`
  TestListUsers              — listagem filtrada por tenant, `read_only` basta, 403 sem grant
  TestGetUser                — OK, não encontrado
  TestUpdateUser             — update parcial (name, password, roles)
  TestDeleteUser             — OK, não encontrado
  TestSeedAdmin              — seed_admin_if_absent cria e é idempotente
  TestPasswordUtils          — hash/verify
  TestJwtUtils               — encode/decode, expirado
  TestHashRefreshToken       — determinístico, diferente do plain
  TestGrantPermission        — grant OK, 401 sem Bearer, 403 sem grant, 401 com token expirado
  TestListPermissions        — list com filtros user_id/module
  TestRevokePermission       — revoke OK e not-found
  TestResolvePermission      — allowed true/false, global e pool scope
  TestTemplates              — CRUD completo de templates
  TestApplyTemplate          — materializa permissões de template em platform_permissions
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import json as _json
import pytest
from fastapi.testclient import TestClient

from plughub_auth_api.config import Settings
from plughub_auth_api.jwt_utils import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from plughub_auth_api.main import build_app
from plughub_auth_api.password import hash_password, verify_password

# ─── Fixtures ────────────────────────────────────────────────────────────────


TEST_SETTINGS = Settings(
    database_url="postgresql://unused",
    jwt_secret="test_secret_key_that_is_long_enough_32c",
    jwt_algorithm="HS256",
    access_token_expire_minutes=60,
    refresh_token_expire_days=7,
    admin_token="test-admin-token",
    seed_admin_email="seed@test.local",
    seed_admin_password="seed_pw_1234",
    seed_tenant_id="tenant_test",
    port=3200,
)

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

_SAMPLE_USER: dict[str, Any] = {
    "id": uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"),
    "tenant_id": "tenant_test",
    "email": "user@test.local",
    "name": "Test User",
    "password_hash": hash_password("correct_pw"),
    "roles": ["operator"],
    "accessible_pools": [],
    "active": True,
    "created_at": _NOW,
    "updated_at": _NOW,
}


def _user_copy(**overrides) -> dict[str, Any]:
    return {**_SAMPLE_USER, **overrides}


@pytest.fixture()
def mock_pool():
    """Pool asyncpg mockado."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock()
    pool.execute = AsyncMock()
    pool.acquire = MagicMock()
    pool.close = AsyncMock()
    return pool


@pytest.fixture()
def client(mock_pool):
    """TestClient com pool injetado e seed suprimido.

    **Todo I/O do lifespan tem de ser mockado — inclusive o que foi acrescentado
    depois (corrigido 2026-08-02).** `ensure_permissions_schema` e
    `_register_platform_modules` entraram no `lifespan` sem entrar aqui, e o efeito não
    foi erro: foi LENTIDÃO. `ensure_permissions_schema` roda DENTRO do laço de 10
    tentativas (`main.py:138-149`), falha contra o pool mockado, e cada tentativa dorme
    `2 × attempt` — 2+4+…+20 = **110 s por teste**, com a fixture em escopo de função e
    58 testes. A suíte deixou de terminar.

    Ninguém percebeu porque esta suíte **não roda no container**: o `Dockerfile` copia
    só `src/` e o `testpaths` é `["tests"]`, na raiz do pacote. Suíte mantida, nunca
    executada — o mesmo desfecho do `ai-gateway`, com outro mecanismo.

    **Regra:** ao acrescentar qualquer `await` ao `lifespan`, acrescentar o `patch`
    correspondente aqui. Um lifespan que faz I/O real num teste de unidade não falha —
    ele fica lento, e lentidão não tem cor no relatório.
    """
    app = build_app()

    # Injeta pool no state ANTES do lifespan completar
    with patch("plughub_auth_api.main.asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)), \
         patch("plughub_auth_api.main.db_mod.ensure_schema", new=AsyncMock()), \
         patch("plughub_auth_api.main.ensure_permissions_schema", new=AsyncMock()), \
         patch("plughub_auth_api.main._register_platform_modules", new=AsyncMock()), \
         patch("plughub_auth_api.main.db_mod.seed_admin_if_absent", new=AsyncMock(return_value=False)), \
         patch("plughub_auth_api.router.get_settings", return_value=TEST_SETTINGS), \
         patch("plughub_auth_api.main.get_settings", return_value=TEST_SETTINGS):
        with TestClient(app, raise_server_exceptions=True) as c:
            c.app.state.pool = mock_pool
            yield c, mock_pool


def _access_token(
    user: dict[str, Any] | None = None,
    module_config: dict[str, Any] | None = None,
) -> str:
    u = user or _SAMPLE_USER
    return create_access_token(
        user_id=str(u["id"]),
        tenant_id=u["tenant_id"],
        email=u["email"],
        name=u["name"],
        roles=list(u["roles"]),
        accessible_pools=list(u["accessible_pools"]),
        settings=TEST_SETTINGS,
        module_config=module_config,
    )


# ─── Autorização das rotas de gestão (G-PROBE, 2026-06-26) ────────────────────
# Gestão de usuários/permissões/templates/módulos NÃO usa mais `X-Admin-Token`:
# autoriza pelo JWT do operador + ABAC `config.users` (`router.py:62-66`,
# *"Strict: sem fallback de admin-token"*). Um teste que ainda mandasse o header
# antigo receberia **401 por falta de Bearer** — que é o que os 23 vermelhos
# relatavam (`assert 401 == 204`), e não um defeito de código.
#
# `_check_config_field` é **grant-first**: ausência do campo = deny. Logo os dois
# modos de recusa têm status DIFERENTES, e é por isso que os testes negativos
# abaixo afirmam o status exato em vez de "não-2xx" — um `assert r.status_code
# >= 400` passaria pelos dois motivos errados (rota inexistente, payload inválido).
#
#   sem Authorization            → 401 (_bearer_claims)
#   Bearer sem config.users      → 403 (_require_config_usuarios)
#   Bearer read_only em escrita  → 403 (rank insuficiente)


def _usuarios_token(access: str = "read_write") -> str:
    """JWT com `config.users` — administrar PESSOAS."""
    return _access_token(module_config={"config": {"users": {"access": access, "scope": []}}})


def _admin_headers(access: str = "read_write") -> dict[str, str]:
    return {"Authorization": f"Bearer {_usuarios_token(access)}"}


# ── `config.users` × `config.permissions` — os dois grants NÃO se substituem ──
#
# ⚠️ Estes helpers nasceram em 2026-08-28, consertando **15 controles POSITIVOS que
# estavam vermelhos desde o split de 2026-08-27**. Naquele dia `config.users` deixou de
# ser a chave-mestra e as rotas de CAPACIDADE (`/permissions`, `/templates`, `/modules`,
# `/module-config`) passaram a exigir `config.permissions` — mas o `_admin_headers`
# continuou cunhando só o grant de pessoas. Resultado: toda rota de capacidade
# respondia 403 no teste, e o suite não tinha prova nenhuma de que ELAS DEIXAM ALGUÉM
# PASSAR. Os negativos seguiam verdes por acidente, pelo motivo errado.
#
# É a mesma família do que se mediu na pricing e no config-api no passo 2 da migração
# do verificador: o teste concedia um campo que o backend nunca conferiu. E o modo de
# falha é o de sempre — **o vermelho de um controle positivo parece "a rota está
# protegida"**, que é justamente o que se queria ver.
#
# Os três helpers ficam SEPARADOS de propósito. Um `_headers()` que cunhasse tudo
# apagaria a distinção que o split existe para criar, e o suite voltaria a não saber
# dizer qual porta abriu.


def _perms_headers(access: str = "read_write") -> dict[str, str]:
    """JWT com `config.permissions` — conceder CAPACIDADE (papéis, módulos, escopo)."""
    tok = _access_token(
        module_config={"config": {"permissions": {"access": access, "scope": []}}}
    )
    return {"Authorization": f"Bearer {tok}"}


def _ambos_headers(access: str = "read_write") -> dict[str, str]:
    """Os DOIS grants. Necessário quando o CORPO carrega campo de capacidade.

    `_assert_may_grant` recusa alto sobre `model_fields_set`: enviar `roles` num
    `POST /users` é CONCEDER, ainda que o valor coincida com o default — então a rota é
    `config.users` e o corpo pede `config.permissions`.
    """
    tok = _access_token(
        module_config={
            "config": {
                "users":       {"access": access, "scope": []},
                "permissions": {"access": access, "scope": []},
            }
        }
    )
    return {"Authorization": f"Bearer {tok}"}


# ─── TestHealth ───────────────────────────────────────────────────────────────


class TestHealth:
    def test_health_ok(self, client):
        c, _ = client
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ─── TestLogin ────────────────────────────────────────────────────────────────


class TestLogin:
    def test_login_ok(self, client):
        c, pool = client
        pool.fetchrow.return_value = _SAMPLE_USER
        pool.execute.return_value = "INSERT 0 1"

        with patch("plughub_auth_api.router.db_mod.get_user_by_email", new=AsyncMock(return_value=_SAMPLE_USER)), \
             patch("plughub_auth_api.router.db_mod.create_session", new=AsyncMock(return_value=str(uuid.uuid4()))):
            r = c.post("/auth/login", json={"email": "user@test.local", "password": "correct_pw", "tenant_id": "tenant_test"})

        assert r.status_code == 200
        body = r.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"

    def test_login_wrong_password(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_email", new=AsyncMock(return_value=_SAMPLE_USER)):
            r = c.post("/auth/login", json={"email": "user@test.local", "password": "wrong", "tenant_id": "tenant_test"})
        assert r.status_code == 401

    def test_login_user_not_found(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_email", new=AsyncMock(return_value=None)):
            r = c.post("/auth/login", json={"email": "nobody@x.com", "password": "pw", "tenant_id": "tenant_test"})
        assert r.status_code == 401

    def test_login_inactive_user(self, client):
        c, _ = client
        inactive = _user_copy(active=False)
        with patch("plughub_auth_api.router.db_mod.get_user_by_email", new=AsyncMock(return_value=inactive)):
            r = c.post("/auth/login", json={"email": "user@test.local", "password": "correct_pw", "tenant_id": "tenant_test"})
        assert r.status_code == 403


# ─── TestRefresh ──────────────────────────────────────────────────────────────


class TestRefresh:
    def test_refresh_ok(self, client):
        c, _ = client
        plain = generate_refresh_token()
        session = {
            "id": uuid.uuid4(),
            "user_id": _SAMPLE_USER["id"],
            "tenant_id": "tenant_test",
            "refresh_token_hash": hash_refresh_token(plain),
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        }
        with patch("plughub_auth_api.router.db_mod.get_session_by_token_hash", new=AsyncMock(return_value=session)), \
             patch("plughub_auth_api.router.db_mod.get_user_by_id", new=AsyncMock(return_value=_SAMPLE_USER)), \
             patch("plughub_auth_api.router.db_mod.rotate_session", new=AsyncMock(return_value=True)):
            r = c.post("/auth/refresh", json={"refresh_token": plain})
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_refresh_invalid_token(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_session_by_token_hash", new=AsyncMock(return_value=None)):
            r = c.post("/auth/refresh", json={"refresh_token": "bogus"})
        assert r.status_code == 401

    def test_refresh_inactive_user(self, client):
        c, _ = client
        plain = generate_refresh_token()
        session = {"user_id": _SAMPLE_USER["id"], "tenant_id": "tenant_test"}
        inactive = _user_copy(active=False)
        with patch("plughub_auth_api.router.db_mod.get_session_by_token_hash", new=AsyncMock(return_value=session)), \
             patch("plughub_auth_api.router.db_mod.get_user_by_id", new=AsyncMock(return_value=inactive)):
            r = c.post("/auth/refresh", json={"refresh_token": plain})
        assert r.status_code == 403


# ─── TestLogout ───────────────────────────────────────────────────────────────


class TestLogout:
    def test_logout_ok(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.delete_session", new=AsyncMock(return_value=True)):
            r = c.post("/auth/logout", json={"refresh_token": "anytoken"})
        assert r.status_code == 204

    def test_logout_idempotent(self, client):
        c, _ = client
        # Mesmo token inválido → sem erro (idempotente)
        with patch("plughub_auth_api.router.db_mod.delete_session", new=AsyncMock(return_value=False)):
            r = c.post("/auth/logout", json={"refresh_token": "notfound"})
        assert r.status_code == 204


# ─── TestMe ───────────────────────────────────────────────────────────────────


class TestMe:
    def test_me_ok(self, client):
        c, _ = client
        token = _access_token()
        r = c.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == _SAMPLE_USER["email"]
        assert "operator" in body["roles"]

    def test_me_no_token(self, client):
        c, _ = client
        r = c.get("/auth/me")
        assert r.status_code == 401

    def test_me_bad_token(self, client):
        c, _ = client
        r = c.get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401


# ─── TestCreateUser ───────────────────────────────────────────────────────────


class TestCreateUser:
    def test_create_ok(self, client):
        c, _ = client
        created = _user_copy(email="new@test.local", roles=["supervisor"])
        with patch("plughub_auth_api.router.db_mod.get_user_by_email", new=AsyncMock(return_value=None)), \
             patch("plughub_auth_api.router.db_mod.create_user", new=AsyncMock(return_value=created)):
            r = c.post("/auth/users",
                       json={"tenant_id": "tenant_test", "email": "new@test.local",
                             "password": "password123", "roles": ["supervisor"]},
                       headers=_ambos_headers())
        assert r.status_code == 201
        assert r.json()["email"] == "new@test.local"

    def test_create_duplicate_email(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_email", new=AsyncMock(return_value=_SAMPLE_USER)):
            r = c.post("/auth/users",
                       json={"tenant_id": "tenant_test", "email": "user@test.local",
                             "password": "password123"},
                       headers=_admin_headers())
        assert r.status_code == 409

    def test_create_without_bearer_is_401(self, client):
        c, _ = client
        r = c.post("/auth/users",
                   json={"tenant_id": "tenant_test", "email": "x@x.com", "password": "pw12345678"})
        assert r.status_code == 401

    def test_create_with_bearer_but_no_grant_is_403(self, client):
        """Grant-first: token válido SEM `config.usuarios` é 403, não 401.

        Distinguir os dois é o ponto — 401 diz "não sei quem é você" e 403 diz
        "sei, e não pode". Se o gate ABAC sumisse, este caso viraria 201 e o
        de cima continuaria verde.
        """
        c, _ = client
        r = c.post("/auth/users",
                   json={"tenant_id": "tenant_test", "email": "x@x.com", "password": "pw12345678"},
                   headers={"Authorization": f"Bearer {_access_token()}"})
        assert r.status_code == 403

    def test_create_with_read_only_grant_is_403(self, client):
        """`read_only` lê, não escreve — `_ACCESS_RANK` 1 < 2."""
        c, _ = client
        r = c.post("/auth/users",
                   json={"tenant_id": "tenant_test", "email": "x@x.com", "password": "pw12345678"},
                   headers=_admin_headers("read_only"))
        assert r.status_code == 403


# ─── TestListUsers ────────────────────────────────────────────────────────────


class TestListUsers:
    def test_list_users(self, client):
        c, _ = client
        users = [_SAMPLE_USER, _user_copy(email="b@test.local")]
        with patch("plughub_auth_api.router.db_mod.list_users", new=AsyncMock(return_value=users)):
            r = c.get("/auth/users?tenant_id=tenant_test",
                      headers=_admin_headers())
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_list_users_read_only_grant_is_enough(self, client):
        """Controle positivo do rank: leitura aceita `read_only`.

        Sem ele, os testes de 403 acima passariam mesmo se o gate recusasse
        TUDO — verde por recusa universal, que é o modo de falha mais fácil de
        não ver num teste negativo.
        """
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.list_users", new=AsyncMock(return_value=[_SAMPLE_USER])):
            r = c.get("/auth/users?tenant_id=tenant_test",
                      headers=_admin_headers("read_only"))
        assert r.status_code == 200

    def test_list_users_without_grant_is_403(self, client):
        c, _ = client
        r = c.get("/auth/users?tenant_id=tenant_test",
                  headers={"Authorization": f"Bearer {_access_token()}"})
        assert r.status_code == 403


# ─── TestGetUser ──────────────────────────────────────────────────────────────


class TestGetUser:
    def test_get_ok(self, client):
        c, _ = client
        uid = str(_SAMPLE_USER["id"])
        with patch("plughub_auth_api.router.db_mod.get_user_by_id", new=AsyncMock(return_value=_SAMPLE_USER)):
            r = c.get(f"/auth/users/{uid}", headers=_admin_headers())
        assert r.status_code == 200
        assert r.json()["id"] == uid

    def test_get_not_found(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_id", new=AsyncMock(return_value=None)):
            r = c.get(f"/auth/users/{uuid.uuid4()}", headers=_admin_headers())
        assert r.status_code == 404


# ─── TestUpdateUser ───────────────────────────────────────────────────────────


class TestUpdateUser:
    def test_update_name(self, client):
        c, _ = client
        updated = _user_copy(name="New Name")
        uid = str(_SAMPLE_USER["id"])
        with patch("plughub_auth_api.router.db_mod.get_user_by_id", new=AsyncMock(return_value=_SAMPLE_USER)), \
             patch("plughub_auth_api.router.db_mod.update_user", new=AsyncMock(return_value=updated)):
            r = c.patch(f"/auth/users/{uid}",
                        json={"name": "New Name"},
                        headers=_admin_headers())
        assert r.status_code == 200
        assert r.json()["name"] == "New Name"

    def test_update_not_found(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_id", new=AsyncMock(return_value=None)):
            r = c.patch(f"/auth/users/{uuid.uuid4()}",
                        json={"name": "x"},
                        headers=_admin_headers())
        assert r.status_code == 404


# ─── TestDeleteUser ───────────────────────────────────────────────────────────


class TestDeleteUser:
    def test_delete_ok(self, client):
        c, _ = client
        uid = str(_SAMPLE_USER["id"])
        # ⚠️ `get_user_by_id` PRECISA ser mockado desde o split de 2026-08-27: o handler
        # busca o ALVO para `_assert_may_touch`. Sem o patch, a função real rodava sobre
        # o `AsyncMock` do pool e o teste estourava `TypeError` em `db.py:336` — falha
        # que parece de mock e é, na verdade, um teste deixado para trás pela mudança.
        with patch("plughub_auth_api.router.db_mod.get_user_by_id", new=AsyncMock(return_value=_SAMPLE_USER)),              patch("plughub_auth_api.router.db_mod.delete_user", new=AsyncMock(return_value=True)):
            r = c.delete(f"/auth/users/{uid}", headers=_admin_headers())
        assert r.status_code == 204

    def test_delete_not_found(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_id", new=AsyncMock(return_value=None)),              patch("plughub_auth_api.router.db_mod.delete_user", new=AsyncMock(return_value=False)):
            r = c.delete(f"/auth/users/{uuid.uuid4()}", headers=_admin_headers())
        assert r.status_code == 404


# ─── As DUAS guardas que fazem o split entregar o que promete ─────────────────
#
# Acrescentadas em 2026-08-28. Elas não tinham teste NENHUM, e são o miolo da divisão
# `config.users` × `config.permissions`: sem as duas, quem administra pessoas volta a
# ser a chave-mestra do tenant por dois caminhos diferentes.
#
# As duas chamam `abac_can` (o verificador canônico desde o passo 5) — logo são também
# o controle positivo E negativo da migração deste serviço.


class TestGuardaDeCorpo:
    """`_assert_may_grant` — o CORPO carrega campo de capacidade."""

    def test_roles_no_corpo_sem_config_permissions_e_403(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_email", new=AsyncMock(return_value=None)):
            r = c.post("/auth/users",
                       json={"tenant_id": "tenant_test", "email": "x@test.local",
                             "password": "password123", "roles": ["admin"]},
                       headers=_admin_headers())
        assert r.status_code == 403
        assert "config.permissions" in r.json()["detail"]

    def test_sem_campo_de_capacidade_no_corpo_passa(self, client):
        """O discriminador é o que foi ENVIADO, não o valor resultante: omitir `roles`
        aceita o default, e isso não é conceder."""
        c, _ = client
        created = _user_copy(email="y@test.local")
        with patch("plughub_auth_api.router.db_mod.get_user_by_email", new=AsyncMock(return_value=None)),              patch("plughub_auth_api.router.db_mod.create_user", new=AsyncMock(return_value=created)):
            r = c.post("/auth/users",
                       json={"tenant_id": "tenant_test", "email": "y@test.local",
                             "password": "password123"},
                       headers=_admin_headers())
        assert r.status_code == 201


class TestGuardaDeAlvo:
    """`_assert_may_touch` — quem detém `config.permissions` só é tocado por um par.

    É esta guarda que fecha *"reseto a senha do admin e entro como admin"*. Resetar
    senha é campo de PESSOA e segue permitido de propósito; o que barra o vetor é a
    proteção do ALVO, nunca a guarda de corpo.
    """

    _PRIVILEGIADO = {
        **_SAMPLE_USER,
        "module_config": {"config": {"permissions": {"access": "read_write", "scope": []}}},
    }

    def test_apagar_privilegiado_so_com_config_users_e_403(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_id",
                   new=AsyncMock(return_value=self._PRIVILEGIADO)),              patch("plughub_auth_api.router.db_mod.delete_user", new=AsyncMock(return_value=True)):
            r = c.delete(f"/auth/users/{_SAMPLE_USER['id']}", headers=_admin_headers())
        assert r.status_code == 403
        assert "config.permissions" in r.json()["detail"]

    def test_apagar_privilegiado_por_um_PAR_passa(self, client):
        """Um "par" precisa dos DOIS grants, e isto não é redundância.

        A ROTA é `config.users` (apagar é administrar pessoa); a proteção do ALVO é
        `config.permissions`. Quem tem só o segundo não chega ao handler — 403 na
        porta. Escrevi este caso com `_perms_headers` primeiro e ele reprovou: a
        composição das duas portas não é dedutível de nenhuma das duas sozinha.
        """
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_id",
                   new=AsyncMock(return_value=self._PRIVILEGIADO)),              patch("plughub_auth_api.router.db_mod.delete_user", new=AsyncMock(return_value=True)):
            r = c.delete(f"/auth/users/{_SAMPLE_USER['id']}", headers=_ambos_headers())
        assert r.status_code == 204

    def test_so_config_permissions_nao_alcanca_a_rota_de_pessoa(self, client):
        """Testemunha do lado oposto: o grant de CAPACIDADE não administra pessoa."""
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_id",
                   new=AsyncMock(return_value=_SAMPLE_USER)),              patch("plughub_auth_api.router.db_mod.delete_user", new=AsyncMock(return_value=True)):
            r = c.delete(f"/auth/users/{_SAMPLE_USER['id']}", headers=_perms_headers())
        assert r.status_code == 403

    def test_linha_legada_com_unrestricted_NAO_protege_mais_o_alvo(self, client):
        """TESTEMUNHA da AUT-15 — invertida, nunca apagada.

        Este caso afirmava o oposto (*"`_is_privileged` olha DOIS fatos"*), e foi o
        disjunto `unrestricted` que saiu em 2026-08-31: desde a AUT-12/AUT-13 o campo
        não é emitido, não é lido e não decide escopo nenhum, então ele tornava alguém
        intocável por deter uma flag INERTE — mais difícil de administrar que um par,
        sem que a flag lhe desse poder algum.

        Apagar o caso deixaria a mudança sem testemunha, e a direção importa: remover
        um disjunto de um predicado de SEGURANÇA o enfraquece. Invertido, ele
        documenta a decisão e denuncia a volta silenciosa do ramo.

        População contada antes de decidir, nunca depois: 8 usuários, 2 com `true`, e
        **1** privilegiado só por ela — `probe@plughub.local`, fixture de portão.

        O par indispensável é `test_o_disjunto_que_FICA_ainda_protege`: sem ele este
        caso passaria num mundo onde `_is_privileged` sempre devolve False, que é o
        defeito grave.
        """
        c, _ = client
        alvo = {**_SAMPLE_USER, "module_config": {}, "unrestricted": True}
        with patch("plughub_auth_api.router.db_mod.get_user_by_id", new=AsyncMock(return_value=alvo)),              patch("plughub_auth_api.router.db_mod.delete_user", new=AsyncMock(return_value=True)):
            r = c.delete(f"/auth/users/{_SAMPLE_USER['id']}", headers=_admin_headers())
        assert r.status_code == 204

    def test_o_disjunto_que_FICA_ainda_protege(self, client):
        """Controle positivo do caso acima, na MESMA rodada.

        `config.permissions` no alvo continua barrando quem só administra pessoas — é
        este predicado que impede o *"reseto a senha do admin e entro como admin"*, e
        o caso acima não significaria nada sem ele ao lado.
        """
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_id", new=AsyncMock(return_value=self._PRIVILEGIADO)),              patch("plughub_auth_api.router.db_mod.delete_user", new=AsyncMock(return_value=True)):
            r = c.delete(f"/auth/users/{_SAMPLE_USER['id']}", headers=_admin_headers())
        assert r.status_code == 403


# ─── TestSeedAdmin ────────────────────────────────────────────────────────────


class TestSeedAdmin:
    @pytest.mark.asyncio
    async def test_seed_creates_when_absent(self):
        from plughub_auth_api import db as db_mod
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.execute = AsyncMock(return_value="INSERT 0 1")

        with patch.object(db_mod, "get_user_by_email", new=AsyncMock(return_value=None)),              patch.object(db_mod, "create_user",
                          new=AsyncMock(return_value=_user_copy(roles=["admin"]))),              patch.object(db_mod, "list_modules", new=AsyncMock(return_value=[])),              patch.object(db_mod, "set_user_module_config", new=AsyncMock()):
            result = await db_mod.seed_admin_if_absent(
                pool, "t1", "a@t.com", "hashed", "Admin", roles=["admin"],
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_seed_skips_when_exists(self):
        from plughub_auth_api import db as db_mod
        pool = MagicMock()
        with patch.object(db_mod, "get_user_by_email", new=AsyncMock(return_value=_SAMPLE_USER)):
            result = await db_mod.seed_admin_if_absent(
                pool, "t1", "a@t.com", "hashed", "Admin", roles=["admin"],
            )
        assert result is False

    # ── AUT-12 (2026-08-31): a testemunha que faltava ────────────────────────
    #
    # Ate aqui esta classe so afirmava `True`/`False` — "criou" e "nasceu com grants"
    # sao DOIS fatos, e so o primeiro era testado. Foi essa lacuna que deixou passar um
    # IMPASSE DE BOOTSTRAP: `create_user` grava `roles` e NAO grava `module_config`, e
    # quem aplicava o preset era so o router. Medido em 2026-08-31, o admin semeado
    # nascia com `module_config = '{}'` — sob o portao grant-first, sem menu nenhum, e
    # sem poder se corrigir, porque conceder exige `config.permissions`.
    #
    # O teste exerce `apply_role_preset` de verdade, com a linha de registry na forma
    # CRUA do banco (coluna `schema`, JSON em STRING). Isso e de proposito: o construtor
    # le `permission_schema`, e a linha crua o faz devolver `{}` — indistinguivel de
    # "nenhum preset declarado".
    @pytest.mark.asyncio
    async def test_seed_aplica_preset_e_admin_nasce_com_config_permissions(self):
        from plughub_auth_api import db as db_mod
        pool = MagicMock()
        gravado: dict = {}

        async def _captura(_pool, _uid, cfg):
            gravado.update(cfg)

        registry_cru = [{
            "module_id": "config",
            "schema": _json.dumps({
                "permissions": {"role_defaults": {"admin": "read_write"}},
                "users":       {"role_defaults": {"admin": "read_write",
                                                  "supervisor": "read_write"}},
            }),
        }]

        with patch.object(db_mod, "get_user_by_email", new=AsyncMock(return_value=None)),              patch.object(db_mod, "create_user",
                          new=AsyncMock(return_value=_user_copy(roles=["admin"]))),              patch.object(db_mod, "list_modules", new=AsyncMock(return_value=registry_cru)),              patch.object(db_mod, "set_user_module_config", new=_captura):
            assert await db_mod.seed_admin_if_absent(
                pool, "t1", "a@t.com", "hashed", "Admin", roles=["admin"],
            ) is True

        # Sem este campo o admin nao consegue conceder nada — nem a si mesmo.
        assert gravado["config"]["permissions"]["access"] == "read_write"
        assert gravado["config"]["users"]["access"] == "read_write"

    @pytest.mark.asyncio
    async def test_papel_sem_role_defaults_nasce_SEM_grants(self):
        """Testemunha negativa: o preset nao inventa acesso para papel nao declarado.

        Sem ela, um `apply_role_preset` que concedesse tudo a qualquer papel passaria no
        teste acima — ele so prova que ALGUEM recebe, nunca que o CERTO recebe.
        """
        from plughub_auth_api import db as db_mod
        pool = MagicMock()
        chamou = False

        async def _nao_deveria(_pool, _uid, _cfg):
            nonlocal chamou
            chamou = True

        registry_cru = [{
            "module_id": "config",
            "schema": _json.dumps({"permissions": {"role_defaults": {"admin": "read_write"}}}),
        }]
        with patch.object(db_mod, "get_user_by_email", new=AsyncMock(return_value=None)),              patch.object(db_mod, "create_user",
                          new=AsyncMock(return_value=_user_copy(roles=["operator"]))),              patch.object(db_mod, "list_modules", new=AsyncMock(return_value=registry_cru)),              patch.object(db_mod, "set_user_module_config", new=_nao_deveria):
            await db_mod.seed_admin_if_absent(
                pool, "t1", "o@t.com", "hashed", "Op", roles=["operator"],
            )
        assert chamou is False, "gravou config para papel sem `role_defaults` declarado"


# ─── TestPasswordUtils ────────────────────────────────────────────────────────


class TestPasswordUtils:
    def test_hash_and_verify(self):
        pw = "my_secret_pass_123"
        h = hash_password(pw)
        assert h != pw
        assert verify_password(pw, h)

    def test_wrong_password(self):
        h = hash_password("correct")
        assert not verify_password("wrong", h)

    def test_empty_password(self):
        h = hash_password("")
        assert verify_password("", h)
        assert not verify_password("nonempty", h)


# ─── TestJwtUtils ─────────────────────────────────────────────────────────────


class TestJwtUtils:
    def test_encode_decode(self):
        token = create_access_token(
            user_id="uid-1", tenant_id="t1", email="e@t.com",
            name="User", roles=["admin"], accessible_pools=["pool_a"],
            settings=TEST_SETTINGS,
        )
        claims = decode_access_token(token, TEST_SETTINGS)
        assert claims["sub"] == "uid-1"
        assert claims["tenant_id"] == "t1"
        assert "admin" in claims["roles"]
        assert "pool_a" in claims["accessible_pools"]

    def test_expired_token(self):
        from jose import JWTError, jwt as jose_jwt
        from datetime import datetime, timedelta, timezone
        expired_settings = Settings(
            **{**TEST_SETTINGS.model_dump(), "access_token_expire_minutes": -1}
        )
        token = create_access_token(
            user_id="uid-2", tenant_id="t1", email="e@t.com",
            name="User", roles=[], accessible_pools=[],
            settings=expired_settings,
        )
        with pytest.raises(JWTError):
            decode_access_token(token, TEST_SETTINGS)

    def test_wrong_secret(self):
        from jose import JWTError
        token = create_access_token(
            user_id="uid-3", tenant_id="t1", email="e@t.com",
            name="User", roles=[], accessible_pools=[],
            settings=TEST_SETTINGS,
        )
        other_settings = Settings(**{**TEST_SETTINGS.model_dump(), "jwt_secret": "other_secret_key_long_enough_here!"})
        with pytest.raises(JWTError):
            decode_access_token(token, other_settings)


# ─── TestHashRefreshToken ─────────────────────────────────────────────────────


class TestHashRefreshToken:
    def test_deterministic(self):
        token = "abc123"
        assert hash_refresh_token(token) == hash_refresh_token(token)

    def test_different_from_plain(self):
        token = "mytoken"
        assert hash_refresh_token(token) != token

    def test_different_tokens_different_hashes(self):
        a = hash_refresh_token("token_a")
        b = hash_refresh_token("token_b")
        assert a != b


# ─── helpers para permissões ──────────────────────────────────────────────────

_SAMPLE_PERM: dict = {
    "id": uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001"),
    "tenant_id": "tenant_test",
    "user_id": str(_SAMPLE_USER["id"]),
    "module": "analytics",
    "action": "view",
    "scope_type": "global",
    "scope_id": None,
    "granted_by": "admin",
    "template_id": None,
    "created_at": _NOW,
}

_SAMPLE_TMPL: dict = {
    "id": uuid.UUID("cccccccc-0000-0000-0000-000000000001"),
    "tenant_id": "tenant_test",
    "name": "operator_default",
    "description": "Permissões padrão de operador",
    "permissions": [
        {"module": "analytics", "action": "view", "scope_type": "global", "scope_id": None},
        {"module": "evaluation", "action": "view", "scope_type": "global", "scope_id": None},
    ],
    "created_at": _NOW,
    "updated_at": _NOW,
}


# As suites de /permissions e de apply_template sairam em 2026-08-30, junto do
# subsistema que elas exercitavam (platform_permissions). Nao eram testes ruins:
# eram os UNICOS consumidores daquelas rotas, e e por isso que o subsistema
# parecia vivo. Ver o cabecalho de permissions.py.


# ─── TestTemplates ────────────────────────────────────────────────────────────


class TestTemplates:
    def test_create_template(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.create_template",
                   new=AsyncMock(return_value=_SAMPLE_TMPL)):
            r = c.post("/auth/templates",
                       json={"tenant_id": "tenant_test", "name": "operator_default",
                             "permissions": [{"module": "analytics", "action": "view"}]},
                       headers=_perms_headers())
        assert r.status_code == 201
        assert r.json()["name"] == "operator_default"

    def test_list_templates(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.list_templates",
                   new=AsyncMock(return_value=[_SAMPLE_TMPL])):
            r = c.get("/auth/templates?tenant_id=tenant_test",
                      headers=_perms_headers())
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_get_template(self, client):
        c, _ = client
        tid = str(_SAMPLE_TMPL["id"])
        with patch("plughub_auth_api.router.perms_mod.get_template",
                   new=AsyncMock(return_value=_SAMPLE_TMPL)):
            r = c.get(f"/auth/templates/{tid}",
                      headers=_perms_headers())
        assert r.status_code == 200
        assert r.json()["id"] == tid

    def test_get_template_not_found(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.get_template",
                   new=AsyncMock(return_value=None)):
            r = c.get(f"/auth/templates/{uuid.uuid4()}",
                      headers=_perms_headers())
        assert r.status_code == 404

    def test_update_template(self, client):
        c, _ = client
        updated = {**_SAMPLE_TMPL, "description": "Updated description"}
        tid = str(_SAMPLE_TMPL["id"])
        with patch("plughub_auth_api.router.perms_mod.get_template",
                   new=AsyncMock(return_value=_SAMPLE_TMPL)), \
             patch("plughub_auth_api.router.perms_mod.update_template",
                   new=AsyncMock(return_value=updated)):
            r = c.patch(f"/auth/templates/{tid}",
                        json={"description": "Updated description"},
                        headers=_perms_headers())
        assert r.status_code == 200
        assert r.json()["description"] == "Updated description"

    def test_delete_template(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.delete_template",
                   new=AsyncMock(return_value=True)):
            r = c.delete(f"/auth/templates/{uuid.uuid4()}",
                         headers=_perms_headers())
        assert r.status_code == 204
