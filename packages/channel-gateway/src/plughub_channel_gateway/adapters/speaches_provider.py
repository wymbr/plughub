"""
adapters/speaches_provider.py — STT e TTS do bot leg por serviço AUTO-HOSPEDADO (VOZ-05).

Decisão do dono (2026-09-15): a conversão de voz acompanha o deploy, como o SFU. O serviço é o
`speaches` do compose — faster-whisper e Piper atrás de uma API compatível com a da OpenAI:
  POST /v1/audio/transcriptions   (multipart: arquivo WAV, `model`, `language`)
  POST /v1/audio/speech           (JSON: `model`, `input`, `voice`, `response_format=pcm`, `sample_rate`)

POR QUE SEGMENTAR AQUI
  A transcrição do serviço é por ARQUIVO, e o bot recebe quadros contínuos. O provedor junta os
  quadros de uma FALA — fecha o trecho em silêncio sustentado, em lacuna sem quadro nenhum
  (microfone mudo não manda silêncio, manda nada) ou no teto de duração — e transcreve o trecho.
  É o `is_final` do contrato `ISTTProvider`; parciais não existem neste provedor.

FALHA NUNCA É MUDA
  Serviço fora, modelo não instalado (404 do serviço) ou resposta ilegível: o trecho é perdido e
  o log diz qual, com o modelo — o cliente falou e ninguém ouviu é exatamente o que não pode
  passar calado.
"""
from __future__ import annotations

import asyncio
import io
import logging
import math
import wave
from typing import AsyncIterator

import httpx
import numpy as np

from .voice_provider import SpeechTuning, STTResult

logger = logging.getLogger("plughub.channel-gateway.speaches")

# A transcrição trabalha a 16 kHz; é a taxa que o bot entrega a este provedor.
STT_SAMPLE_RATE = 16_000


def pcm16_48k_to_16k(chunk: bytes) -> bytes:
    """48 kHz → 16 kHz, 16 bits mono: média de 3 amostras (passa-baixa grosseiro) e decimação."""
    x = np.frombuffer(chunk[: len(chunk) - len(chunk) % 6], dtype=np.int16)
    if x.size == 0:
        return b""
    return x.reshape(-1, 3).mean(axis=1).astype(np.int16).tobytes()


def rms(chunk: bytes) -> float:
    x = np.frombuffer(chunk[: len(chunk) - len(chunk) % 2], dtype=np.int16)
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if x.size else 0.0


def wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class SpeachesSTTProvider:
    """`ISTTProvider` sobre o `speaches`. Recebe PCM16 mono a `input_sample_rate`."""

    input_sample_rate = STT_SAMPLE_RATE
    # VOZ-18: a transcrição pede `verbose_json` e a confiança é MEDIDA — exp da média de
    # `avg_logprob` dos segmentos, ponderada pela duração (a probabilidade média por token).
    # `no_speech_prob` não entra: medido em 2026-09-16, veio 0,0 em 102 de 102 casos, inclusive
    # ruído puro. Resposta sem segmentos = confiança None (não medida), nunca 1,0.
    measures_confidence = True
    # silêncio que fecha a fala e fala máxima ajustáveis por coleta, lidos a cada quadro
    supports_tuning = True

    def __init__(
        self,
        base_url: str,
        model:    str,
        *,
        energy_threshold: float = 400.0,   # RMS em escala int16 — fala próxima ao microfone passa de ~1000
        silence_ms:       int   = 700,     # silêncio que fecha a fala
        gap_ms:           int   = 700,     # sem quadro nenhum por tanto tempo também fecha
        min_speech_ms:    int   = 250,     # abaixo disto é ruído, não fala
        max_utterance_ms: int   = 15_000,
        http:             httpx.AsyncClient | None = None,
    ) -> None:
        self._url = base_url.rstrip("/")
        self._model = model
        self._thr = energy_threshold
        self._silence_ms = silence_ms
        self._gap_s = gap_ms / 1000
        self._min_speech_ms = min_speech_ms
        self._max_ms = max_utterance_ms
        self._http = http

    async def stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        sample_rate:  int = STT_SAMPLE_RATE,
        language:     str = "pt-BR",
        tuning:       SpeechTuning | None = None,
    ) -> AsyncIterator[STTResult]:
        it = audio_chunks.__aiter__()
        buf = bytearray()
        speech_ms = silence_ms = pos_ms = start_ms = 0.0
        lang = (language or "").split("-")[0] or None

        async def _fecha() -> STTResult | None:
            nonlocal buf, speech_ms, silence_ms
            pcm, falou = bytes(buf), speech_ms
            buf, speech_ms, silence_ms = bytearray(), 0.0, 0.0
            if falou < self._min_speech_ms:
                return None
            texto, confianca = await self._transcribe(pcm, sample_rate, lang)
            if not texto:
                return None
            return STTResult(transcript=texto, is_final=True, confidence=confianca,
                             start_ms=int(start_ms), end_ms=int(pos_ms))

        while True:
            try:
                chunk = await asyncio.wait_for(it.__anext__(), timeout=self._gap_s if buf else None)
            except asyncio.TimeoutError:
                res = await _fecha()           # lacuna sem quadro: microfone mudo fecha a fala
                if res:
                    yield res
                continue
            except StopAsyncIteration:
                break
            dur = len(chunk) / 2 / sample_rate * 1000
            pos_ms += dur
            voz = rms(chunk) >= self._thr
            if voz:
                if not buf:
                    start_ms = pos_ms - dur
                buf += chunk
                speech_ms += dur
                silence_ms = 0.0
            elif buf:
                buf += chunk
                silence_ms += dur
            limite_silencio = (tuning.silence_ms if tuning and tuning.silence_ms else self._silence_ms)
            limite_fala = (tuning.max_utterance_ms if tuning and tuning.max_utterance_ms else self._max_ms)
            if buf and (silence_ms >= limite_silencio or len(buf) / 2 / sample_rate * 1000 >= limite_fala):
                res = await _fecha()
                if res:
                    yield res
        if buf:
            res = await _fecha()
            if res:
                yield res

    async def _transcribe(self, pcm: bytes, sample_rate: int, language: str | None) -> tuple[str, float | None]:
        data = {"model": self._model, "response_format": "verbose_json"}
        if language:
            data["language"] = language
        try:
            client = self._http or httpx.AsyncClient(timeout=60)
            try:
                r = await client.post(f"{self._url}/v1/audio/transcriptions",
                                      files={"file": ("fala.wav", wav_bytes(pcm, sample_rate), "audio/wav")},
                                      data=data)
            finally:
                if self._http is None:
                    await client.aclose()
        except Exception as exc:
            logger.error("speaches STT: servico inalcancavel (%s, modelo %s): %s — fala PERDIDA",
                         self._url, self._model, exc)
            return "", None
        if r.status_code != 200:
            logger.error("speaches STT: http %s (modelo %s): %s — fala PERDIDA",
                         r.status_code, self._model, r.text[:200])
            return "", None
        try:
            corpo = r.json()
        except ValueError:
            logger.error("speaches STT: resposta nao-JSON (modelo %s) — fala PERDIDA", self._model)
            return "", None
        texto = str(corpo.get("text", "")).strip()
        confianca = confianca_dos_segmentos(corpo.get("segments"))
        if texto and confianca is None:
            logger.warning("speaches STT: resposta sem segmentos com avg_logprob (modelo %s) — "
                           "confianca da fala NAO medida", self._model)
        return texto, confianca


def confianca_dos_segmentos(segments: object) -> float | None:
    """exp da média de `avg_logprob`, ponderada pela duração de cada segmento. `None` sem segmento
    legível. Medido em 2026-09-16 (fala sintetizada, 3 vozes, limpa e com ruído): certas mediana
    0,81, erradas 0,45, alucinação em ruído 0,49–0,61 — separação boa, mas o LIMIAR não é
    default: é `collect.voice.min_confidence`, declarado por quem escreve o fluxo."""
    if not isinstance(segments, list):
        return None
    soma = peso = 0.0
    for s in segments:
        if not isinstance(s, dict) or not isinstance(s.get("avg_logprob"), (int, float)):
            continue
        dur = max(0.01, float(s.get("end", 0) or 0) - float(s.get("start", 0) or 0))
        soma += float(s["avg_logprob"]) * dur
        peso += dur
    return round(math.exp(soma / peso), 4) if peso else None


class SpeachesTTSProvider:
    """`ITTSProvider` sobre o `speaches`. Devolve PCM16 mono a `output_sample_rate` — nunca MP3."""

    output_sample_rate = 24_000

    def __init__(self, base_url: str, model: str, voice: str = "", *, http: httpx.AsyncClient | None = None) -> None:
        self._url = base_url.rstrip("/")
        self._model = model
        # O Piper tem UMA voz por modelo e o serviço não confere o campo; ele é obrigatório na API.
        self._voice = voice or "default"
        self._http = http

    async def synthesize(self, text: str, voice_id: str | None = None) -> bytes | None:
        body = {"model": self._model, "input": text, "voice": voice_id or self._voice,
                "response_format": "pcm", "sample_rate": self.output_sample_rate}
        try:
            client = self._http or httpx.AsyncClient(timeout=60)
            try:
                r = await client.post(f"{self._url}/v1/audio/speech", json=body)
            finally:
                if self._http is None:
                    await client.aclose()
        except Exception as exc:
            logger.error("speaches TTS: servico inalcancavel (%s, modelo %s): %s — fala NAO sintetizada",
                         self._url, self._model, exc)
            return None
        if r.status_code != 200 or not r.content:
            logger.error("speaches TTS: http %s (modelo %s): %s — fala NAO sintetizada",
                         r.status_code, self._model, r.text[:200])
            return None
        return r.content
