"""
test_att07_upload_limit.py — ATT-07 (2026-10-01)

PROPOSIÇÃO: `webchat.upload_limits_mb`, que a tela de WebChat edita, passa a valer no upload do
webchat — no tamanho DECLARADO (reserve) e no REAL (POST) — e só ABAIXA o teto da plataforma.
Antes nenhum código o lia: mudar na tela não mudava nada.

Cada recusa tem o controle ao lado (dentro do teto passa), e o valor acima do teto da plataforma
prova que o tenant não sobe o que o antivírus e a allowlist dimensionaram.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from plughub_channel_gateway import main as _main  # noqa: F401 — main antes: o router o importa
from plughub_channel_gateway import attachment_store as st
from plughub_channel_gateway import upload_router as up
from plughub_channel_gateway.attachment_store import (
    MIME_LIMITS,
    AttachmentMeta,
    FilesystemAttachmentStore,
    webchat_upload_limit,
)

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _esquece_avisos():
    st._avisados.clear()


class TestWebchatUploadLimit:
    def test_sem_config_vale_o_da_plataforma(self):
        assert webchat_upload_limit("image/png", None) == MIME_LIMITS["image/png"]
        assert webchat_upload_limit("image/png", {}) == MIME_LIMITS["image/png"]

    def test_o_tenant_abaixa(self):
        assert webchat_upload_limit("image/jpeg", {"image": 2}) == 2 * MB
        assert webchat_upload_limit("application/pdf", {"pdf": 10}) == 10 * MB
        assert webchat_upload_limit("video/webm", {"video": 50}) == 50 * MB

    def test_o_tipo_certo_le_a_chave_certa(self):
        cfg = {"image": 1, "pdf": 2, "video": 3}
        assert webchat_upload_limit("image/gif", cfg) == 1 * MB
        assert webchat_upload_limit("application/pdf", cfg) == 2 * MB
        assert webchat_upload_limit("video/mp4", cfg) == 3 * MB

    def test_acima_do_teto_da_plataforma_e_cortado_e_dito(self, caplog):
        with caplog.at_level(logging.WARNING, logger="plughub.channel-gateway.attachment"):
            assert webchat_upload_limit("image/png", {"image": 2048}) == MIME_LIMITS["image/png"]
            webchat_upload_limit("image/png", {"image": 2048})
        avisos = [r for r in caplog.records if "ATT-07" in r.getMessage()]
        assert len(avisos) == 1, "o aviso é uma vez por valor, não por upload"

    @pytest.mark.parametrize("valor", [0, -5, "16", True, None])
    def test_invalido_vale_o_da_plataforma(self, valor):
        assert webchat_upload_limit("image/png", {"image": valor}) == MIME_LIMITS["image/png"]

    def test_fora_da_allowlist_nao_tem_teto(self):
        assert webchat_upload_limit("text/html", {"image": 1}) is None


class TestValidateMime:
    def test_declarado_acima_do_teto_do_tenant_recusa(self):
        assert "muito grande" in FilesystemAttachmentStore.validate_mime("image/png", 3 * MB, limit=2 * MB)

    def test_controle_dentro_do_teto_passa(self):
        assert FilesystemAttachmentStore.validate_mime("image/png", 1 * MB, limit=2 * MB) is None

    def test_limite_acima_da_plataforma_nao_sobe_o_teto(self):
        acima = MIME_LIMITS["image/png"] + 1
        assert FilesystemAttachmentStore.validate_mime("image/png", acima, limit=10 * MIME_LIMITS["image/png"])


# ── o POST: tamanho REAL ──────────────────────────────────────────────────────

def _post(corpo: bytes) -> Request:
    async def receive():
        return {"type": "http.request", "body": corpo, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/webchat/v1/upload/f",
                    "headers": [], "query_string": b""}, receive)


@pytest.fixture
def porta(monkeypatch):
    store = MagicMock()
    store.resolve = AsyncMock(return_value=AttachmentMeta(
        file_id="f", tenant_id="t", session_id="s", original_name="x.png", mime_type="image/png",
        size_bytes=10, file_path=None, serving_url="", expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        deleted_at=None, artifact_class="webchat_attachment", attrs={}, scan_status=None, sha256=None))
    store.commit = AsyncMock(side_effect=RuntimeError("commit chamado"))
    monkeypatch.setattr(up._main_module, "_attachment_store", store, raising=False)
    monkeypatch.setattr(up._main_module, "_registry", None, raising=False)
    monkeypatch.setattr(up._main_module, "get_settings", lambda: SimpleNamespace(tenant_id="t"))
    monkeypatch.setattr(up.webchat_config, "get",
                        lambda k, d=None: {"image": 1} if k == "upload_limits_mb" else d)
    return store


async def test_real_acima_do_teto_do_tenant_413_sem_gravar(porta):
    with pytest.raises(HTTPException) as e:
        await up.upload_file("f", _post(b"x" * (1 * MB + 1)))
    assert e.value.status_code == 413
    porta.commit.assert_not_called()


async def test_controle_real_dentro_do_teto_chega_ao_commit(porta):
    # o commit de mentira levanta: chegar até ele é o que este controle prova
    with pytest.raises(HTTPException) as e:
        await up.upload_file("f", _post(b"x" * 100))
    assert e.value.status_code == 500
    porta.commit.assert_awaited_once()
