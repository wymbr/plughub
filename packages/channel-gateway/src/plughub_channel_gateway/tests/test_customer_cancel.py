"""PID-15 — o cliente que provou identidade CANCELA a tarefa de aprovação; nunca a decide.

Medido em 2026-09-14: o intake do limite oferece "Cancelar solicitação" e o gateway
devolvia 401 (AUT-46: tarefa que declara capacidade exige Bearer humano), então o cliente
lia "não consegui processar" e o processo seguia suspenso. A dispensa do Bearer vale só
com as quatro condições juntas; cada teste negativo tira UMA delas do caso positivo.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.datastructures import Headers

from plughub_channel_gateway import main as cg_main

APROVACAO = ("approvals", "decide")


def _req(auth: str | None = None):
    return SimpleNamespace(headers=Headers({"authorization": auth} if auth else {}))


def _body(decision: str | None = "rejected"):
    return cg_main.WebhookResumeRequest(tenant_id="t", payload={"decision": decision} if decision else {})


@pytest.fixture
def adapter(monkeypatch):
    a = MagicMock()
    a.resume_requirement = AsyncMock(return_value=["otp"])
    monkeypatch.setattr(cg_main, "_webhook_adapter", a)
    return a


async def _permitido(req=None, body=None, abac=APROVACAO, clearance="session_evidence"):
    return await cg_main._customer_cancel_allowed(req or _req(), body or _body(), "tk", abac, clearance)


@pytest.mark.asyncio
async def test_controle_cliente_provado_cancela(adapter):
    assert await _permitido() is True


@pytest.mark.asyncio
async def test_cliente_nunca_decide(adapter):
    for d in ("input", "approved", None):
        assert await _permitido(body=_body(d)) is False, d


@pytest.mark.asyncio
async def test_sem_atestado_nao_dispensa(adapter):
    assert await _permitido(clearance=None) is False


@pytest.mark.asyncio
async def test_com_bearer_segue_o_caminho_humano(adapter):
    assert await _permitido(req=_req("Bearer x.y.z")) is False


@pytest.mark.asyncio
async def test_tarefa_sem_capacidade_nao_passa_por_aqui(adapter):
    assert await _permitido(abac=None) is False


@pytest.mark.asyncio
async def test_sem_exigencia_de_identidade_nao_ha_base_para_o_cliente(adapter):
    adapter.resume_requirement = AsyncMock(return_value=[])
    assert await _permitido() is False
