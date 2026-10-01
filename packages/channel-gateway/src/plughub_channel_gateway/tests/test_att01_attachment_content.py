"""
test_att01_attachment_content.py — ATT-01 (2026-10-01)

PROPOSIÇÃO: nenhum escritor grava, e a porta pública não serve como página, um arquivo cujo
tipo o REMETENTE escolheu. Medido antes do conserto:
  * WhatsApp e e-mail gravavam sem `validate_mime` (MIME e tamanho do remetente);
  * `validate_magic_bytes` aceitava tipo sem assinatura (fail-open);
  * a porta pública servia `inline` com o MIME gravado, nome cru entre aspas, sem `nosniff`
    nem CSP — um `text/html` vindo pelo WhatsApp saía renderizável na origem do gateway.

Cada recusa tem o controle positivo ao lado: um portão que recusa TUDO passaria nos negativos.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from plughub_channel_gateway.adapters.email import EmailAdapter
from plughub_channel_gateway.adapters.email_provider import EmailAttachment, MockEmailProvider
from plughub_channel_gateway.adapters.whatsapp import WhatsAppAdapter
from plughub_channel_gateway.adapters.whatsapp_provider import MockWhatsAppProvider
from plughub_channel_gateway.attachment_store import (
    CLASS_MIME_LIMITS,
    normalize_mime,
    validate_content,
    validate_magic_bytes,
)
from plughub_channel_gateway.tests.test_attachment_store import make_store
from plughub_channel_gateway.tests.test_attachment_writers_contract import (
    SESSION_ID,
    TENANT_ID,
    ProtocolBoundStore,
    _settings,
)
from plughub_channel_gateway.attachment_store import content_disposition, served_media_type

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PDF  = b"%PDF-1.4\n"
OGG  = b"OggS" + b"\x00" * 16
HTML = b"<html><script>alert(1)</script></html>"
EXPIRES = datetime.now(timezone.utc) + timedelta(days=30)


# ── A regra única ─────────────────────────────────────────────────────────────

class TestValidateContent:
    def test_imagem_valida_passa(self):
        assert validate_content("webchat_attachment", "image/jpeg", JPEG) is None

    def test_html_e_recusado(self):
        err = validate_content("webchat_attachment", "text/html", HTML)
        assert err and "text/html" in err

    def test_tipo_permitido_com_conteudo_de_outro_e_recusado(self):
        assert validate_content("webchat_attachment", "image/jpeg", HTML) is not None

    def test_nota_de_voz_do_whatsapp_passa(self):
        assert validate_content("webchat_attachment", "audio/ogg", OGG) is None

    def test_audio_fora_da_allowlist_e_recusado(self):
        assert validate_content("webchat_attachment", "audio/mpeg", b"ID3" + b"\x00" * 16) is not None

    def test_gravacao_aceita_so_ogg(self):
        assert validate_content("call_recording", "audio/ogg", OGG) is None
        assert validate_content("call_recording", "image/jpeg", JPEG) is not None

    def test_classe_desconhecida_e_recusada(self):
        err = validate_content("classe_que_nao_existe", "image/jpeg", JPEG)
        assert err and "classe" in err

    def test_classe_ausente_e_a_de_contato(self):
        assert validate_content(None, "image/jpeg", JPEG) is None

    def test_vazio_e_recusado(self):
        assert validate_content("webchat_attachment", "image/jpeg", b"") is not None

    def test_tamanho_REAL_acima_do_teto_e_recusado(self):
        teto = CLASS_MIME_LIMITS["webchat_attachment"]["image/gif"]
        no_teto  = b"GIF89a" + b"\x00" * (teto - 6)
        acima    = no_teto + b"\x00"
        assert validate_content("webchat_attachment", "image/gif", no_teto) is None
        assert validate_content("webchat_attachment", "image/gif", acima) is not None


class TestMagicFailClosed:
    def test_tipo_sem_assinatura_e_recusado(self):
        assert validate_magic_bytes(b"anything", "application/octet-stream") is not None

    def test_tipo_com_assinatura_que_bate_passa(self):
        assert validate_magic_bytes(PDF, "application/pdf") is None


class TestNormalizeMime:
    def test_parametros_e_caixa(self):
        assert normalize_mime("Audio/OGG; codecs=opus") == "audio/ogg"

    def test_ausente(self):
        assert normalize_mime(None) == ""


# ── O commit, nos dois backends, passa pela regra ─────────────────────────────

class TestCommitAplicaARegra:
    async def test_commit_recusa_html_declarado(self, tmp_path):
        row = {"session_id": "s", "original_name": "x.html", "mime_type": "text/html",
               "expires_at": EXPIRES, "artifact_class": "webchat_attachment"}
        store, _ = make_store(tmp_path, fetchrow=row)
        with pytest.raises(ValueError, match="text/html"):
            await store.commit(file_id=str(uuid.uuid4()), tenant_id="t", data=HTML)
        assert not any(tmp_path.rglob("*.*")), "o arquivo recusado foi escrito em disco"

    async def test_commit_aceita_imagem(self, tmp_path):
        row = {"session_id": "s", "original_name": "x.jpg", "mime_type": "image/jpeg",
               "expires_at": EXPIRES, "artifact_class": "webchat_attachment"}
        store, _ = make_store(tmp_path, fetchrow=row)
        meta = await store.commit(file_id=str(uuid.uuid4()), tenant_id="t", data=JPEG)
        assert meta.size_bytes == len(JPEG)

    async def test_commit_de_gravacao_recusa_tipo_de_contato(self, tmp_path):
        row = {"session_id": "s", "original_name": "r.ogg", "mime_type": "image/jpeg",
               "expires_at": EXPIRES, "artifact_class": "call_recording"}
        store, _ = make_store(tmp_path, fetchrow=row)
        with pytest.raises(ValueError, match="call_recording"):
            await store.commit(file_id=str(uuid.uuid4()), tenant_id="t", data=JPEG)


# ── A porta pública ───────────────────────────────────────────────────────────

class TestPortaPublica:
    def test_imagem_e_inline(self):
        assert content_disposition("foto.jpg", "image/jpeg").startswith("inline;")

    def test_pdf_e_download(self):
        assert content_disposition("nota.pdf", "application/pdf").startswith("attachment;")

    def test_video_e_download(self):
        assert content_disposition("v.mp4", "video/mp4").startswith("attachment;")

    def test_tipo_fora_da_allowlist_sai_opaco(self):
        assert served_media_type("text/html") == "application/octet-stream"
        assert served_media_type("image/png") == "image/png"

    def test_nome_hostil_nao_reescreve_o_cabecalho(self):
        hostil = 'a".html; filename=evil.html\r\nX-Injected: 1'
        valor = content_disposition(hostil, "application/pdf")
        assert "\r" not in valor and "\n" not in valor
        # Exatamente as duas aspas que delimitam o nome ASCII
        assert valor.count('"') == 2
        assert "filename*=UTF-8''" in valor
        assert valor.count(";") == 2, valor

    def test_nome_unicode_preservado_em_filename_estrela(self):
        valor = content_disposition("relatório.pdf", "application/pdf")
        assert "filename*=UTF-8''relat%C3%B3rio.pdf" in valor

    def test_sem_nome(self):
        assert content_disposition(None, "image/png") == 'inline; filename="attachment"'


# ── Os escritores de canal conferem antes do reserve ──────────────────────────

class TestWhatsAppRecusa:
    async def _rodar(self, data: bytes, mime: str):
        provider = MockWhatsAppProvider()
        provider.load_media("m1", data, mime, "https://cdn.meta/m1")
        producer = AsyncMock()
        store = ProtocolBoundStore()
        adapter = WhatsAppAdapter(
            producer=producer, redis=AsyncMock(),
            settings=_settings(whatsapp_app_secret="s" * 28),
            provider=provider, attachment_store=store,
        )
        await adapter._handle_media(
            contact_id="+5511999990001", session_id=SESSION_ID, tenant_id=TENANT_ID,
            msg_type="document", media_id="m1", caption=None, wamid="wamid.x",
        )
        eventos = [json.loads(c.kwargs["value"]) for c in producer.send.call_args_list]
        midia = [e for e in eventos if e.get("content", {}).get("type") == "media"]
        return store, midia

    async def test_html_nao_e_gravado_e_a_mensagem_segue(self):
        store, midia = await self._rodar(HTML, "text/html")
        assert store.metodos() == [], "o HTML chegou ao store"
        assert midia and midia[0]["content"]["payload"]["file_id"] is None

    async def test_pdf_e_gravado(self):
        store, midia = await self._rodar(PDF, "application/pdf")
        assert store.metodos() == ["reserve", "commit"]
        assert midia[0]["content"]["payload"]["file_id"] is not None

    async def test_nota_de_voz_com_parametro_e_gravada_normalizada(self):
        store, _ = await self._rodar(OGG, "audio/ogg; codecs=opus")
        assert store.metodos() == ["reserve", "commit"]
        assert store.chamadas[0][1]["mime_type"] == "audio/ogg"


class TestEmailRecusa:
    def _adapter(self, store):
        return EmailAdapter(
            producer=AsyncMock(), redis=AsyncMock(),
            settings=_settings(
                email_api_key="k", email_domain="empresa.com", email_signing_key="s",
                email_from_address="suporte@empresa.com", email_reply_domain="mail.empresa.com",
                email_default_pool_id="email_test_pool",
            ),
            provider=MockEmailProvider(), attachment_store=store,
        )

    async def test_html_e_descartado_e_o_pdf_vizinho_e_gravado(self):
        store = ProtocolBoundStore()
        refs = await self._adapter(store)._store_attachments(SESSION_ID, TENANT_ID, [
            EmailAttachment(filename="fatura.html", mime_type="text/html", data=HTML),
            EmailAttachment(filename="nota.pdf", mime_type="application/pdf", data=PDF),
        ])
        assert [r["filename"] for r in refs] == ["nota.pdf"]
        assert store.metodos() == ["reserve", "commit"]

    async def test_tamanho_vem_dos_bytes_nao_do_declarado(self):
        store = ProtocolBoundStore()
        att = EmailAttachment(filename="nota.pdf", mime_type="application/pdf", data=PDF)
        att.size_bytes = 999_999_999
        refs = await self._adapter(store)._store_attachments(SESSION_ID, TENANT_ID, [att])
        assert refs[0]["size_bytes"] == len(PDF)
        assert store.chamadas[0][1]["size_bytes"] == len(PDF)
