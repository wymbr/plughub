"""test_att02_attachment_internal.py — ATT-02 (2026-10-01): os bytes do anexo, só para SERVIÇO.

PROPOSIÇÃO: a rota interna não é porta alternativa — sem a credencial de serviço do gateway ela
recusa, inclusive o usuário com grant; e a gravação de chamada não sai por ela (tem porta própria).
O controle positivo (serviço lê meta e conteúdo) fica ao lado de cada recusa.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from plughub_channel_gateway import main as _main  # noqa: F401 — o main monta os routers; importá-lo depois faz ciclo
from plughub_channel_gateway import attachment_internal_router as r
from plughub_channel_gateway.attachment_store import AttachmentMeta

TOKEN = "svc-token-att02"
FILE = "11111111-2222-3333-4444-555555555555"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def _req(headers: dict[str, str]) -> Request:
    return Request({"type": "http", "method": "GET", "path": f"/v1/attachments/{FILE}/meta",
                    "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                    "query_string": b""})


class _Store:
    def __init__(self, klass="webchat_attachment", deleted=False):
        self.meta = AttachmentMeta(
            file_id=FILE, tenant_id="t", session_id="sess-1", original_name="foto.jpg",
            mime_type="image/jpeg", size_bytes=len(JPEG), file_path="x", serving_url="",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            deleted_at=datetime.now(timezone.utc) if deleted else None,
            artifact_class=klass, attrs={}, scan_status="clean", sha256=None,   # ATT-05
        )

    async def resolve(self, *, file_id, tenant_id):
        return self.meta if file_id == FILE else None

    async def stream_bytes(self, *, file_id, tenant_id):
        async def _g():
            yield JPEG
        return _g()


@pytest.fixture
def ambiente(monkeypatch):
    settings = SimpleNamespace(channel_gateway_service_token=TOKEN, auth_jwt_secret="x" * 32)
    monkeypatch.setattr(r._main_module, "get_settings", lambda: settings)
    store = _Store()
    monkeypatch.setattr(r._main_module, "_attachment_store", store, raising=False)
    return store


async def test_servico_le_meta_controle_positivo(ambiente):
    m = await r.attachment_meta(FILE, _req({"x-service-token": TOKEN}), tenant_id="t")
    assert m["session_id"] == "sess-1" and m["expired"] is False


async def test_servico_le_conteudo_com_cabecalhos_da_porta(ambiente):
    resp = await r.attachment_content(FILE, _req({"x-service-token": TOKEN}), tenant_id="t", variant=None)
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-disposition"].startswith("inline;")
    assert resp.headers["cache-control"] == "no-store"


async def test_sem_credencial_401(ambiente):
    with pytest.raises(HTTPException) as e:
        await r.attachment_meta(FILE, _req({}), tenant_id="t")
    assert e.value.status_code == 401


async def test_token_de_servico_errado_401(ambiente):
    with pytest.raises(HTTPException) as e:
        await r.attachment_meta(FILE, _req({"x-service-token": "outro"}), tenant_id="t")
    assert e.value.status_code == 401


async def test_gravacao_nao_sai_por_aqui(ambiente, monkeypatch):
    monkeypatch.setattr(r._main_module, "_attachment_store", _Store(klass="call_recording"))
    with pytest.raises(HTTPException) as e:
        await r.attachment_meta(FILE, _req({"x-service-token": TOKEN}), tenant_id="t")
    assert e.value.status_code == 404


async def test_expirado_responde_410_no_conteudo_e_diz_na_meta(ambiente, monkeypatch):
    monkeypatch.setattr(r._main_module, "_attachment_store", _Store(deleted=True))
    m = await r.attachment_meta(FILE, _req({"x-service-token": TOKEN}), tenant_id="t")
    assert m["expired"] is True
    with pytest.raises(HTTPException) as e:
        await r.attachment_content(FILE, _req({"x-service-token": TOKEN}), tenant_id="t", variant=None)
    assert e.value.status_code == 410


async def test_id_desconhecido_404(ambiente):
    with pytest.raises(HTTPException) as e:
        await r.attachment_meta("00000000-0000-0000-0000-000000000000",
                                _req({"x-service-token": TOKEN}), tenant_id="t")
    assert e.value.status_code == 404
