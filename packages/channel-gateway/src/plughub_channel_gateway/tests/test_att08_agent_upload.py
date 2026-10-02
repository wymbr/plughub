"""test_att08_agent_upload.py — ATT-08 (2026-10-02): o arquivo do ATENDENTE entra pela MESMA esteira.

PROPOSIÇÃO: a rota interna `POST /v1/attachments/agent-upload` só aceita SERVIÇO (o mcp-server, que
decidiu quem atende), confere tipo e tamanho como a reserva do cliente, e grava pelo MESMO `commit`
do store — onde moram classe, assinatura, antivírus e re-codificação (ATT-01/05). Uma segunda
esteira, mais frouxa para quem está do lado de dentro, seria o defeito.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from plughub_channel_gateway import main as _main  # noqa: F401 — o main monta os routers
from plughub_channel_gateway import attachment_internal_router as r
from plughub_channel_gateway.attachment_store import AttachmentInfected, AttachmentMeta

TOKEN = "svc-token-att08"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF\n"


def _req(headers: dict[str, str], body: bytes) -> Request:
    enviado = {"v": False}

    async def receive():
        if enviado["v"]:
            return {"type": "http.disconnect"}
        enviado["v"] = True
        return {"type": "http.request", "body": body, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/v1/attachments/agent-upload",
                    "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                    "query_string": b""}, receive)


class _Store:
    def __init__(self, falha: Exception | None = None):
        self.reservas: list[dict] = []
        self.commits: list[bytes] = []
        self.falha = falha

    async def reserve(self, **kw):
        self.reservas.append(kw)
        return "f-agente", "http://x/upload/f-agente"

    async def commit(self, *, file_id, tenant_id, data):
        if self.falha:
            raise self.falha
        self.commits.append(data)
        return AttachmentMeta(file_id=file_id, tenant_id=tenant_id, session_id="s1",
                              original_name="contrato.pdf", mime_type="application/pdf",
                              size_bytes=len(data), file_path="p", serving_url="", expires_at=None,
                              deleted_at=None, artifact_class="webchat_attachment", attrs={},
                              scan_status="clean", sha256=None)


@pytest.fixture
def ambiente(monkeypatch):
    s = SimpleNamespace(channel_gateway_service_token=TOKEN, auth_jwt_secret="x" * 32,
                        attachment_expiry_days=30)
    monkeypatch.setattr(r._main_module, "get_settings", lambda: s)
    monkeypatch.setattr(r._main_module, "_producer", None, raising=False)
    monkeypatch.setattr(r._main_module, "_redis", None, raising=False)

    async def _dias(*a, **k):
        return 30
    monkeypatch.setattr(r, "resolve_attachment_expiry_days", _dias)
    monkeypatch.setattr(r, "webchat_config", SimpleNamespace(get=lambda k: None))

    def _com(store):
        monkeypatch.setattr(r._main_module, "_attachment_store", store, raising=False)
        return store
    return _com


async def _envia(body=PDF, mime="application/pdf", headers=None):
    return await r.agent_upload(_req(headers if headers is not None else {"x-service-token": TOKEN}, body),
                                tenant_id="t", session_id="s1", file_name="contrato.pdf",
                                mime_type=mime, uploaded_by="human-u1")


async def test_servico_grava_pelo_commit_do_store(ambiente):
    store = ambiente(_Store())
    out = await _envia()
    assert out["file_id"] == "f-agente" and out["content_type"] == "document"
    assert store.commits == [PDF], "os bytes passaram pelo commit — a esteira do cliente"
    assert store.reservas[0]["session_id"] == "s1"
    assert store.reservas[0]["artifact_class"] == "webchat_attachment"
    assert store.reservas[0]["attrs"] == {"uploaded_by": "human-u1"}


async def test_sem_credencial_de_servico_401(ambiente):
    store = ambiente(_Store())
    with pytest.raises(HTTPException) as e:
        await _envia(headers={})
    assert e.value.status_code == 401 and store.reservas == []


async def test_tipo_fora_da_lista_415_antes_de_reservar(ambiente):
    store = ambiente(_Store())
    with pytest.raises(HTTPException) as e:
        await _envia(body=b"oi", mime="text/plain")
    assert e.value.status_code == 415 and store.reservas == []


async def test_conteudo_que_nao_e_o_tipo_415(ambiente):
    ambiente(_Store(falha=ValueError("assinatura nao confere")))
    with pytest.raises(HTTPException) as e:
        await _envia()
    assert e.value.status_code == 415


async def test_antivirus_recusa_422(ambiente):
    ambiente(_Store(falha=AttachmentInfected("EICAR")))
    with pytest.raises(HTTPException) as e:
        await _envia()
    assert e.value.status_code == 422


async def test_corpo_vazio_400(ambiente):
    ambiente(_Store())
    with pytest.raises(HTTPException) as e:
        await _envia(body=b"")
    assert e.value.status_code == 400


async def test_parametros_ausentes_400_depois_do_portao(ambiente):
    store = ambiente(_Store())
    with pytest.raises(HTTPException) as e:
        await r.agent_upload(_req({"x-service-token": TOKEN}, PDF), tenant_id="t", session_id="s1",
                             file_name="", mime_type="application/pdf", uploaded_by="")
    assert e.value.status_code == 400 and store.reservas == []


def test_anonimo_ouve_401_antes_da_validacao_da_query(ambiente):
    """A varredura anônima (AUT-58) dispara SEM os parâmetros obrigatórios; com o portão dentro do
    handler, a resposta era 422 e a rota parecia aberta."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(r.router)
    assert TestClient(app).post("/v1/attachments/agent-upload").status_code == 401
