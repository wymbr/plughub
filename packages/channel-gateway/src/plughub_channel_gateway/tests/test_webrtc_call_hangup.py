"""
test_webrtc_call_hangup.py — WCH-07 (2026-09-22): o ATENDENTE desliga a chamada, não o contato.

Proposições, cada uma com o controle ao lado:
  · a rota só aceita quem ATENDE o contato (VOZ-15), só em contato `webchat` com chamada em curso,
    e o portão de capacidade é o MESMO do token de mídia (uma casa);
  · a rota não encerra nada: grava o pedido no stream, com o autor;
  · o observador da chamada honra o pedido POSTERIOR ao seu início — o de uma chamada anterior do
    mesmo contato, relido no replay desde o início do stream, NÃO encerra a chamada nova;
  · `last_call_state` lê o último `media.call`, atravessando páginas.
"""
from __future__ import annotations

import json
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from plughub_channel_gateway import main as cg_main
from plughub_channel_gateway.adapters.webrtc import WebRTCAdapter
from .test_webrtc_adapter import _assigned, _fake_ws
from .test_webrtc_call import _Setup, CONTACT

SECRET = "segredo-de-teste-hs256"
POOL = "atendimento_humano"
SID = "sessao-de-teste"
ATENDER = {"agent_assist": {"atender": {"access": "read_write", "scope": [POOL]}}}


def _req(sub: str, mc: dict = ATENDER):
    tok = pyjwt.encode({"sub": sub, "tenant_id": "tenant_a", "module_config": mc,
                        "exp": int(time.time()) + 600}, SECRET, algorithm="HS256")
    return SimpleNamespace(headers={"authorization": "Bearer " + tok})


@pytest.fixture
def adapter(monkeypatch):
    for alvo in ("attendant_ids", "last_call_state", "request_agent_hangup"):
        assert hasattr(WebRTCAdapter, alvo), f"o alvo mockado {alvo} sumiu do adapter"
    a = MagicMock()
    a.provider_unavailable = None
    a._redis = MagicMock()
    a._redis.get = AsyncMock(return_value=json.dumps(
        {"tenant_id": "tenant_a", "pool_id": POOL, "channel": "webchat"}))
    a.attendant_ids = AsyncMock(return_value={"human-ana"})
    a.last_call_state = AsyncMock(return_value="started")
    a.request_agent_hangup = AsyncMock()
    monkeypatch.setattr(cg_main, "_webrtc_adapter", a)
    monkeypatch.setattr(cg_main.get_settings(), "auth_jwt_secret", SECRET, raising=False)
    return a


class TestRota:
    async def test_quem_atende_pede_e_o_pedido_vai_ao_stream_com_o_autor(self, adapter):
        r = await cg_main.webrtc_call_end(SID, _req("ana"))
        assert r == {"requested": True, "session_id": SID}
        adapter.request_agent_hangup.assert_awaited_once_with(SID, "human-ana")

    async def test_mesmo_grant_sem_atender_403(self, adapter):
        with pytest.raises(HTTPException) as e:
            await cg_main.webrtc_call_end(SID, _req("bruno"))
        assert e.value.status_code == 403
        adapter.request_agent_hangup.assert_not_awaited()

    async def test_sem_credencial_401_e_sem_capacidade_403(self, adapter):
        with pytest.raises(HTTPException) as e:
            await cg_main.webrtc_call_end(SID, SimpleNamespace(headers={}))
        assert e.value.status_code == 401
        with pytest.raises(HTTPException) as e:
            await cg_main.webrtc_call_end(SID, _req("ana", {}))
        assert e.value.status_code == 403
        adapter.request_agent_hangup.assert_not_awaited()

    async def test_canal_webrtc_409_not_a_chat_call(self, adapter):
        adapter._redis.get.return_value = json.dumps(
            {"tenant_id": "tenant_a", "pool_id": POOL, "channel": "webrtc"})
        with pytest.raises(HTTPException) as e:
            await cg_main.webrtc_call_end(SID, _req("ana"))
        assert e.value.status_code == 409 and e.value.detail["code"] == "not_a_chat_call"

    @pytest.mark.parametrize("estado", ["ended", ""])
    async def test_sem_chamada_em_curso_409(self, adapter, estado):
        adapter.last_call_state.return_value = estado
        with pytest.raises(HTTPException) as e:
            await cg_main.webrtc_call_end(SID, _req("ana"))
        assert e.value.status_code == 409 and e.value.detail["code"] == "no_active_call"
        adapter.request_agent_hangup.assert_not_awaited()


# ── O observador honra o pedido — só o posterior ao seu início ────────────────

REQ = {"type": "media.call.end_requested", "author_id": "human-ana"}


class TestObservador(_Setup):
    async def _run(self, batches, tail="1-9"):
        seq = [[("k", [(f"{i}-{j}", f) for j, f in enumerate(lote)])] for i, lote in enumerate(batches, 1)]
        seq.append([("k", [("999-0", {"type": "session_closed"})])])
        self.redis.xread.side_effect = seq
        self.redis.xrevrange = AsyncMock(return_value=[(tail, {})])

        async def _pool_field(sid, fields):
            return {**fields, "pool": json.dumps({"pool_id": "humano", "media_policy_source": "registry",
                                                  "media_policy": {"customer_publish": ["audio"],
                                                                   "agent_publish": ["audio"]}})}
        self.adapter._attached_pool_field = _pool_field
        self.adapter._attached.add(self.sid)
        await self.redis.setex(f"session:{self.sid}:contact_id", 3600, CONTACT)
        self.ws = _fake_ws()
        await self.adapter._call_stream_watcher(self.ws, self.sid)
        return self.ws.sent_messages[-1]

    @pytest.mark.asyncio
    async def test_pedido_depois_do_inicio_encerra_com_agent_hangup(self):
        fim = await self._run([[_assigned("human", "human-ana", source="not_webrtc", pool_id="humano")],
                               [REQ]])
        assert fim == {"type": "webrtc.call_ended", "reason": "agent_hangup"}
        assert self.adapter._call_end_reason[self.sid] == "agent_hangup"

    @pytest.mark.asyncio
    async def test_controle_pedido_de_chamada_ANTERIOR_nao_encerra_a_nova(self):
        # o pedido velho está em "1-1", antes do fim do stream no início ("1-9")
        fim = await self._run([[_assigned("human", "human-ana", source="not_webrtc", pool_id="humano"), REQ]])
        assert fim == {"type": "webrtc.call_ended", "reason": "contact_closed"}


class TestUltimoEstado(_Setup):
    @pytest.mark.asyncio
    async def test_le_o_ultimo_media_call_atravessando_paginas(self):
        pagina1 = [(f"{900 - i}-0", {"type": "message"}) for i in range(200)]
        pagina2 = [("100-0", {"type": "media.call", "state": "started"}),
                   ("90-0", {"type": "media.call", "state": "ended"})]
        self.redis.xrevrange = AsyncMock(side_effect=[pagina1, pagina2])
        assert await self.adapter.last_call_state(self.sid) == "started"
        # a segunda página começa EXCLUSIVA depois da última da primeira
        assert self.redis.xrevrange.await_args_list[1].kwargs["max"] == "(701-0"

    @pytest.mark.asyncio
    async def test_sem_media_call_e_vazio(self):
        self.redis.xrevrange = AsyncMock(side_effect=[[("5-0", {"type": "message"})], []])
        assert await self.adapter.last_call_state(self.sid) == ""

    @pytest.mark.asyncio
    async def test_estado_so_no_payload(self):
        self.redis.xrevrange = AsyncMock(return_value=[
            ("5-0", {"type": "media.call", "payload": json.dumps({"state": "ended"})})])
        assert await self.adapter.last_call_state(self.sid) == "ended"
