"""
Testes do provedor auto-hospedado de STT/TTS (VOZ-05, fatia 2).

O serviço é trocado por um `httpx.MockTransport` que RESPONDE como o `speaches` e GRAVA o que
recebeu — os testes afirmam o que foi PEDIDO (quantas falas, com que áudio, em que língua) e o
que sai quando o serviço falha. A fala real contra o serviço real é do
`infra/test/probe_webrtc_stt_speaches.sh`.
"""
from __future__ import annotations

import asyncio
import io
import math
import wave

import httpx
import numpy as np
import pytest

from ..adapters.speaches_provider import (
    SpeachesSTTProvider,
    SpeachesTTSProvider,
    confianca_dos_segmentos,
    pcm16_48k_to_16k,
    rms,
)
from ..adapters.voice_provider import SpeechTuning

SR = 16_000
QUADRO_MS = 20


def _voz(ms: int, amp: int = 3000) -> bytes:
    n = SR * ms // 1000
    t = np.arange(n) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.int16).tobytes()


def _silencio(ms: int) -> bytes:
    return b"\x00\x00" * (SR * ms // 1000)


def _quadros(pcm: bytes) -> list[bytes]:
    passo = SR * QUADRO_MS // 1000 * 2
    return [pcm[i:i + passo] for i in range(0, len(pcm), passo)]


class Servico:
    """`segmentos`: o que o `verbose_json` devolve em `segments` (default: um, avg_logprob -0,2)."""
    def __init__(self, status: int = 200, textos: list[str] | None = None,
                 segmentos: list | None = None) -> None:
        self.status = status
        self.textos = list(textos or ["ola"])
        self.segmentos = [{"start": 0.0, "end": 1.0, "avg_logprob": -0.2, "no_speech_prob": 0.0}] \
            if segmentos is None else segmentos
        self.pedidos: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.pedidos.append(req)
        if req.url.path == "/v1/audio/transcriptions":
            if self.status != 200:
                return httpx.Response(self.status, text="Model 'x' is not installed locally")
            return httpx.Response(200, json={"text": self.textos.pop(0) if self.textos else "",
                                             "segments": self.segmentos})
        if req.url.path == "/v1/audio/speech":
            if self.status != 200:
                return httpx.Response(self.status, text="erro")
            return httpx.Response(200, content=b"\x01\x00" * 100)
        return httpx.Response(404)


async def _gera(quadros: list[bytes], pausa_s: float = 0.0):
    for q in quadros:
        yield q
        if pausa_s:
            await asyncio.sleep(pausa_s)


def _stt(servico: Servico, **kw) -> SpeachesSTTProvider:
    return SpeachesSTTProvider("http://speaches:8000", "Systran/faster-whisper-small",
                               http=httpx.AsyncClient(transport=httpx.MockTransport(servico)), **kw)


async def _colhe(stt: SpeachesSTTProvider, quadros, **kw):
    return [r async for r in stt.stream(quadros, sample_rate=SR, language="pt-BR", **kw)]


def _wav_do_pedido(req: httpx.Request) -> tuple[int, float]:
    corpo = req.content
    ini = corpo.index(b"RIFF")
    with wave.open(io.BytesIO(corpo[ini:]), "rb") as w:
        return w.getframerate(), w.getnframes() / w.getframerate()


class TestSegmentacao:
    @pytest.mark.asyncio
    async def test_duas_falas_separadas_por_silencio_viram_dois_pedidos(self):
        svc = Servico(textos=["quero minha fatura", "obrigado"])
        pcm = _voz(600) + _silencio(900) + _voz(500) + _silencio(900)
        res = await _colhe(_stt(svc), _gera(_quadros(pcm)))
        assert [r.transcript for r in res] == ["quero minha fatura", "obrigado"]
        assert all(r.is_final for r in res)
        assert len(svc.pedidos) == 2
        taxa, dur = _wav_do_pedido(svc.pedidos[0])
        assert taxa == SR and 0.6 <= dur <= 1.4          # a fala e o silêncio que a fechou
        assert b'name="language"' in svc.pedidos[0].content and b"\r\n\r\npt\r\n" in svc.pedidos[0].content

    @pytest.mark.asyncio
    async def test_silencio_puro_nao_gera_pedido(self):
        svc = Servico()
        assert await _colhe(_stt(svc), _gera(_quadros(_silencio(3000)))) == []
        assert svc.pedidos == []

    @pytest.mark.asyncio
    async def test_estalo_curto_e_ruido_nao_fala(self):
        svc = Servico()
        pcm = _voz(100) + _silencio(1000)
        assert await _colhe(_stt(svc, min_speech_ms=250), _gera(_quadros(pcm))) == []
        assert svc.pedidos == []

    @pytest.mark.asyncio
    async def test_microfone_mudo_sem_quadros_fecha_a_fala_pela_lacuna(self):
        svc = Servico(textos=["alo"])
        fila: asyncio.Queue = asyncio.Queue()
        for q in _quadros(_voz(600)):
            fila.put_nowait(q)

        async def fonte():
            while True:
                q = await fila.get()
                if q is None:
                    return
                yield q

        stt = _stt(svc, gap_ms=200)
        coleta = asyncio.create_task(_colhe(stt, fonte()))
        await asyncio.sleep(0.6)          # nenhum quadro: a lacuna tem de fechar a fala
        assert len(svc.pedidos) == 1
        fila.put_nowait(None)
        res = await coleta
        assert [r.transcript for r in res] == ["alo"]

    @pytest.mark.asyncio
    async def test_fala_longa_e_cortada_no_teto(self):
        svc = Servico(textos=["a", "b"])
        pcm = _voz(2600)
        res = await _colhe(_stt(svc, max_utterance_ms=1200), _gera(_quadros(pcm)))
        assert len(svc.pedidos) >= 2 and [r.transcript for r in res][:2] == ["a", "b"]


class TestConfianca:
    """VOZ-18: a confiança é MEDIDA do `verbose_json`, e ausência de medida nunca vira 1,0."""

    @pytest.mark.asyncio
    async def test_pede_verbose_json_e_mede_pela_media_ponderada_pela_duracao(self):
        # 1 s a -0,1 e 3 s a -0,9: média ponderada -0,7 → exp = 0,4966 (a simples daria 0,6065)
        svc = Servico(textos=["quero a fatura"], segmentos=[
            {"start": 0.0, "end": 1.0, "avg_logprob": -0.1}, {"start": 1.0, "end": 4.0, "avg_logprob": -0.9}])
        res = await _colhe(_stt(svc), _gera(_quadros(_voz(600) + _silencio(900))))
        assert b"verbose_json" in svc.pedidos[0].content
        assert res[0].confidence == pytest.approx(math.exp(-0.7), abs=1e-3)

    @pytest.mark.asyncio
    async def test_sem_segmentos_a_confianca_e_nao_medida_e_o_log_diz(self, caplog):
        svc = Servico(textos=["ola"], segmentos=[])
        with caplog.at_level("WARNING"):
            res = await _colhe(_stt(svc), _gera(_quadros(_voz(600) + _silencio(900))))
        assert [r.transcript for r in res] == ["ola"]
        assert res[0].confidence is None
        assert "NAO medida" in caplog.text

    def test_segmento_sem_avg_logprob_nao_entra_na_media(self):
        assert confianca_dos_segmentos([{"start": 0, "end": 1}, {"start": 1, "end": 2, "avg_logprob": 0.0}]) == 1.0
        assert confianca_dos_segmentos([{"start": 0, "end": 1}]) is None
        assert confianca_dos_segmentos(None) is None

    def test_provedor_declara_que_mede_e_que_ajusta(self):
        assert SpeachesSTTProvider.measures_confidence is True
        assert SpeachesSTTProvider.supports_tuning is True


class TestVad:
    """VOZ-19: o VAD do serviço vai ligado em todo pedido, e o trecho que ele esvazia é dito no log."""

    @pytest.mark.asyncio
    async def test_todo_pedido_leva_vad_ligado(self):
        svc = Servico(textos=["ola"])
        await _colhe(_stt(svc), _gera(_quadros(_voz(600) + _silencio(900))))
        assert b'name="vad_filter"' in svc.pedidos[0].content and b"\r\n\r\ntrue\r\n" in svc.pedidos[0].content

    @pytest.mark.asyncio
    async def test_controle_vad_desligado_manda_false(self):
        svc = Servico(textos=["ola"])
        await _colhe(_stt(svc, vad_filter=False), _gera(_quadros(_voz(600) + _silencio(900))))
        assert b"\r\n\r\nfalse\r\n" in svc.pedidos[0].content

    @pytest.mark.asyncio
    async def test_trecho_sem_fala_pelo_vad_nao_vira_resultado_e_o_log_diz(self, caplog):
        svc = Servico(textos=[""], segmentos=[])
        with caplog.at_level("INFO"):
            res = await _colhe(_stt(svc), _gera(_quadros(_voz(600) + _silencio(900))))
        assert res == [] and len(svc.pedidos) == 1
        assert "sem fala pelo VAD" in caplog.text


class TestAjustePorColeta:
    """VOZ-18: silêncio de fim e fala máxima mudam NO MEIO do fluxo, lidos a cada quadro."""

    @pytest.mark.asyncio
    async def test_silencio_de_fim_ajustado_junta_falas_que_o_default_separaria(self):
        pcm = _voz(600) + _silencio(900) + _voz(500) + _silencio(1800)
        controle = Servico(textos=["a", "b"])
        assert len(await _colhe(_stt(controle), _gera(_quadros(pcm)))) == 2   # default 700 ms: duas
        svc = Servico(textos=["a b"])
        res = await _colhe(_stt(svc), _gera(_quadros(pcm)), tuning=SpeechTuning(silence_ms=1500))
        assert [r.transcript for r in res] == ["a b"] and len(svc.pedidos) == 1

    @pytest.mark.asyncio
    async def test_fala_maxima_ajustada_corta_antes_do_teto_default(self):
        svc = Servico(textos=["a", "b", "c"])
        res = await _colhe(_stt(svc), _gera(_quadros(_voz(2600))), tuning=SpeechTuning(max_utterance_ms=1000))
        assert len(svc.pedidos) >= 2
        controle = Servico(textos=["x"])
        await _colhe(_stt(controle), _gera(_quadros(_voz(2600) + _silencio(900))))
        assert len(controle.pedidos) == 1                                     # default 15 s: uma

    @pytest.mark.asyncio
    async def test_ajuste_mudado_durante_o_fluxo_vale_do_quadro_seguinte(self):
        pcm_a = _voz(600) + _silencio(900)          # 900 ms: não fecha sob 1500, fecha sob 700
        pcm_b = _silencio(100) + _voz(500) + _silencio(900)

        async def fonte(tuning, limpa):
            for q in _quadros(pcm_a):
                yield q
            if limpa:
                tuning.clear()                # a coleta terminou: volta o default (700 ms)
            for q in _quadros(pcm_b):
                yield q

        controle = Servico(textos=["a b"])
        t = SpeechTuning(silence_ms=1500)
        assert len(await _colhe(_stt(controle), fonte(t, False), tuning=t)) == 1   # sem limpar: uma
        svc = Servico(textos=["a", "b"])
        t = SpeechTuning(silence_ms=1500)
        assert len(await _colhe(_stt(svc), fonte(t, True), tuning=t)) == 2


class TestFalhaNaoEMuda:
    @pytest.mark.asyncio
    async def test_modelo_ausente_perde_a_fala_e_diz(self, caplog):
        svc = Servico(status=404)
        with caplog.at_level("ERROR"):
            res = await _colhe(_stt(svc), _gera(_quadros(_voz(600) + _silencio(900))))
        assert res == []
        assert "http 404" in caplog.text and "faster-whisper-small" in caplog.text and "PERDIDA" in caplog.text

    @pytest.mark.asyncio
    async def test_servico_fora_perde_a_fala_e_diz(self, caplog):
        def cai(req):
            raise httpx.ConnectError("recusado")
        stt = SpeachesSTTProvider("http://speaches:8000", "m", http=httpx.AsyncClient(transport=httpx.MockTransport(cai)))
        with caplog.at_level("ERROR"):
            res = await _colhe(stt, _gera(_quadros(_voz(600) + _silencio(900))))
        assert res == [] and "inalcancavel" in caplog.text


class TestTTS:
    @pytest.mark.asyncio
    async def test_pede_pcm_na_taxa_declarada_e_devolve_os_bytes(self):
        svc = Servico()
        tts = SpeachesTTSProvider("http://speaches:8000", "speaches-ai/piper-pt_BR-faber-medium", "faber",
                                  http=httpx.AsyncClient(transport=httpx.MockTransport(svc)))
        pcm = await tts.synthesize("Bom dia")
        assert pcm == b"\x01\x00" * 100
        import json
        corpo = json.loads(svc.pedidos[0].content)
        assert corpo["response_format"] == "pcm" and corpo["sample_rate"] == tts.output_sample_rate
        assert corpo["input"] == "Bom dia" and corpo["model"].endswith("faber-medium")

    @pytest.mark.asyncio
    async def test_falha_devolve_none_e_diz(self, caplog):
        tts = SpeachesTTSProvider("http://speaches:8000", "m", http=httpx.AsyncClient(
            transport=httpx.MockTransport(Servico(status=500))))
        with caplog.at_level("ERROR"):
            assert await tts.synthesize("x") is None
        assert "NAO sintetizada" in caplog.text


def test_reamostragem_48k_para_16k_mantem_duracao_e_energia():
    n = 48000 // 10
    t = np.arange(n) / 48000
    x = (4000 * np.sin(2 * np.pi * 300 * t)).astype(np.int16).tobytes()
    y = pcm16_48k_to_16k(x)
    assert len(y) == len(x) // 3
    assert 0.8 * rms(x) <= rms(y) <= 1.05 * rms(x)
