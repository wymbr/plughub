"""
test_stream_subscriber.py
Unit tests for StreamSubscriber — XREAD loop, event mapping, cursor tracking.

Strategy:
  xread mock returns pre-configured event batches then blocks (sleep + []).
  collect() drains N messages from the async generator then breaks out.
  All assertions are against the dict yielded by subscriber.messages().
"""

from __future__ import annotations

import asyncio
import json
import pytest

from plughub_channel_gateway.stream_subscriber import StreamSubscriber


SESSION_ID = "sess-test-001"
STREAM_KEY = f"session:{SESSION_ID}:stream".encode()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _enc(value) -> bytes:
    """Encode a value to bytes the way Redis would: dicts/lists as JSON."""
    if isinstance(value, (dict, list)):
        return json.dumps(value).encode()
    return str(value).encode()


def _event(entry_id: str, **fields) -> list:
    """
    Build one Redis XREAD response batch containing a single event.
    entry_id is the Redis stream entry ID (used for cursor tracking).
    fields are the event data fields (type, visibility, author, payload, …).
    Returns the list structure that redis.asyncio.xread() returns:
      [(stream_key, [(entry_id, {field: value, ...})])]
    """
    raw = {k.encode(): _enc(v) for k, v in fields.items()}
    return [(STREAM_KEY, [(entry_id.encode(), raw)])]


def make_xread(*batches):
    """
    Returns an async callable that yields each batch in turn,
    then sleeps briefly and returns [] forever (simulates BLOCK with no new data).
    """
    remaining = list(batches)

    async def xread(*args, **kwargs):
        if remaining:
            return remaining.pop(0)
        await asyncio.sleep(0.005)
        return []

    return xread


async def collect(subscriber: StreamSubscriber, n: int) -> list[dict]:
    """Collect exactly n messages from the subscriber then stop."""
    results: list[dict] = []
    async for msg in subscriber.messages():
        results.append(msg)
        if len(results) >= n:
            break
    return results


def make_subscriber(xread_fn, cursor: str = "0", stream_exists: bool = True) -> StreamSubscriber:
    from unittest.mock import AsyncMock
    import redis.asyncio as aioredis

    redis = AsyncMock(spec=aioredis.Redis)
    redis.xread  = xread_fn
    # XRANGE returns a non-empty list when the stream exists, empty list when it doesn't.
    # count=1 probe used in messages() to detect expired streams atomically.
    redis.xrange = AsyncMock(return_value=[("fake-entry",)] if stream_exists else [])
    return StreamSubscriber(redis=redis, session_id=SESSION_ID, cursor=cursor)


# ── Cursor ────────────────────────────────────────────────────────────────────

class TestCursor:
    def test_default_cursor_is_zero(self):
        s = make_subscriber(make_xread())
        assert s.cursor == "0"

    def test_custom_initial_cursor(self):
        s = make_subscriber(make_xread(), cursor="1234-0")
        assert s.cursor == "1234-0"

    async def test_cursor_advances_after_delivered_event(self):
        batch = _event(
            "1001-0",
            type="message", visibility="all",
            author={"role": "primary", "participant_id": "p1"},
            payload={"content": {"type": "text", "text": "hi"}},
            event_id="evt-001", timestamp="2024-01-01T10:00:00Z",
        )
        s = make_subscriber(make_xread(batch))
        await collect(s, 1)
        assert s.cursor == "1001-0"

    async def test_cursor_advances_on_filtered_event(self):
        """Cursor must advance even for agents_only events that are not delivered."""
        batch = _event(
            "2000-0",
            type="message", visibility="agents_only",
            author={"role": "primary", "participant_id": "p1"},
            payload={"content": {"type": "text", "text": "internal"}},
            event_id="evt-internal", timestamp="2024-01-01T10:00:00Z",
        )
        # Second batch — a visible event so collect() can return
        batch2 = _event(
            "2001-0",
            type="session_closed",
            visibility="all",
            close_reason="agent_done",
        )
        s = make_subscriber(make_xread(batch, batch2))
        await collect(s, 1)
        # cursor must be at 2001-0, not 2000-0
        assert s.cursor == "2001-0"

    async def test_cursor_passes_to_xread(self):
        """XREAD must be called with the current cursor value."""
        received_cursor = []

        async def xread(streams, count, block):
            # streams is {key: cursor}
            for key, cur in streams.items():
                received_cursor.append(cur)
            await asyncio.sleep(0.01)  # prevent tight loop before cancellation
            return []

        s = make_subscriber(xread, cursor="9999-0")
        # Run one iteration then cancel
        task = asyncio.create_task(collect(s, 1))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(received_cursor) >= 1
        assert received_cursor[0] == "9999-0"


# ── Visibility filtering ──────────────────────────────────────────────────────

class TestVisibilityFilter:
    async def test_all_visibility_is_delivered(self):
        batch = _event(
            "3000-0",
            type="message", visibility="all",
            author={"role": "primary", "participant_id": "p1"},
            payload={"content": {"type": "text", "text": "visible"}},
            event_id="evt-vis", timestamp="T",
        )
        s = make_subscriber(make_xread(batch))
        msgs = await collect(s, 1)
        assert len(msgs) == 1
        assert msgs[0]["text"] == "visible"

    async def test_agents_only_is_filtered(self):
        """agents_only events must never reach the client."""
        filtered = _event(
            "3001-0",
            type="message", visibility="agents_only",
            author={"role": "primary", "participant_id": "p1"},
            payload={"content": {"type": "text", "text": "secret"}},
            event_id="evt-sec", timestamp="T",
        )
        visible = _event(
            "3002-0",
            type="session_closed", visibility="all", close_reason="agent_done"
        )
        s = make_subscriber(make_xread(filtered, visible))
        msgs = await collect(s, 1)
        assert msgs[0]["type"] == "conn.session_ended"  # filtered event skipped

    async def test_list_visibility_is_filtered(self):
        """Participant-list visibility is private — never delivered to browser."""
        filtered = _event(
            "3003-0",
            type="message", visibility=["p1", "p2"],
            author={"role": "supervisor", "participant_id": "sup"},
            payload={"content": {"type": "text", "text": "whisper"}},
            event_id="evt-w", timestamp="T",
        )
        visible = _event(
            "3004-0",
            type="session_closed", visibility="all", close_reason="agent_done"
        )
        s = make_subscriber(make_xread(filtered, visible))
        msgs = await collect(s, 1)
        assert msgs[0]["type"] == "conn.session_ended"


# ── Message event mapping ─────────────────────────────────────────────────────

class TestMessageMapping:
    async def test_text_message(self):
        batch = _event(
            "4000-0",
            type="message", visibility="all",
            author={"role": "primary", "participant_id": "p1"},
            payload={"content": {"type": "text", "text": "Olá!"}},
            event_id="evt-txt", timestamp="2024-01-01T10:00:00Z",
        )
        s = make_subscriber(make_xread(batch))
        msgs = await collect(s, 1)
        msg = msgs[0]
        assert msg["type"] == "msg.text"
        assert msg["text"] == "Olá!"
        assert msg["author"]["role"] == "primary"
        assert msg["author"]["participant_id"] == "p1"
        assert msg["timestamp"] == "2024-01-01T10:00:00Z"

    async def test_image_message(self):
        batch = _event(
            "4001-0",
            type="message", visibility="all",
            author={"role": "primary", "participant_id": "p1"},
            payload={"content": {
                "type": "image", "file_id": "f-001", "url": "http://host/f-001",
                "mime_type": "image/jpeg", "original_name": "foto.jpg",
                "size_bytes": 2048, "caption": "legenda",
            }},
            event_id="evt-img", timestamp="T",
        )
        s = make_subscriber(make_xread(batch))
        msgs = await collect(s, 1)
        msg = msgs[0]
        assert msg["type"] == "msg.image"
        assert msg["file_id"] == "f-001"
        assert msg["url"] == "http://host/f-001"
        assert msg["mime_type"] == "image/jpeg"
        assert msg["caption"] == "legenda"

    async def test_document_message(self):
        batch = _event(
            "4002-0",
            type="message", visibility="all",
            author={"role": "primary", "participant_id": "p1"},
            payload={"content": {
                "type": "document", "file_id": "f-doc", "url": "http://host/f-doc",
                "mime_type": "application/pdf", "original_name": "contrato.pdf",
                "size_bytes": 51200, "page_count": 4,
            }},
            event_id="evt-doc", timestamp="T",
        )
        s = make_subscriber(make_xread(batch))
        msgs = await collect(s, 1)
        msg = msgs[0]
        assert msg["type"] == "msg.document"
        assert msg["file_id"] == "f-doc"
        assert msg["page_count"] == 4

    async def test_video_message(self):
        batch = _event(
            "4003-0",
            type="message", visibility="all",
            author={"role": "primary", "participant_id": "p1"},
            payload={"content": {
                "type": "video", "file_id": "f-vid", "url": "http://host/f-vid",
                "mime_type": "video/mp4", "original_name": "demo.mp4",
                "size_bytes": 1048576, "duration_secs": 30,
            }},
            event_id="evt-vid", timestamp="T",
        )
        s = make_subscriber(make_xread(batch))
        msgs = await collect(s, 1)
        msg = msgs[0]
        assert msg["type"] == "msg.video"
        assert msg["duration_secs"] == 30

    async def test_unsupported_content_type_returns_nothing(self):
        """Unknown content types are silently ignored."""
        unknown = _event(
            "4004-0",
            type="message", visibility="all",
            author={"role": "primary", "participant_id": "p1"},
            payload={"content": {"type": "sticker", "url": "http://host/sticker"}},
            event_id="evt-unk", timestamp="T",
        )
        visible = _event(
            "4005-0",
            type="session_closed", visibility="all", close_reason="agent_done"
        )
        s = make_subscriber(make_xread(unknown, visible))
        msgs = await collect(s, 1)
        assert msgs[0]["type"] == "conn.session_ended"  # sticker silently dropped

    async def test_content_as_json_string(self):
        """content field may arrive as a JSON string (double-encoded)."""
        content_str = json.dumps({"type": "text", "text": "string-encoded"})
        batch = _event(
            "4006-0",
            type="message", visibility="all",
            author={"role": "primary", "participant_id": "p1"},
            payload={"content": content_str},
            event_id="evt-str", timestamp="T",
        )
        s = make_subscriber(make_xread(batch))
        msgs = await collect(s, 1)
        assert msgs[0]["type"] == "msg.text"
        assert msgs[0]["text"] == "string-encoded"


# ── Other event types ─────────────────────────────────────────────────────────

class TestOtherEventTypes:
    async def test_session_closed(self):
        batch = _event(
            "5000-0",
            type="session_closed", visibility="all", close_reason="agent_done",
        )
        s = make_subscriber(make_xread(batch))
        msgs = await collect(s, 1)
        assert msgs[0]["type"] == "conn.session_ended"
        assert msgs[0]["reason"] == "agent_done"

    async def test_session_closed_fallback_reason(self):
        batch = _event(
            "5001-0",
            type="session_closed", visibility="all", reason="timeout",
        )
        s = make_subscriber(make_xread(batch))
        msgs = await collect(s, 1)
        assert msgs[0]["reason"] == "timeout"

    async def test_interaction_request(self):
        batch = _event(
            "5002-0",
            type="interaction_request", visibility="all",
            payload={
                "menu_id": "menu-001",
                "interaction": "button",
                "prompt": "Escolha uma opção:",
                "options": [{"id": "a", "label": "Cancelar"}],
            },
        )
        s = make_subscriber(make_xread(batch))
        msgs = await collect(s, 1)
        msg = msgs[0]
        assert msg["type"] == "interaction.request"
        assert msg["menu_id"] == "menu-001"
        assert msg["interaction"] == "button"
        assert msg["prompt"] == "Escolha uma opção:"
        assert msg["options"] == [{"id": "a", "label": "Cancelar"}]

    async def test_participant_joined_agent_delivered(self):
        batch = _event(
            "5003-0",
            type="participant_joined", visibility="all",
            author={"role": "primary", "participant_id": "p-agent"},
        )
        s = make_subscriber(make_xread(batch))
        msgs = await collect(s, 1)
        assert msgs[0]["type"] == "presence.agent_joined"
        assert msgs[0]["participant_id"] == "p-agent"
        assert msgs[0]["role"] == "primary"

    async def test_participant_joined_customer_filtered(self):
        """Customer joining the session must not be sent to themselves."""
        customer_joined = _event(
            "5004-0",
            type="participant_joined", visibility="all",
            author={"role": "customer", "participant_id": "c-001"},
        )
        agent_joined = _event(
            "5005-0",
            type="participant_joined", visibility="all",
            author={"role": "primary", "participant_id": "p-001"},
        )
        s = make_subscriber(make_xread(customer_joined, agent_joined))
        msgs = await collect(s, 1)
        assert msgs[0]["role"] == "primary"  # first delivered is the agent, not customer

    async def test_participant_left_agent_delivered(self):
        batch = _event(
            "5006-0",
            type="participant_left", visibility="all",
            author={"role": "specialist", "participant_id": "p-spec"},
        )
        s = make_subscriber(make_xread(batch))
        msgs = await collect(s, 1)
        assert msgs[0]["type"] == "presence.agent_left"
        assert msgs[0]["participant_id"] == "p-spec"

    async def test_participant_left_customer_filtered(self):
        customer_left = _event(
            "5007-0",
            type="participant_left", visibility="all",
            author={"role": "customer", "participant_id": "c-001"},
        )
        agent_left = _event(
            "5008-0",
            type="participant_left", visibility="all",
            author={"role": "primary", "participant_id": "p-001"},
        )
        s = make_subscriber(make_xread(customer_left, agent_left))
        msgs = await collect(s, 1)
        assert msgs[0]["role"] == "primary"

    async def test_unknown_event_type_silently_skipped(self):
        unknown = _event("6000-0", type="flow_step_completed", visibility="all")
        visible = _event(
            "6001-0",
            type="session_closed", visibility="all", close_reason="agent_done"
        )
        s = make_subscriber(make_xread(unknown, visible))
        msgs = await collect(s, 1)
        assert msgs[0]["type"] == "conn.session_ended"


# ── Resilience ────────────────────────────────────────────────────────────────

class TestResilience:
    async def test_empty_xread_does_not_yield(self):
        """Empty XREAD response (timeout) must not produce any message."""
        # empty once, then a real event
        real_event = _event(
            "7000-0",
            type="session_closed", visibility="all", close_reason="agent_done"
        )
        call_count = 0

        async def xread(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []  # simulates BLOCK timeout
            await asyncio.sleep(0)
            return real_event

        s = make_subscriber(xread)
        msgs = await collect(s, 1)
        assert len(msgs) == 1
        assert call_count >= 2  # at least one empty + one real

    async def test_xread_error_retries(self):
        """Network/Redis error must not crash the loop — it retries."""
        call_count = 0
        real_event = _event(
            "7001-0",
            type="session_closed", visibility="all", close_reason="agent_done"
        )

        async def xread(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("redis down")
            await asyncio.sleep(0)
            return real_event

        s = make_subscriber(xread)
        msgs = await collect(s, 1)
        assert len(msgs) == 1

    async def test_cancellation_stops_generator(self):
        """CancelledError propagated from xread must exit messages() cleanly."""
        async def xread(*args, **kwargs):
            raise asyncio.CancelledError()

        s = make_subscriber(xread)
        results = []
        async for msg in s.messages():
            results.append(msg)

        # Generator exited cleanly without raising
        assert results == []


# ── Stream expired (reconexão com TTL expirado) ───────────────────────────────

class TestStreamExpired:
    async def test_raises_stream_expired_error_when_stream_missing_on_reconnect(self):
        """
        Se o cliente reconecta com cursor != "0" mas o stream não existe
        (EXISTS retorna 0), StreamExpiredError deve ser levantado antes do
        primeiro XREAD, sinalizando que a sessão foi encerrada.
        """
        from plughub_channel_gateway.stream_subscriber import StreamExpiredError

        s = make_subscriber(make_xread(), cursor="1234-0", stream_exists=False)

        with pytest.raises(StreamExpiredError):
            async for _ in s.messages():
                pass  # não deve chegar aqui

    async def test_cursor_zero_does_not_check_exists(self):
        """
        Nova conexão (cursor="0") não verifica EXISTS — o stream pode ainda
        não ter nenhum evento.  Nenhuma exceção deve ser levantada.
        """
        s = make_subscriber(make_xread(), cursor="0", stream_exists=False)

        # Deve iterar normalmente (sem eventos) sem levantar StreamExpiredError
        results = []
        task = asyncio.create_task(collect(s, 1))
        await asyncio.sleep(0.02)   # deixa o loop rodar pelo menos um ciclo
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # O importante: nenhuma StreamExpiredError foi levantada
        assert results == []

    async def test_xrange_called_with_correct_stream_key(self):
        """XRANGE probe é chamado com a chave correta do stream."""
        s = make_subscriber(make_xread(), cursor="abc-0", stream_exists=True)

        task = asyncio.create_task(collect(s, 1))
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        expected_key = f"session:{SESSION_ID}:stream"
        s._redis.xrange.assert_called_once_with(expected_key, "-", "+", count=1)

    async def test_reconnect_with_valid_stream_delivers_events(self):
        """
        Reconnect com cursor != "0" mas stream existente deve funcionar
        normalmente — StreamExpiredError não é levantado.
        """
        batch = _event(
            "5000-0",
            type="message", visibility="all",
            author={"role": "primary", "participant_id": "p99"},
            payload={"content": {"type": "text", "text": "retomada"}},
            event_id="evt-reconnect", timestamp="2024-01-01T12:00:00Z",
        )
        s = make_subscriber(make_xread(batch), cursor="4999-0", stream_exists=True)
        msgs = await collect(s, 1)
        assert len(msgs) == 1
        assert msgs[0]["text"] == "retomada"

    async def test_redis_error_on_xrange_does_not_raise(self):
        """
        Se XRANGE falhar (Redis indisponível), o subscriber não levanta erro —
        presume que o stream existe e continua normalmente.
        """
        from unittest.mock import AsyncMock

        s = make_subscriber(make_xread(), cursor="abc-0", stream_exists=True)
        # Simula falha no XRANGE probe
        s._redis.xrange = AsyncMock(side_effect=ConnectionError("Redis down"))

        # Deve iterar sem levantar StreamExpiredError
        task = asyncio.create_task(collect(s, 1))
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # nenhuma exceção inesperada
