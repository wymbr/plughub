"""PID-13 — a exigência de identidade vale em TODAS as portas de retomada.

Medido antes: sete caminhos chegam ao `handle_resume`, um deles público e anônimo
(`/channel/webhook/resume/{token}`), e nenhum conferia a exigência que a PID-06 passou a
reter na liberação do token. A regra mora no `handle_resume`; três atores passam (evidência
atestada pelo mcp-server, principal humano verificado, scanner de prazo). Cada recusa tem o
controle positivo ao lado — um portão que recusasse tudo passaria nelas.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plughub_channel_gateway import main as cg_main
from plughub_channel_gateway.adapters.webhook import WebhookAdapter


def _adapter(meta: dict | None) -> WebhookAdapter:
    a = WebhookAdapter.__new__(WebhookAdapter)
    a._redis = MagicMock()
    a._redis.get = AsyncMock(return_value=json.dumps(meta) if meta is not None else None)
    return a


async def _gate(a, clearance=None, approver=None):
    await a._enforce_resume_requirement("t", "tk", "sess", "external", approver, clearance)


@pytest.mark.asyncio
async def test_anonimo_recusado_quando_ha_exigencia():
    with pytest.raises(PermissionError) as e:
        await _gate(_adapter({"session_id": "sess", "resume_requires": ["otp"]}))
    assert "resume_requires_unproven" in str(e.value)


@pytest.mark.asyncio
async def test_os_tres_atores_passam():
    meta = {"session_id": "sess", "resume_requires": ["otp"]}
    await _gate(_adapter(meta), clearance="session_evidence")
    await _gate(_adapter(meta), clearance="timeout")
    await _gate(_adapter(meta), approver={"decided_by": "u-aprovador"})


@pytest.mark.asyncio
async def test_clearance_desconhecido_nao_libera():
    with pytest.raises(PermissionError):
        await _gate(_adapter({"resume_requires": ["otp"]}), clearance="qualquer_coisa")


@pytest.mark.asyncio
async def test_sem_exigencia_passa_em_qualquer_porta():
    await _gate(_adapter(None))                                  # suspend sem registro
    await _gate(_adapter({"session_id": "sess"}))                # registro anterior à PID-06
    await _gate(_adapter({"session_id": "sess", "resume_requires": []}))


@pytest.mark.asyncio
async def test_registro_do_token_carrega_a_exigencia_e_falha_fechado_ao_perde_la():
    a = WebhookAdapter.__new__(WebhookAdapter)
    a._redis = MagicMock()
    a._redis.set = AsyncMock()
    await a._write_resume_meta("t", "tk", "sess", "aprovar", "2026-09-20T00:00:00+00:00", "input", 3600, ["otp"])
    gravado = json.loads(a._redis.set.call_args.args[1])
    assert gravado["resume_requires"] == ["otp"]

    a._redis.set = AsyncMock(side_effect=RuntimeError("redis caiu"))
    with pytest.raises(RuntimeError):
        await a._write_resume_meta("t", "tk", "sess", "aprovar", "x", "input", 3600, ["otp"])
    # controle: sem exigência a escrita segue best-effort (loga, não sobe)
    await a._write_resume_meta("t", "tk", "sess", "aprovar", "x", "input", 3600, None)


def _req(headers: dict) -> SimpleNamespace:
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()})


def test_atestado_so_vale_com_a_credencial_de_servico(monkeypatch):
    s = cg_main.get_settings()
    monkeypatch.setattr(s, "channel_gateway_service_token", "svc-pid13", raising=False)
    h = "x-resume-identity-clearance"
    assert cg_main._resume_identity_clearance(_req({h: "session_evidence", "x-service-token": "svc-pid13"})) == "session_evidence"
    assert cg_main._resume_identity_clearance(_req({h: "session_evidence"})) is None
    assert cg_main._resume_identity_clearance(_req({h: "session_evidence", "x-service-token": "errado"})) is None
    assert cg_main._resume_identity_clearance(_req({h: "timeout", "x-service-token": "svc-pid13"})) is None
    assert cg_main._resume_identity_clearance(_req({"x-service-token": "svc-pid13"})) is None


def test_sem_credencial_configurada_nenhum_atestado_vale(monkeypatch):
    s = cg_main.get_settings()
    monkeypatch.setattr(s, "channel_gateway_service_token", "", raising=False)
    assert cg_main._resume_identity_clearance(
        _req({"x-resume-identity-clearance": "session_evidence", "x-service-token": ""})) is None
