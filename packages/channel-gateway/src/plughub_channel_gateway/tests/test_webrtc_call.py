"""
WCH-01 — a chamada que se prende a um contato de chat (`adapters/webrtc_call.py`).

Proposições julgadas, cada uma com o seu controle:
  * só o DONO de um contato `webchat` aberto, sem outra chamada, prende uma chamada;
  * a sala nasce dos atendentes de AGORA (replay do stream), não do primeiro `routing.assigned`;
  * sem atendente que ofereça mídia não há sala — e isso é dito (`webrtc.call_pending`);
  * a queda da chamada NÃO encerra o contato; o fim do contato encerra a chamada;
  * a conexão de chamada não tem caminho de texto;
  * sem bot leg: o agente de IA não consome áudio numa chamada de chat.
"""
from __future__ import annotations

import ast
import json
import pathlib
import uuid

import jwt as pyjwt
import pytest

import plughub_channel_gateway.main as gw_main
from ..adapters import webrtc_call
from ..adapters.webrtc_call import CallRefused
from ..adapters.webrtc_provider import MockWebRTCProvider
from .test_webrtc_adapter import _assigned, _fake_redis, _fake_settings, _fake_ws, _make_adapter

SECRET = "changeme_32chars_webchat_secret!"
TENANT = "default"
CONTACT = "c-1"


def _token(sub=CONTACT, **extra):
    return pyjwt.encode({"sub": sub, **extra}, SECRET, algorithm="HS256")


def _hs(token, session_id):
    return [json.dumps({"type": "conn.hello", "version": "1"}),
            json.dumps({"type": "conn.authenticate", "token": token, "session_id": session_id})]


class _Setup:
    def setup_method(self):
        self.provider = MockWebRTCProvider()
        self.redis = _fake_redis()
        self.redis.xadd = _Rec(self, "xadds")
        self.redis.publish = _Rec(self, "publishes")
        self.xadds, self.publishes = [], []
        self.settings = _fake_settings(webrtc_stt_enabled=False)
        self.adapter = _make_adapter(provider=self.provider, redis=self.redis, settings=self.settings)
        self.sid = str(uuid.uuid4())

    async def _chat(self, channel="webchat", contact=CONTACT, tenant=TENANT):
        await self.redis.setex(f"session:{self.sid}:meta", 3600, json.dumps(
            {"tenant_id": tenant, "contact_id": contact, "channel": channel, "pool_id": "chat_pool"}))
        await self.redis.setex(f"session:{self.sid}:contact_id", 3600, contact)


class _Rec:
    def __init__(self, owner, attr):
        self.owner, self.attr = owner, attr

    async def __call__(self, *a, **k):
        getattr(self.owner, self.attr).append(a)


# ── A porta ───────────────────────────────────────────────────────────────────

class TestHandshake(_Setup):
    @pytest.mark.asyncio
    async def test_dono_de_contato_de_chat_aberto_passa(self):
        await self._chat()
        sid = await self.adapter._call_handshake(_fake_ws(_hs(_token(), self.sid)))
        assert sid == self.sid

    @pytest.mark.asyncio
    async def test_sessao_do_claim_serve_sem_session_id_na_mensagem(self):
        await self._chat()
        sid = await self.adapter._call_handshake(_fake_ws(_hs(_token(session_id=self.sid), "")))
        assert sid == self.sid

    @pytest.mark.parametrize("caso,esperado", [
        ("outro_cliente", "session_not_found"),
        ("outro_tenant", "session_not_found"),
        ("inexistente", "session_not_found"),
        ("claim_diverge", "session_not_found"),
        ("canal_webrtc", "not_a_chat_contact"),
        ("fechado", "contact_closed"),
        ("ja_tem_chamada", "call_already_active"),
        ("token_ruim", "invalid_token"),
    ])
    @pytest.mark.asyncio
    async def test_recusas(self, caso, esperado):
        token, sid = _token(), self.sid
        if caso == "outro_cliente":
            await self._chat(contact="c-OUTRO")
        elif caso == "outro_tenant":
            await self._chat(tenant="outro")
        elif caso == "inexistente":
            pass
        elif caso == "claim_diverge":
            await self._chat()
            token = _token(session_id=str(uuid.uuid4()))
        elif caso == "canal_webrtc":
            await self._chat(channel="webrtc")
        elif caso == "fechado":
            await self._chat()
            await self.redis.setex(f"session:{sid}:closed_recorded", 60, "1")
        elif caso == "ja_tem_chamada":
            await self._chat()
            self.adapter._connections[sid] = object()
        elif caso == "token_ruim":
            await self._chat()
            token = pyjwt.encode({"sub": CONTACT}, "outro-segredo-de-32-caracteres!!", algorithm="HS256")
        with pytest.raises(CallRefused) as exc:
            await self.adapter._call_handshake(_fake_ws(_hs(token, sid)))
        assert exc.value.code == esperado

    @pytest.mark.asyncio
    async def test_sem_plano_de_midia_recusa_antes_de_autenticar(self):
        self.adapter._provider = None
        ws = _fake_ws(_hs(_token(), self.sid))
        await self.adapter.handle_call_ws(ws)
        assert ws.sent_messages[0]["code"] == "media_plane_unavailable"
        assert self.sid not in self.adapter._attached


# ── Os atendentes de agora ────────────────────────────────────────────────────

def _left(who):
    return {"type": "participant_left", "author_id": who}


class TestWatcher(_Setup):
    def _stream(self, *batches):
        """Cada lote é uma resposta do XREAD; o último fecha o contato para o laço terminar."""
        seq = [[("k", [(f"{i}-{j}", f) for j, f in enumerate(lote)])] for i, lote in enumerate(batches, 1)]
        seq.append([("k", [("999-0", {"type": "session_closed"})])])
        self.redis.xread.side_effect = seq

    async def _run(self):
        async def _pool_field(sid, fields):     # o registry responde a política pelo pool
            pool = json.loads(fields["pool"])
            if pool.get("media_policy_source") != "not_webrtc":
                return fields
            politicas = {"humano": {"customer_publish": ["audio"], "agent_publish": ["audio"]},
                         "fila_ia": {"customer_publish": ["audio"], "agent_publish": []}}
            return {**fields, "pool": json.dumps({"pool_id": pool["pool_id"], "media_policy_source": "registry",
                                                  "media_policy": politicas.get(pool["pool_id"])})}
        self.adapter._attached_pool_field = _pool_field
        self.adapter._attached.add(self.sid)
        await self.redis.setex(f"session:{self.sid}:contact_id", 3600, CONTACT)
        self.ws = _fake_ws()
        await self.adapter._call_stream_watcher(self.ws, self.sid)
        return [m["type"] for m in self.ws.sent_messages]

    @pytest.mark.asyncio
    async def test_primeiro_atendente_que_ja_saiu_nao_decide(self):
        """O caso que o `_stream_watcher` do canal erraria: IA de fila entra e sai, humano entra."""
        self._stream([_assigned("native", "ia1", source="not_webrtc", pool_id="fila_ia"),
                      _left("ia1"),
                      _assigned("human", "human-u1", source="not_webrtc", pool_id="humano")])
        tipos = await self._run()
        assert tipos.count("webrtc.ready") == 1
        ready = next(m for m in self.ws.sent_messages if m["type"] == "webrtc.ready")
        assert ready["publish"] == ["audio"] and ready["policy_sources"] == ["pool:humano"]
        assert len(self.provider.rooms_created) == 1
        started = [f for (k, f) in self.xadds if f.get("type") == "media.call"]
        assert [f["state"] for f in started] == ["started"]
        assert any(ch == f"agent:events:{self.sid}" for ch, *_ in self.publishes)
        assert tipos[-1] == "webrtc.call_ended"

    @pytest.mark.asyncio
    async def test_so_ia_nao_monta_sala_e_diz_por_que(self):
        """Sem bot leg nesta fatia, a IA não consome áudio: nada a oferecer, nenhuma sala."""
        self._stream([_assigned("native", "ia1", source="not_webrtc", pool_id="fila_ia")])
        tipos = await self._run()
        assert "webrtc.ready" not in tipos and "webrtc.call_pending" in tipos
        assert self.provider.rooms_created == []
        assert self.xadds == []                   # nenhum `media.call`: não houve chamada

    @pytest.mark.asyncio
    async def test_espera_e_monta_quando_o_humano_chega(self):
        self._stream([_assigned("native", "ia1", source="not_webrtc", pool_id="fila_ia")],
                     [_assigned("human", "human-u1", source="not_webrtc", pool_id="humano")])
        tipos = await self._run()
        assert tipos.index("webrtc.call_pending") < tipos.index("webrtc.ready")

    @pytest.mark.asyncio
    async def test_fim_do_contato_encerra_a_chamada(self):
        self._stream([])
        await self._run()
        assert self.ws.sent_messages[-1] == {"type": "webrtc.call_ended", "reason": "contact_closed"}
        assert self.adapter._call_end_reason[self.sid] == "contact_closed"


# ── Política do pool lida só quando há chamada ────────────────────────────────

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


class _Client:
    resposta: object = None
    urls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        _Client.urls.append(url)
        if isinstance(_Client.resposta, Exception):
            raise _Client.resposta
        return _Client.resposta


class TestPoolField(_Setup):
    @pytest.fixture(autouse=True)
    def _httpx(self, monkeypatch):
        monkeypatch.setattr(webrtc_call.httpx, "AsyncClient", _Client)
        _Client.urls = []
        self.settings.agent_registry_url = "http://registry:3300"
        self.settings.agent_registry_service_token = ""

    @pytest.mark.asyncio
    async def test_le_a_politica_do_registry(self):
        _Client.resposta = _Resp(200, {"media_policy": {"customer_publish": ["audio"]}})
        out = await self.adapter._attached_pool_field(self.sid, _assigned("human", "h", source="not_webrtc", pool_id="humano"))
        pool = json.loads(out["pool"])
        assert pool == {"pool_id": "humano", "media_policy": {"customer_publish": ["audio"]},
                        "media_policy_source": "registry"}
        assert _Client.urls == ["http://registry:3300/v1/pools/humano"]

    @pytest.mark.parametrize("resp", [_Resp(503, {}), ConnectionError("fora")])
    @pytest.mark.asyncio
    async def test_registry_fora_nao_vira_permissao(self, resp):
        _Client.resposta = resp
        out = await self.adapter._attached_pool_field(self.sid, _assigned("human", "h", source="not_webrtc", pool_id="humano"))
        assert json.loads(out["pool"])["media_policy_source"] == "registry_unavailable"

    @pytest.mark.asyncio
    async def test_evento_ja_com_politica_nao_consulta(self):
        _Client.resposta = AssertionError("nao devia consultar")
        ev = _assigned("human", "h", pool_id="humano")
        assert await self.adapter._attached_pool_field(self.sid, ev) is ev


# ── Fim da chamada, nunca do contato ──────────────────────────────────────────

class TestEnd(_Setup):
    @pytest.mark.asyncio
    async def test_encerra_so_a_chamada(self):
        self.adapter._attached.add(self.sid)
        self.adapter._call_started.add(self.sid)
        await self.redis.setex(f"channel:webrtc:{self.sid}:room_name", 60, "sala")
        await self.redis.setex(f"session:{self.sid}:ws_alive", 60, "1")
        ws = _fake_ws()
        self.adapter._connections[self.sid] = ws
        await self.adapter._end_attached_call(self.sid, "customer_disconnect")
        assert not [c for c in self.adapter._producer.send.await_args_list    # nenhum contact_closed
                    if c.args and c.args[0] != "media.calls"]
        assert await self.redis.get(f"session:{self.sid}:ws_alive") == "1"   # é do webchat
        assert await self.redis.get(f"channel:webrtc:{self.sid}:room_name") is None
        assert self.provider.rooms_deleted
        ended = [f for (k, f) in self.xadds if f.get("type") == "media.call"]
        assert [(f["state"], f["reason"]) for f in ended] == [("ended", "customer_disconnect")]
        await self.adapter._end_attached_call(self.sid, "de novo")      # idempotente
        assert len(self.xadds) == 1

    @pytest.mark.asyncio
    async def test_chamada_que_nunca_montou_nao_anuncia_fim(self):
        self.adapter._attached.add(self.sid)
        await self.adapter._end_attached_call(self.sid, "customer_hangup")
        assert self.xadds == [] and self.provider.rooms_deleted == []


class TestReceive(_Setup):
    @pytest.mark.asyncio
    async def test_texto_e_recusado_e_desligar_e_dito(self):
        ws = _fake_ws([json.dumps({"type": "webrtc.message", "text": "oi"}),
                       json.dumps({"type": "webrtc.hangup"})])
        await self.adapter._call_receive_loop(ws, self.sid)
        assert ws.sent_messages[0]["code"] == "text_goes_through_chat"
        assert self.adapter._call_end_reason[self.sid] == "customer_hangup"
        self.adapter._producer.send.assert_not_awaited()


# ── Sem bot leg nesta fatia ───────────────────────────────────────────────────

class TestSemBotLeg(_Setup):
    def _estado(self):
        rec = {"framework": "native", "pool_id": "p", "customer_publish": ["audio"],
               "agent_publish": [], "policy_source": "pool:p", "recording": False}
        return {"attendants": {"ia1": rec}}

    def test_ia_nao_consome_audio_em_chamada_de_chat(self):
        self.adapter._convert_available = lambda: True
        st = self._estado()
        assert self.adapter._ceiling(st, "canal") == frozenset({"audio"})      # controle: canal webrtc
        self.adapter._attached.add(self.sid)
        assert self.adapter._ceiling(st, self.sid) == frozenset()
        assert self.adapter._bot_leg_should_run(st, frozenset({"audio"}), self.sid) is False
        assert self.adapter._decide_voice(self.sid, st) is False
        assert self.adapter._voice_absent_why[self.sid] == webrtc_call.NO_BOT_LEG_REASON


def test_rota_ligada():
    tree = ast.parse(pathlib.Path(gw_main.__file__).read_text(encoding="utf-8"))
    rota = [f for f in ast.walk(tree) if isinstance(f, ast.AsyncFunctionDef)
            and any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "websocket"
                    and d.args and getattr(d.args[0], "value", "") == "/ws/call" for d in f.decorator_list)]
    assert len(rota) == 1
    chamadas = [n for n in ast.walk(rota[0]) if isinstance(n, ast.Call)
                and getattr(n.func, "attr", "") == "handle_call_ws"]
    assert len(chamadas) == 1


def test_bloqueio_do_xread_abaixo_do_timeout_do_socket():
    """Medido na WCH-01: com bloqueio = timeout do socket, toda leitura ociosa estourava (28 WARNING
    numa chamada de 3 min). O limite é o default do cliente que o gateway usa (`from_url` sem opções)."""
    import redis.asyncio as aioredis
    from ..adapters.webrtc import _STREAM_BLOCK_MS
    kw = aioredis.from_url("redis://x:6379").connection_pool.connection_kwargs
    timeout_s = kw.get("socket_timeout") or kw.get("orig_socket_timeout")
    assert timeout_s, kw
    assert _STREAM_BLOCK_MS < timeout_s * 1000
