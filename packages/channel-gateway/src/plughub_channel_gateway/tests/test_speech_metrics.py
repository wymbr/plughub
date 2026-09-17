"""
tests/test_speech_metrics.py — telemetria passiva da fala (VOZ-22, camada A da recalibragem).

Três casas contam e uma publica: o provedor (quadros, descartes, confiança), o núcleo da coleta
(tentativas) e o renderizador (um evento por fluxo do cliente e um por coleta com voz). A proposição
que mais importa é negativa: **nenhum texto sai** — nem transcrição, nem valor coletado.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from ..adapters.speaches_provider import SpeachesSTTProvider
from ..adapters.voice_provider import SpeechSegmentation, SpeechStats, STTResult
from ..adapters.webrtc_room_client import MockRoomClient
from ..collect_core import CollectPlan, CollectSession
from .. import speech_metrics
from .test_speaches_provider import SR, Servico, _gera, _quadros, _silencio, _voz
from .test_webrtc_collect import _adapter, _ate, _encerra, _menu
from .test_webrtc_stt_tts import SESSION_ID, _abre, _make_adapter


def _metricas(producer) -> list[dict]:
    return [json.loads(c.kwargs["value"]) for c in producer.send.call_args_list
            if c.args and c.args[0] == speech_metrics.TOPIC]


def _stt(svc: Servico) -> SpeachesSTTProvider:
    return SpeachesSTTProvider("http://speaches:8000", "m", http=httpx.AsyncClient(transport=httpx.MockTransport(svc)))


async def _conta(svc: Servico, pcm: bytes, **kw) -> SpeechStats:
    stats = SpeechStats()
    async for _ in _stt(svc).stream(_gera(_quadros(pcm)), sample_rate=SR, stats=stats, **kw):
        pass
    return stats


class TestProvedorConta:
    async def test_quadros_ruido_falas_e_confianca(self):
        svc = Servico(textos=["a", "b"], segmentos=[{"start": 0, "end": 1, "avg_logprob": -0.2}])
        ruido = _voz(400, amp=200)                           # RMS ~140: abaixo do limiar, é chão
        st = await _conta(svc, ruido + _voz(600) + _silencio(900) + _voz(500) + _silencio(900))
        assert st.frames == len(_quadros(ruido + _voz(600) + _silencio(900) + _voz(500) + _silencio(900)))
        assert st.voiced_frames == 55 and st.utterances_sent == 2 and st.utterances_transcribed == 2
        assert len(st.confidences) == 2 and st.discarded_vad == st.discarded_short == st.stt_errors == 0
        p10, p50, p90 = st.noise_percentiles()
        assert p10 == 0.0 and p90 == pytest.approx(141.4, abs=1)

    async def test_esvaziado_pelo_vad_curto_e_teto_sao_contados_separados(self):
        svc = Servico(textos=[""], segmentos=[])
        st = await _conta(svc, _voz(100) + _silencio(900) + _voz(600) + _silencio(900))
        assert st.discarded_short == 1 and st.discarded_vad == 1 and st.utterances_transcribed == 0
        teto = await _conta(Servico(textos=["a", "b", "c"]), _voz(2600),
                            segmentation=SpeechSegmentation(max_speech_ms=1000))
        assert teto.cut_max_speech >= 2

    async def test_erro_do_servico_nao_conta_como_descarte_do_vad(self):
        st = await _conta(Servico(status=500), _voz(600) + _silencio(900))
        assert st.stt_errors == 1 and st.discarded_vad == 0 and st.utterances_sent == 1

    def test_sem_amostra_os_percentis_sao_ausentes_nunca_zero(self):
        st = SpeechStats()
        assert st.noise_percentiles() == (None, None, None)
        assert st.confidence_percentiles() == (None, None, None)


class TestNucleoConta:
    def test_tentativas_confianca_e_tecla_depois_de_fala(self):
        plano = CollectPlan.from_menu({**_menu(voice={"min_confidence": 0.8}), "collect": {
            "input": ["dtmf", "voice"], "first_input_timeout_s": 30, "voice": {"min_confidence": 0.8}}})
        s = CollectSession(plano)
        s.speech("email", 0.4, 1.0)          # recusada por confiança
        s.speech("nada a ver", 0.9, 2.0)     # não casa
        s.digit("2", 3.0)                    # desistiu de falar
        assert s.counters() == {"speech_inputs": 2, "digit_inputs": 1, "invalid_attempts": 2,
                                "invalid_low_confidence": 1, "digit_after_speech": True}

    def test_controle_so_tecla_nao_marca_tecla_depois_de_fala(self):
        s = CollectSession(CollectPlan.from_menu(_menu()))
        s.digit("1", 1.0)
        assert s.counters()["digit_after_speech"] is False and s.counters()["speech_inputs"] == 0


class _SttComStats:
    supports_tuning = True
    measures_confidence = True

    async def stream(self, chunks, language=None, stats=None, **kw):
        async for c in chunks:
            if stats is not None:
                stats.frame(20, 100.0, False)
        if stats is not None:
            stats.confidences.append(0.7)
        yield STTResult(transcript="segredo dito pelo cliente", is_final=True, confidence=0.7)



async def _sem_voz_do_tenant(tenant):
    """O tenant não declara modelo/língua/voz (VOZ-17): a resolução cai no env do gateway."""
    return {}, {}


class TestRenderizadorPublica:
    async def test_resumo_do_fluxo_do_cliente_sem_texto_e_so_do_cliente(self):
        room = MockRoomClient()
        room.inject_audio(b"\x00" * 960)
        room.inject_audio(b"\x00" * 960, identity="agent-sub-hum")
        room.end_audio()
        adapter, _, producer = _make_adapter(stt=_SttComStats(), room_client=room)
        _abre(adapter)

        async def _seg(t):
            return SpeechSegmentation(end_silence_ms=900, provenance={"end_silence_ms": "tenant"})
        _seg.voice = _sem_voz_do_tenant              # VOZ-17: o tenant não declara voz
        adapter.speech_config = _seg
        await adapter._stt_pipeline(SESSION_ID, room)
        assert await _ate(lambda: _metricas(producer))
        ev = _metricas(producer)
        assert [e["event_type"] for e in ev] == ["stt_stream_summary"]          # o humano não entra
        e = ev[0]
        assert e["session_id"] == SESSION_ID and e["pool_id"] == "p-stt" and e["speaker"] == "customer"
        assert e["confidence_p50"] == 0.7 and e["noise_rms_p50"] == 100.0
        assert e["segmentation"]["end_silence_ms"] == 900 and e["segmentation_scope"]["end_silence_ms"] == "tenant"
        assert "segredo" not in json.dumps(e)
        chave = [c.kwargs["key"] for c in producer.send.call_args_list if c.args and c.args[0] == speech_metrics.TOPIC]
        assert chave == [SESSION_ID.encode()]

    async def test_desfecho_da_coleta_por_voz_sem_o_valor(self):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_menu(voice={"min_confidence": 0.7}, max_invalid=2))
        await adapter._publish_transcript(SESSION_ID, "email", 0.3, 0, 900)
        await adapter._publish_transcript(SESSION_ID, "correio", 0.9, 0, 900)
        assert await _ate(lambda: _metricas(producer))
        (e,) = _metricas(producer)
        assert (e["event_type"], e["outcome"], e["via"], e["release_reason"]) == ("collect_outcome", "value", "voice", None)
        assert (e["speech_inputs"], e["invalid_attempts"], e["invalid_low_confidence"]) == (2, 1, 1)
        assert e["min_confidence"] == 0.7 and e["inputs"] == ["dtmf", "voice"]
        assert "correio" not in json.dumps(e) and "email" not in json.dumps(e)

    async def test_coleta_liberada_sai_uma_vez_com_o_motivo(self):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_menu())
        await _encerra(adapter)                        # sessão encerrada (fim do teste)
        await asyncio.sleep(0.05)
        adapter._end_collect(SESSION_ID, "de novo", "session_closed")
        assert await _ate(lambda: _metricas(producer))
        await asyncio.sleep(0.1)
        ev = _metricas(producer)
        assert len(ev) == 1 and ev[0]["outcome"] == "released"

    async def test_controle_coleta_so_de_teclado_nao_gera_evento(self):
        adapter, _, producer, _ = _adapter()
        await adapter.deliver_menu(_menu(input=["dtmf"]))
        adapter._end_collect(SESSION_ID, "fim", "session_closed")
        await asyncio.sleep(0.1)
        assert _metricas(producer) == []
