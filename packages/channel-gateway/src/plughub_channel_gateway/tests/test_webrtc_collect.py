"""
tests/test_webrtc_collect.py — a coleta por teclado e fala no canal WebRTC (VOZ-05 fatia 5b).

O que o renderizador faz com o núcleo (`collect_core`): fala o prompt com as teclas, lê a tecla
do CLIENTE no ouvinte, entrega a fala ao menu só no modo `voice`, recusa texto que a coleta não
aceita e publica UM desfecho como `menu_result`. Cada caso com o seu controle.
"""
from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from plughub_channel_gateway.adapters.voice_provider import STTResult
from plughub_channel_gateway.adapters.webrtc_room_client import MockRoomClient
from plughub_channel_gateway.tests.test_webrtc_stt_tts import (
    SESSION_ID,
    _abre,
    _make_adapter,
    _ws_streaming,
)

pytestmark = pytest.mark.asyncio


def _menu(interaction="button", masked_fields=None, **collect) -> dict:
    c = {"input": ["dtmf", "voice"], "first_input_timeout_s": 30}
    c.update(collect)
    m = {"session_id": SESSION_ID, "menu_id": "m1", "interaction": interaction,
         "prompt": "Como prefere a fatura?", "collect": c,
         "options": [{"id": "email", "label": "Email"}, {"id": "correio", "label": "Correio"}]}
    if masked_fields:
        m["masked_fields"] = masked_fields
    return m


def _adapter(room: MockRoomClient | None = None):
    adapter, redis, producer = _make_adapter(room_client=room)
    adapter._connections[SESSION_ID] = AsyncMock()
    _abre(adapter)
    falas: list[str] = []

    def _speak(session_id, text, played=None):
        falas.append(text)
        if played is not None:
            played.set()
    adapter._speak = _speak          # a fala real tem teste próprio; aqui importa o que é dito
    return adapter, redis, producer, falas


def _eventos(producer) -> list[dict]:
    return [json.loads(c.args[1]) for c in producer.send.call_args_list]


def _resultados(producer) -> list[dict]:
    return [e["content"]["payload"] for e in _eventos(producer) if e["content"]["type"] == "menu_result"]


async def _ate(cond, secs=2.0):
    fim = asyncio.get_running_loop().time() + secs
    while not cond():
        if asyncio.get_running_loop().time() > fim:
            return False
        await asyncio.sleep(0.02)
    return True


async def _encerra(adapter):
    adapter._end_collect(SESSION_ID, "fim do teste")
    await asyncio.sleep(0)


class TestTeclado:
    async def test_prompt_com_teclas_e_tecla_do_cliente_vira_o_valor(self):
        room = MockRoomClient()
        adapter, _, producer, falas = _adapter(room)
        await adapter.deliver_menu(_menu())
        assert falas == ["Como prefere a fatura? Para Email, tecle um ou diga Email. "
                         "Para Correio, tecle dois ou diga Correio."]
        leitor = asyncio.create_task(adapter._dtmf_reader(SESSION_ID, room))
        room.inject_dtmf("2")
        assert await _ate(lambda: _resultados(producer))
        assert _resultados(producer) == [{"menu_id": "m1", "interaction": "button", "result": "correio"}]
        adapter._registry.append_message.assert_awaited()
        assert await _ate(lambda: SESSION_ID not in adapter._collects)
        leitor.cancel()

    async def test_tecla_de_quem_nao_e_o_cliente_nao_responde(self):
        room = MockRoomClient()
        adapter, _, producer, _ = _adapter(room)
        await adapter.deliver_menu(_menu())
        leitor = asyncio.create_task(adapter._dtmf_reader(SESSION_ID, room))
        room.inject_dtmf("2", identity="agent-humano")
        await asyncio.sleep(0.2)
        assert _resultados(producer) == []
        room.inject_dtmf("1")                                      # controle: o cliente responde
        assert await _ate(lambda: _resultados(producer))
        assert _resultados(producer)[0]["result"] == "email"
        leitor.cancel()

    async def test_eco_da_tecla_falado_quando_plain(self):
        room = MockRoomClient()
        adapter, _, producer, falas = _adapter(room)
        await adapter.deliver_menu(_menu(echo="plain"))
        await adapter._collect_digit(SESSION_ID, "2")
        assert falas[-1] == "dois"
        await _encerra(adapter)


class TestPrazoEInvalido:
    async def test_sem_entrada_publica_timeout_e_libera(self):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_menu(first_input_timeout_s=0.3))
        assert await _ate(lambda: _resultados(producer))
        assert _resultados(producer) == [{"menu_id": "m1", "outcome": "timeout"}]
        assert await _ate(lambda: SESSION_ID not in adapter._collects)

    async def test_invalidos_ate_o_limite_publicam_invalid_com_mensagem(self):
        adapter, _, producer, falas = _adapter()
        await adapter.deliver_menu(_menu(invalid_message="Opção inválida.", max_invalid=2))
        await adapter._collect_digit(SESSION_ID, "9")
        assert falas[-1] == "Opção inválida." and _resultados(producer) == []
        await adapter._collect_digit(SESSION_ID, "9")
        assert _resultados(producer) == [{"menu_id": "m1", "outcome": "invalid"}]

    async def test_menu_que_o_motor_nao_espera_mais_libera_sem_desfecho(self, caplog):
        adapter, redis, producer, _ = _adapter()
        estados = [{"i1": "{}"}, {}]
        redis.hgetall = AsyncMock(side_effect=lambda k: estados.pop(0) if estados else {})
        with caplog.at_level(logging.INFO):
            await adapter.deliver_menu(_menu())
            assert await _ate(lambda: SESSION_ID not in adapter._collects, 3.0)
        assert _resultados(producer) == []
        assert "nao espera mais o menu m1" in caplog.text


class TestFala:
    async def test_no_modo_voz_a_fala_e_registro_e_responde_o_menu(self):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_menu())
        await adapter._publish_transcript(SESSION_ID, "pode ser por email", 0.9, 0, 900)
        tipos = [(e["content"]["type"], e.get("content_type")) for e in _eventos(producer)]
        assert tipos == [("text", "audio_transcript"), ("menu_result", "text")]
        assert _resultados(producer)[0]["result"] == "email"

    async def test_fora_do_modo_voz_a_fala_fica_so_como_registro(self):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_menu(input=["dtmf"]))
        await adapter._publish_transcript(SESSION_ID, "pode ser por email", 0.9, 0, 900)
        assert [e["content"]["type"] for e in _eventos(producer)] == ["text"]
        await _encerra(adapter)

    async def test_voz_do_cliente_nao_corta_prompt_de_menu_so_de_teclado(self):
        adapter, _, _, _ = _adapter()
        adapter._speak = lambda *a, **k: None                      # prompt ainda tocando
        await adapter.deliver_menu(_menu(input=["dtmf"]))
        assert adapter._voice_barge_allowed(SESSION_ID) is False
        await _encerra(adapter)
        assert adapter._voice_barge_allowed(SESSION_ID) is True   # controle: sem coleta, corta


class _SttAjustavel:
    """STT que declara ajuste por coleta e anota, por fluxo, o ajuste que recebeu."""
    supports_tuning = True
    measures_confidence = True

    def __init__(self) -> None:
        self.ajustes: list = []

    async def stream(self, chunks, language=None, **kw):
        self.ajustes.append(kw.get("tuning"))
        async for _ in chunks:
            pass
        yield STTResult(transcript="ok", is_final=True, confidence=0.9)


class TestAjusteDeFala:
    """VOZ-18: `end_silence_ms`/`max_speech_s` da coleta por voz valem enquanto o menu espera, só
    na fala do cliente, e voltam ao default quando a coleta termina."""

    async def test_coleta_por_voz_liga_o_ajuste_e_o_fim_desliga(self):
        adapter, _, _, _ = _adapter()
        adapter._stt = _SttAjustavel()
        await adapter.deliver_menu(_menu(voice={"end_silence_ms": 1200, "max_speech_s": 4}))
        t = adapter._speech_tuning[SESSION_ID]
        assert (t.silence_ms, t.max_utterance_ms) == (1200, 4000)
        await _encerra(adapter)
        assert (t.silence_ms, t.max_utterance_ms) == (None, None)

    async def test_coleta_so_de_teclado_nao_mexe_na_fala(self):
        adapter, _, _, _ = _adapter()
        adapter._stt = _SttAjustavel()
        await adapter.deliver_menu(_menu(input=["dtmf"], voice={"end_silence_ms": 1200}))
        t = adapter._speech_tuning.get(SESSION_ID)
        assert t is None or (t.silence_ms, t.max_utterance_ms) == (None, None)
        await _encerra(adapter)

    async def test_desfecho_da_coleta_tambem_desliga(self):
        adapter, _, producer, _ = _adapter()
        adapter._stt = _SttAjustavel()
        await adapter.deliver_menu(_menu(voice={"end_silence_ms": 1200}))
        await adapter._publish_transcript(SESSION_ID, "email", 0.9, 0, 900)
        assert await _ate(lambda: _resultados(producer))
        assert await _ate(lambda: adapter._speech_tuning[SESSION_ID].silence_ms is None)

    async def test_so_o_fluxo_do_cliente_recebe_o_ajuste(self):
        stt = _SttAjustavel()
        room = MockRoomClient()
        room.inject_audio(b"\x00" * 960)
        room.inject_audio(b"\x00" * 960, identity="agent-sub-hum")
        room.end_audio()
        adapter, _, _ = _make_adapter(stt=stt, room_client=room)
        _abre(adapter)
        await adapter._stt_pipeline(SESSION_ID, room)
        recebidos = [a for a in stt.ajustes if a is not None]
        assert len(stt.ajustes) == 2 and len(recebidos) == 1
        assert recebidos[0] is adapter._speech_tuning[SESSION_ID]

    async def test_stt_sem_ajuste_avisa_que_nao_aplica_e_controle_com_ajuste_nao_avisa(self, caplog):
        adapter, _, _, _ = _adapter()
        with caplog.at_level(logging.WARNING):
            await adapter.deliver_menu(_menu(voice={"end_silence_ms": 1200}))
        assert "nao ajusta a segmentacao" in caplog.text
        await _encerra(adapter)
        caplog.clear()
        adapter._stt = _SttAjustavel()
        with caplog.at_level(logging.WARNING):
            await adapter.deliver_menu(_menu(voice={"end_silence_ms": 1200}))
        assert "nao ajusta a segmentacao" not in caplog.text
        await _encerra(adapter)

    async def test_fala_abaixo_do_minimo_e_tentativa_invalida_com_a_medida_no_log(self, caplog):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_menu(voice={"min_confidence": 0.7}, max_invalid=1))
        with caplog.at_level(logging.INFO):
            await adapter._publish_transcript(SESSION_ID, "email", 0.42, 0, 900)
        assert await _ate(lambda: _resultados(producer))
        assert _resultados(producer)[0]["outcome"] == "invalid"
        assert "abaixo da confianca minima no menu m1 (0.420 < 0.7)" in caplog.text
        assert "email" not in [r.getMessage() for r in caplog.records if "confianca minima" in r.getMessage()][0]

    async def test_controle_fala_acima_do_minimo_responde(self):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_menu(voice={"min_confidence": 0.7}, max_invalid=1))
        await adapter._publish_transcript(SESSION_ID, "email", 0.81, 0, 900)
        assert await _ate(lambda: _resultados(producer))
        assert _resultados(producer)[0]["result"] == "email"

    async def test_fala_sem_confianca_medida_responde_e_o_log_diz(self, caplog):
        adapter, _, producer, _ = _adapter()
        adapter._stt = _SttAjustavel()
        await adapter.deliver_menu(_menu(voice={"min_confidence": 0.7}))
        with caplog.at_level(logging.WARNING):
            await adapter._publish_transcript(SESSION_ID, "email", None, 0, 900)
        assert await _ate(lambda: _resultados(producer))
        assert _resultados(producer)[0]["result"] == "email"
        assert "NAO medida" in caplog.text


class TestTextoETela:
    async def test_texto_digitado_recusado_quando_a_coleta_nao_aceita_texto(self):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_menu())
        ws = _ws_streaming([json.dumps({"type": "webrtc.message", "text": "correio"})])
        await adapter._receive_loop(ws, SESSION_ID)
        assert producer.send.call_count == 0
        assert ws.send_json.call_args.args[0]["code"] == "collect_input_not_accepted"
        await _encerra(adapter)

    async def test_controle_coleta_que_aceita_texto_publica(self):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_menu(input=["dtmf", "text"]))
        ws = _ws_streaming([json.dumps({"type": "webrtc.message", "text": "correio"})])
        await adapter._receive_loop(ws, SESSION_ID)
        assert producer.send.call_count == 1
        await _encerra(adapter)

    async def test_resposta_pela_tela_encerra_a_coleta(self):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_menu())
        ws = _ws_streaming([json.dumps({"type": "webrtc.menu_submit", "menu_id": "m1",
                                        "interaction": "button", "result": "email"})])
        await adapter._receive_loop(ws, SESSION_ID)
        assert SESSION_ID not in adapter._collects
        assert _resultados(producer) == [{"menu_id": "m1", "interaction": "button", "result": "email"}]


class TestSemColeta:
    async def test_menu_mascarado_vai_a_tela(self, caplog):
        adapter, _, _, falas = _adapter()
        with caplog.at_level(logging.INFO):
            await adapter.deliver_menu(_menu(masked_fields=["senha"]))
        assert SESSION_ID not in adapter._collects and falas == ["Como prefere a fatura?"]
        assert "campo protegido da tela" in caplog.text

    async def test_formulario_com_coleta_e_dito_e_responde_pela_tela(self, caplog):
        adapter, _, _, _ = _adapter()
        with caplog.at_level(logging.WARNING):
            await adapter.deliver_menu(_menu("form"))
        assert SESSION_ID not in adapter._collects
        assert "NAO sera coletado assim" in caplog.text

    async def test_menu_sem_collect_so_fala_o_prompt(self):
        adapter, _, _, falas = _adapter()
        m = _menu()
        m.pop("collect")
        await adapter.deliver_menu(m)
        assert SESSION_ID not in adapter._collects and falas == ["Como prefere a fatura?"]


class TestFalaReal:
    async def test_prazo_arma_quando_a_fala_nao_acontece(self):
        # sem `_speak` substituído: sem voz decidida na chamada, `played` marca na hora
        adapter, _, producer = _make_adapter()
        adapter._connections[SESSION_ID] = AsyncMock()
        _abre(adapter)
        adapter._customer_media[SESSION_ID] = frozenset()
        await adapter.deliver_menu(_menu(first_input_timeout_s=0.2))
        assert adapter._collects[SESSION_ID].played.is_set()
        assert await _ate(lambda: _resultados(producer))
        assert _resultados(producer) == [{"menu_id": "m1", "outcome": "timeout"}]


def _campo(masked=True, **collect) -> dict:
    c = {"input": ["dtmf"], "first_input_timeout_s": 30, "min_digits": 4, "max_digits": 6, "terminator": "#"}
    c.update(collect)
    m = {"session_id": SESSION_ID, "menu_id": "pin", "interaction": "text", "prompt": "Digite o PIN", "collect": c}
    if masked:
        m["masked_fields"] = ["pin"]
    return m


def _submit(valor) -> str:
    return json.dumps({"type": "webrtc.menu_submit", "menu_id": "pin", "interaction": "text", "result": valor})


class TestTela:
    """VOZ-05 fatia 5c: o widget ganha o domínio para o teclado, e a resposta pela tela passa pela
    mesma regra da tecla — inclusive a do campo mascarado, cujo valor nunca vai ao log."""

    async def test_frame_leva_a_visao_da_coleta_e_controle_sem_collect(self):
        adapter, _, _, _ = _adapter()
        ws = adapter._connections[SESSION_ID]
        await adapter.deliver_menu(_campo())
        frame = ws.send_json.call_args.args[0]
        assert frame["collect"] == {"input": ["dtmf"], "domain": "digits", "min_digits": 4,
                                    "max_digits": 6, "terminator": "#"}
        m = _campo()
        m.pop("collect")
        await adapter.deliver_menu(m)
        assert ws.send_json.call_args.args[0]["collect"] is None

    async def test_campo_mascarado_invalido_nao_vai_ao_menu_e_o_valor_nao_vai_ao_log(self, caplog):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_campo())
        ws = _ws_streaming([_submit("12x9")])
        with caplog.at_level(logging.DEBUG):
            await adapter._receive_loop(ws, SESSION_ID)
        assert _resultados(producer) == []
        assert ws.send_json.call_args.args[0]["code"] == "collect_invalid"
        assert "12x9" not in caplog.text

    async def test_controle_valido_vai_ao_menu_sem_o_terminador(self):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_campo())
        await adapter._receive_loop(_ws_streaming([_submit("4821#")]), SESSION_ID)
        assert _resultados(producer) == [{"menu_id": "pin", "interaction": "text", "result": "4821"}]

    async def test_invalidos_esgotados_pela_tela_viram_desfecho_invalid(self):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_campo(max_invalid=2))
        await adapter._receive_loop(_ws_streaming([_submit("1"), _submit("22")]), SESSION_ID)
        assert _resultados(producer) == [{"menu_id": "pin", "outcome": "invalid"}]

    async def test_campo_de_texto_livre_nao_e_validado_como_digitos(self):
        adapter, _, producer, _ = _adapter()
        m = {"session_id": SESSION_ID, "menu_id": "pin", "interaction": "text", "prompt": "Nome?",
             "collect": {"input": ["voice", "text"], "first_input_timeout_s": 30}}
        await adapter.deliver_menu(m)
        await adapter._receive_loop(_ws_streaming([_submit("Maria")]), SESSION_ID)
        assert _resultados(producer) == [{"menu_id": "pin", "interaction": "text", "result": "Maria"}]
        await _encerra(adapter)
