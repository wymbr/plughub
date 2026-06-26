"""
test_gprobe_phase2.py — G-PROBE fase 2: gates de credencial de serviço + leitura any-of.

Funções puras sobre `Request` (sem DB/Kafka):
  - `_require_service`              — STRICT X-Service-Token; vazio = no-op (demo).
  - `_require_service_or_eval_write`— serviço OU Bearer+ABAC formularios:read_write.
  - `_has_any_evaluation_access` / `_require_any_evaluation` — any-of dos campos do
    módulo `evaluation` (degradação graciosa; nega só JWT com module_config sem grant).

Constrói um `Request` falso com headers case-insensitive e JWTs HS256 reais (pyjwt)
assinados com `settings.jwt_secret`.
"""
from __future__ import annotations

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from ..config import settings
from ..router import (
    _require_service,
    _require_service_or_eval_write,
    _require_any_evaluation,
    _has_any_evaluation_access,
)


# ─── fakes ────────────────────────────────────────────────────────────────────

class _Headers(dict):
    """Headers case-insensitive (espelha starlette.Headers.get para os campos usados)."""
    def __init__(self, d: dict[str, str]):
        super().__init__({k.lower(): v for k, v in d.items()})

    def get(self, key: str, default=None):  # type: ignore[override]
        return super().get(key.lower(), default)


class _Req:
    def __init__(self, **headers: str):
        self.headers = _Headers(headers)


def _bearer(**fields: str) -> str:
    """JWT HS256 com module_config.evaluation.{field}={access, scope:[]} (scope global)."""
    eval_cfg = {f: {"access": acc, "scope": []} for f, acc in fields.items()}
    payload = {"sub": "u_test", "module_config": {"evaluation": eval_cfg}}
    return pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def svc_token(monkeypatch):
    """Configura um service_token (gate ativo)."""
    monkeypatch.setattr(settings, "service_token", "svc_secret_123")
    return "svc_secret_123"


@pytest.fixture
def no_svc_token(monkeypatch):
    """service_token vazio → no-op (postura demo)."""
    monkeypatch.setattr(settings, "service_token", "")


# ─── _require_service (STRICT) ────────────────────────────────────────────────

def test_service_noop_when_unset(no_svc_token):
    # vazio = aberto (demo), espelha _require_admin.
    _require_service(_Req())  # não levanta


def test_service_ok_with_correct_token(svc_token):
    _require_service(_Req(**{"X-Service-Token": svc_token}))  # não levanta


def test_service_rejects_wrong_token(svc_token):
    with pytest.raises(HTTPException) as exc:
        _require_service(_Req(**{"X-Service-Token": "nope"}))
    assert exc.value.status_code == 401


def test_service_rejects_missing_token(svc_token):
    with pytest.raises(HTTPException) as exc:
        _require_service(_Req())
    assert exc.value.status_code == 401


def test_service_does_not_accept_admin_token(svc_token, monkeypatch):
    # STRICT (decisão Q1): X-Admin-Token NÃO é fallback do serviço.
    monkeypatch.setattr(settings, "admin_token", "admin_secret")
    with pytest.raises(HTTPException) as exc:
        _require_service(_Req(**{"X-Admin-Token": "admin_secret"}))
    assert exc.value.status_code == 401


def test_service_case_insensitive_header(svc_token):
    _require_service(_Req(**{"x-service-token": svc_token}))  # não levanta


# ─── _require_service_or_eval_write (dual UI/ops) ─────────────────────────────

def test_dual_noop_when_unset(no_svc_token):
    _require_service_or_eval_write(_Req())  # demo aberto


def test_dual_ok_with_service_token(svc_token):
    _require_service_or_eval_write(_Req(**{"X-Service-Token": svc_token}))


def test_dual_ok_with_bearer_formularios_rw(svc_token):
    req = _Req(**_auth(_bearer(formularios="read_write")))
    _require_service_or_eval_write(req)  # caminho da UI (operador)


def test_dual_rejects_bearer_read_only(svc_token):
    # read_only NÃO satisfaz a escrita.
    req = _Req(**_auth(_bearer(formularios="read_only")))
    with pytest.raises(HTTPException) as exc:
        _require_service_or_eval_write(req)
    assert exc.value.status_code == 403


def test_dual_rejects_bearer_other_field(svc_token):
    # grant em outro campo (report) não concede formularios.
    req = _Req(**_auth(_bearer(report="read_write")))
    with pytest.raises(HTTPException) as exc:
        _require_service_or_eval_write(req)
    assert exc.value.status_code == 403


def test_dual_rejects_no_credential(svc_token):
    with pytest.raises(HTTPException) as exc:
        _require_service_or_eval_write(_Req())
    # sem service token e sem Bearer → _decode_jwt levanta 401
    assert exc.value.status_code == 401


def test_dual_wrong_service_falls_to_bearer(svc_token):
    # header de serviço errado → exige Bearer; sem Bearer → 401.
    with pytest.raises(HTTPException) as exc:
        _require_service_or_eval_write(_Req(**{"X-Service-Token": "wrong"}))
    assert exc.value.status_code == 401


# ─── _has_any_evaluation_access ───────────────────────────────────────────────

def test_anyof_none_payload_allowed():
    assert _has_any_evaluation_access(None) is True


def test_anyof_admin_role_allowed():
    assert _has_any_evaluation_access({"sub": "u", "roles": ["admin"]}) is True


def test_anyof_empty_module_config_allowed():
    assert _has_any_evaluation_access({"sub": "u", "module_config": {}}) is True


def test_anyof_one_grant_allowed():
    jwt = {"sub": "u", "module_config": {"evaluation": {"report": {"access": "read_only", "scope": []}}}}
    assert _has_any_evaluation_access(jwt) is True


def test_anyof_module_config_without_evaluation_denied():
    jwt = {"sub": "u", "module_config": {"contacts": {"operacao": {"access": "read_write"}}}}
    assert _has_any_evaluation_access(jwt) is False


def test_anyof_all_none_denied():
    jwt = {"sub": "u", "module_config": {"evaluation": {"report": {"access": "none", "scope": []}}}}
    assert _has_any_evaluation_access(jwt) is False


# ─── _require_any_evaluation (Bearer opcional) ────────────────────────────────

def test_require_anyof_anonymous_allowed():
    _require_any_evaluation(_Req())  # sem Bearer → demo aberto


def test_require_anyof_with_grant_allowed():
    _require_any_evaluation(_Req(**_auth(_bearer(report="read_only"))))


def test_require_anyof_no_eval_grant_denied():
    # Bearer com module_config evaluation mas todos none → 403.
    token = pyjwt.encode(
        {"sub": "u", "module_config": {"evaluation": {"report": {"access": "none", "scope": []}}}},
        settings.jwt_secret, algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        _require_any_evaluation(_Req(**_auth(token)))
    assert exc.value.status_code == 403


def test_require_anyof_invalid_bearer_degrades_allowed():
    # token inválido → _decode_jwt_optional retorna None → degrada p/ permitir (demo).
    _require_any_evaluation(_Req(**{"Authorization": "Bearer not-a-jwt"}))
