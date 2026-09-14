"""
PID-04 (2026-09-14) — `deploy.config` semeia o config_json do slot.

O RegistrySyncer só sabia semear `max_concurrent_sessions`. Seis slots vivos já
dependiam de outras chaves que o promote exige (`form_id`, `resume_requires`), e a
porta de plataforma declara oito: numa base limpa esses pools não promoviam.

  1. slot vazio → o PUT leva as chaves de `deploy.config` + a capacidade do YAML;
  2. slot quebrado com config DB-owned → a do slot VENCE a semente (seed-if-absent);
  3. controle: sem `deploy.config`, o payload é o de sempre.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from plughub_orchestrator_bridge.registry_syncer import RegistrySyncer, SyncReport


def _cm(resp):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _resp(status, body=None):
    r = MagicMock()
    r.status = status
    r.json = AsyncMock(return_value=body or {})
    r.text = AsyncMock(return_value="")
    return r


def _http(current):
    http = MagicMock()
    http.get = MagicMock(return_value=_cm(_resp(200, {"slots": {"current": current}})))
    http.put = MagicMock(return_value=_cm(_resp(200)))
    http.post = MagicMock(return_value=_cm(_resp(200)))
    return http


def _syncer():
    s = RegistrySyncer.__new__(RegistrySyncer)
    s._registry_url = "http://reg"
    return s


def _payload(http):
    return http.put.call_args.kwargs["json"]


@pytest.mark.asyncio
async def test_slot_vazio_recebe_a_config_semeada():
    http = _http({})
    cfg = {"dialog_form_id": "dialog_limite_roteiro", "require": "otp"}
    await _syncer()._ensure_deploy_slot(http, {}, "limite_ia", "skill_intake_runner_v1", 5, SyncReport(tenant_id="t"), cfg)
    assert _payload(http)["config_json"] == {
        "dialog_form_id": "dialog_limite_roteiro", "require": "otp", "max_concurrent_sessions": 5,
    }


@pytest.mark.asyncio
async def test_config_do_slot_vence_a_semente():
    # snapshot nulo = inexecutável → re-semeia, mas preservando o que o operador gravou
    http = _http({"set": True, "skill_id": "skill_intake_runner_v1", "yaml_snapshot": None,
                  "config_json": {"require": "none", "max_concurrent_sessions": 9}})
    await _syncer()._ensure_deploy_slot(
        http, {}, "limite_ia", "skill_intake_runner_v1", 5, SyncReport(tenant_id="t"),
        {"require": "otp", "degrade_target": "sac_ia"},
    )
    assert _payload(http)["config_json"] == {
        "require": "none", "degrade_target": "sac_ia", "max_concurrent_sessions": 5,
    }


@pytest.mark.asyncio
async def test_controle_sem_deploy_config_payload_de_sempre():
    http = _http({})
    await _syncer()._ensure_deploy_slot(http, {}, "p", "skill_x", 3, SyncReport(tenant_id="t"))
    assert _payload(http)["config_json"] == {"max_concurrent_sessions": 3}
