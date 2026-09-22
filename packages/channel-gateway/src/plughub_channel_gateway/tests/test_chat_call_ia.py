"""
test_chat_call_ia.py — WCH-02 (2026-09-22): a IA na chamada presa a um contato de chat.

Proposições, cada uma com o controle ao lado:
  · a chamada pronta põe ouvinte e voz na sala quando o atendente é IA de áudio, e só o ouvinte
    quando é humano;
  · a fala transcrita volta à sessão como mensagem do canal do CONTATO (`webchat`), marcada;
  · o que o agente de IA escreve no chat é também FALADO — texto do humano e aviso de sistema não;
    sem chamada pronta, nada é falado; o prompt de menu é falado, ou vira coleta quando o menu a
    declara;
  · o consumidor de saída chama o gancho ANTES de entregar ao chat, e só para `webchat`;
  · o chat do cliente não mostra fala transcrita, e mostra o texto;
  · o fim da chamada tira o registro da sessão;
  · o menu que chegou ANTES da chamada é rearmado quando ela fica pronta — só se o motor ainda
    espera, só se é menu ao cliente, e uma vez só (a saída do Kafka não o arma de novo).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ..outbound_consumer import OutboundConsumer
from ..stream_subscriber import StreamSubscriber
from .test_webrtc_adapter import _assigned, _fake_ws
from .test_webrtc_call import CONTACT, _Setup

VOZ = {"customer_publish": ["audio"], "agent_publish": ["audio"]}


def _alvos(adapter, *nomes):
    for n in nomes:
        assert hasattr(type(adapter), n), f"o alvo mockado {n} sumiu do adapter"


class _Pronta(_Setup):
    async def _montar(self, *atendentes):
        seq = [[("k", [(f"1-{j}", f) for j, f in enumerate(atendentes)])],
               [("k", [("999-0", {"type": "session_closed"})])]]
        self.redis.xread.side_effect = seq
        _alvos(self.adapter, "_start_stt_pipeline", "_start_voice")
        self.adapter._start_stt_pipeline = AsyncMock()
        self.adapter._start_voice = AsyncMock()
        self.adapter._convert_available = lambda: True
        self.adapter._stt_unavailable = None
        self.adapter._attached.add(self.sid)
        await self.redis.setex(f"session:{self.sid}:contact_id", 3600, CONTACT)
        await self.redis.setex(f"session:{self.sid}:meta", 3600, json.dumps(
            {"tenant_id": "t", "contact_id": CONTACT, "channel": "webchat", "pool_id": "demo_llm_ia"}))
        await self.adapter._call_stream_watcher(_fake_ws(), self.sid)


class TestSalaPronta(_Pronta):
    @pytest.mark.asyncio
    async def test_ia_de_audio_poe_ouvinte_e_voz_e_registra_a_sessao_como_webchat(self):
        await self._montar(_assigned("native", "ia1", policy=VOZ, pool_id="demo_llm_ia"))
        self.adapter._start_stt_pipeline.assert_called_once()
        self.adapter._start_voice.assert_called_once()
        assert self.sid in self.adapter._call_started
        # o registro sobrevive até o fim da chamada, que aqui não aconteceu
        info = self.adapter._sessions[self.sid]
        assert info["channel"] == "webchat" and info["contact_id"] == CONTACT

    @pytest.mark.asyncio
    async def test_controle_humano_so_ouvinte(self):
        await self._montar(_assigned("human", "human-u1", policy=VOZ, pool_id="retencao_humano"))
        self.adapter._start_stt_pipeline.assert_called_once()
        self.adapter._start_voice.assert_not_called()

    @pytest.mark.asyncio
    async def test_fim_da_chamada_tira_o_registro(self):
        await self._montar(_assigned("native", "ia1", policy=VOZ, pool_id="demo_llm_ia"))
        await self.adapter._end_attached_call(self.sid, "customer_hangup")
        assert self.sid not in self.adapter._sessions


class TestTranscricao(_Setup):
    @pytest.mark.asyncio
    async def test_fala_do_cliente_volta_como_mensagem_webchat_marcada(self):
        self.adapter._sessions[self.sid] = {"contact_id": CONTACT, "pool_id": "p", "channel": "webchat",
                                            "speech_profile_id": None, "started_at": ""}
        _alvos(self.adapter, "_publish_inbound", "_collect_speech")
        self.adapter._publish_inbound = AsyncMock()
        self.adapter._collect_speech = AsyncMock()
        await self.adapter._publish_transcript(self.sid, "quero cancelar", 0.9, 0, 1200)
        (ev,), _ = self.adapter._publish_inbound.await_args
        assert ev["channel"] == "webchat" and ev["content_type"] == "audio_transcript"
        assert ev["contact_id"] == CONTACT and ev["content"]["text"] == "quero cancelar"
        self.adapter._registry.append_message.assert_not_called()      # não vira histórico de chat


class TestFalaDoChat(_Setup):
    def setup_method(self):
        super().setup_method()
        _alvos(self.adapter, "_speak", "_plan_collect", "_start_collect")
        self.adapter._speak = MagicMock()
        self.adapter._start_collect = MagicMock()
        self.adapter._attached.add(self.sid)
        self.adapter._call_started.add(self.sid)

    def _texto(self, autor):
        return {"session_id": self.sid, "text": "posso ajudar?", "author": {"type": autor}}

    def test_texto_da_ia_e_falado(self):
        self.adapter.chat_call_outbound("message.text", self._texto("agent_ai"))
        self.adapter._speak.assert_called_once_with(self.sid, "posso ajudar?")

    @pytest.mark.parametrize("autor", ["agent_human", "system"])
    def test_texto_do_humano_e_do_sistema_nao(self, autor):
        self.adapter.chat_call_outbound("message.text", self._texto(autor))
        self.adapter._speak.assert_not_called()

    def test_sem_chamada_pronta_nada_e_falado(self):
        self.adapter._call_started.discard(self.sid)
        self.adapter.chat_call_outbound("message.text", self._texto("agent_ai"))
        self.adapter._attached.discard(self.sid)
        self.adapter._call_started.add(self.sid)
        self.adapter.chat_call_outbound("message.text", self._texto("agent_ai"))
        self.adapter._speak.assert_not_called()

    def test_menu_sem_coleta_fala_o_prompt(self):
        self.adapter._plan_collect = MagicMock(return_value=None)
        self.adapter.chat_call_outbound("menu.payload", {"session_id": self.sid, "menu_id": "m",
                                                          "prompt": "Diga o que precisa", "interaction": "text"})
        self.adapter._speak.assert_called_once_with(self.sid, "Diga o que precisa")
        self.adapter._start_collect.assert_not_called()

    def test_menu_com_coleta_vira_coleta(self):
        plano = object()
        self.adapter._plan_collect = MagicMock(return_value=plano)
        self.adapter.chat_call_outbound("menu.payload", {"session_id": self.sid, "menu_id": "m",
                                                          "prompt": "x", "interaction": "text"})
        self.adapter._start_collect.assert_called_once_with(self.sid, plano)
        self.adapter._speak.assert_not_called()


class TestConsumidorDeSaida:
    def _consumer(self, ordem):
        webrtc = MagicMock()
        webrtc.chat_call_outbound = MagicMock(side_effect=lambda t, p: ordem.append("fala"))
        webchat = MagicMock()
        webchat.deliver_text = AsyncMock(side_effect=lambda p: ordem.append("chat"))
        whatsapp = MagicMock()
        whatsapp.deliver_text = AsyncMock()
        return OutboundConsumer({"webrtc": webrtc, "webchat": webchat, "whatsapp": whatsapp},
                                MagicMock()), webrtc

    @pytest.mark.asyncio
    async def test_webchat_passa_pela_chamada_antes_do_chat(self):
        ordem: list[str] = []
        c, webrtc = self._consumer(ordem)
        await c._dispatch({"type": "message.text", "channel": "webchat", "contact_id": "c", "session_id": "s"})
        assert ordem == ["fala", "chat"]

    @pytest.mark.asyncio
    async def test_controle_outro_canal_nao_passa(self):
        ordem: list[str] = []
        c, webrtc = self._consumer(ordem)
        await c._dispatch({"type": "message.text", "channel": "whatsapp", "contact_id": "c", "session_id": "s"})
        webrtc.chat_call_outbound.assert_not_called()

    @pytest.mark.asyncio
    async def test_falha_do_gancho_nao_impede_a_entrega_ao_chat(self):
        ordem: list[str] = []
        c, webrtc = self._consumer(ordem)
        webrtc.chat_call_outbound.side_effect = RuntimeError("x")
        await c._dispatch({"type": "message.text", "channel": "webchat", "contact_id": "c", "session_id": "s"})
        assert ordem == ["chat"]


class TestChatDoCliente:
    def _entrada(self, content_type, role):
        return {"type": "message", "event_id": "e1", "timestamp": "t", "author_id": "human-u1",
                "author_role": role, "visibility": json.dumps("all"),
                "author": json.dumps({"participant_id": "human-u1", "instance_id": "human-u1", "role": role}),
                "payload": json.dumps({"message_id": "e1", "content": {"type": content_type, "text": "oi"},
                                       "text": "oi"})}

    def test_fala_transcrita_do_atendente_nao_chega_ao_chat(self):
        sub = StreamSubscriber(redis=MagicMock(), session_id="s")
        assert sub._map_event(self._entrada("audio_transcript", "primary")) is None
        assert sub._map_event(self._entrada("text", "primary"))["type"] == "msg.text"    # controle


MENU = {"menu_id": "m-1", "interaction": "text", "prompt": "Diga ou escreva o que você precisa.",
        "options": [], "fields": [], "masked_fields": None,
        "collect": {"input": ["voice", "text"], "first_input_timeout_s": 110}}


def _menu_entry(vis='"all"', menu=MENU):
    return {"type": "interaction_request", "visibility": vis, "author_role": "specialist",
            "payload": json.dumps(menu)}


class TestMenuPendente(_Pronta):
    async def _com(self, *entradas, esperando=1):
        _alvos(self.adapter, "_speak", "_plan_collect", "_start_collect")
        self.adapter._speak = MagicMock()
        self.adapter._start_collect = MagicMock()
        self.plano = object()
        self.adapter._plan_collect = MagicMock(return_value=self.plano)
        self.redis.hlen = AsyncMock(return_value=esperando)
        await self._montar(*entradas)

    @pytest.mark.asyncio
    async def test_menu_anterior_a_chamada_vira_coleta_quando_ela_fica_pronta(self):
        await self._com(_menu_entry(), _assigned("native", "ia1", policy=VOZ, pool_id="demo_llm_ia"))
        self.adapter._start_collect.assert_called_once_with(self.sid, self.plano)
        (sid, payload, masked), _ = self.adapter._plan_collect.call_args
        assert payload["menu_id"] == "m-1" and payload["collect"]["input"] == ["voice", "text"]
        self.redis.hlen.assert_awaited_with(f"menu:waiting:{self.sid}")

    @pytest.mark.asyncio
    async def test_controle_menu_ja_respondido_nao_se_repete(self):
        await self._com(_menu_entry(), _assigned("native", "ia1", policy=VOZ, pool_id="demo_llm_ia"),
                        esperando=0)
        self.adapter._start_collect.assert_not_called()
        self.adapter._speak.assert_not_called()

    @pytest.mark.asyncio
    async def test_menu_dirigido_a_participante_nao_e_falado_ao_cliente(self):
        await self._com(_menu_entry(vis='["human-u1"]'),
                        _assigned("native", "ia1", policy=VOZ, pool_id="demo_llm_ia"))
        self.adapter._start_collect.assert_not_called()

    @pytest.mark.asyncio
    async def test_o_mesmo_menu_pela_saida_do_kafka_nao_arma_de_novo(self):
        await self._com(_menu_entry(), _assigned("native", "ia1", policy=VOZ, pool_id="demo_llm_ia"))
        self.adapter.chat_call_outbound("menu.payload", {**MENU, "session_id": self.sid})
        assert self.adapter._start_collect.call_count == 1
        # e a chamada seguinte do mesmo contato rearma (o fim limpa a marca)
        await self.adapter._end_attached_call(self.sid, "customer_hangup")
        assert self.sid not in self.adapter._call_menu_armed


class TestPoolSemPolitica(_Setup):
    """Pool sem `media_policy` é config legítima num contato de chat — INFO, não WARNING. No canal
    `webrtc` a política é obrigatória, e lá a ausência continua WARNING."""

    def _log(self, caplog):
        import logging
        with caplog.at_level(logging.INFO, logger="plughub.channel-gateway.webrtc"):
            self.adapter._attendant_record(
                {"type": "routing.assigned", "framework": "native", "instance_id": "auth_form_ia-001",
                 "pool": json.dumps({"pool_id": "auth_form_ia", "media_policy_source": "registry",
                                     "media_policy": None})}, self.sid)
        return [r for r in caplog.records if "nao declara media_policy" in r.getMessage()]

    def test_chamada_de_chat_info(self, caplog):
        self.adapter._attached.add(self.sid)
        (r,) = self._log(caplog)
        assert r.levelname == "INFO" and "opcional em contato de chat" in r.getMessage()

    def test_controle_canal_webrtc_warning(self, caplog):
        (r,) = self._log(caplog)
        assert r.levelname == "WARNING"


class TestPrimeiraFala:
    """A trilha da voz nasce na primeira fala e só é alcançável depois de negociada (~2 s medido):
    nada é capturado antes da primeira assinatura; sem assinatura no teto, fala assim mesmo e diz."""

    def _cliente(self, monkeypatch, assinada: bool):
        import asyncio
        from livekit import rtc
        from ..adapters import webrtc_room_client as rc
        ordem: list[str] = []

        class _Pub:
            async def wait_for_subscription(self):
                if not assinada:
                    await asyncio.sleep(3600)
                ordem.append("assinada")

        class _Fonte:
            def __init__(self, **k): pass
            async def capture_frame(self, frame): ordem.append("quadro")

        lp = MagicMock()
        lp.publish_track = AsyncMock(return_value=_Pub())
        monkeypatch.setattr(rtc, "AudioSource", _Fonte)
        monkeypatch.setattr(rtc.LocalAudioTrack, "create_audio_track", staticmethod(lambda *a: object()))
        monkeypatch.setattr(rtc, "TrackPublishOptions", lambda **k: None)
        monkeypatch.setattr(rtc, "AudioFrame", lambda **k: None)
        monkeypatch.setattr(rc, "_FIRST_SUBSCRIPTION_S", 0.05)
        c = rc.LiveKitRoomClient()
        c._room = MagicMock(local_participant=lp)
        c._connected = True
        return c, ordem

    @pytest.mark.asyncio
    async def test_nada_sai_antes_da_primeira_assinatura(self, monkeypatch):
        c, ordem = self._cliente(monkeypatch, assinada=True)
        await c.publish_audio(b"\x00\x01" * 960, sample_rate=24000)
        assert ordem[0] == "assinada" and "quadro" in ordem

    @pytest.mark.asyncio
    async def test_sem_assinatura_fala_assim_mesmo_e_diz(self, monkeypatch, caplog):
        import logging
        c, ordem = self._cliente(monkeypatch, assinada=False)
        with caplog.at_level(logging.WARNING):
            await c.publish_audio(b"\x00\x01" * 960, sample_rate=24000)
        assert "quadro" in ordem and "assinada" not in ordem
        assert "ninguem assinou a trilha da voz" in caplog.text

    @pytest.mark.asyncio
    async def test_segunda_fala_nao_espera(self, monkeypatch):
        c, ordem = self._cliente(monkeypatch, assinada=True)
        await c.publish_audio(b"\x00\x01" * 960, sample_rate=24000)
        ordem.clear()
        await c.publish_audio(b"\x00\x01" * 960, sample_rate=24000)
        assert "assinada" not in ordem
