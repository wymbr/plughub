"""
test_call_events.py — WCH-02 (relatórios, 2026-09-22): o intervalo da chamada vai a `media.calls`.

Proposições, cada uma com o controle ao lado:
  · a chamada que MONTOU publica início e fim, com o MESMO `call_id` (= id da entrada
    `media.call started` no stream), chave = `session_id`, pool de quem ATENDE e o tenant da SESSÃO;
  · o fim carrega a linha inteira do início (o destino é `ReplacingMergeTree`) mais duração e motivo;
  · chamada que nunca montou não publica nada — nem início nem fim;
  · sem id de stream não há `call_id`: nada é publicado, e isso é DITO (um id inventado daria dois
    nomes à mesma chamada);
  · falha de publicação nunca levanta.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

import pytest

from .. import call_events
from .test_webrtc_adapter import _assigned, _fake_ws
from .test_webrtc_call import CONTACT, _Setup

TENANT_DA_SESSAO = "tenant_da_sessao"


class _Xadd:
    """XADD que devolve id como o Redis — o `call_id` nasce dele."""

    def __init__(self, owner, falhar=False):
        self.owner, self.falhar, self.n = owner, falhar, 0

    async def __call__(self, key, fields, *a, **k):
        if self.falhar:
            raise ConnectionError("redis fora")
        self.n += 1
        self.owner.xadds.append((key, fields))
        return f"{1000 + self.n}-0".encode()


def _media_calls(adapter):
    out = []
    for c in adapter._producer.send.await_args_list:
        if c.args and c.args[0] == call_events.TOPIC:
            out.append((c.kwargs["key"], json.loads(c.kwargs["value"])))
    return out


class _Chamada(_Setup):
    async def _montar(self, *lotes):
        seq = [[("k", [(f"{i}-{j}", f) for j, f in enumerate(lote)])] for i, lote in enumerate(lotes, 1)]
        seq.append([("k", [("999-0", {"type": "session_closed"})])])
        self.redis.xread.side_effect = seq

        async def _pool_field(sid, fields):
            pool = json.loads(fields["pool"])
            politicas = {"humano": {"customer_publish": ["audio"], "agent_publish": ["audio"]},
                         "fila_ia": {"customer_publish": ["audio"], "agent_publish": []}}
            return {**fields, "pool": json.dumps({"pool_id": pool["pool_id"], "media_policy_source": "registry",
                                                  "media_policy": politicas.get(pool["pool_id"])})}
        self.adapter._attached_pool_field = _pool_field
        self.adapter._attached.add(self.sid)
        await self.redis.setex(f"session:{self.sid}:contact_id", 3600, CONTACT)
        await self.redis.setex(f"session:{self.sid}:meta", 3600, json.dumps(
            {"tenant_id": TENANT_DA_SESSAO, "contact_id": CONTACT, "channel": "webchat"}))
        await self.adapter._call_stream_watcher(_fake_ws(), self.sid)


class TestIntervalo(_Chamada):
    @pytest.mark.asyncio
    async def test_inicio_e_fim_com_o_mesmo_call_id(self):
        self.redis.xadd = _Xadd(self)
        await self._montar([_assigned("human", "human-ana", source="not_webrtc", pool_id="humano")])
        await self.adapter._end_attached_call(self.sid, self.adapter._call_end_reason.pop(self.sid))

        evs = _media_calls(self.adapter)
        assert [e["event_type"] for _, e in evs] == ["call_started", "call_ended"]
        assert all(k == self.sid.encode() for k, _ in evs)          # ordem por partição
        ini, fim = evs[0][1], evs[1][1]

        media = [f for (_, f) in self.xadds if f.get("type") == "media.call"]
        assert [f["state"] for f in media] == ["started", "ended"]
        assert ini["call_id"] == fim["call_id"] == "1001-0"          # a entrada `started` no stream

        assert ini["tenant_id"] == TENANT_DA_SESSAO                   # da SESSÃO, não do processo
        assert ini["pool_id"] == "humano" and ini["customer_publish"] == ["audio"]
        assert ini["channel"] == "webchat" and ini["session_id"] == self.sid
        for campo in ("tenant_id", "session_id", "call_id", "channel", "pool_id",
                      "customer_publish", "started_at"):
            assert fim[campo] == ini[campo], campo                    # linha inteira no fim
        assert fim["end_reason"] == "contact_closed"
        assert fim["duration_ms"] >= 0 and fim["ended_at"] >= fim["started_at"]
        assert ini["event_id"] != fim["event_id"]
        assert self.sid not in self.adapter._call_meta                # não vaza entre chamadas

    @pytest.mark.asyncio
    async def test_controle_so_ia_nao_publica_nada(self):
        self.redis.xadd = _Xadd(self)
        await self._montar([_assigned("native", "ia1", source="not_webrtc", pool_id="fila_ia")])
        await self.adapter._end_attached_call(self.sid, "contact_closed")
        assert _media_calls(self.adapter) == []

    @pytest.mark.asyncio
    async def test_sem_id_de_stream_nao_inventa_call_id_e_diz(self, caplog):
        self.redis.xadd = _Xadd(self, falhar=True)
        with caplog.at_level(logging.WARNING):
            await self._montar([_assigned("human", "human-ana", source="not_webrtc", pool_id="humano")])
            await self.adapter._end_attached_call(self.sid, "contact_closed")
        assert _media_calls(self.adapter) == []
        assert "intervalo NAO publicado" in caplog.text
        assert "intervalo NAO fechado" in caplog.text


class TestFuncoes:
    def test_pool_de_quem_atende(self):
        assert call_events.pool_from_sources(["registry", "pool:humano", "pool:outro"]) == "humano"
        assert call_events.pool_from_sources(["pool:", "registry"]) is None
        assert call_events.pool_from_sources([]) is None

    def test_duracao_e_motivo_nunca_vazio(self):
        ini = call_events.started(tenant_id="t", session_id="s", call_id="1-0", channel="webchat",
                                  pool_id=None, customer_publish=["audio", "video"],
                                  started_at="2026-09-22T10:00:00+00:00")
        fim = call_events.ended(begun=ini, ended_at="2026-09-22T10:01:30.250000+00:00", end_reason="")
        assert fim["duration_ms"] == 90250 and fim["end_reason"] == "unknown"
        assert fim["pool_id"] is None and fim["customer_publish"] == ["audio", "video"]

    @pytest.mark.asyncio
    async def test_publicar_nunca_levanta(self, caplog):
        ev = {"session_id": "s", "event_type": "call_started", "call_id": "1-0"}
        with caplog.at_level(logging.WARNING):
            assert await call_events.publish(None, ev) is False
            prod = AsyncMock()
            prod.send.side_effect = RuntimeError("kafka fora")
            assert await call_events.publish(prod, ev) is False
        assert caplog.text.count("NAO publicado") == 2
