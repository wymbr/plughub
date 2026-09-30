"""AUT-65 — a API do rules-engine exige credencial, e o grau do `config.rules` decide.

Cada portão tem o controle positivo ao lado: um portão que recusa TODO MUNDO passaria em
todos os testes de recusa, e é o controle (o admin ativa, o serviço lista) que o denuncia."""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from plughub_rules import api as api_mod
from plughub_rules import config as cfg

SECRET = "segredo-de-teste-com-32-caracteres!!"
SVC = "svc-token"
T = "tenant_a"


def _tok(access: str | None, tenant: str = T) -> dict:
    mc = {"config": {"rules": {"access": access, "scope": []}}} if access else {}
    tok = jwt.encode({"sub": "u1", "tenant_id": tenant, "module_config": mc,
                      "exp": int(time.time()) + 600}, SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


class _FakeRedis:
    def __init__(self):
        self.kv: dict = {}
        self.sets: dict = {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, *a, **kw):
        self.kv[k] = v

    async def delete(self, *k):
        for x in k:
            self.kv.pop(x, None)

    async def sadd(self, k, *m):
        self.sets.setdefault(k, set()).update(m)

    async def srem(self, k, *m):
        self.sets.get(k, set()).difference_update(m)

    async def smembers(self, k):
        return set(self.sets.get(k, set()))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PLUGHUB_AUTH_JWT_SECRET", SECRET)
    monkeypatch.setenv("PLUGHUB_RULES_SERVICE_TOKEN", SVC)
    cfg.get_settings.cache_clear()
    fake = _FakeRedis()
    api_mod.app.dependency_overrides[api_mod.get_redis] = lambda: fake
    yield TestClient(api_mod.app)
    api_mod.app.dependency_overrides.clear()
    cfg.get_settings.cache_clear()


RULE = {"rule_id": "r1", "tenant_id": T, "name": "n",
        "conditions": [{"parameter": "elapsed_ms", "operator": "gte", "value": 0}],
        "target_pool": "p"}

PROTEGIDAS = [
    ("get", f"/rules?tenant_id={T}", None),
    ("get", f"/rules/r1?tenant_id={T}", None),
    ("get", f"/rules/r1/report?tenant_id={T}", None),
    ("post", "/rules", {"x": 1}),                       # corpo inválido: 401, nunca 422
    ("patch", f"/rules/r1/status?tenant_id={T}", {"x": 1}),
    ("post", "/evaluate", {}),
    ("post", "/rules/r1/dry-run", {"x": 1}),
    ("post", "/rules/dry-run", {}),
]


@pytest.mark.parametrize("method,path,body", PROTEGIDAS)
def test_anonymous_is_401_on_every_protected_route(client, method, path, body):
    r = getattr(client, method)(path, **({"json": body} if body is not None else {}))
    assert r.status_code == 401, (path, r.status_code, r.text)


def test_health_stays_open(client):
    assert client.get("/health").status_code == 200


def test_admin_creates_and_activates_control(client):
    h = _tok("read_write")
    assert client.post("/rules", json=RULE, headers=h).status_code == 201
    for s in ("dry_run", "shadow", "active"):
        r = client.patch(f"/rules/r1/status?tenant_id={T}", json={"status": s}, headers=h)
        assert r.status_code == 200, (s, r.text)


def test_reader_reads_but_cannot_activate_nor_create(client):
    client.post("/rules", json=RULE, headers={"X-Service-Token": SVC})
    ro = _tok("read_only")
    assert client.get(f"/rules?tenant_id={T}", headers=ro).status_code == 200
    assert client.get(f"/rules/r1?tenant_id={T}", headers=ro).status_code == 200
    r = client.patch(f"/rules/r1/status?tenant_id={T}", json={"status": "dry_run"}, headers=ro)
    assert r.status_code == 403 and "config.rules" in r.text
    assert client.post("/rules", json={**RULE, "rule_id": "r2"}, headers=ro).status_code == 403


def test_user_without_the_field_is_403_even_to_read(client):
    assert client.get(f"/rules?tenant_id={T}", headers=_tok(None)).status_code == 403


def test_tenant_comes_from_the_token(client):
    h = _tok("read_write")
    assert client.get("/rules?tenant_id=outro", headers=h).status_code == 403
    assert client.post("/rules", json={**RULE, "tenant_id": "outro"}, headers=h).status_code == 403


def test_service_token_passes_and_a_wrong_one_does_not(client):
    assert client.get(f"/rules?tenant_id={T}", headers={"X-Service-Token": SVC}).status_code == 200
    assert client.get(f"/rules?tenant_id={T}", headers={"X-Service-Token": "outro"}).status_code == 401


def test_empty_service_token_config_closes_the_door(client, monkeypatch):
    monkeypatch.setenv("PLUGHUB_RULES_SERVICE_TOKEN", "")
    cfg.get_settings.cache_clear()
    assert client.get(f"/rules?tenant_id={T}", headers={"X-Service-Token": ""}).status_code == 401


def test_missing_jwt_secret_is_503_never_accept(client, monkeypatch):
    monkeypatch.setenv("PLUGHUB_AUTH_JWT_SECRET", "")
    cfg.get_settings.cache_clear()
    assert client.get(f"/rules?tenant_id={T}", headers=_tok("read_write")).status_code == 503


def test_dry_run_refuses_loudly_instead_of_zeros(client):
    h = _tok("read_write")
    client.post("/rules", json=RULE, headers=h)
    r = client.post("/rules/r1/dry-run", json={"start_date": "2026-09-01", "end_date": "2026-09-30",
                                                "tenant_id": T}, headers=h)
    assert r.status_code == 501 and "RUL-05" in r.text
    r = client.post("/rules/dry-run", json={"tenant_id": T, "rule": {"name": "x"}},
                    headers={"X-Service-Token": SVC})
    assert r.status_code == 501 and "RUL-05" in r.text
    assert client.post("/rules/nao_existe/dry-run", json={"start_date": "a", "end_date": "b",
                                                           "tenant_id": T}, headers=h).status_code == 404


# ─── RUL-03 — editar e apagar só em draft/disabled ─────────────────────────────

UPD = {"name": "novo", "conditions": [{"parameter": "turn_count", "operator": "gte", "value": 5}],
       "logic": "OR", "target_pool": "  outro  ", "priority": 3, "customer_notice": ""}


def _to(client, h, *status):
    for s in status:
        r = client.patch(f"/rules/r1/status?tenant_id={T}", json={"status": s}, headers=h)
        assert r.status_code == 200, (s, r.text)


def test_edit_in_draft_replaces_what_the_rule_decides(client):
    h = _tok("read_write")
    client.post("/rules", json=RULE, headers=h)
    r = client.put(f"/rules/r1?tenant_id={T}", json=UPD, headers=h)
    assert r.status_code == 200, r.text
    got = client.get(f"/rules/r1?tenant_id={T}", headers=h).json()
    assert got["name"] == "novo" and got["logic"] == "OR" and got["priority"] == 3
    assert got["target_pool"] == "outro" and got["customer_notice"] is None
    assert got["conditions"][0]["parameter"] == "turn_count" and got["status"] == "draft"


@pytest.mark.parametrize("path", [("dry_run",), ("dry_run", "shadow"), ("dry_run", "shadow", "active")])
def test_rule_that_acts_or_measures_is_locked(client, path):
    h = _tok("read_write")
    client.post("/rules", json=RULE, headers=h)
    _to(client, h, *path)
    r = client.put(f"/rules/r1?tenant_id={T}", json=UPD, headers=h)
    assert r.status_code == 409 and "disabled" in r.json()["detail"], r.text
    assert client.delete(f"/rules/r1?tenant_id={T}", headers=h).status_code == 409
    assert client.get(f"/rules/r1?tenant_id={T}", headers=h).json()["name"] == "n"


def test_disabled_rule_is_editable_and_deletable_and_leaves_the_active_cache(client):
    h = _tok("read_write")
    client.post("/rules", json=RULE, headers=h)
    _to(client, h, "dry_run", "shadow", "active")
    fake = api_mod.app.dependency_overrides[api_mod.get_redis]()
    assert "r1" in fake.kv[f"rules:{T}:active"]
    _to(client, h, "disabled")
    assert client.put(f"/rules/r1?tenant_id={T}", json=UPD, headers=h).status_code == 200
    assert client.delete(f"/rules/r1?tenant_id={T}", headers=h).status_code == 204
    assert client.get(f"/rules/r1?tenant_id={T}", headers=h).status_code == 404
    assert client.get(f"/rules?tenant_id={T}", headers=h).json() == []
    assert not fake.sets.get(f"{T}:rules:ids"), "o índice guardaria um id sem regra"
    assert client.delete(f"/rules/r1?tenant_id={T}", headers=h).status_code == 404


def test_edit_and_delete_need_read_write_and_own_tenant(client):
    client.post("/rules", json=RULE, headers={"X-Service-Token": SVC})
    ro = _tok("read_only")
    assert client.put(f"/rules/r1?tenant_id={T}", json=UPD, headers=ro).status_code == 403
    assert client.delete(f"/rules/r1?tenant_id={T}", headers=ro).status_code == 403
    rw = _tok("read_write")
    assert client.delete("/rules/r1?tenant_id=outro", headers=rw).status_code == 403
    assert client.put(f"/rules/r1?tenant_id={T}", json=UPD).status_code == 401
    assert client.delete(f"/rules/r1?tenant_id={T}").status_code == 401
    assert client.get(f"/rules/r1?tenant_id={T}", headers=rw).status_code == 200


def test_lifecycle_is_served_not_copied(client):
    r = client.get(f"/lifecycle?tenant_id={T}", headers=_tok("read_only"))
    assert r.status_code == 200
    body = r.json()
    assert body["editable_statuses"] == ["disabled", "draft"]
    assert body["transitions"]["draft"] == ["disabled", "dry_run"]
    assert client.get(f"/lifecycle?tenant_id={T}").status_code == 401
