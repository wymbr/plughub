"""
test_supervisor_message_delivery.py — a nota do supervisor CHEGA ao Console do atendente.

Medido em 2026-09-21: a nota interna digitada na supervisão aparecia na transcrição da
supervisão (como JSON cru) e em lugar nenhum da tela de quem ela orienta. O Console não lê
o stream — recebe por `agent:events:{sid}` —, e `/supervisor/message` só fazia XADD.

Proposições:
  - a nota é PUBLICADA em `agent:events:{sid}` como `message.text` de autor `supervisor`,
    com o `session_id` (sem ele o Console descarta) e a visibilidade pedida;
  - o stream guarda `{message_id, content}` — a forma que a transcrição desembrulha;
  - pub/sub fora do ar NÃO derruba a escrita: o registro vale, a entrega degrada dita.
"""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plughub_analytics_api.pool_auth import PoolPrincipal, require_pool_principal
from plughub_analytics_api.supervisor import router

TENANT  = "tenant_a"
SESSION = "sess_20260921T120000_01HX5K3MNJP8QVWZ4RSUP"


class _FakeRedis:
    def __init__(self, publish_fails: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.streams: list[dict] = []
        self.published: list[tuple[str, dict]] = []
        self._fail = publish_fails

    async def get(self, key: str):
        if key.endswith(":meta"):
            return json.dumps({"tenant_id": TENANT, "channel": "webchat"})
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value

    async def smembers(self, key: str):
        return set()

    async def xadd(self, key: str, fields: dict):
        self.streams.append({"key": key, **fields})
        return f"{len(self.streams)}-0"

    async def publish(self, channel: str, data: str):
        if self._fail:
            raise ConnectionError("pubsub fora")
        self.published.append((channel, json.loads(data)))
        return 1


def _joined(publish_fails: bool = False):
    app = FastAPI()
    app.include_router(router)
    app.state.redis = _FakeRedis(publish_fails)
    app.dependency_overrides[require_pool_principal] = lambda: PoolPrincipal(
        accessible_pools=None, tenant_id=TENANT, sub="sup1")
    client = TestClient(app)
    r = client.post("/supervisor/join", json={
        "tenant_id": TENANT, "session_id": SESSION, "operator_id": "sup1"})
    assert r.status_code == 200, r.text
    return client, app.state.redis, r.json()["participant_id"]


def _send(client, pid, visibility="agents_only"):
    return client.post("/supervisor/message", json={
        "tenant_id": TENANT, "session_id": SESSION, "participant_id": pid,
        "text": "  confirme o CPF antes  ", "visibility": visibility})


def test_nota_e_ENTREGUE_ao_console():
    client, redis, pid = _joined()
    r = _send(client, pid)
    assert r.status_code == 200, r.text
    assert len(redis.published) == 1
    channel, ev = redis.published[0]
    assert channel == f"agent:events:{SESSION}"
    assert ev["type"] == "message.text"
    assert ev["session_id"] == SESSION
    assert ev["author"] == {"type": "supervisor", "id": pid}
    assert ev["text"] == "confirme o CPF antes"
    assert ev["visibility"] == "agents_only"


def test_visibilidade_all_viaja_como_pedida():
    client, redis, pid = _joined()
    _send(client, pid, visibility="all")
    assert redis.published[0][1]["visibility"] == "all"


def test_stream_guarda_a_forma_que_a_transcricao_desembrulha():
    client, redis, pid = _joined()
    _send(client, pid)
    msg = [s for s in redis.streams if s["type"] == "message"]
    assert len(msg) == 1
    payload = json.loads(msg[0]["payload"])
    assert payload["content"] == {"type": "text", "text": "confirme o CPF antes"}
    # o mesmo id no stream e na entrega: o Console deduplica por ele
    assert payload["message_id"] == msg[0]["event_id"] == redis.published[0][1]["message_id"]


def test_pubsub_fora_nao_derruba_o_registro():
    client, redis, pid = _joined(publish_fails=True)
    r = _send(client, pid)
    assert r.status_code == 200, r.text
    assert [s["type"] for s in redis.streams].count("message") == 1
    assert redis.published == []
