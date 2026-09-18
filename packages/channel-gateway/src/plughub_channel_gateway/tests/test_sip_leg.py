"""
test_sip_leg.py — VOZ-02 (fatia 1): a chamada de telefone vira contato, fala, e desliga.

As proposições, cada uma com o controle ao lado:

  * CHEGADA — participante SIP na sala do nosso prefixo vira contato `voice`, endereçado pelo número
    DISCADO; o que não é chamada nossa (bot, agente, sala alheia) não vira nada. Número sem endpoint
    é RECUSADO (a sala cai), nunca roteado para um pool default. Webhook repetido não abre dois.
  * SAÍDA — o canal `voice` entrega à sessão SIP pelo adapter WebRTC e o resto ao legado Twilio; o
    texto da IA é FALADO e o texto que o telefone não pode ouvir é DITO no log.
  * FIM — o chamador desligando fecha como `customer_hangup`; a plataforma encerrando derruba a
    chamada. Os dois publicam `contact_closed` com canal `voice`.
  * CONTROLE COMPENSATÓRIO — com `auto_create` ligado, sala `plughub-{uuid}` sem sessão viva é
    apagada ao nascer; a de sessão viva, a do telefone e a alheia ficam.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from unittest.mock import AsyncMock

import pytest

from ..adapters.sip_leg import SIP_ROOM_PREFIX, SipCall, normalize_number, parse_sip_participant
from ..adapters.voice_router import VoiceChannelRouter
from ..adapters.webrtc import WebRTCAdapter
from ..endpoint_resolver import ResolvedEndpoint
from .test_webrtc_stt_tts import _make_adapter

SALA = f"{SIP_ROOM_PREFIX}_+5511999990000_abc"
PARTICIPANTE = {"identity": "sip_+5511999990000", "kind": "SIP", "attributes": {
    "sip.callID": "SCL_1", "sip.trunkPhoneNumber": "+551140000000", "sip.phoneNumber": "+5511999990000"}}


class _Redis:
    """O Redis que a perna SIP usa, em memória: o `set nx` é o que torna o webhook idempotente, e um
    AsyncMock responderia `True` para sempre — o teste de repetição passaria sem medir nada."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def setex(self, k, ttl, v):
        self.kv[k] = v
        return True

    async def get(self, k):
        return self.kv.get(k)

    async def exists(self, k):
        return 1 if k in self.kv else 0

    async def delete(self, *ks):
        return sum(1 for k in ks if self.kv.pop(k, None) is not None)

    async def expire(self, k, ttl):
        return k in self.kv

    async def hgetall(self, k):
        return {}


def _adapter():
    ad, _, producer = _make_adapter()
    ad._redis = _Redis()
    # O watcher do stream e o keepalive são tasks longas do produto; aqui só importa que NASÇAM.
    assert hasattr(WebRTCAdapter, "_stream_watcher") and hasattr(WebRTCAdapter, "_keepalive")
    ad._stream_watcher = AsyncMock()
    ad._keepalive = AsyncMock()
    return ad, producer


def _eventos(producer, topico_final="events"):
    out = []
    for c in producer.send.await_args_list:
        topico, corpo = c.args[0], c.args[1]
        if topico.endswith(topico_final):
            out.append(json.loads(corpo))
    return out


def _endpoint(monkeypatch, pool="telefone_ia", outcome="found", settings=None):
    async def _resolve(**kw):
        assert kw["channel"] == "voice"
        return ResolvedEndpoint(pool_id=pool if outcome == "found" else None, origin=None, auth_required=False,
                                token_hash=None, outcome=outcome, settings=settings or {})
    from .. import endpoint_resolver
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", _resolve)


class TestLeitura:
    def test_participante_sip_na_nossa_sala_e_chamada(self):
        c = parse_sip_participant(SALA, PARTICIPANTE)
        assert c == SipCall(room=SALA, identity="sip_+5511999990000", call_id="SCL_1",
                            dnis="+551140000000", ani="+5511999990000")

    @pytest.mark.parametrize("sala,part", [
        (SALA, {**PARTICIPANTE, "kind": "STANDARD", "identity": "bot-12345678"}),  # o bot leg entrando
        ("plughub-2f1c0a3e-0000-0000-0000-000000000000", PARTICIPANTE),           # sala de browser
        ("sala-de-outro", PARTICIPANTE),
    ])
    def test_o_que_nao_e_chamada_nossa_nao_vira_contato(self, sala, part):
        assert parse_sip_participant(sala, part) is None

    def test_numero_oculto_fica_vazio_nao_inventado(self):
        p = {**PARTICIPANTE, "attributes": {"sip.trunkPhoneNumber": "+551140000000"}}
        assert parse_sip_participant(SALA, p).ani == ""

    @pytest.mark.parametrize("bruto,esperado", [("+55 (11) 4000-0000", "+551140000000"),
                                                ("551140000000", "+551140000000"), ("", ""), (None, "")])
    def test_numero_normalizado_sem_adivinhar_pais(self, bruto, esperado):
        assert normalize_number(bruto) == esperado


class TestChegada:
    async def test_numero_com_endpoint_vira_contato_voice(self, monkeypatch):
        _endpoint(monkeypatch, settings={"speech_profile_id": "tronco"})
        ad, producer = _adapter()
        await ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        assert len(ad._sip) == 1
        sid = next(iter(ad._sip))
        meta = json.loads(ad._redis.kv[f"session:{sid}:meta"])
        assert meta["channel"] == "voice" and meta["pool_id"] == "telefone_ia"
        assert meta["customer_id"] == "+5511999990000"
        assert ad._sessions[sid]["speech_profile_id"] == "tronco"
        abertura = [e for e in _eventos(producer) if e.get("event_type") == "contact_open"]
        assert abertura and abertura[0]["channel"] == "voice"
        roteamento = _eventos(producer, "inbound")
        assert roteamento and roteamento[0]["channel"] == "voice"
        assert ad._room_of(sid) == SALA and ad.is_sip_session(sid)
        assert await ad._customer_identity(sid) == "sip_+5511999990000"
        # espera pelas TASKS que o produto guardou, nunca por contagem de `sleep(0)`
        await asyncio.gather(*ad._sip_tasks[sid])
        assert ad._stream_watcher.await_count == 1 and ad._keepalive.await_count == 1

    async def test_webhook_repetido_nao_abre_dois(self, monkeypatch):
        _endpoint(monkeypatch)
        ad, _ = _adapter()
        await ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        await ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        assert len(ad._sip) == 1

    async def test_numero_sem_endpoint_e_recusado_e_a_sala_cai(self, monkeypatch, caplog):
        _endpoint(monkeypatch, outcome="not_found")
        ad, producer = _adapter()
        with caplog.at_level(logging.ERROR):
            await ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        assert ad._sip == {} and _eventos(producer) == []
        assert ad._provider.rooms_deleted == [SALA]
        assert "+551140000000" in caplog.text and "RECUSADA" in caplog.text

    async def test_registro_fora_tambem_recusa_e_diz_o_motivo_certo(self, monkeypatch, caplog):
        _endpoint(monkeypatch, outcome="unavailable")
        ad, _ = _adapter()
        with caplog.at_level(logging.ERROR):
            await ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        assert ad._sip == {} and "INALCANCAVEL" in caplog.text

    async def test_numero_oculto_vira_contato_com_o_id_da_chamada(self, monkeypatch):
        _endpoint(monkeypatch)
        ad, _ = _adapter()
        p = {**PARTICIPANTE, "attributes": {"sip.callID": "SCL_9", "sip.trunkPhoneNumber": "+551140000000"}}
        await ad.on_livekit_event("participant_joined", SALA, p)
        sid = next(iter(ad._sip))
        assert json.loads(ad._redis.kv[f"session:{sid}:meta"])["customer_id"] == "sip:SCL_9"


class TestSaida:
    async def _sessao(self, monkeypatch):
        _endpoint(monkeypatch)
        ad, producer = _adapter()
        await ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        return ad, producer, next(iter(ad._sip))

    async def test_roteador_manda_a_sessao_sip_ao_adapter_webrtc_e_o_resto_ao_legado(self, monkeypatch):
        ad, _, sid = await self._sessao(monkeypatch)
        assert hasattr(WebRTCAdapter, "deliver_text")
        ad.deliver_text = AsyncMock()
        legado = AsyncMock()
        legado.handle_collect_event = "do-legado"
        r = VoiceChannelRouter(ad, legado)
        await r.deliver_text({"session_id": sid})
        await r.deliver_text({"session_id": "sessao-twilio"})
        assert ad.deliver_text.await_count == 1 and legado.deliver_text.await_count == 1
        assert r.handle_collect_event == "do-legado"       # o resto segue sendo do legado

    async def test_fala_da_ia_e_falada_e_texto_humano_e_dito(self, monkeypatch, caplog):
        ad, _, sid = await self._sessao(monkeypatch)
        assert hasattr(WebRTCAdapter, "_speak")
        falas = []
        ad._speak = lambda s, t, played=None: falas.append(t)
        await ad.deliver_text({"session_id": sid, "content": {"text": "Olá!"}, "author": {"type": "agent_ai"}})
        with caplog.at_level(logging.WARNING):
            await ad.deliver_text({"session_id": sid, "content": {"text": "digitei"}, "author": {"type": "agent"}})
        assert falas == ["Olá!"]
        assert "NAO entregue" in caplog.text

    async def test_menu_sem_coleta_tem_o_prompt_falado(self, monkeypatch):
        ad, _, sid = await self._sessao(monkeypatch)
        falas = []
        ad._speak = lambda s, t, played=None: falas.append(t)
        await ad.deliver_menu({"session_id": sid, "menu_id": "m", "prompt": "Diga o motivo."})
        assert falas == ["Diga o motivo."]


class TestFim:
    async def _sessao(self, monkeypatch):
        _endpoint(monkeypatch)
        ad, producer = _adapter()
        await ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        return ad, producer, next(iter(ad._sip))

    async def test_chamador_desligou_fecha_como_customer_hangup(self, monkeypatch):
        ad, producer, sid = await self._sessao(monkeypatch)
        await ad.on_livekit_event("participant_left", SALA, PARTICIPANTE)
        fech = [e for e in _eventos(producer) if e.get("event_type") == "contact_closed"]
        assert len(fech) == 1 and fech[0]["channel"] == "voice"
        assert fech[0]["close_reason"] == "customer_hangup"
        assert not ad.is_sip_session(sid) and SALA in ad._provider.rooms_deleted
        assert f"channel:webrtc:{sid}:room_name" not in ad._redis.kv

    async def test_bot_saindo_da_sala_nao_desliga(self, monkeypatch):
        ad, producer, sid = await self._sessao(monkeypatch)
        await ad.on_livekit_event("participant_left", SALA, {"identity": f"bot-{sid[:8]}", "kind": "STANDARD"})
        assert ad.is_sip_session(sid)
        assert not [e for e in _eventos(producer) if e.get("event_type") == "contact_closed"]

    async def test_plataforma_encerra_e_derruba_a_chamada(self, monkeypatch):
        ad, producer, sid = await self._sessao(monkeypatch)
        await ad.deliver_session_closed({"session_id": sid, "reason": "flow_complete"})
        fech = [e for e in _eventos(producer) if e.get("event_type") == "contact_closed"]
        assert len(fech) == 1 and fech[0]["reason"] == "agent_done" and fech[0]["channel"] == "voice"
        assert SALA in ad._provider.rooms_deleted and not ad.is_sip_session(sid)

    async def test_desligar_depois_de_encerrar_nao_publica_dois(self, monkeypatch):
        ad, producer, sid = await self._sessao(monkeypatch)
        await ad.deliver_session_closed({"session_id": sid, "reason": "flow_complete"})
        await ad.on_livekit_event("participant_left", SALA, PARTICIPANTE)
        await ad.on_livekit_event("room_finished", SALA, None)
        assert len([e for e in _eventos(producer) if e.get("event_type") == "contact_closed"]) == 1


class TestControleCompensatorio:
    async def test_sala_de_sessao_sem_chave_e_apagada(self, caplog):
        ad, _ = _adapter()
        sala = f"plughub-{uuid.uuid4()}"
        with caplog.at_level(logging.ERROR):
            assert await ad.police_room(sala) is True
        assert ad._provider.rooms_deleted == [sala] and "APAGADA" in caplog.text

    async def test_sala_de_sessao_viva_fica(self):
        """CONTROLE POSITIVO: sem ele, 'apaga tudo' passaria por controle."""
        ad, _ = _adapter()
        sid = str(uuid.uuid4())
        ad._redis.kv[f"channel:webrtc:{sid}:room_name"] = f"plughub-{sid}"
        assert await ad.police_room(f"plughub-{sid}") is False
        assert ad._provider.rooms_deleted == []

    @pytest.mark.parametrize("sala", [SALA, "probe-sala-propria", "plughub-nao-e-uuid"])
    async def test_sala_do_telefone_e_alheia_nao_sao_policiadas(self, sala):
        ad, _ = _adapter()
        assert await ad.police_room(sala) is False and ad._provider.rooms_deleted == []

    async def test_a_chave_nasce_antes_da_sala(self):
        """O `routing.assigned` grava a chave da sala ANTES do `create_room` — senão o próprio controle
        apagaria a sala legítima na corrida com o `room_started`."""
        ad, _ = _adapter()
        sid = str(uuid.uuid4())
        ad._sessions[sid] = {"contact_id": "c", "pool_id": "p", "started_at": "x", "channel": "webrtc"}
        ordem = []
        orig_setex, orig_create = ad._redis.setex, ad._provider.create_room

        async def _setex(k, ttl, v):
            if k.endswith(":room_name"):
                ordem.append("chave")
            return await orig_setex(k, ttl, v)

        async def _create(name, **kw):
            ordem.append("sala")
            return await orig_create(name, **kw)
        ad._redis.setex, ad._provider.create_room = _setex, _create
        ws = AsyncMock()
        await ad._on_routing_assigned(ws, sid, {"instance_id": "h1", "framework": "human",
                                                "pool": json.dumps({"media_policy_source": "registry",
                                                                    "media_policy": {"customer_publish": ["audio"],
                                                                                     "agent_publish": ["audio"]}})},
                                      ad._settings)
        assert ordem[:2] == ["chave", "sala"]


class TestTeclas:
    """VOZ-31 — a tecla do TELEFONE. O serviço SIP converte o RFC 4733 em `sip_dtmf_received` com a
    identidade `sip_…`; ela responde o menu, e o desfecho sai com o canal da SESSÃO (`voice`), não com
    o do adapter."""

    CHAMADOR = PARTICIPANTE["identity"]

    async def _sessao(self, monkeypatch):
        _endpoint(monkeypatch)
        ad, producer = _adapter()
        await ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        assert hasattr(WebRTCAdapter, "_speak")
        falas: list[str] = []

        def _speak(session_id, text, played=None):
            falas.append(text)
            if played is not None:
                played.set()
        ad._speak = _speak
        return ad, producer, next(iter(ad._sip)), falas

    @staticmethod
    def _menu(sid, **extra):
        return {"session_id": sid, "menu_id": "m1", "interaction": "text", "prompt": "Digite o codigo.",
                "collect": {"input": ["dtmf"], "first_input_timeout_s": 20, "max_digits": 6,
                            "terminator": "#"}, **extra}

    @staticmethod
    def _resultados(producer):
        return [e for e in _eventos(producer, "inbound") if e.get("content", {}).get("type") == "menu_result"]

    async def _espera(self, cond, secs=2.0):
        fim = asyncio.get_running_loop().time() + secs
        while not cond():
            if asyncio.get_running_loop().time() > fim:
                return False
            await asyncio.sleep(0.02)
        return True

    async def test_tecla_do_chamador_responde_o_menu_com_o_canal_da_sessao(self, monkeypatch):
        from ..adapters.webrtc_room_client import MockRoomClient
        ad, producer, sid, falas = await self._sessao(monkeypatch)
        room = MockRoomClient()
        await ad.deliver_menu(self._menu(sid))
        assert falas and falas[0].startswith("Digite o codigo.")
        leitor = asyncio.create_task(ad._dtmf_reader(sid, room))
        try:
            for d in "1234#":
                room.inject_dtmf(d, identity=self.CHAMADOR)
            assert await self._espera(lambda: self._resultados(producer))
        finally:
            leitor.cancel()
        r = self._resultados(producer)
        assert len(r) == 1
        assert r[0]["content"]["payload"]["result"] == "1234"
        assert r[0]["channel"] == "voice"            # a sessão é telefone, não browser

    async def test_tecla_de_outro_participante_nao_responde(self, monkeypatch):
        from ..adapters.webrtc_room_client import MockRoomClient
        ad, producer, sid, _ = await self._sessao(monkeypatch)
        room = MockRoomClient()
        await ad.deliver_menu(self._menu(sid))
        leitor = asyncio.create_task(ad._dtmf_reader(sid, room))
        try:
            for d in "99#":
                room.inject_dtmf(d, identity="agent-humano")
            await asyncio.sleep(0.2)
            assert self._resultados(producer) == []
            for d in "7#":                               # controle: o chamador responde
                room.inject_dtmf(d, identity=self.CHAMADOR)
            assert await self._espera(lambda: self._resultados(producer))
        finally:
            leitor.cancel()
        assert self._resultados(producer)[0]["content"]["payload"]["result"] == "7"

    async def test_fala_transcrita_do_chamador_sai_como_voice(self, monkeypatch):
        ad, producer, sid, _ = await self._sessao(monkeypatch)
        assert hasattr(WebRTCAdapter, "_publish_customer_text")
        await ad._publish_customer_text(sid, "quero falar com atendente", content_type="audio_transcript")
        falas = [e for e in _eventos(producer, "inbound") if e.get("content", {}).get("text")]
        assert falas and falas[-1]["channel"] == "voice"


class TestColetaMascaradaNoTelefone:
    """NIV-07 — dado protegido pelo TELEFONE. A tecla SIP chega a TODOS os participantes da sala
    (medido: nem `can_subscribe=False` barra), então a coleta mascarada corre sob PAUSA DE MÍDIA:
    quem não é o cliente nem bot da plataforma sai da sala ANTES do prompt, a rota de token os
    recusa até o fim, e quem entra no meio DESFAZ a coleta (`aborted` → `on_failure`). O valor não
    vai ao log nem ao histórico em claro. Cada caso com o controle ao lado."""

    CHAMADOR = PARTICIPANTE["identity"]

    async def _sessao(self, monkeypatch, humanos=("agent-u1", "supervisor-u2")):
        from ..adapters.webrtc import listener_identity, voice_identity
        _endpoint(monkeypatch)
        ad, producer = _adapter()
        await ad.on_livekit_event("participant_joined", SALA, PARTICIPANTE)
        sid = next(iter(ad._sip))
        # na sala: o chamador, os dois bots da plataforma e quem mais o teste pedir
        ad._provider.joined.update({self.CHAMADOR, listener_identity(sid), voice_identity(sid), *humanos})
        assert hasattr(WebRTCAdapter, "_speak")
        falas: list[tuple[str, int]] = []

        def _speak(session_id, text, played=None):
            # quantos já tinham saído da sala quando o prompt foi falado
            falas.append((text, len(ad._provider.participants_removed)))
            if played is not None:
                played.set()
        ad._speak = _speak
        return ad, producer, sid, falas

    @staticmethod
    def _menu(sid, **extra):
        return {"session_id": sid, "menu_id": "m2", "interaction": "text", "prompt": "Digite o PIN.",
                "masked": True, "masked_fields": ["pin"],
                "collect": {"input": ["dtmf"], "first_input_timeout_s": 20, "max_digits": 6,
                            "terminator": "#"}, **extra}

    @staticmethod
    def _resultados(producer):
        return [e for e in _eventos(producer, "inbound") if e.get("content", {}).get("type") == "menu_result"]

    async def _espera(self, cond, secs=2.0):
        fim = asyncio.get_running_loop().time() + secs
        while not cond():
            if asyncio.get_running_loop().time() > fim:
                return False
            await asyncio.sleep(0.02)
        return True

    async def test_pin_por_tecla_com_a_sala_esvaziada_antes_do_prompt(self, monkeypatch, caplog):
        from ..adapters.webrtc_room_client import MockRoomClient
        ad, producer, sid, falas = await self._sessao(monkeypatch)
        room = MockRoomClient()
        with caplog.at_level(logging.DEBUG):
            await ad.deliver_menu(self._menu(sid))
            assert await self._espera(lambda: bool(falas))
            removidos = {i for _, i in ad._provider.participants_removed}
            assert removidos == {"agent-u1", "supervisor-u2"}       # bots e chamador ficam
            assert falas[0][1] == 2                                  # o prompt só DEPOIS de esvaziar
            assert await ad._redis.exists(f"channel:webrtc:{sid}:media_hold")
            leitor = asyncio.create_task(ad._dtmf_reader(sid, room))
            try:
                for d in "5566#":
                    room.inject_dtmf(d, identity=self.CHAMADOR)
                assert await self._espera(lambda: self._resultados(producer))
                # a pausa sai com a coleta: humano e supervisor podem voltar
                assert await self._espera(lambda: sid not in ad._media_hold)
            finally:
                leitor.cancel()
        r = self._resultados(producer)
        assert r[0]["content"]["payload"]["result"] == "5566"      # o valor vai ao motor (maskedScope)
        assert not await ad._redis.exists(f"channel:webrtc:{sid}:media_hold")
        historico = ad._registry.append_message.await_args.kwargs["text"]
        assert "5566" not in historico and "mascarada" in historico
        assert "5566" not in caplog.text

    async def test_token_de_humano_e_recusado_durante_a_pausa(self, monkeypatch):
        from ..adapters.webrtc import MaskedCollectInProgress
        ad, _, sid, _ = await self._sessao(monkeypatch)
        await ad._redis.setex(f"channel:webrtc:{sid}:media_hold", 60, "m2")
        with pytest.raises(MaskedCollectInProgress):
            await ad.get_token(sid, "agent", "u1")
        with pytest.raises(MaskedCollectInProgress):
            await ad.get_token(sid, "supervisor", "u2")
        await ad._redis.delete(f"channel:webrtc:{sid}:media_hold")
        # controle: sem a pausa a rota segue o caminho de sempre (aqui, "sala ainda não pronta")
        assert await ad.get_token(sid, "agent", "u1") is None

    async def test_quem_entra_durante_o_bloco_sai_e_desfaz_a_coleta(self, monkeypatch, caplog):
        ad, producer, sid, falas = await self._sessao(monkeypatch, humanos=())
        with caplog.at_level(logging.ERROR):
            await ad.deliver_menu(self._menu(sid))
            assert await self._espera(lambda: bool(falas))
            ad._provider.joined.add("agent-atrasado")
            await ad.on_livekit_event("participant_joined", SALA, {"identity": "agent-atrasado", "kind": "STANDARD"})
            assert await self._espera(lambda: self._resultados(producer))
        assert ("" + SALA, "agent-atrasado") in ad._provider.participants_removed
        assert self._resultados(producer)[0]["content"]["payload"] == {"menu_id": "m2", "outcome": "aborted"}
        assert "DESFEITO" in caplog.text
        assert await self._espera(lambda: sid not in ad._media_hold)

    async def test_bot_da_plataforma_entrando_nao_desfaz(self, monkeypatch):
        from ..adapters.webrtc import voice_identity
        ad, producer, sid, falas = await self._sessao(monkeypatch, humanos=())
        await ad.deliver_menu(self._menu(sid))
        assert await self._espera(lambda: bool(falas))
        await ad.on_livekit_event("participant_joined", SALA, {"identity": voice_identity(sid), "kind": "STANDARD"})
        await asyncio.sleep(0.1)
        assert self._resultados(producer) == [] and sid in ad._collects
        ad._end_collect(sid, "fim do teste")

    async def test_pausa_que_nao_se_completa_desfaz_sem_falar_o_prompt(self, monkeypatch, caplog):
        ad, producer, sid, falas = await self._sessao(monkeypatch)

        async def _recusa(room_name, identity):
            raise RuntimeError("SFU fora")
        ad._provider.remove_participant = _recusa
        with caplog.at_level(logging.ERROR):
            await ad.deliver_menu(self._menu(sid))
            assert await self._espera(lambda: self._resultados(producer))
        assert self._resultados(producer)[0]["content"]["payload"]["outcome"] == "aborted"
        assert falas == []                                        # o cliente nunca foi convidado a teclar
        assert "NAO saiu da sala" in caplog.text

    async def test_mascarado_que_pede_fala_e_recusado(self, monkeypatch, caplog):
        ad, _, sid, _ = await self._sessao(monkeypatch)
        m = self._menu(sid)
        m["collect"]["input"] = ["voice", "dtmf"]
        with caplog.at_level(logging.ERROR):
            await ad.deliver_menu(m)
        assert sid not in ad._collects and "NIV-08" in caplog.text
        assert ad._provider.participants_removed == []           # nada de pausa para o que não coleta

    async def test_perna_twilio_recusa_menu_mascarado(self, caplog):
        legado = AsyncMock()
        sip = AsyncMock()
        sip.is_sip_session = lambda sid: False
        r = VoiceChannelRouter(sip, legado)
        with caplog.at_level(logging.ERROR):
            await r.deliver_menu({"session_id": "tw1", "menu_id": "m", "masked": True, "masked_fields": ["pin"]})
        assert legado.deliver_menu.await_count == 0 and "RECUSADO na perna Twilio" in caplog.text
        await r.deliver_menu({"session_id": "tw1", "menu_id": "m"})   # controle: menu comum segue
        assert legado.deliver_menu.await_count == 1
