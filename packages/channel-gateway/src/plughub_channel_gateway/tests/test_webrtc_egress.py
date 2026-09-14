"""
tests/test_webrtc_egress.py
Arc 15 Phase D — WebRTC Egress Recording tests.

Covers:
  TestEgressStart            — _start_egress: notice delivery, Redis guard, start_egress call
  TestEgressDoubleStartGuard — double-start is a no-op (Redis key exists)
  TestEgressStopAndStore     — _stop_egress_and_store: stop, wait, read, commit, stream event
  TestEgressStopNoFile       — file missing after wait → graceful skip (no crash)
  TestEgressStopAllIdempotent— _stop_all_egress is idempotent (second call is no-op)
  TestEgressRoutingAssigned  — _on_routing_assigned NAO dispara egress (gatilho sem produtor, VOZ-10)
  TestEgressProviderImpl     — sem credencial nao ha egress placebo (VOZ-01) + MockProvider
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from plughub_channel_gateway.adapters.webrtc import WebRTCAdapter, _SESSION_TTL
from plughub_channel_gateway.adapters.webrtc_provider import (
    MockWebRTCProvider,
    LiveKitProvider,
    WebRTCProviderUnavailable,
)
from plughub_channel_gateway.config import Settings

from .conftest import (
    CONTACT_ID,
    SESSION_ID,
    TENANT_ID,
    mock_redis,
    mock_producer,
)

# ── Constants ─────────────────────────────────────────────────────────────────

SEGMENT_ID = "seg-test-001"
ROOM_NAME  = f"plughub-{SESSION_ID}"
EGRESS_ID  = "EG_mock_0001"

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_settings(**overrides) -> Settings:
    base = dict(
        kafka_brokers             = "localhost:9092",
        kafka_group_id            = "test-group",
        kafka_topic_inbound       = "conversations.inbound",
        kafka_topic_outbound      = "conversations.outbound",
        kafka_topic_events        = "conversations.events",
        redis_url                 = "redis://localhost:6379/0",
        ws_connection_timeout_s   = 30,
        ws_heartbeat_interval_s   = 10,
        ws_contact_max_duration_s = 3600,
        session_ttl_seconds       = 3600,
        jwt_secret                = "test_secret_32chars_webchat_ok!!",
        ws_auth_timeout_s         = 10,
        storage_root              = "/tmp/plughub_test",
        attachment_expiry_days    = 1,
        database_url              = "postgresql://plughub:plughub@localhost:5432/plughub",
        webchat_serving_base_url  = "http://localhost:8010/webchat/v1/attachments",
        webchat_upload_base_url   = "http://localhost:8010/webchat/v1/upload",
        tenant_id                 = TENANT_ID,
        webrtc_stt_enabled        = False,
        webrtc_tts_injection_enabled = False,
        webrtc_recording_notice   = "Gravação ativa para qualidade.",
        webrtc_egress_output_dir  = "/tmp/plughub_test_egress",
        webrtc_egress_wait_s      = 0.0,  # no wait in tests
    )
    base.update(overrides)
    return Settings(**base)


def _make_adapter(
    redis=None,
    producer=None,
    settings=None,
    attachment_store=None,
    webrtc_provider=None,
):
    """Return a (WebRTCAdapter, mock_redis, mock_producer) triple."""
    if redis is None:
        redis = AsyncMock()
        redis.setex   = AsyncMock(return_value=True)
        redis.get     = AsyncMock(return_value=None)
        redis.delete  = AsyncMock(return_value=1)
        redis.publish = AsyncMock(return_value=1)
        redis.exists  = AsyncMock(return_value=0)
        redis.set     = AsyncMock(return_value=True)
        redis.xadd    = AsyncMock(return_value=b"1-0")
        redis.lpush   = AsyncMock(return_value=1)
        redis.expire  = AsyncMock(return_value=1)

    if producer is None:
        producer = AsyncMock()
        producer.send  = AsyncMock()
        producer.start = AsyncMock()
        producer.stop  = AsyncMock()

    if settings is None:
        settings = _make_settings()

    if webrtc_provider is None:
        webrtc_provider = MockWebRTCProvider()

    adapter = WebRTCAdapter(
        producer         = producer,
        redis            = redis,
        settings         = settings,
        webrtc_provider  = webrtc_provider,
        attachment_store = attachment_store,
    )
    return adapter, redis, producer


# ── MockAttachmentStore ───────────────────────────────────────────────────────


class MockAttachmentMeta:
    def __init__(self, file_id: str, serving_url: str):
        self.file_id     = file_id
        self.serving_url = serving_url
        self.size_bytes  = 0


class MockAttachmentStore:
    def __init__(self, serving_url: str = "http://test/recording.mp4"):
        self._serving_url = serving_url
        self.reserved:  list[dict] = []
        self.committed: list[dict] = []

    async def reserve(self, *, tenant_id, session_id, file_name, mime_type,
                       size_bytes, expires_at) -> tuple[str, str]:
        file_id = str(uuid.uuid4())
        self.reserved.append({
            "file_id": file_id, "file_name": file_name, "mime_type": mime_type,
        })
        return file_id, f"http://upload/{file_id}"

    async def commit(self, *, file_id, tenant_id, data) -> MockAttachmentMeta:
        self.committed.append({"file_id": file_id, "size": len(data)})
        return MockAttachmentMeta(file_id=file_id, serving_url=self._serving_url)


# ── TestEgressStart ───────────────────────────────────────────────────────────


class TestEgressStart:
    """_start_egress: successful path — notice sent, egress started, Redis updated."""

    @pytest.mark.asyncio
    async def test_notice_sent_as_text_when_tts_disabled(self):
        adapter, redis, _ = _make_adapter()

        # Pre-register a mock WS connection
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        adapter._connections[SESSION_ID] = ws
        adapter._customer_media[SESSION_ID] = frozenset({"audio"})

        await adapter._start_egress(SESSION_ID, SEGMENT_ID, ROOM_NAME)

        # WS should have received the LGPD notice as a text message
        ws.send_json.assert_called()
        calls_args = [c.args[0] for c in ws.send_json.call_args_list]
        notice_sent = any(
            m.get("type") == "webrtc.message"
            and "Gravação" in m.get("text", "")
            for m in calls_args
        )
        assert notice_sent, f"LGPD notice not sent via WS. calls={calls_args}"

    @pytest.mark.asyncio
    async def test_provider_start_egress_called(self):
        adapter, redis, _ = _make_adapter()
        adapter._connections[SESSION_ID] = AsyncMock()
        adapter._customer_media[SESSION_ID] = frozenset({"audio"})

        await adapter._start_egress(SESSION_ID, SEGMENT_ID, ROOM_NAME)

        provider: MockWebRTCProvider = adapter._provider
        assert len(provider.egresses_started) == 1
        started = provider.egresses_started[0]
        assert started["room_name"] == ROOM_NAME
        assert SEGMENT_ID in started["output_url"]

    @pytest.mark.asyncio
    async def test_egress_id_stored_in_memory(self):
        adapter, redis, _ = _make_adapter()
        adapter._connections[SESSION_ID] = AsyncMock()
        adapter._customer_media[SESSION_ID] = frozenset({"audio"})

        await adapter._start_egress(SESSION_ID, SEGMENT_ID, ROOM_NAME)

        assert SESSION_ID in adapter._session_egress
        assert SEGMENT_ID in adapter._session_egress[SESSION_ID]

    @pytest.mark.asyncio
    async def test_egress_id_stored_in_redis(self):
        adapter, redis, _ = _make_adapter()
        adapter._connections[SESSION_ID] = AsyncMock()
        adapter._customer_media[SESSION_ID] = frozenset({"audio"})

        await adapter._start_egress(SESSION_ID, SEGMENT_ID, ROOM_NAME)

        # `pytest.approx(str, rel=0)` comparava o valor gravado com a CLASSE `str` —
        # "aproximadamente igual a <class 'str'>" não casa com nada, então este teste
        # nunca pôde passar (corrigido 2026-08-03). O autor queria "um str qualquer";
        # o instrumento para isso é `mock.ANY`, e aqui dá para afirmar mais: o valor é
        # o egress_id, e é ele que o teardown usa para parar a gravação. Um valor vazio
        # gravado aqui deixaria a sessão sem como encerrar o egress — falha silenciosa,
        # que é exatamente o que este teste existe para pegar.
        # O TTL é `_SESSION_TTL` (4 h), não 3600 — o literal antigo congelava um valor
        # que mudou. Afirmar contra a constante mantém o contrato que importa: a chave
        # de gravação vive tanto quanto a sessão, senão o teardown perde o egress_id e
        # a gravação fica órfã no LiveKit.
        rec_key = f"channel:webrtc:{SESSION_ID}:egress:{SEGMENT_ID}"
        redis.set.assert_any_call(rec_key, ANY, ex=_SESSION_TTL)

        stored = next(
            c.args[1] for c in redis.set.call_args_list if c.args[0] == rec_key
        )
        assert isinstance(stored, str) and stored, "egress_id gravado vazio"

    @pytest.mark.asyncio
    async def test_output_path_contains_session_and_segment(self):
        adapter, _, _ = _make_adapter()
        adapter._connections[SESSION_ID] = AsyncMock()
        adapter._customer_media[SESSION_ID] = frozenset({"audio"})

        await adapter._start_egress(SESSION_ID, SEGMENT_ID, ROOM_NAME)

        provider: MockWebRTCProvider = adapter._provider
        output_url = provider.egresses_started[0]["output_url"]
        assert SESSION_ID in output_url
        assert SEGMENT_ID in output_url
        assert output_url.endswith(".mp4")


# ── TestEgressDoubleStartGuard ────────────────────────────────────────────────


class TestEgressDoubleStartGuard:
    """Redis key already set → _start_egress returns without starting again."""

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self):
        adapter, redis, _ = _make_adapter()
        adapter._connections[SESSION_ID] = AsyncMock()
        adapter._customer_media[SESSION_ID] = frozenset({"audio"})

        # Simulate Redis already holding the egress key
        redis.exists = AsyncMock(return_value=1)

        await adapter._start_egress(SESSION_ID, SEGMENT_ID, ROOM_NAME)

        provider: MockWebRTCProvider = adapter._provider
        assert provider.egresses_started == [], "start_egress must not be called when guard fires"

    @pytest.mark.asyncio
    async def test_notice_not_sent_when_guard_fires(self):
        adapter, redis, _ = _make_adapter()
        ws = AsyncMock()
        adapter._connections[SESSION_ID] = ws
        adapter._customer_media[SESSION_ID] = frozenset({"audio"})
        redis.exists = AsyncMock(return_value=1)

        await adapter._start_egress(SESSION_ID, SEGMENT_ID, ROOM_NAME)

        # No WS message should have been sent
        ws.send_json.assert_not_called()


# ── TestEgressRecordingOptOut — REMOVIDA em 2026-09-14 (VOZ-09) ──────────────────
# Os dois testes desta classe REIMPLEMENTAVAM a condição de gravação no próprio corpo
# (`if should_record and segment_id and medium in ...`) e afirmavam sobre a cópia — não
# tocavam o produto, logo não podiam reprovar. É o mesmo defeito que a reescrita de
# 2026-08-03 fechou em `TestEgressRoutingAssigned`, que já cobre os dois casos chamando
# `_on_routing_assigned` de verdade.


# ── TestEgressStopAndStore ────────────────────────────────────────────────────


class TestEgressStopAndStore:
    """_stop_egress_and_store: stop egress, read file, commit, write stream event."""

    @pytest.fixture
    def tmp_recording(self, tmp_path) -> Path:
        """Create a fake MP4 recording file in tmp_path."""
        session_dir = tmp_path / SESSION_ID
        session_dir.mkdir()
        recording   = session_dir / f"{SEGMENT_ID}.mp4"
        recording.write_bytes(b"\x00\x01\x02\x03" * 256)  # 1024 bytes
        return tmp_path

    @pytest.mark.asyncio
    async def test_stop_egress_called(self, tmp_recording):
        attachment_store = MockAttachmentStore()
        settings = _make_settings(
            webrtc_egress_output_dir = str(tmp_recording),
            webrtc_egress_wait_s     = 0.0,
        )
        adapter, redis, _ = _make_adapter(
            settings         = settings,
            attachment_store = attachment_store,
        )

        await adapter._stop_egress_and_store(SESSION_ID, SEGMENT_ID, EGRESS_ID)

        provider: MockWebRTCProvider = adapter._provider
        assert EGRESS_ID in provider.egresses_stopped

    @pytest.mark.asyncio
    async def test_attachment_store_reserve_called(self, tmp_recording):
        attachment_store = MockAttachmentStore()
        settings = _make_settings(
            webrtc_egress_output_dir = str(tmp_recording),
            webrtc_egress_wait_s     = 0.0,
        )
        adapter, redis, _ = _make_adapter(
            settings         = settings,
            attachment_store = attachment_store,
        )

        await adapter._stop_egress_and_store(SESSION_ID, SEGMENT_ID, EGRESS_ID)

        assert len(attachment_store.reserved) == 1
        reserved = attachment_store.reserved[0]
        assert reserved["mime_type"] == "video/mp4"
        assert ".mp4" in reserved["file_name"]

    @pytest.mark.asyncio
    async def test_attachment_store_commit_called_with_bytes(self, tmp_recording):
        attachment_store = MockAttachmentStore()
        settings = _make_settings(
            webrtc_egress_output_dir = str(tmp_recording),
            webrtc_egress_wait_s     = 0.0,
        )
        adapter, redis, _ = _make_adapter(
            settings         = settings,
            attachment_store = attachment_store,
        )

        await adapter._stop_egress_and_store(SESSION_ID, SEGMENT_ID, EGRESS_ID)

        assert len(attachment_store.committed) == 1
        assert attachment_store.committed[0]["size"] == 1024

    @pytest.mark.asyncio
    async def test_recording_completed_event_written_to_stream(self, tmp_recording):
        attachment_store = MockAttachmentStore(
            serving_url = "http://test-serving/recording.mp4"
        )
        settings = _make_settings(
            webrtc_egress_output_dir = str(tmp_recording),
            webrtc_egress_wait_s     = 0.0,
        )
        adapter, redis, _ = _make_adapter(
            settings         = settings,
            attachment_store = attachment_store,
        )

        await adapter._stop_egress_and_store(SESSION_ID, SEGMENT_ID, EGRESS_ID)

        stream_key = f"session:{SESSION_ID}:stream"
        redis.xadd.assert_called_once()
        call_args = redis.xadd.call_args
        assert call_args.args[0] == stream_key or call_args[0][0] == stream_key
        event_data = call_args.args[1] if call_args.args else call_args[0][1]
        assert event_data["type"] == "recording.completed"
        assert event_data["session_id"] == SESSION_ID
        assert event_data["segment_id"] == SEGMENT_ID
        assert event_data["egress_id"]  == EGRESS_ID

    @pytest.mark.asyncio
    async def test_serving_url_in_stream_event(self, tmp_recording):
        attachment_store = MockAttachmentStore(
            serving_url = "http://test-serving/recording.mp4"
        )
        settings = _make_settings(
            webrtc_egress_output_dir = str(tmp_recording),
            webrtc_egress_wait_s     = 0.0,
        )
        adapter, redis, _ = _make_adapter(
            settings         = settings,
            attachment_store = attachment_store,
        )

        await adapter._stop_egress_and_store(SESSION_ID, SEGMENT_ID, EGRESS_ID)

        call_args  = redis.xadd.call_args
        event_data = call_args.args[1] if call_args.args else call_args[0][1]
        assert event_data["serving_url"] == "http://test-serving/recording.mp4"

    @pytest.mark.asyncio
    async def test_redis_egress_key_deleted_after_stop(self, tmp_recording):
        attachment_store = MockAttachmentStore()
        settings = _make_settings(
            webrtc_egress_output_dir = str(tmp_recording),
            webrtc_egress_wait_s     = 0.0,
        )
        adapter, redis, _ = _make_adapter(
            settings         = settings,
            attachment_store = attachment_store,
        )

        await adapter._stop_egress_and_store(SESSION_ID, SEGMENT_ID, EGRESS_ID)

        expected_key = f"channel:webrtc:{SESSION_ID}:egress:{SEGMENT_ID}"
        redis.delete.assert_any_call(expected_key)

    @pytest.mark.asyncio
    async def test_temp_file_deleted_after_store(self, tmp_recording):
        attachment_store = MockAttachmentStore()
        settings = _make_settings(
            webrtc_egress_output_dir = str(tmp_recording),
            webrtc_egress_wait_s     = 0.0,
        )
        adapter, redis, _ = _make_adapter(
            settings         = settings,
            attachment_store = attachment_store,
        )
        recording_path = tmp_recording / SESSION_ID / f"{SEGMENT_ID}.mp4"
        assert recording_path.exists(), "Pre-condition: file must exist"

        await adapter._stop_egress_and_store(SESSION_ID, SEGMENT_ID, EGRESS_ID)

        assert not recording_path.exists(), "Recording file should be deleted after commit"


# ── TestEgressStopNoFile ──────────────────────────────────────────────────────


class TestEgressStopNoFile:
    """Recording file missing after egress stop → graceful degradation."""

    @pytest.mark.asyncio
    async def test_no_crash_when_file_missing(self, tmp_path):
        """File is gone (or never written) — no exception should propagate."""
        settings = _make_settings(
            webrtc_egress_output_dir = str(tmp_path),
            webrtc_egress_wait_s     = 0.0,
        )
        adapter, redis, _ = _make_adapter(settings=settings)

        # Should not raise even though the file doesn't exist
        await adapter._stop_egress_and_store(SESSION_ID, SEGMENT_ID, EGRESS_ID)

    @pytest.mark.asyncio
    async def test_stream_event_still_written_when_file_missing(self, tmp_path):
        """Even without a file, recording.completed must be written to stream."""
        settings = _make_settings(
            webrtc_egress_output_dir = str(tmp_path),
            webrtc_egress_wait_s     = 0.0,
        )
        adapter, redis, _ = _make_adapter(settings=settings)

        await adapter._stop_egress_and_store(SESSION_ID, SEGMENT_ID, EGRESS_ID)

        redis.xadd.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_attachment_store_call_when_no_bytes(self, tmp_path):
        attachment_store = MockAttachmentStore()
        settings = _make_settings(
            webrtc_egress_output_dir = str(tmp_path),
            webrtc_egress_wait_s     = 0.0,
        )
        adapter, redis, _ = _make_adapter(
            settings         = settings,
            attachment_store = attachment_store,
        )

        await adapter._stop_egress_and_store(SESSION_ID, SEGMENT_ID, EGRESS_ID)

        # No bytes available → store.reserve/commit should NOT be called
        assert attachment_store.reserved == []
        assert attachment_store.committed == []


# ── TestEgressStopAllIdempotent ───────────────────────────────────────────────


class TestEgressStopAllIdempotent:
    """_stop_all_egress is idempotent — second call does nothing."""

    @pytest.mark.asyncio
    async def test_second_stop_all_is_noop(self, tmp_path):
        settings = _make_settings(
            webrtc_egress_output_dir = str(tmp_path),
            webrtc_egress_wait_s     = 0.0,
        )
        adapter, redis, _ = _make_adapter(settings=settings)

        # Seed one active egress
        adapter._session_egress[SESSION_ID] = {SEGMENT_ID: EGRESS_ID}

        # First call — fires stop task
        await adapter._stop_all_egress(SESSION_ID)
        assert SESSION_ID not in adapter._session_egress, "Session egress should be cleared"

        # Second call — should do nothing (no more active egresses)
        provider: MockWebRTCProvider = adapter._provider
        initial_stops = len(provider.egresses_stopped)

        await adapter._stop_all_egress(SESSION_ID)
        # No new tasks created (we can't easily await created tasks here, but
        # the fact that _session_egress is empty means no new stop is triggered)
        assert len(provider.egresses_stopped) == initial_stops, (
            "Second _stop_all_egress should not call stop_egress again"
        )


# ── TestEgressRoutingAssigned ─────────────────────────────────────────────────


class TestEgressRoutingAssigned:
    """_on_routing_assigned NÃO dispara gravação (VOZ-10).

    O gatilho lia `pool.webrtc_recording`, campo que não existia em schema, tabela, tela
    nem produtor: leitor sem produtor. Os testes anteriores o exercitavam injetando o campo
    à mão no evento — provavam a CHAMADA e escondiam a AUSÊNCIA. A gravação volta com a
    VOZ-06, junto do campo e de quem o escreve; até lá, nem o campo injetado liga nada.
    """

    async def _run(self, fields: dict):
        adapter, _, _ = _make_adapter()
        with patch.object(adapter, "_start_egress", new=AsyncMock()) as start, \
             patch.object(adapter, "_start_stt_pipeline", new=AsyncMock()):
            await adapter._on_routing_assigned(
                AsyncMock(), SESSION_ID, fields, adapter._settings,
            )
            await asyncio.sleep(0)      # deixa as tasks criadas iniciarem
        return start

    @pytest.mark.asyncio
    async def test_nem_campo_injetado_liga_gravacao(self):
        start = await self._run({
            "framework":  "human",
            "segment_id": SEGMENT_ID,
            "pool": json.dumps({
                "pool_id": "p", "media_policy_source": "registry", "webrtc_recording": True,
                "media_policy": {"customer_publish": ["audio", "video"], "agent_publish": []},
            }),
        })
        start.assert_not_called()


# ── TestEgressProviderImpl ────────────────────────────────────────────────────


class TestEgressProviderImpl:
    """LiveKitProvider sem plano de mídia: a recusa acontece ANTES de qualquer egress.

    Até a VOZ-01 esta classe cobrava `EG_dev_…` devolvido em `_dev_mode` — o chamador
    registrava "gravação iniciada" contra um SFU que não existia — e tinha um teste que
    terminava em `assert True`, isto é, que não podia reprovar. Os dois saíram.
    """

    def test_sem_credencial_nao_ha_egress_placebo(self):
        with pytest.raises(WebRTCProviderUnavailable):
            LiveKitProvider(url="ws://livekit", api_key="", api_secret="")

    @pytest.mark.asyncio
    async def test_mock_provider_start_egress(self):
        provider = MockWebRTCProvider()
        eid1 = await provider.start_egress("room-1", "/tmp/r1.mp4")
        eid2 = await provider.start_egress("room-2", "/tmp/r2.mp4")
        assert eid1 != eid2
        assert len(provider.egresses_started) == 2

    @pytest.mark.asyncio
    async def test_mock_provider_stop_egress(self):
        provider = MockWebRTCProvider()
        eid = await provider.start_egress("room-1", "/tmp/r1.mp4")
        await provider.stop_egress(eid)
        assert eid in provider.egresses_stopped

    @pytest.mark.asyncio
    async def test_mock_provider_records_layout_and_dual_channel(self):
        provider = MockWebRTCProvider()
        await provider.start_egress(
            room_name    = "room-x",
            output_url   = "/tmp/rx.mp4",
            layout       = "grid",
            dual_channel = False,
        )
        started = provider.egresses_started[0]
        assert started["layout"]       == "grid"
        assert started["dual_channel"] == False


# ── TestEgressNoAttachmentStore ───────────────────────────────────────────────


class TestEgressNoAttachmentStore:
    """When no AttachmentStore is configured, egress still completes gracefully."""

    @pytest.mark.asyncio
    async def test_stop_and_store_without_attachment_store(self, tmp_path):
        session_dir = tmp_path / SESSION_ID
        session_dir.mkdir()
        recording   = session_dir / f"{SEGMENT_ID}.mp4"
        recording.write_bytes(b"\xFF" * 512)

        settings = _make_settings(
            webrtc_egress_output_dir = str(tmp_path),
            webrtc_egress_wait_s     = 0.0,
        )
        adapter, redis, _ = _make_adapter(
            settings         = settings,
            attachment_store = None,   # no store
        )

        # Should not raise
        await adapter._stop_egress_and_store(SESSION_ID, SEGMENT_ID, EGRESS_ID)

        # Stream event must still be written with local path as serving_url
        redis.xadd.assert_called_once()
        call_args  = redis.xadd.call_args
        event_data = call_args.args[1] if call_args.args else call_args[0][1]
        assert event_data["type"]      == "recording.completed"
        assert event_data["serving_url"] != ""   # local path or empty string
