"""
test_aut71_tenant_scope.py — AUT-71 (2026-10-01): o tenant de toda gestão é o do TOKEN.

PROPOSIÇÃO: um token de `tenant_test` — inclusive de `admin`, que administra todo mundo DENTRO
do tenant — não lê nem grava usuário, template, grupo, module_config ou módulo de outro tenant;
e continua fazendo tudo isso no próprio.

Medido ao vivo antes (2026-10-01): o admin do `tenant_demo` criou usuário em outro tenant (201),
listou os usuários e os templates de lá, leu ficha e module_config por id e listou grupos.

Formas da recusa (ver `tenant_scope.py`): tenant DECLARADO no corpo/query diferente → 403
`tenant_mismatch`; LINHA por id de outro tenant → 404, igual a inexistente; módulo de PLATAFORMA
→ 403 `platform_module_write`. Cada recusa vem com o controle POSITIVO ao lado, e as escritas
com o mock da escrita conferido como NÃO chamado — um 403/404 que ainda grava não é portão.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_router import (  # noqa: F401 — fixtures
    _SAMPLE_TMPL, _SAMPLE_USER, _access_token, _user_copy, client, mock_pool,
)

OUTRO = "outro_tenant"
DB = "plughub_auth_api.router.db_mod"
PERMS = "plughub_auth_api.router.perms_mod"

_USER_ALHEIO = _user_copy(id=uuid.UUID("aaaaaaaa-0000-0000-0000-0000000000ff"),
                          tenant_id=OUTRO, email="alheio@outro.local")
_TMPL_ALHEIO = {**_SAMPLE_TMPL, "id": uuid.UUID("cccccccc-0000-0000-0000-0000000000ff"),
                "tenant_id": OUTRO, "config": {}}
_GRUPO = "11111111-2222-3333-4444-555555555555"
_GRUPO_ROW = {"group_id": uuid.UUID(_GRUPO), "tenant_id": "tenant_test", "name": "g",
              "description": "", "created_at": "", "updated_at": ""}
_GRUPO_ALHEIO_ROW = {**_GRUPO_ROW, "tenant_id": OUTRO}


def _admin() -> dict[str, str]:
    """O sujeito que o furo deixava passar: `admin` (irrestrito para pessoas) com os
    dois grants. Delegado seria barrado pelo organograma antes — e o teste passaria pelo
    motivo errado."""
    tok = _access_token(
        user=_user_copy(roles=["admin"]),
        module_config={"config": {"users": {"access": "read_write", "scope": []},
                                  "permissions": {"access": "read_write", "scope": []}}},
    )
    return {"Authorization": f"Bearer {tok}"}


def _novo(tenant: str) -> dict[str, Any]:
    return {"tenant_id": tenant, "email": "n@x.local", "password": "password123",
            "roles": [], "accessible_pools": []}


# ── usuários ─────────────────────────────────────────────────────────────────

class TestUsuarios:
    def test_criar_em_outro_tenant_e_403_e_nao_grava(self, client):
        c, _ = client
        with patch(f"{DB}.get_user_by_email", new=AsyncMock(return_value=None)), \
             patch(f"{DB}.create_user", new=AsyncMock()) as criou:
            r = c.post("/auth/users", json=_novo(OUTRO), headers=_admin())
        assert r.status_code == 403 and r.json()["detail"] == "tenant_mismatch"
        criou.assert_not_awaited()

    def test_controle_criar_no_proprio_tenant_201(self, client):
        c, _ = client
        with patch(f"{DB}.get_user_by_email", new=AsyncMock(return_value=None)), \
             patch(f"{DB}.create_user", new=AsyncMock(return_value=_user_copy(roles=[]))), \
             patch("plughub_auth_api.router.presets_mod.apply_role_preset", new=AsyncMock(return_value={})):
            r = c.post("/auth/users", json=_novo("tenant_test"), headers=_admin())
        assert r.status_code == 201

    def test_criar_por_template_em_outro_tenant_e_403(self, client):
        c, _ = client
        with patch(f"{PERMS}.get_template", new=AsyncMock(return_value=_SAMPLE_TMPL)), \
             patch(f"{DB}.create_user", new=AsyncMock()) as criou:
            r = c.post(f"/auth/users/from-template/{_SAMPLE_TMPL['id']}",
                       json={k: v for k, v in _novo(OUTRO).items() if k != "roles"}, headers=_admin())
        assert r.status_code == 403 and r.json()["detail"] == "tenant_mismatch"
        criou.assert_not_awaited()

    def test_criar_com_template_de_outro_tenant_e_404(self, client):
        c, _ = client
        with patch(f"{PERMS}.get_template", new=AsyncMock(return_value=_TMPL_ALHEIO)), \
             patch(f"{DB}.create_user", new=AsyncMock()) as criou:
            r = c.post(f"/auth/users/from-template/{_TMPL_ALHEIO['id']}",
                       json={k: v for k, v in _novo("tenant_test").items() if k != "roles"},
                       headers=_admin())
        assert r.status_code == 404
        criou.assert_not_awaited()

    def test_listar_outro_tenant_e_403(self, client):
        c, _ = client
        with patch(f"{DB}.list_users", new=AsyncMock(return_value=[])) as listou:
            r = c.get(f"/auth/users?tenant_id={OUTRO}", headers=_admin())
        assert r.status_code == 403 and r.json()["detail"] == "tenant_mismatch"
        listou.assert_not_awaited()

    def test_listar_sem_query_e_o_tenant_do_token_nao_o_do_build(self, client):
        """O default era `"tenant_demo"`: sem `?tenant_id=`, todo tenant lia o do demo."""
        c, _ = client
        with patch(f"{DB}.list_users", new=AsyncMock(return_value=[_SAMPLE_USER])) as listou:
            r = c.get("/auth/users", headers=_admin())
        assert r.status_code == 200
        assert listou.await_args.args[1] == "tenant_test"

    @pytest.mark.parametrize("metodo,caminho,corpo", [
        ("get",    "/auth/users/{id}", None),
        ("patch",  "/auth/users/{id}", {"name": "x"}),
        ("delete", "/auth/users/{id}", None),
        ("get",    "/auth/users/{id}/module-config", None),
        ("put",    "/auth/users/{id}/module-config", {}),
        ("patch",  "/auth/users/{id}/module-config/contacts", {}),
    ])
    def test_usuario_de_outro_tenant_por_id_e_404_e_nada_muda(self, client, metodo, caminho, corpo):
        c, _ = client
        with patch(f"{DB}.get_user_by_id", new=AsyncMock(return_value=_USER_ALHEIO)), \
             patch(f"{DB}.update_user", new=AsyncMock()) as atualizou, \
             patch(f"{DB}.delete_user", new=AsyncMock()) as apagou, \
             patch(f"{DB}.set_user_module_config", new=AsyncMock()) as gravou, \
             patch(f"{DB}.get_user_module_config", new=AsyncMock(return_value={})) as leu_cfg:
            kw = {"headers": _admin()}
            if corpo is not None:
                kw["json"] = corpo
            r = getattr(c, metodo)(caminho.format(id=_USER_ALHEIO["id"]), **kw)
        assert r.status_code == 404
        atualizou.assert_not_awaited(); apagou.assert_not_awaited(); gravou.assert_not_awaited()
        leu_cfg.assert_not_awaited()

    def test_controle_usuario_do_proprio_tenant_por_id_200(self, client):
        c, _ = client
        with patch(f"{DB}.get_user_by_id", new=AsyncMock(return_value=_SAMPLE_USER)), \
             patch(f"{DB}.get_user_module_config", new=AsyncMock(return_value={"x": {}})):
            assert c.get(f"/auth/users/{_SAMPLE_USER['id']}", headers=_admin()).status_code == 200
            r = c.get(f"/auth/users/{_SAMPLE_USER['id']}/module-config", headers=_admin())
        assert r.status_code == 200 and r.json() == {"x": {}}

    def test_nascer_em_grupo_de_outro_tenant_e_422(self, client):
        """A mensagem dizia "nao existe neste tenant" e o código não olhava o tenant."""
        c, _ = client
        with patch(f"{DB}.get_group", new=AsyncMock(return_value=_GRUPO_ALHEIO_ROW)), \
             patch(f"{DB}.get_user_by_email", new=AsyncMock(return_value=None)), \
             patch(f"{DB}.create_user", new=AsyncMock()) as criou:
            r = c.post("/auth/users", json={**_novo("tenant_test"), "group_ids": [_GRUPO]},
                       headers=_admin())
        assert r.status_code == 422
        criou.assert_not_awaited()


# ── templates ────────────────────────────────────────────────────────────────

class TestTemplates:
    def test_criar_em_outro_tenant_e_403(self, client):
        c, _ = client
        with patch(f"{PERMS}.create_template", new=AsyncMock()) as criou:
            r = c.post("/auth/templates", json={"tenant_id": OUTRO, "name": "t"}, headers=_admin())
        assert r.status_code == 403 and r.json()["detail"] == "tenant_mismatch"
        criou.assert_not_awaited()

    def test_listar_outro_tenant_e_403_e_sem_query_e_o_do_token(self, client):
        c, _ = client
        with patch(f"{PERMS}.list_templates", new=AsyncMock(return_value=[_SAMPLE_TMPL])) as listou:
            assert c.get(f"/auth/templates?tenant_id={OUTRO}", headers=_admin()).status_code == 403
            listou.assert_not_awaited()
            assert c.get("/auth/templates", headers=_admin()).status_code == 200
        assert listou.await_args.args[1] == "tenant_test"

    @pytest.mark.parametrize("metodo,corpo", [("get", None), ("patch", {"name": "x"}), ("delete", None)])
    def test_template_de_outro_tenant_por_id_e_404(self, client, metodo, corpo):
        c, _ = client
        with patch(f"{PERMS}.get_template", new=AsyncMock(return_value=_TMPL_ALHEIO)), \
             patch(f"{PERMS}.update_template", new=AsyncMock()) as atualizou, \
             patch(f"{PERMS}.delete_template", new=AsyncMock()) as apagou:
            kw = {"headers": _admin(), **({"json": corpo} if corpo is not None else {})}
            r = getattr(c, metodo)(f"/auth/templates/{_TMPL_ALHEIO['id']}", **kw)
        assert r.status_code == 404
        atualizou.assert_not_awaited(); apagou.assert_not_awaited()


# ── módulos ──────────────────────────────────────────────────────────────────

_MOD = {"module_id": "plugin_y", "tenant_id": "tenant_test", "label": "y", "icon": "x",
        "nav_path": "", "schema": {}, "active": True, "registered_at": "", "updated_at": ""}


class TestModulos:
    def test_gravar_modulo_de_plataforma_e_403(self, client):
        c, _ = client
        with patch(f"{DB}.upsert_module", new=AsyncMock()) as gravou:
            r = c.post("/auth/modules", json={"module_id": "contacts"}, headers=_admin())
        assert r.status_code == 403 and r.json()["detail"] == "platform_module_write"
        gravou.assert_not_awaited()

    def test_gravar_modulo_em_outro_tenant_e_403(self, client):
        c, _ = client
        with patch(f"{DB}.upsert_module", new=AsyncMock()) as gravou:
            r = c.post("/auth/modules", json={"module_id": "plugin_y", "tenant_id": OUTRO}, headers=_admin())
        assert r.status_code == 403 and r.json()["detail"] == "tenant_mismatch"
        gravou.assert_not_awaited()

    def test_repetir_module_id_de_plataforma_e_409(self, client):
        """O upsert é por `module_id`: sem a conferência, o tenant sobrescrevia o módulo
        de plataforma só por repetir o id."""
        c, _ = client
        with patch(f"{DB}.get_module", new=AsyncMock(return_value={**_MOD, "module_id": "contacts", "tenant_id": None})), \
             patch(f"{DB}.upsert_module", new=AsyncMock()) as gravou:
            r = c.post("/auth/modules", json={"module_id": "contacts", "tenant_id": "tenant_test"},
                       headers=_admin())
        assert r.status_code == 409
        gravou.assert_not_awaited()

    def test_controle_modulo_do_proprio_tenant_201(self, client):
        c, _ = client
        with patch(f"{DB}.get_module", new=AsyncMock(return_value=None)), \
             patch(f"{DB}.upsert_module", new=AsyncMock(return_value=_MOD)) as gravou:
            r = c.post("/auth/modules", json={"module_id": "plugin_y", "tenant_id": "tenant_test"},
                       headers=_admin())
        assert r.status_code == 201
        assert gravou.await_args.kwargs["tenant_id"] == "tenant_test"

    @pytest.mark.parametrize("linha,esperado", [
        ({**_MOD, "tenant_id": None}, 403),
        ({**_MOD, "tenant_id": OUTRO}, 404),
        (_MOD, 200),
    ])
    def test_ligar_desligar_so_o_do_proprio_tenant(self, client, linha, esperado):
        c, _ = client
        with patch(f"{DB}.get_module", new=AsyncMock(return_value=linha)), \
             patch(f"{DB}.set_module_active", new=AsyncMock(return_value=True)) as mudou:
            r = c.patch("/auth/modules/plugin_y/active?active=false", headers=_admin())
        assert r.status_code == esperado
        assert mudou.await_count == (1 if esperado == 200 else 0)


# ── grupos ───────────────────────────────────────────────────────────────────

class TestGrupos:
    def test_listar_outro_tenant_e_403_e_sem_query_e_o_do_token(self, client):
        c, _ = client
        with patch(f"{DB}.list_groups", new=AsyncMock(return_value=[])) as listou:
            assert c.get(f"/auth/v1/groups?tenant_id={OUTRO}", headers=_admin()).status_code == 403
            listou.assert_not_awaited()
            assert c.get("/auth/v1/groups", headers=_admin()).status_code == 200
        assert listou.await_args.args[1] == "tenant_test"

    def test_criar_em_outro_tenant_e_403(self, client):
        c, _ = client
        with patch(f"{DB}.create_group", new=AsyncMock()) as criou:
            r = c.post("/auth/v1/groups", json={"tenant_id": OUTRO, "name": "g"}, headers=_admin())
        assert r.status_code == 403
        criou.assert_not_awaited()

    @pytest.mark.parametrize("metodo,caminho,corpo", [
        ("get",    "", None),
        ("put",    "", {"name": "x"}),
        ("delete", "", None),
        ("get",    "/users", None),
        ("post",   "/users", {"user_id": str(_SAMPLE_USER["id"])}),
        ("delete", f"/users/{_SAMPLE_USER['id']}", None),
        ("get",    "/supervisors", None),
        ("post",   "/supervisors", {"user_id": str(_SAMPLE_USER["id"])}),
        ("delete", f"/supervisors/{_SAMPLE_USER['id']}", None),
    ])
    def test_grupo_de_outro_tenant_e_404_e_nada_muda(self, client, metodo, caminho, corpo):
        c, _ = client
        escritas = ["update_group", "delete_group", "add_group_user", "remove_group_user",
                    "add_group_supervisor", "remove_group_supervisor"]
        mocks = {n: patch(f"{DB}.{n}", new=AsyncMock()) for n in escritas}
        with patch(f"{DB}.get_group", new=AsyncMock(return_value=_GRUPO_ALHEIO_ROW)), \
             patch(f"{DB}.get_user_by_id", new=AsyncMock(return_value=_SAMPLE_USER)):
            ativos = {n: m.start() for n, m in mocks.items()}
            try:
                kw = {"headers": _admin(), **({"json": corpo} if corpo is not None else {})}
                r = getattr(c, metodo)(f"/auth/v1/groups/{_GRUPO}{caminho}", **kw)
            finally:
                for m in mocks.values():
                    m.stop()
        assert r.status_code == 404
        for n, m in ativos.items():
            assert m.await_count == 0, n

    @pytest.mark.parametrize("papel", ["users", "supervisors"])
    def test_por_usuario_de_outro_tenant_no_proprio_grupo_e_404(self, client, papel):
        c, _ = client
        alvo = "add_group_user" if papel == "users" else "add_group_supervisor"
        with patch(f"{DB}.get_group", new=AsyncMock(return_value=_GRUPO_ROW)), \
             patch(f"{DB}.get_user_by_id", new=AsyncMock(return_value=_USER_ALHEIO)), \
             patch(f"{DB}.{alvo}", new=AsyncMock()) as pos:
            r = c.post(f"/auth/v1/groups/{_GRUPO}/{papel}",
                       json={"user_id": str(_USER_ALHEIO["id"])}, headers=_admin())
        assert r.status_code == 404
        pos.assert_not_awaited()

    @pytest.mark.parametrize("papel", ["users", "supervisors"])
    def test_controle_usuario_do_proprio_tenant_no_proprio_grupo_201(self, client, papel):
        c, _ = client
        alvo = "add_group_user" if papel == "users" else "add_group_supervisor"
        with patch(f"{DB}.get_group", new=AsyncMock(return_value=_GRUPO_ROW)), \
             patch(f"{DB}.get_user_by_id", new=AsyncMock(return_value=_SAMPLE_USER)), \
             patch(f"{DB}.{alvo}", new=AsyncMock(return_value={"user_id": str(_SAMPLE_USER["id"])})) as pos:
            r = c.post(f"/auth/v1/groups/{_GRUPO}/{papel}",
                       json={"user_id": str(_SAMPLE_USER["id"])}, headers=_admin())
        assert r.status_code == 201
        pos.assert_awaited_once()

    def test_token_sem_tenant_e_403(self, client):
        c, _ = client
        tok = _access_token(user=_user_copy(roles=["admin"], tenant_id=""),
                            module_config={"config": {"users": {"access": "read_write", "scope": []}}})
        with patch(f"{DB}.list_groups", new=AsyncMock(return_value=[])) as listou:
            r = c.get("/auth/v1/groups", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403 and r.json()["detail"] == "tenant_claim_missing"
        listou.assert_not_awaited()
