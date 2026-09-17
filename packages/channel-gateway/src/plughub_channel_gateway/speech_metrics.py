"""
speech_metrics.py — telemetria passiva da fala, só NÚMEROS (VOZ-22, camada A da recalibragem de STT).

Tópico `speech.metrics` (schema em `@plughub/schemas/speech-metrics.ts`), chave = `session_id`: os
eventos de uma chamada saem na ordem em que aconteceram. Dois eventos:

  stt_stream_summary  no fim do fluxo de fala do CLIENTE — chão de ruído, falas, descartes, confiança
                      e a segmentação em vigor (com a procedência)
  collect_outcome     no fim de cada menu com coleta por VOZ — desfecho, tentativas, recusas por
                      confiança, tecla depois de fala

⚠️ **Nenhum áudio e nenhum texto** — nem transcrição, nem valor coletado, nem opção escolhida. É o que
faz a camada A dispensar consentimento de tratamento de voz (ADR `adr-voice-media-plane.md` V13); um
campo de texto aqui é regressão de LGPD, não detalhe.

Fire-and-forget: falha de publicação é dita no log e nunca afeta a chamada.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from .adapters.voice_provider import SpeechSegmentation, SpeechStats

logger = logging.getLogger("plughub.channel-gateway.speech_metrics")

TOPIC = "speech.metrics"

_SEG_FIELDS = ("energy_threshold", "end_silence_ms", "gap_ms", "min_speech_ms", "max_speech_ms", "vad_filter")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def publish(producer: Any, event: dict, key: str | None = None) -> bool:
    """Publica com a chave da sessão (ou `key`, para evento que não é de uma sessão — o resultado de
    uma verificação ativa, VOZ-23, chaveado pela verificação). Nunca levanta; `False` = não publicado,
    e o log diz."""
    try:
        await producer.send(TOPIC, key=(key or event["session_id"]).encode("utf-8"),
                            value=json.dumps(event).encode("utf-8"))
        return True
    except Exception as exc:  # noqa: BLE001 — telemetria não derruba chamada, mas não some calada
        logger.warning("speech_metrics: evento %s da sessao %s NAO publicado: %s",
                       event.get("event_type"), event.get("session_id"), exc)
        return False


def stream_summary(
    *, tenant_id: str, session_id: str, pool_id: str | None, stt_provider: str,
    stats: SpeechStats, segmentation: SpeechSegmentation | None,
    speech_profile_id: str | None = None, stt_model: str | None = None,
) -> dict:
    ruido = stats.noise_percentiles()
    conf = stats.confidence_percentiles()
    seg = segmentation or SpeechSegmentation()
    return {
        "event_id":        str(uuid.uuid4()),
        "event_type":      "stt_stream_summary",
        "tenant_id":       tenant_id,
        "session_id":      session_id,
        "pool_id":         pool_id or None,
        "channel":         "webrtc",
        "speaker":         "customer",
        "stt_provider":    stt_provider,
        # VOZ-25: o perfil de fala EM VIGOR (None = sem perfil) e o modelo usado — é por eles que a
        # recalibragem compara, não só por pool
        "speech_profile_id": speech_profile_id or None,
        "stt_model":       stt_model or None,
        "timestamp":       _now_iso(),
        "audio_ms":        int(stats.audio_ms),
        "frames":          stats.frames,
        "voiced_frames":   stats.voiced_frames,
        "noise_rms_p10":   ruido[0],
        "noise_rms_p50":   ruido[1],
        "noise_rms_p90":   ruido[2],
        "utterances_sent":        stats.utterances_sent,
        "utterances_transcribed": stats.utterances_transcribed,
        "discarded_vad":          stats.discarded_vad,
        "discarded_short":        stats.discarded_short,
        "cut_max_speech":         stats.cut_max_speech,
        "stt_errors":             stats.stt_errors,
        "confidence_count":       len(stats.confidences),
        "confidence_p10":  conf[0],
        "confidence_p50":  conf[1],
        "confidence_p90":  conf[2],
        "segmentation":    {f: getattr(seg, f) for f in _SEG_FIELDS},
        # só o ESCOPO (tenant/global/config/default/profile), sem o motivo nem o id — a tabela agrega por ele
        "segmentation_scope": {f: (seg.provenance.get(f) or "default").split(":")[0] for f in _SEG_FIELDS},
    }


def collect_outcome(
    *, tenant_id: str, session_id: str, pool_id: str | None, menu_id: str, interaction: str,
    inputs: list[str], outcome: str, via: str, release_reason: str | None, counters: dict,
    min_confidence: float | None, end_silence_ms: int | None, max_speech_ms: int | None,
    duration_ms: int, speech_profile_id: str | None = None,
) -> dict:
    return {
        "event_id":        str(uuid.uuid4()),
        "event_type":      "collect_outcome",
        "tenant_id":       tenant_id,
        "session_id":      session_id,
        "pool_id":         pool_id or None,
        "channel":         "webrtc",
        "speech_profile_id": speech_profile_id or None,
        "timestamp":       _now_iso(),
        "menu_id":         menu_id,
        "interaction":     interaction,
        "inputs":          sorted(inputs),
        "outcome":         outcome,
        "via":             via or None,
        "release_reason":  release_reason,
        "speech_inputs":          counters["speech_inputs"],
        "digit_inputs":           counters["digit_inputs"],
        "invalid_attempts":       counters["invalid_attempts"],
        "invalid_low_confidence": counters["invalid_low_confidence"],
        "digit_after_speech":     counters["digit_after_speech"],
        "min_confidence":  min_confidence,
        "end_silence_ms":  end_silence_ms,
        "max_speech_ms":   max_speech_ms,
        "duration_ms":     int(duration_ms),
    }
