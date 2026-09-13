"""
test_attachment_writers_contract.py — VOZ-06 (2026-09-13)

Os escritores do AttachmentStore chamam o store DENTRO do contrato?

⚠️ O DEFEITO QUE O ORIGINOU. Tres dos cinco escritores estavam quebrados ao mesmo
tempo, e nenhum teste ficava vermelho:
  * `voice.py` chamava `self._store.store(...)` — metodo que o Protocol nunca teve;
  * `whatsapp.py` e `email.py` chamavam `commit(file_id=, data=, mime_type=)` —
    `mime_type` nao existe na assinatura e `tenant_id` e obrigatorio.
Os tres caiam num `except Exception` e viravam uma linha de log. O teste de midia
do WhatsApp afirmava que o EVENTO saia — e saia, com `file_id: None`, porque o
adapter era construido SEM store e o caminho de armazenamento nunca era exercido.

POR QUE O FAKE VALIDA PELO PROTOCOL, E NAO POR UMA ASSINATURA COPIADA
Um fake com a assinatura escrita a mao (como o `MockAttachmentStore` do egress)
concorda com o produto enquanto ninguem mexe no Protocol. Este liga cada chamada
em `inspect.signature` do PROPRIO `AttachmentStore`: se o contrato mudar, o fake
muda junto, e o escritor que ficou para tras reprova aqui.

Censo irmao, sobre a populacao inteira: `infra/test/probe_adapter_self_calls.sh`
ramos C/D.
"""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plughub_channel_gateway.adapters.email import EmailAdapter
from plughub_channel_gateway.adapters.email_provider import (
    EmailAttachment,
    MockEmailProvider,
)
from plughub_channel_gateway.adapters.whatsapp import WhatsAppAdapter
from plughub_channel_gateway.adapters.whatsapp_provider import MockWhatsAppProvider
from plughub_channel_gateway.attachment_store import AttachmentMeta, AttachmentStore
from plughub_channel_gateway.config import Settings
from plughub_channel_gateway.tests.test_voice_adapter import _fake_settings, _make_adapter

TENANT_ID = "tenant_test"
SESSION_ID = "sid-voz06-001"
SERVING = "http://localhost:8010/webchat/v1/attachments"


class ProtocolBoundStore:
    """Store falso cujas chamadas sao LIGADAS a assinatura do Protocol.

    Metodo inexistente -> AttributeError (o `__getattr__` nao inventa nada);
    kwarg inexistente ou obrigatorio ausente -> TypeError, vindo do `bind`.
    """

    def __init__(self) -> None:
        self.chamadas: list[tuple[str, dict]] = []
        self._slots: dict[str, dict] = {}

    def _liga(self, metodo: str, kwargs: dict) -> None:
        sig = inspect.signature(getattr(AttachmentStore, metodo))
        sig.bind(self, **kwargs)  # levanta TypeError fora do contrato
        self.chamadas.append((metodo, kwargs))

    async def reserve(self, **kwargs):
        self._liga("reserve", kwargs)
        file_id = str(uuid.uuid4())
        self._slots[file_id] = kwargs
        return file_id, f"http://upload/{file_id}"

    async def commit(self, **kwargs):
        self._liga("commit", kwargs)
        slot = self._slots[kwargs["file_id"]]
        assert slot["tenant_id"] == kwargs["tenant_id"], "commit em tenant diferente do reserve"
        return AttachmentMeta(
            file_id=kwargs["file_id"],
            tenant_id=kwargs["tenant_id"],
            session_id=slot["session_id"],
            original_name=slot["file_name"],
            mime_type=slot["mime_type"],
            size_bytes=len(kwargs["data"]),
            file_path="x",
            serving_url=f"{SERVING}/{kwargs['file_id']}",
            expires_at=slot["expires_at"],
            deleted_at=None,
        )

    def metodos(self) -> list[str]:
        return [m for m, _ in self.chamadas]


# ── A testemunha: o fake SABE reprovar ────────────────────────────────────────

class TestFakeReprova:
    """Sem isto, um fake permissivo deixaria os testes abaixo verdes por ausencia."""

    async def test_commit_com_kwarg_inexistente_reprova(self):
        store = ProtocolBoundStore()
        with pytest.raises(TypeError):
            await store.commit(file_id="f", data=b"x", mime_type="audio/mpeg")

    async def test_commit_sem_tenant_reprova(self):
        store = ProtocolBoundStore()
        with pytest.raises(TypeError):
            await store.commit(file_id="f", data=b"x")

    async def test_metodo_store_nao_existe(self):
        store = ProtocolBoundStore()
        with pytest.raises(AttributeError):
            await store.store(session_id="s", file_bytes=b"x")  # type: ignore[attr-defined]


# ── WhatsApp ──────────────────────────────────────────────────────────────────

def _settings(**extra) -> Settings:
    base = dict(
        kafka_brokers="localhost:9092",
        kafka_group_id="test-group",
        kafka_topic_inbound="conversations.inbound",
        kafka_topic_outbound="conversations.outbound",
        kafka_topic_events="conversations.events",
        redis_url="redis://localhost:6379",
        tenant_id=TENANT_ID,
        storage_root="/tmp/plughub_test",
        attachment_expiry_days=30,
        database_url="postgresql://plughub:plughub@localhost/plughub",
        webchat_serving_base_url=SERVING,
        webchat_upload_base_url="http://localhost:8010/webchat/v1/upload",
    )
    base.update(extra)
    return Settings(**base)


class TestWhatsAppArmazenaMidia:
    async def test_midia_e_armazenada_e_o_evento_leva_o_file_id(self):
        provider = MockWhatsAppProvider()
        provider.load_media("media_001", b"\xff\xd8\xff\xe0", "image/jpeg", "https://cdn.meta/m1")
        producer = AsyncMock()
        store = ProtocolBoundStore()
        adapter = WhatsAppAdapter(
            producer=producer,
            redis=AsyncMock(),
            settings=_settings(whatsapp_app_secret="s" * 28),
            provider=provider,
            attachment_store=store,
        )

        await adapter._handle_media(
            contact_id="+5511999990001",
            session_id=SESSION_ID,
            tenant_id=TENANT_ID,
            msg_type="image",
            media_id="media_001",
            caption=None,
            wamid="wamid.x",
        )

        assert store.metodos() == ["reserve", "commit"]
        commit = store.chamadas[1][1]
        assert commit["tenant_id"] == TENANT_ID
        assert commit["data"] == b"\xff\xd8\xff\xe0"

        eventos = [json.loads(c.kwargs["value"]) for c in producer.send.call_args_list]
        midia = [e for e in eventos if e.get("content", {}).get("type") == "media"]
        assert midia, "o evento de midia nao saiu"
        # ⚠️ A asserção que faltava: antes do conserto o evento saía IGUAL, com None.
        assert midia[0]["content"]["payload"]["file_id"] == store.chamadas[1][1]["file_id"]


# ── E-mail ────────────────────────────────────────────────────────────────────

class TestEmailArmazenaAnexo:
    async def test_anexo_e_armazenado_e_devolve_ref(self):
        store = ProtocolBoundStore()
        adapter = EmailAdapter(
            producer=AsyncMock(),
            redis=AsyncMock(),
            settings=_settings(
                email_api_key="k", email_domain="empresa.com", email_signing_key="s",
                email_from_address="suporte@empresa.com", email_reply_domain="mail.empresa.com",
                email_default_pool_id="email_test_pool",
            ),
            provider=MockEmailProvider(),
            attachment_store=store,
        )

        refs = await adapter._store_attachments(
            SESSION_ID, TENANT_ID,
            [EmailAttachment(filename="nota.pdf", mime_type="application/pdf", data=b"%PDF-1.4")],
        )

        assert store.metodos() == ["reserve", "commit"]
        assert store.chamadas[1][1]["tenant_id"] == TENANT_ID
        assert len(refs) == 1, "o anexo foi descartado — o commit falhou dentro do except"
        assert refs[0]["file_id"] == store.chamadas[1][1]["file_id"]


# ── Voz ───────────────────────────────────────────────────────────────────────

class TestVozArmazenaGravacao:
    async def test_gravacao_usa_reserve_commit_e_publica_a_url_do_store(self):
        settings = _fake_settings()
        settings.attachment_expiry_days = 30
        adapter = _make_adapter(settings=settings)
        store = ProtocolBoundStore()
        adapter._store = store

        resp = MagicMock()
        resp.content = b"ID3\x03fake-mp3"
        resp.raise_for_status = MagicMock()
        cliente = AsyncMock()
        cliente.get = AsyncMock(return_value=resp)
        ctx = AsyncMock()
        ctx.__aenter__.return_value = cliente
        ctx.__aexit__.return_value = False

        antes = datetime.now(timezone.utc)
        with patch("httpx.AsyncClient", return_value=ctx):
            await adapter._download_and_store_recording(
                session_id=SESSION_ID,
                segment_id="seg-1",
                recording_url="https://api.twilio.com/rec/RE1",
                recording_sid="RE1",
                duration_s=12,
            )

        assert store.metodos() == ["reserve", "commit"], (
            "a gravacao nao chegou ao store — o erro foi engolido pelo except do escritor"
        )
        reserve = store.chamadas[0][1]
        assert reserve["mime_type"] == "audio/mpeg"
        assert reserve["tenant_id"] == settings.tenant_id
        assert reserve["size_bytes"] == len(resp.content)
        # a expiracao vem da politica resolvida (default 30 aqui), nunca de constante
        dias = (reserve["expires_at"] - antes).days
        assert 29 <= dias <= 30

        publicado = adapter._normalize_text.call_args.kwargs["text"]
        evento = json.loads(publicado)
        assert evento["type"] == "recording.completed"
        assert evento["url"] == f"{SERVING}/{store.chamadas[1][1]['file_id']}"
