"""
test_speech_transcript.py — VOZ-05 fatia 4: a fala transcrita da chamada entra na sessão como
MENSAGEM do falante, com a marca de origem, e não aparece como digitada.

Decisões do dono (2026-09-16):
  1. a fala transcrita do CLIENTE não vai ao Console do humano (ele a ouviu);
  2. visibilidade `all` com a marca de fala, e nunca reenviada ao cliente;
  3. a fala do HUMANO acorda os steps `receive` pelo mesmo caminho da mensagem digitada
     (`conversations.events` / `message_sent`).
"""
from __future__ import annotations

import json

import pytest

import plughub_orchestrator_bridge.main as bridge_mod

SID = "sess-voz05-4"


def _inbound(autor: dict, texto: str, *, falado: bool = True) -> dict:
    msg = {"session_id": SID, "contact_id": "c-1", "message_id": "m-1",
           "timestamp": "2026-09-16T12:00:00Z", "channel": "webrtc", "author": autor,
           "content": {"type": "text", "text": texto,
                       "payload": {"confidence": 0.93, "start_ms": 100, "end_ms": 1900}}}
    if falado:
        msg["content_type"] = "audio_transcript"
    return msg


class _Redis:
    def __init__(self, *, human: bool = False, roster: list | None = None):
        self.human = human
        self.roster = roster
        self.xadds: list[tuple[str, dict]] = []
        self.publishes: list[tuple[str, str]] = []
        self.lpushes: list = []

    async def get(self, key):
        if key.endswith(":human_agent"):
            return "1" if self.human else None
        if key.endswith(":participants"):
            return json.dumps(self.roster) if self.roster is not None else None
        return None

    async def hgetall(self, key):
        return {}

    async def xinfo_groups(self, key):
        return []

    async def xadd(self, key, fields, **kw):
        self.xadds.append((key, fields))

    async def expire(self, key, ttl):
        pass

    async def publish(self, channel, data):
        self.publishes.append((channel, data))

    async def lpush(self, key, value):
        self.lpushes.append((key, value))


class _Producer:
    def __init__(self):
        self.sent: list[tuple[str, dict, bytes | None]] = []

    async def send_and_wait(self, topic, value, key=None):
        self.sent.append((topic, json.loads(value), key))


@pytest.fixture
def producer(monkeypatch):
    p = _Producer()
    monkeypatch.setattr(bridge_mod, "_kafka_producer", p)

    async def _sem_receive(**kw):
        return 0
    monkeypatch.setattr(bridge_mod, "_route_to_receive_waiting", _sem_receive)
    return p


def _payload(fields: dict) -> dict:
    return json.loads(fields["payload"])


class TestSpeechMeta:
    def test_texto_digitado_nao_tem_marca(self):
        assert bridge_mod.speech_meta(_inbound({"type": "customer"}, "oi", falado=False)) == ("text", None)

    def test_fala_leva_marca_confianca_e_janela(self):
        assert bridge_mod.speech_meta(_inbound({"type": "customer"}, "oi")) == (
            "audio_transcript", {"confidence": 0.93, "start_ms": 100, "end_ms": 1900})


class TestFalaDoCliente:
    async def test_com_humano_vai_ao_stream_marcada_e_nao_ao_console(self, producer):
        r = _Redis(human=True)
        await bridge_mod.process_inbound(_inbound({"type": "customer"}, "quero cancelar"), r)
        assert r.publishes == []                                   # decisão 1
        [(_, f)] = r.xadds
        conteudo = _payload(f)["content"]
        assert conteudo["type"] == "audio_transcript" and conteudo["text"] == "quero cancelar"
        assert conteudo["speech"]["confidence"] == 0.93
        [ev] = [v for t, v, _ in producer.sent if t == "conversations.events"]
        assert ev["content_type"] == "audio_transcript" and ev["author_role"] == "customer"

    async def test_controle_texto_digitado_com_humano_vai_ao_console(self, producer):
        # sem este, o anterior passaria com um bridge que nunca publica no Console
        r = _Redis(human=True)
        await bridge_mod.process_inbound(_inbound({"type": "customer"}, "quero cancelar", falado=False), r)
        assert [c for c, _ in r.publishes] == [f"agent:events:{SID}"]
        assert _payload(r.xadds[0][1])["content"] == {"type": "text", "text": "quero cancelar"}
        [ev] = [v for t, v, _ in producer.sent if t == "conversations.events"]
        assert ev["content_type"] == "text"


class TestFalaDoAtendente:
    async def test_vira_mensagem_do_humano_com_papel_do_roster(self, producer):
        r = _Redis(roster=[{"participant_id": "human-sub-1", "role": "specialist"}])
        await bridge_mod.process_inbound(
            _inbound({"type": "agent_human", "id": "human-sub-1"}, "vou verificar sua fatura"), r)
        [(chave, f)] = r.xadds
        assert chave == f"session:{SID}:stream"
        assert f["author_id"] == "human-sub-1" and f["author_role"] == "specialist"
        assert json.loads(f["author"]) == {"participant_id": "human-sub-1", "instance_id": "human-sub-1",
                                           "role": "specialist"}
        assert json.loads(f["visibility"]) == "all"                 # decisão 2
        conteudo = _payload(f)["content"]
        assert conteudo["type"] == "audio_transcript" and conteudo["text"] == "vou verificar sua fatura"
        # decisão 3: o mesmo evento da mensagem digitada, com a chave da sessão (ordem)
        [(topico, ev, key)] = producer.sent
        assert topico == "conversations.events" and key == SID.encode()
        assert ev["event_type"] == "message_sent" and ev["author_id"] == "human-sub-1"
        assert ev["author_role"] == "specialist" and ev["content_type"] == "audio_transcript"
        assert ev["content"] == "vou verificar sua fatura" and ev["visibility"] == "all"
        # nunca ao Console do humano nem ao cliente
        assert r.publishes == [] and all(t != "conversations.outbound" for t, _, _ in producer.sent)

    async def test_fora_do_roster_cai_em_primary_e_diz(self, producer, caplog):
        r = _Redis(roster=[])
        with caplog.at_level("WARNING"):
            await bridge_mod.process_inbound(_inbound({"type": "agent_human", "id": "human-sub-2"}, "ok"), r)
        assert r.xadds[0][1]["author_role"] == "primary"
        assert "roster sem human-sub-2" in caplog.text

    @pytest.mark.parametrize("autor,texto", [({"type": "agent_human", "id": "agente-x"}, "oi"),
                                             ({"type": "agent_human", "id": "human-sub-3"}, "   ")])
    async def test_autor_que_nao_e_humano_ou_fala_vazia_nao_grava(self, producer, autor, texto):
        r = _Redis()
        await bridge_mod.process_inbound(_inbound(autor, texto), r)
        assert r.xadds == [] and producer.sent == []

    async def test_mensagem_de_humano_nao_falada_segue_ignorada_no_inbound(self, producer):
        # o humano DIGITA pelo WebSocket do mcp-server; `conversations.inbound` com autor humano
        # e sem a marca continua sem efeito aqui
        r = _Redis()
        await bridge_mod.process_inbound(
            _inbound({"type": "agent_human", "id": "human-sub-1"}, "oi", falado=False), r)
        assert r.xadds == [] and producer.sent == []


class _RedisMenu(_Redis):
    """Um menu de IA voltado ao cliente esperando na sessão."""
    async def hgetall(self, key):
        if key.startswith("menu:waiting:"):
            return {"inst-1": json.dumps({"visibility": "all"})}
        return {}


class TestFalaNaoRespondeMenu:
    """VOZ-05 fatia 5b (decisão 5): a fala transcrita é registro; quem responde menu por voz é a
    coleta do canal, com `menu_result`."""

    async def test_fala_nao_vai_ao_menu_mas_fica_no_stream(self, producer):
        r = _RedisMenu()
        await bridge_mod.process_inbound(_inbound({"type": "customer"}, "espera um pouco"), r)
        assert [k for k, _ in r.lpushes if k.startswith("menu:result:")] == []
        assert any(f.get("content_type") == "audio_transcript" or "audio_transcript" in json.dumps(f)
                   for _, f in r.xadds), r.xadds

    async def test_controle_texto_digitado_responde_o_menu(self, producer):
        r = _RedisMenu()
        await bridge_mod.process_inbound(_inbound({"type": "customer"}, "segunda via", falado=False), r)
        assert (f"menu:result:{SID}:inst-1", "segunda via") in r.lpushes

    async def test_controle_valor_da_coleta_responde_o_menu(self, producer):
        r = _RedisMenu()
        msg = {"session_id": SID, "contact_id": "c-1", "message_id": "m-2", "channel": "webrtc",
               "author": {"type": "customer"},
               "content": {"type": "menu_result",
                           "payload": {"menu_id": "m1", "interaction": "button", "result": "email"}}}
        await bridge_mod.process_inbound(msg, r)
        assert (f"menu:result:{SID}:inst-1", "email") in r.lpushes
