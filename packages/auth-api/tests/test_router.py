"""
test_router.py
Testes da auth-api — mock asyncpg, sem banco real.

Cobertura:
  TestHealth                 — GET /health
  TestLogin                  — login OK, senha errada, usuário inativo
  TestRefresh                — refresh token rotation, token inválido
  TestLogout                 — logout OK e idempotente
  TestMe                     — Bearer válido, sem token, token expirado
  TestCreateUser             — criação OK, e-mail duplicado, sem admin token
  TestListUsers              — listagem filtrada por tenant
  TestGetUser                — OK, não encontrado
  TestUpdateUser             — update parcial (name, password, roles)
  TestDeleteUser             — OK, não encontrado
  TestSeedAdmin              — seed_admin_if_absent cria e é idempotente
  TestPasswordUtils          — hash/verify
  TestJwtUtils               — encode/decode, expirado
  TestHashRefreshToken       — determinístico, diferente do plain
  TestGrantPermission        — grant OK, idempotente
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
    """TestClient com pool injetado e seed suprimido."""
    app = build_app()

    # Injeta pool no state ANTES do lifespan completar
    with patch("plughub_auth_api.main.asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)), \
         patch("plughub_auth_api.main.db_mod.ensure_schema", new=AsyncMock()), \
         patch("plughub_auth_api.main.db_mod.seed_admin_if_absent", new=AsyncMock(return_value=False)), \
         patch("plughub_auth_api.router.get_settings", return_value=TEST_SETTINGS), \
         patch("plughub_auth_api.main.get_settings", return_value=TEST_SETTINGS):
        with TestClient(app, raise_server_exceptions=True) as c:
            c.app.state.pool = mock_pool
            yield c, mock_pool


def _access_token(user: dict[str, Any] | None = None) -> str:
    u = user or _SAMPLE_USER
    return create_access_token(
        user_id=str(u["id"]),
        tenant_id=u["tenant_id"],
        email=u["email"],
        name=u["name"],
        roles=list(u["roles"]),
        accessible_pools=list(u["accessible_pools"]),
        settings=TEST_SETTINGS,
    )


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
                       headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 201
        assert r.json()["email"] == "new@test.local"

    def test_create_duplicate_email(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_email", new=AsyncMock(return_value=_SAMPLE_USER)):
            r = c.post("/auth/users",
                       json={"tenant_id": "tenant_test", "email": "user@test.local",
                             "password": "password123"},
                       headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 409

    def test_create_no_admin_token(self, client):
        c, _ = client
        r = c.post("/auth/users",
                   json={"tenant_id": "tenant_test", "email": "x@x.com", "password": "pw12345678"})
        assert r.status_code == 401


# ─── TestListUsers ────────────────────────────────────────────────────────────


class TestListUsers:
    def test_list_users(self, client):
        c, _ = client
        users = [_SAMPLE_USER, _user_copy(email="b@test.local")]
        with patch("plughub_auth_api.router.db_mod.list_users", new=AsyncMock(return_value=users)):
            r = c.get("/auth/users?tenant_id=tenant_test",
                      headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 200
        assert len(r.json()) == 2


# ─── TestGetUser ──────────────────────────────────────────────────────────────


class TestGetUser:
    def test_get_ok(self, client):
        c, _ = client
        uid = str(_SAMPLE_USER["id"])
        with patch("plughub_auth_api.router.db_mod.get_user_by_id", new=AsyncMock(return_value=_SAMPLE_USER)):
            r = c.get(f"/auth/users/{uid}", headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 200
        assert r.json()["id"] == uid

    def test_get_not_found(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_id", new=AsyncMock(return_value=None)):
            r = c.get(f"/auth/users/{uuid.uuid4()}", headers={"x-admin-token": "test-admin-token"})
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
                        headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 200
        assert r.json()["name"] == "New Name"

    def test_update_not_found(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.get_user_by_id", new=AsyncMock(return_value=None)):
            r = c.patch(f"/auth/users/{uuid.uuid4()}",
                        json={"name": "x"},
                        headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 404


# ─── TestDeleteUser ───────────────────────────────────────────────────────────


class TestDeleteUser:
    def test_delete_ok(self, client):
        c, _ = client
        uid = str(_SAMPLE_USER["id"])
        with patch("plughub_auth_api.router.db_mod.delete_user", new=AsyncMock(return_value=True)):
            r = c.delete(f"/auth/users/{uid}", headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 204

    def test_delete_not_found(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.db_mod.delete_user", new=AsyncMock(return_value=False)):
            r = c.delete(f"/auth/users/{uuid.uuid4()}", headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 404


# ─── TestSeedAdmin ────────────────────────────────────────────────────────────


class TestSeedAdmin:
    @pytest.mark.asyncio
    async def test_seed_creates_when_absent(self):
        from plughub_auth_api import db as db_mod
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)           # get_user_by_email → None
        pool.execute = AsyncMock(return_value="INSERT 0 1")

        created_user = _user_copy(roles=["admin", "developer"])
        with patch.object(db_mod, "get_user_by_email", new=AsyncMock(return_value=None)), \
             patch.object(db_mod, "create_user", new=AsyncMock(return_value=created_user)):
            result = await db_mod.seed_admin_if_absent(pool, "t1", "a@t.com", "hashed", "Admin")
        assert result is True

    @pytest.mark.asyncio
    async def test_seed_skips_when_exists(self):
        from plughub_auth_api import db as db_mod
        pool = MagicMock()
        with patch.object(db_mod, "get_user_by_email", new=AsyncMock(return_value=_SAMPLE_USER)):
            result = await db_mod.seed_admin_if_absent(pool, "t1", "a@t.com", "hashed", "Admin")
        assert result is False


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


# ─── TestGrantPermission ──────────────────────────────────────────────────────


class TestGrantPermission:
    def test_grant_ok(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.grant_permission",
                   new=AsyncMock(return_value=_SAMPLE_PERM)):
            r = c.post("/auth/permissions",
                       json={"tenant_id": "tenant_test", "user_id": str(_SAMPLE_USER["id"]),
                             "module": "analytics", "action": "view"},
                       headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 201
        body = r.json()
        assert body["module"] == "analytics"
        assert body["scope_type"] == "global"

    def test_grant_without_admin_token(self, client):
        c, _ = client
        r = c.post("/auth/permissions",
                   json={"tenant_id": "t", "user_id": "u", "module": "analytics", "action": "view"})
        assert r.status_code == 401

    def test_grant_pool_scope(self, client):
        c, _ = client
        perm_pool = {**_SAMPLE_PERM, "scope_type": "pool", "scope_id": "pool_sac"}
        with patch("plughub_auth_api.router.perms_mod.grant_permission",
                   new=AsyncMock(return_value=perm_pool)):
            r = c.post("/auth/permissions",
                       json={"tenant_id": "tenant_test", "user_id": str(_SAMPLE_USER["id"]),
                             "module": "analytics", "action": "view",
                             "scope_type": "pool", "scope_id": "pool_sac"},
                       headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 201
        assert r.json()["scope_id"] == "pool_sac"


# ─── TestListPermissions ──────────────────────────────────────────────────────


class TestListPermissions:
    def test_list_all(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.list_permissions",
                   new=AsyncMock(return_value=[_SAMPLE_PERM])):
            r = c.get("/auth/permissions?tenant_id=tenant_test",
                      headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_list_filtered_by_user(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.list_permissions",
                   new=AsyncMock(return_value=[_SAMPLE_PERM])) as mock:
            r = c.get(f"/auth/permissions?tenant_id=tenant_test&user_id={_SAMPLE_USER['id']}",
                      headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 200
        assert mock.call_args.kwargs.get("user_id") == str(_SAMPLE_USER["id"])


# ─── TestRevokePermission ─────────────────────────────────────────────────────


class TestRevokePermission:
    def test_revoke_ok(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.revoke_permission",
                   new=AsyncMock(return_value=True)):
            r = c.delete(f"/auth/permissions/{uuid.uuid4()}",
                         headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 204

    def test_revoke_not_found(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.revoke_permission",
                   new=AsyncMock(return_value=False)):
            r = c.delete(f"/auth/permissions/{uuid.uuid4()}",
                         headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 404


# ─── TestResolvePermission ────────────────────────────────────────────────────


class TestResolvePermission:
    def test_resolve_allowed(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.resolve_permissions",
                   new=AsyncMock(return_value=True)):
            r = c.get("/auth/permissions/resolve",
                      params={"tenant_id": "t", "user_id": "u",
                              "module": "analytics", "action": "view"})
        assert r.status_code == 200
        assert r.json()["allowed"] is True

    def test_resolve_denied(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.resolve_permissions",
                   new=AsyncMock(return_value=False)):
            r = c.get("/auth/permissions/resolve",
                      params={"tenant_id": "t", "user_id": "u",
                              "module": "billing", "action": "edit"})
        assert r.status_code == 200
        assert r.json()["allowed"] is False

    def test_resolve_pool_scope(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.resolve_permissions",
                   new=AsyncMock(return_value=True)) as mock:
            r = c.get("/auth/permissions/resolve",
                      params={"tenant_id": "t", "user_id": "u",
                              "module": "analytics", "action": "view",
                              "pool_id": "pool_sac"})
        assert r.status_code == 200
        assert mock.call_args.kwargs.get("pool_id") == "pool_sac"


# ─── TestTemplates ────────────────────────────────────────────────────────────


class TestTemplates:
    def test_create_template(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.create_template",
                   new=AsyncMock(return_value=_SAMPLE_TMPL)):
            r = c.post("/auth/templates",
                       json={"tenant_id": "tenant_test", "name": "operator_default",
                             "permissions": [{"module": "analytics", "action": "view"}]},
                       headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 201
        assert r.json()["name"] == "operator_default"

    def test_list_templates(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.list_templates",
                   new=AsyncMock(return_value=[_SAMPLE_TMPL])):
            r = c.get("/auth/templates?tenant_id=tenant_test",
                      headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_get_template(self, client):
        c, _ = client
        tid = str(_SAMPLE_TMPL["id"])
        with patch("plughub_auth_api.router.perms_mod.get_template",
                   new=AsyncMock(return_value=_SAMPLE_TMPL)):
            r = c.get(f"/auth/templates/{tid}",
                      headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 200
        assert r.json()["id"] == tid

    def test_get_template_not_found(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.get_template",
                   new=AsyncMock(return_value=None)):
            r = c.get(f"/auth/templates/{uuid.uuid4()}",
                      headers={"x-admin-token": "test-admin-token"})
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
                        headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 200
        assert r.json()["description"] == "Updated description"

    def test_delete_template(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.delete_template",
                   new=AsyncMock(return_value=True)):
            r = c.delete(f"/auth/templates/{uuid.uuid4()}",
                         headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 204


# ─── TestApplyTemplate ────────────────────────────────────────────────────────


class TestApplyTemplate:
    def test_apply_ok(self, client):
        c, _ = client
        materialized = [
            {**_SAMPLE_PERM, "module": "analytics"},
            {**_SAMPLE_PERM, "module": "evaluation"},
        ]
        with patch("plughub_auth_api.router.perms_mod.apply_template",
                   new=AsyncMock(return_value=materialized)):
            r = c.post(f"/auth/templates/{_SAMPLE_TMPL['id']}/apply",
                       json={"user_id": str(_SAMPLE_USER["id"]), "granted_by": "admin"},
                       headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_apply_template_not_found(self, client):
        c, _ = client
        with patch("plughub_auth_api.router.perms_mod.apply_template",
                   new=AsyncMock(side_effect=ValueError("Template not found"))):
            r = c.post(f"/auth/templates/{uuid.uuid4()}/apply",
                       json={"user_id": str(_SAMPLE_USER["id"])},
                       headers={"x-admin-token": "test-admin-token"})
        assert r.status_code == 404


# ─── TestResolvePermissionsLogic ──────────────────────────────────────────────


class TestResolvePermissionsLogic:
    """Testa a lógica de resolve_permissions diretamente (sem HTTP)."""

    @pytest.mark.asyncio
    async def test_global_permission_grants_access(self):
        from plughub_auth_api import permissions as pm
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[
            {"scope_type": "global", "scope_id": None},
        ])
        result = await pm.resolve_permissions(pool, "t1", "u1", "analytics", "view")
        assert result is True

    @pytest.mark.asyncio
    async def test_pool_permission_grants_access_for_matching_pool(self):
        from plughub_auth_api import permissions as pm
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[
            {"scope_type": "pool", "scope_id": "pool_sac"},
        ])
        result = await pm.resolve_permissions(pool, "t1", "u1", "analytics", "view", pool_id="pool_sac")
        assert result is True

    @pytest.mark.asyncio
    async def test_pool_permission_denies_access_for_different_pool(self):
        from plughub_auth_api import permissions as pm
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[
            {"scope_type": "pool", "scope_id": "pool_sac"},
        ])
        result = await pm.resolve_permissions(pool, "t1", "u1", "analytics", "view", pool_id="pool_retencao")
        assert result is False

    @pytest.mark.asyncio
    async def test_no_permission_denies(self):
        from plughub_auth_api import permissions as pm
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        result = await pm.resolve_permissions(pool, "t1", "u1", "billing", "edit")
        assert result is False

    @pytest.mark.asyncio
    async def test_get_accessible_pools_global_returns_none(self):
        from plughub_auth_api import permissions as pm
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[
            {"scope_type": "global", "scope_id": None},
        ])
        result = await pm.get_accessible_pools_for_module(pool, "t1", "u1", "analytics")
        assert result is None  # acesso irrestrito

    @pytest.mark.asyncio
    async def test_get_accessible_pools_pool_scope_returns_list(self):
        from plughub_auth_api import permissions as pm
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[
            {"scope_type": "pool", "scope_id": "pool_sac"},
            {"scope_type": "pool", "scope_id": "pool_retencao"},
        ])
        result = await pm.get_accessible_pools_for_module(pool, "t1", "u1", "analytics")
        assert set(result) == {"pool_sac", "pool_retencao"}
