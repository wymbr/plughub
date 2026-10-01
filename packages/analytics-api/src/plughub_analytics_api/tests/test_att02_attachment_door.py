"""test_att02_attachment_door.py — ATT-02 (2026-10-01): a porta INTERNA do anexo.

PROPOSIÇÃO: só vê o anexo quem pode ler a conversa dele — `contacts.transcricao` E o pool da
sessão no escopo —, e TODO desfecho fica na trilha, inclusive a recusa.

Ordens que o 403 sozinho não mostra, e por isso são testadas aqui:
  * a capacidade decide ANTES de perguntar ao gateway (quem não pode ler não aprende se o id existe);
  * a recusa é gravada na trilha ANTES de responder.
Cada recusa tem o controle positivo ao lado.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from plughub_analytics_api import attachments
from plughub_analytics_api.pool_auth import PoolPrincipal

FILE = "11111111-2222-3333-4444-555555555555"
SID = "sess-att02"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GRANT = {"contacts": {"transcricao": {"access": "read_only"}}}


def _user(pools, module_config):
    return PoolPrincipal(accessible_pools=pools, tenant_id="tenant_demo", sub="u1",
                         module_config=module_config)


class _Gateway:
    """O gateway falso: conta as perguntas e responde o que o teste declarar."""

    def __init__(self, meta_status=200, expired=False, content_status=200):
        self.chamadas: list[str] = []
        self.meta_status, self.expired, self.content_status = meta_status, expired, content_status

    async def __call__(self, path: str, tenant_id: str) -> httpx.Response:
        self.chamadas.append(path)
        if path.endswith("/meta"):
            if self.meta_status != 200:
                return httpx.Response(self.meta_status)
            return httpx.Response(200, json={"session_id": SID, "expired": self.expired,
                                             "mime_type": "image/jpeg"})
        if self.content_status != 200:
            return httpx.Response(self.content_status)
        return httpx.Response(200, content=JPEG, headers={
            "content-type": "image/jpeg", "content-disposition": 'inline; filename="x.jpg"',
            "content-length": str(len(JPEG))})


@pytest.fixture
def trilha(monkeypatch):
    linhas: list[dict] = []

    async def _rec(request, **kw):
        linhas.append(kw)
    monkeypatch.setattr(attachments, "_record_access", _rec)
    return linhas


@pytest.fixture
def pools_da_sessao(monkeypatch):
    estado = {"pools": {"sac_ia"}}

    async def _vivo(redis, tenant_id, session_id):
        return set(estado["pools"])
    monkeypatch.setattr("plughub_analytics_api.pool_auth.resolve_live_session_pools", _vivo)
    return estado


def _req():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=object(), store=None)))


async def _ver(principal, gw, monkeypatch):
    monkeypatch.setattr(attachments, "_gateway_get", gw)
    return await attachments.view_attachment(FILE, _req(), tenant_id=None, principal=principal)


@pytest.mark.asyncio
async def test_controle_positivo_serve_os_bytes_com_cabecalhos_e_trilha_ok(
        monkeypatch, trilha, pools_da_sessao):
    r = await _ver(_user(["sac_ia"], GRANT), _Gateway(), monkeypatch)
    assert r.status_code == 200 and r.body == JPEG
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert [l["result"] for l in trilha] == ["ok"]
    assert trilha[0]["target_id"] == f"{SID}/{FILE}"
    assert trilha[0]["endpoint"] == "analytics-api:attachments.view"


@pytest.mark.asyncio
async def test_sem_capacidade_recusa_SEM_perguntar_ao_gateway_e_grava_a_recusa(
        monkeypatch, trilha, pools_da_sessao):
    gw = _Gateway()
    with pytest.raises(HTTPException) as e:
        await _ver(_user(["sac_ia"], {"contacts": {"monitorar": {"access": "read_write"}}}), gw, monkeypatch)
    assert e.value.status_code == 403
    assert gw.chamadas == [], "perguntou ao gateway antes de conferir a capacidade (oráculo de id)"
    assert [l["result"] for l in trilha] == ["denied"]


@pytest.mark.asyncio
async def test_usuario_sem_grants_nega(monkeypatch, trilha, pools_da_sessao):
    with pytest.raises(HTTPException) as e:
        await _ver(_user(["sac_ia"], {}), _Gateway(), monkeypatch)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_pool_fora_do_escopo_recusa_sem_buscar_bytes(monkeypatch, trilha, pools_da_sessao):
    pools_da_sessao["pools"] = {"retencao_humano"}
    gw = _Gateway()
    with pytest.raises(HTTPException) as e:
        await _ver(_user(["sac_ia"], GRANT), gw, monkeypatch)
    assert e.value.status_code == 403
    assert all(not c.endswith("/content") for c in gw.chamadas), "buscou os bytes de quem foi recusado"
    assert [l["result"] for l in trilha] == ["denied"]
    assert trilha[0]["target_id"] == f"{SID}/{FILE}"


@pytest.mark.asyncio
async def test_id_desconhecido_404_e_trilha(monkeypatch, trilha, pools_da_sessao):
    with pytest.raises(HTTPException) as e:
        await _ver(_user(["sac_ia"], GRANT), _Gateway(meta_status=404), monkeypatch)
    assert e.value.status_code == 404
    assert [l["result"] for l in trilha] == ["not_found"]


@pytest.mark.asyncio
async def test_expirado_410_e_trilha(monkeypatch, trilha, pools_da_sessao):
    with pytest.raises(HTTPException) as e:
        await _ver(_user(["sac_ia"], GRANT), _Gateway(expired=True), monkeypatch)
    assert e.value.status_code == 410
    assert [l["result"] for l in trilha] == ["expired"]


@pytest.mark.asyncio
async def test_em_verificacao_423_dito_e_trilha_propria(monkeypatch, trilha, pools_da_sessao):
    """ATT-05: a recusa do antivírus é do gateway; aqui ela chega como 423, nunca como 503."""
    with pytest.raises(HTTPException) as e:
        await _ver(_user(["sac_ia"], GRANT), _Gateway(content_status=423), monkeypatch)
    assert e.value.status_code == 423 and e.value.detail == "attachment_pending_scan"
    assert [l["result"] for l in trilha] == ["pending_scan"]


@pytest.mark.asyncio
async def test_gateway_fora_503_nomeado_e_trilha(monkeypatch, trilha, pools_da_sessao):
    async def _quebrado(path, tenant_id):
        raise httpx.ConnectError("recusou")
    with pytest.raises(HTTPException) as e:
        await _ver(_user(["sac_ia"], GRANT), _quebrado, monkeypatch)
    assert e.value.status_code == 503
    assert [l["result"] for l in trilha] == ["unavailable"]


@pytest.mark.asyncio
async def test_servico_escolhe_o_tenant_pela_query(monkeypatch, trilha, pools_da_sessao):
    monkeypatch.setattr(attachments, "_gateway_get", _Gateway())
    svc = PoolPrincipal(accessible_pools=None, tenant_id=None, sub="service:x", module_config=None)
    with pytest.raises(HTTPException) as e:
        await attachments.view_attachment(FILE, _req(), tenant_id=None, principal=svc)
    assert e.value.status_code == 400
    r = await attachments.view_attachment(FILE, _req(), tenant_id="tenant_demo", principal=svc)
    assert r.status_code == 200
