"""test_att03_signed_url.py — ATT-03 (2026-10-01): a porta PÚBLICA aceita só URL assinada.

PROPOSIÇÃO: o `file_id` nu deixa de abrir o arquivo; a URL que o cliente recebe vale para ESTE
anexo DESTA sessão e por pouco tempo, e é cunhada na entrega — nunca copiada da gravada.
Cada recusa tem o controle positivo ao lado.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from plughub_channel_gateway import main as _main  # noqa: F401 — o main monta os routers; importá-lo depois faz ciclo
from plughub_channel_gateway import upload_router as up
from plughub_channel_gateway.attachment_store import (
    AttachmentMeta,
    sign_attachment_url,
    verify_attachment_signature,
)
from plughub_channel_gateway.stream_subscriber import StreamSubscriber

SECRET = "s" * 32
FILE = "11111111-2222-3333-4444-555555555555"
SID = "sess-att03"
BASE = "http://gw/webchat/v1/attachments"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def _qs(url: str) -> tuple[str, str]:
    q = parse_qs(urlparse(url).query)
    return q["exp"][0], q["sig"][0]


# ── assinatura ────────────────────────────────────────────────────────────────

class TestAssinatura:
    def test_ida_e_volta_vale(self):
        exp, sig = _qs(sign_attachment_url(BASE, FILE, SID, secret=SECRET))
        assert verify_attachment_signature(FILE, SID, exp, sig, secret=SECRET) is None

    def test_a_url_nao_mostra_a_sessao(self):
        assert SID not in sign_attachment_url(BASE, FILE, SID, secret=SECRET)

    def test_link_de_outra_sessao_nao_abre(self):
        exp, sig = _qs(sign_attachment_url(BASE, FILE, "outra-sessao", secret=SECRET))
        assert verify_attachment_signature(FILE, SID, exp, sig, secret=SECRET) == "bad"

    def test_link_de_outro_arquivo_nao_abre(self):
        exp, sig = _qs(sign_attachment_url(BASE, "outro-arquivo", SID, secret=SECRET))
        assert verify_attachment_signature(FILE, SID, exp, sig, secret=SECRET) == "bad"

    def test_esticar_o_prazo_quebra_a_assinatura(self):
        exp, sig = _qs(sign_attachment_url(BASE, FILE, SID, secret=SECRET))
        assert verify_attachment_signature(FILE, SID, str(int(exp) + 86400), sig, secret=SECRET) == "bad"

    def test_vencido(self):
        exp, sig = _qs(sign_attachment_url(BASE, FILE, SID, secret=SECRET, now=1_000))
        assert verify_attachment_signature(FILE, SID, exp, sig, secret=SECRET, now=10_000) == "expired"

    def test_sem_assinatura(self):
        assert verify_attachment_signature(FILE, SID, None, None, secret=SECRET) == "missing"

    def test_sem_segredo_nao_ha_url_nem_aceite(self):
        assert sign_attachment_url(BASE, FILE, SID, secret="") == ""
        assert verify_attachment_signature(FILE, SID, "1", "x", secret="") == "no_secret"


# ── a porta ───────────────────────────────────────────────────────────────────

class _Store:
    async def resolve(self, *, file_id, tenant_id):
        if file_id != FILE:
            return None
        return AttachmentMeta(
            file_id=FILE, tenant_id="t", session_id=SID, original_name="f.jpg",
            mime_type="image/jpeg", size_bytes=len(JPEG), file_path="x", serving_url="",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1), deleted_at=None,
            artifact_class="webchat_attachment", attrs={}, scan_status="clean", sha256=None)  # ATT-05

    async def stream_bytes(self, *, file_id, tenant_id):
        async def _g():
            yield JPEG
        return _g()


@pytest.fixture
def porta(monkeypatch):
    s = SimpleNamespace(tenant_id="t", jwt_secret=SECRET, webchat_serving_base_url=BASE)
    monkeypatch.setattr(up._main_module, "get_settings", lambda: s)
    monkeypatch.setattr(up._main_module, "_attachment_store", _Store(), raising=False)


async def test_url_assinada_abre_controle_positivo(porta):
    exp, sig = _qs(up.public_attachment_url(FILE, SID))
    resp = await up.serve_attachment(FILE, exp=exp, sig=sig)
    assert resp.status_code == 200


async def test_file_id_nu_responde_como_desconhecido(porta):
    with pytest.raises(HTTPException) as e:
        await up.serve_attachment(FILE, exp=None, sig=None)
    assert e.value.status_code == 404


async def test_link_de_outra_sessao_404(porta):
    exp, sig = _qs(sign_attachment_url(BASE, FILE, "outra", secret=SECRET))
    with pytest.raises(HTTPException) as e:
        await up.serve_attachment(FILE, exp=exp, sig=sig)
    assert e.value.status_code == 404


async def test_link_vencido_403_dito(porta):
    exp, sig = _qs(sign_attachment_url(BASE, FILE, SID, secret=SECRET, now=1_000))
    with pytest.raises(HTTPException) as e:
        await up.serve_attachment(FILE, exp=exp, sig=sig)
    assert e.value.status_code == 403 and e.value.detail == "link_expired"


# ── a entrega cunha, nunca copia ──────────────────────────────────────────────

class TestEntrega:
    def _sub(self, signer):
        return StreamSubscriber(redis=None, session_id=SID, url_signer=signer)  # type: ignore[arg-type]

    def test_com_assinador_a_url_e_recunhada_pelo_file_id(self):
        sub = self._sub(lambda fid: f"assinada:{fid}")
        assert sub._attachment_url({"file_id": FILE, "url": "http://velha/nua"}) == f"assinada:{FILE}"

    def test_sem_file_id_mantem_a_url_do_conteudo(self):
        sub = self._sub(lambda fid: f"assinada:{fid}")
        assert sub._attachment_url({"url": "https://externa/x.png"}) == "https://externa/x.png"

    def test_sem_assinador_comportamento_antigo(self):
        assert self._sub(None)._attachment_url({"file_id": FILE, "url": "u"}) == "u"
