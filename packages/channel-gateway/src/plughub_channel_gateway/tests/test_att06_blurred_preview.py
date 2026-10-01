"""
test_att06_blurred_preview.py — ATT-06 (2026-10-01)

PROPOSIÇÃO: a prévia de quem não atende o contato não carrega o DETALHE da imagem — não é só um
filtro por cima (reversível em parte), é a imagem reduzida a poucos pixels. E a variante passa
pelas mesmas recusas da porta (antivírus, expiração): borrar não é atalho para o que não se serve.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image, ImageStat

from plughub_channel_gateway import main as _main  # noqa: F401 — main antes: o router o importa
from plughub_channel_gateway import attachment_internal_router as r
from plughub_channel_gateway.attachment_store import AttachmentMeta
from plughub_channel_gateway.media_sanitize import PREVIEW_SIZE_PX, blurred_preview
from plughub_channel_gateway.tests._media import real_image
from plughub_channel_gateway.tests.test_att02_attachment_internal import _req as _req_att02

TOKEN = "svc-token-att06"
FILE = "11111111-2222-3333-4444-555555555555"


def _xadrez(n=200) -> bytes:
    """Só alta frequência: cada pixel o oposto do vizinho. É o detalhe que a prévia tem de perder."""
    img = Image.new("L", (n, n))
    img.putdata([255 * ((x + y) % 2) for y in range(n) for x in range(n)])
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


class TestBlurredPreview:
    def test_o_detalhe_some(self):
        original = Image.open(io.BytesIO(_xadrez())).convert("L")
        assert ImageStat.Stat(original).stddev[0] > 100, "o controle não tinha detalhe"
        previa = Image.open(io.BytesIO(blurred_preview(_xadrez(), "image/png"))).convert("L")
        assert ImageStat.Stat(previa).stddev[0] < 10

    def test_a_forma_e_a_cor_ficam(self):
        previa = Image.open(io.BytesIO(blurred_preview(real_image("PNG", size=(400, 200), color=(200, 30, 30)),
                                                       "image/png")))
        assert previa.format == "JPEG" and max(previa.size) == PREVIEW_SIZE_PX
        assert previa.size[0] > previa.size[1]
        rr, g, b = ImageStat.Stat(previa.convert("RGB")).mean
        assert rr > 150 and g < 80 and b < 80

    def test_orientacao_do_exif_e_respeitada(self):
        ex = Image.Exif()
        ex[0x0112] = 6
        previa = Image.open(io.BytesIO(blurred_preview(real_image("JPEG", size=(80, 40), exif=ex), "image/jpeg")))
        assert previa.size[1] > previa.size[0]

    def test_nao_imagem_recusa(self):
        with pytest.raises(ValueError):
            blurred_preview(b"%PDF-1.4\n", "application/pdf")


# ── a variante na rota interna ────────────────────────────────────────────────

class _Store:
    def __init__(self, mime="image/png", scan="clean", dados=None):
        self.dados = dados if dados is not None else _xadrez(60)
        self.meta = AttachmentMeta(
            file_id=FILE, tenant_id="t", session_id="s", original_name="f", mime_type=mime,
            size_bytes=len(self.dados), file_path="x", serving_url="",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1), deleted_at=None,
            artifact_class="webchat_attachment", attrs={}, scan_status=scan, sha256=None)

    async def resolve(self, *, file_id, tenant_id):
        return self.meta

    async def stream_bytes(self, *, file_id, tenant_id):
        async def _g():
            yield self.dados
        return _g()


def _req():
    return _req_att02({"x-service-token": TOKEN})


@pytest.fixture
def ambiente(monkeypatch):
    def _usa(store):
        monkeypatch.setattr(r._main_module, "_attachment_store", store, raising=False)
    monkeypatch.setattr(r._main_module, "get_settings",
                        lambda: SimpleNamespace(channel_gateway_service_token=TOKEN, auth_jwt_secret="x" * 32))
    return _usa


async def _conteudo(variant):
    return await r.attachment_content(FILE, _req(), tenant_id="t", variant=variant)


async def test_variante_borrada_sai_em_jpeg_sem_o_detalhe(ambiente):
    ambiente(_Store())
    resp = await _conteudo("blurred")
    assert resp.media_type == "image/jpeg" and resp.headers["x-content-type-options"] == "nosniff"
    assert ImageStat.Stat(Image.open(io.BytesIO(resp.body)).convert("L")).stddev[0] < 10


async def test_sem_variante_continua_o_original_controle(ambiente):
    ambiente(_Store())
    resp = await _conteudo(None)
    corpo = b"".join([c async for c in resp.body_iterator])
    assert corpo == _Store().dados


async def test_nao_imagem_nao_tem_previa(ambiente):
    ambiente(_Store(mime="application/pdf", dados=b"%PDF-1.4\n"))
    with pytest.raises(HTTPException) as e:
        await _conteudo("blurred")
    assert e.value.status_code == 415


async def test_quarentena_vale_tambem_para_a_previa(ambiente):
    ambiente(_Store(scan="quarantined"))
    with pytest.raises(HTTPException) as e:
        await _conteudo("blurred")
    assert e.value.status_code == 423


async def test_variante_desconhecida_400(ambiente):
    ambiente(_Store())
    with pytest.raises(HTTPException) as e:
        await _conteudo("thumbnail")
    assert e.value.status_code == 400
