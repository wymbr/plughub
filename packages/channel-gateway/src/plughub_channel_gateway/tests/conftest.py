"""
conftest.py
Shared fixtures for Channel Gateway tests.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from plughub_channel_gateway.models import ContextSnapshot
from plughub_channel_gateway.session_registry import SessionRegistry

# ── Constants ─────────────────────────────────────────────────────────────────

CONTACT_ID = "cid-test-001"
SESSION_ID  = "sid-test-001"
TENANT_ID   = "tenant_test"
JWT_SECRET  = "test_secret_32chars_webchat_ok!!"

# Fake JWT claims returned by the _token_validator bypass in tests
FAKE_CLAIMS = {
    "sub":        CONTACT_ID,
    "session_id": SESSION_ID,
    "tenant_id":  TENANT_ID,
}


# ── Redis mock ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    """Async Redis client mock covering all paths used by WebchatAdapter."""
    redis = AsyncMock()
    redis.setex   = AsyncMock(return_value=True)
    redis.get     = AsyncMock(return_value=None)
    redis.delete  = AsyncMock(return_value=1)
    redis.publish = AsyncMock(return_value=1)
    redis.exists  = AsyncMock(return_value=1)   # stream exists by default
    # O StreamSubscriber sonda o stream com XRANGE, não EXISTS (troca deliberada, para
    # eliminar a janela de race entre a sondagem e o primeiro XREAD —
    # `stream_subscriber.py:112-118`). Sem esta linha o atributo cai no AsyncMock
    # genérico, que devolve um MagicMock TRUTHY: "o stream existe" por acidente, e
    # nenhum teste consegue alcançar o caminho de stream expirado.
    redis.xrange  = AsyncMock(return_value=[(b"0-0", {})])   # stream existe por default
    redis.aclose  = AsyncMock()

    # StreamSubscriber uses xread with BLOCK.  In tests we add a small sleep so
    # the tight empty-response loop doesn't starve other tasks before they can
    # complete and unblock asyncio.wait(FIRST_COMPLETED).
    async def _xread_mock(*args, **kwargs):
        await asyncio.sleep(0.005)
        return []

    redis.xread = _xread_mock

    # _typing_listener uses pubsub.  The mock listen() is an empty async
    # generator so the typing task completes on the first event-loop tick,
    # which causes asyncio.wait(FIRST_COMPLETED) to return quickly.
    pubsub_mock = AsyncMock()

    async def _pubsub_listen():
        """Empty async generator — typing task exits immediately in tests."""
        return
        yield  # pragma: no cover — makes this function an async generator

    pubsub_mock.subscribe   = AsyncMock()
    pubsub_mock.unsubscribe = AsyncMock()
    pubsub_mock.aclose      = AsyncMock()
    pubsub_mock.listen      = _pubsub_listen  # not AsyncMock — real async gen

    redis.pubsub = lambda: pubsub_mock
    return redis


def redis_get_by_key(mapping: dict[str, object], default=None):
    """`side_effect` para `redis.get` que responde POR CHAVE, não com um valor único.

    **Por que existe (2026-08-03).** `mock_redis.get.return_value = "existing-sid"`
    responde o mesmo para *todas* as chaves. O `SMSAdapter._handle_inbound` faz duas
    consultas distintas — a sessão e `…:pending_collect` — e a segunda recebia
    `"existing-sid"`, que o `json.loads` não decodifica. O teste morria com
    `JSONDecodeError` num caminho que ele nem pretendia exercitar, e o log dizia
    *"sms inbound processing failed"*, apontando para o adapter.

    Um dublê de Redis que ignora a chave não está simulando Redis — está simulando *uma*
    leitura. Casa por substring para não acoplar o teste ao formato exato da chave (que é
    detalhe do adapter), mas exige que o teste DECLARE cada leitura que pretende atender;
    o que não estiver no mapa devolve `default` (ausência), nunca o valor do vizinho.
    """
    # Fragmento MAIS ESPECÍFICO vence, não o primeiro declarado. Com "primeiro vence",
    # um mapa `{"channel:sms": …, "menu_collect": …}` faz a chave
    # `channel:sms:{sid}:menu_collect` casar com o prefixo genérico — e o teste recebe o
    # valor da sessão numa leitura de collect. Aconteceu na 1ª versão deste helper
    # (2026-08-03): trocou um dublê cego por um dublê quase-cego, com a mesma assinatura
    # de falha (JSONDecodeError num caminho não pretendido).
    _ordered = sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True)

    def _get(key, *_a, **_kw):
        k = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
        for fragment, value in _ordered:
            if fragment in k:
                return value
        return default
    return _get


# ── Kafka producer mock ───────────────────────────────────────────────────────

@pytest.fixture
def mock_producer():
    """AIOKafkaProducer mock."""
    producer = AsyncMock()
    producer.send  = AsyncMock()
    producer.start = AsyncMock()
    producer.stop  = AsyncMock()
    return producer


# ── WebSocket mock helpers ────────────────────────────────────────────────────

def make_auth_msg(cursor: str | None = None) -> str:
    """Returns a JSON-encoded conn.authenticate message for the test handshake."""
    msg: dict = {"type": "conn.authenticate", "token": "test_token"}
    if cursor is not None:
        msg["cursor"] = cursor
    return json.dumps(msg)


def make_ws_mock(messages: list[str] | None = None, *, skip_auth: bool = False):
    """
    Build a mock WebSocket that:
      1. Yields a conn.authenticate message (prepended automatically)
      2. Yields the provided *messages* in order
      3. Raises WebSocketDisconnect when exhausted

    Set skip_auth=True to omit the auth message (for testing auth-failure paths).
    """
    from fastapi import WebSocketDisconnect

    ws = AsyncMock()
    ws.accept   = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close    = AsyncMock()

    _msgs = []
    if not skip_auth:
        _msgs.append(make_auth_msg())
    _msgs.extend(messages or [])

    async def receive_text():
        if _msgs:
            return _msgs.pop(0)
        raise WebSocketDisconnect(code=1000)

    ws.receive_text = receive_text
    return ws


@pytest.fixture
def ws_factory():
    return make_ws_mock


# ── Adapter collaborator fixtures ─────────────────────────────────────────────

@pytest.fixture
def registry(mock_redis):
    """SessionRegistry mock wired to the shared redis mock.

    **Os métodos SÍNCRONOS do real precisam ser síncronos no dublê** (corrigido
    2026-08-03). `AsyncMock()` torna *todo* atributo assíncrono, então
    `registry.pop_menu_masked_fields(...)` devolvia uma **corrotina**, e o
    `_handle_menu_submit` estourava em `set(masked_fields)` —
    `'coroutine' object is not iterable`. Três testes de submissão de menu morriam por
    isso, e o erro apontava para o código de produção (`webchat.py:825`), que está
    correto: `pop_menu_masked_fields` **é** sync (`session_registry.py:175`).

    A lista não é escrita à mão: é derivada do `SessionRegistry` real. Escrever
    `reg.pop_menu_masked_fields = MagicMock()` conserta hoje e quebra de novo no próximo
    método sync que alguém acrescentar — foi assim que este chegou aqui (o
    `test_outbound_consumer.py` já declarava `store_menu_masked_fields = MagicMock()`,
    isolado, e a lição não alcançou o vizinho). Derivar do real é a mesma regra dos
    quatro dublês de 2026-08-02: *responde à ESTRUTURA do objeto, não a uma foto dela*.
    """
    reg = AsyncMock()

    for name in dir(SessionRegistry):
        if name.startswith("__"):
            continue
        attr = inspect.getattr_static(SessionRegistry, name, None)
        if inspect.isfunction(attr) and not inspect.iscoroutinefunction(attr):
            setattr(reg, name, MagicMock())

    # Retornos que o chamador consome de fato (o resto pode ser MagicMock cru).
    reg.pop_menu_masked_fields = MagicMock(return_value=[])   # nenhum campo mascarado
    reg.is_local               = MagicMock(return_value=True)

    reg.register   = AsyncMock()
    reg.unregister = AsyncMock(return_value="2024-01-01T10:00:00Z")
    reg.send       = AsyncMock(return_value=True)
    reg.append_message = AsyncMock()
    reg._redis     = mock_redis
    return reg


@pytest.fixture
def context_reader():
    """ContextReader mock that returns a generic snapshot."""
    cr = AsyncMock()
    cr.get_snapshot = AsyncMock(
        return_value=ContextSnapshot(
            intent="general_inquiry", sentiment_score=0.7, turn_number=1
        )
    )
    return cr


# ── Settings mock ─────────────────────────────────────────────────────────────

@pytest.fixture
def settings():
    from plughub_channel_gateway.config import Settings
    return Settings(
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
        jwt_secret                = JWT_SECRET,
        ws_auth_timeout_s         = 10,
        storage_root              = "/tmp/plughub_test_attachments",
        attachment_expiry_days    = 1,
        database_url              = "postgresql://plughub:plughub@localhost:5432/plughub",
        webchat_serving_base_url  = "http://localhost:8010/webchat/v1/attachments",
        webchat_upload_base_url   = "http://localhost:8010/webchat/v1/upload",
        tenant_id                 = TENANT_ID,
    )
