"""
runner.py — UMA verificação ativa do caminho de fala (VOZ-23).

Ordem, e por que cada passo existe:

  1. perfil pedido existe e a língua dele tem conjunto de referência — senão a chamada cairia na config
     do tenant EM SILÊNCIO (o gateway só avisa no log) e o resultado seria atribuído ao perfil errado
  2. endpoint WebRTC TEMPORÁRIO → pool de calibração, com o perfil em `settings`: a chamada passa pela
     resolução REAL (registro → gateway → perfil), sem caminho especial (decisão do dono)
  3. ouvinte no Kafka ANTES da chamada: transcrições em `conversations.inbound` (o que o gateway publica
     depois do STT, independe do passo do fluxo) e o resumo em `speech.metrics` (que perfil e modelo o
     gateway APLICOU — o pedido não prova a aplicação)
  4. a chamada: widget WS + participante LiveKit; espera a voz do bot terminar; fala item a item, cada um
     com a sua janela; desliga
  5. o resumo do gateway fecha o resultado; o endpoint temporário é apagado SEMPRE (finally)

Falha em qualquer passo vira resultado `failed` com `failure_reason` nomeado — nunca exceção solta, e
nunca "completed" com zeros.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from . import reference

logger = logging.getLogger("plughub.speech-check.runner")

FAILURE_REASONS = (
    "profile_not_found", "unsupported_language", "config_unavailable", "endpoint_create_failed",
    "tts_unavailable", "listener_unavailable", "call_not_answered", "call_failed", "summary_missing",
)


class CheckFailed(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        assert reason in FAILURE_REASONS, reason
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


class Deps(Protocol):
    """O que a verificação usa do mundo — trocado nos testes."""
    async def get_profiles(self, tenant_id: str) -> dict: ...
    async def create_endpoint(self, tenant_id: str, identifier: str, pool_id: str, settings: dict) -> str: ...
    async def delete_endpoint(self, tenant_id: str, endpoint_id: str) -> None: ...
    async def synthesize(self, text: str) -> bytes: ...
    def listener(self) -> "Listener": ...
    def call(self) -> "Call": ...


class Listener(Protocol):
    async def start(self) -> None: ...
    def transcripts(self, session_id: str, since: float, until: float) -> list[tuple[str, float | None]]: ...
    async def summary(self, session_id: str, timeout_s: float) -> dict | None: ...
    async def stop(self) -> None: ...


class Call(Protocol):
    async def open(self, identifier: str) -> str: ...          # devolve o session_id; levanta CheckFailed
    async def wait_bot_voice(self, timeout_s: float) -> bool: ...
    async def speak(self, pcm: bytes) -> None: ...             # volta quando o áudio acabou de sair
    async def close(self) -> None: ...


@dataclass
class Timing:
    settle_s:        float = 2.0     # depois de criar o endpoint (invalidação do cache do gateway)
    window_s:        float = 6.0     # depois de cada item: fim de fala + transcrição
    bot_voice_s:     float = 30.0
    summary_s:       float = 30.0


@dataclass
class CheckRequest:
    tenant_id:         str
    speech_profile_id: str | None
    requested_by:      str
    check_id:          str = field(default_factory=lambda: str(uuid.uuid4()))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def result_event(req: CheckRequest, *, pool_id: str, started_at: str, status: str, failure_reason: str | None,
                 session_id: str | None, language: str | None, judged: list[dict], summary: dict | None,
                 bot_voice_heard: bool | None) -> dict:
    agg = reference.summarize(judged) if status == "completed" else {k: None for k in reference.summarize([])}
    if status != "completed":
        agg.update(phrases_total=0, phrases_correct=0, phrases_transcribed=0, noise_total=0, hallucinations=0)
    s = summary or {}
    return {
        "event_id":          str(uuid.uuid4()),
        "event_type":        "speech_check_result",
        "tenant_id":         req.tenant_id,
        "session_id":        session_id,
        "pool_id":           pool_id,
        "channel":           "webrtc",
        "speech_profile_id": req.speech_profile_id,
        "timestamp":         _now(),
        "check_id":          req.check_id,
        "requested_by":      req.requested_by,
        "reference_version": reference.REFERENCE_VERSION,
        "language":          language,
        "status":            status,
        "failure_reason":    failure_reason,
        "started_at":        started_at,
        "bot_voice_heard":   bot_voice_heard,
        # o que o GATEWAY aplicou, lido do resumo dele — o pedido não prova a aplicação
        "profile_in_effect": s.get("speech_profile_id") if summary else None,
        "stt_model":         s.get("stt_model") if summary else None,
        "discarded_vad":     s.get("discarded_vad") if summary else None,
        "utterances_sent":   s.get("utterances_sent") if summary else None,
        "segmentation":      s.get("segmentation") if summary else None,
        **agg,
        "items":             judged if status == "completed" else [],
    }


async def run_check(req: CheckRequest, deps: Deps, *, pool_id: str, default_language: str,
                    timing: Timing | None = None) -> dict:
    tm = timing or Timing()
    started = _now()
    session_id: str | None = None
    language: str | None = None
    endpoint_id: str | None = None
    listener: Listener | None = None
    call: Call | None = None
    bot_voice: bool | None = None

    def falhou(exc: CheckFailed) -> dict:
        logger.warning("speech-check %s FALHOU (%s): %s", req.check_id, exc.reason, exc.detail)
        return result_event(req, pool_id=pool_id, started_at=started, status="failed",
                            failure_reason=exc.reason, session_id=session_id, language=language,
                            judged=[], summary=None, bot_voice_heard=bot_voice)

    try:
        # 1 — perfil e língua
        language = default_language
        if req.speech_profile_id:
            try:
                perfis = await deps.get_profiles(req.tenant_id)
            except Exception as exc:  # noqa: BLE001 — vira resultado nomeado
                raise CheckFailed("config_unavailable", str(exc)) from exc
            perfil = perfis.get(req.speech_profile_id)
            if not isinstance(perfil, dict):
                raise CheckFailed("profile_not_found", req.speech_profile_id)
            language = perfil.get("stt_language") or default_language
        itens = reference.items_for(language)
        if itens is None:
            raise CheckFailed("unsupported_language", language)

        # frases sintetizadas ANTES de abrir a chamada: TTS fora não deixa sessão pendurada
        audios: dict[str, bytes] = {}
        for it in itens:
            try:
                audios[it.id] = reference.noise_pcm() if it.kind == "noise" else await deps.synthesize(it.text)
            except Exception as exc:  # noqa: BLE001
                raise CheckFailed("tts_unavailable", f"{it.id}: {exc}") from exc

        # 2 — endpoint temporário
        identifier = f"speech-check-{req.check_id[:12]}"
        settings = {"speech_profile_id": req.speech_profile_id} if req.speech_profile_id else {}
        try:
            endpoint_id = await deps.create_endpoint(req.tenant_id, identifier, pool_id, settings)
        except Exception as exc:  # noqa: BLE001
            raise CheckFailed("endpoint_create_failed", str(exc)) from exc
        await asyncio.sleep(tm.settle_s)

        # 3 — ouvinte antes da chamada
        listener = deps.listener()
        try:
            await listener.start()
        except Exception as exc:  # noqa: BLE001
            raise CheckFailed("listener_unavailable", str(exc)) from exc

        # 4 — a chamada
        call = deps.call()
        session_id = await call.open(identifier)
        bot_voice = await call.wait_bot_voice(tm.bot_voice_s)
        janelas: list[tuple[reference.Item, float, float]] = []
        for it in itens:
            t0 = time.monotonic()
            await call.speak(audios[it.id])
            await asyncio.sleep(tm.window_s)
            janelas.append((it, t0, time.monotonic()))
        await call.close()
        call = None

        # 5 — o resumo do gateway e o julgamento
        resumo = await listener.summary(session_id, tm.summary_s)
        if resumo is None:
            raise CheckFailed("summary_missing", f"sem stt_stream_summary da sessao {session_id} em {tm.summary_s:.0f} s")
        judged = [reference.judge(it, listener.transcripts(session_id, ini, fim)) for it, ini, fim in janelas]
        ev = result_event(req, pool_id=pool_id, started_at=started, status="completed", failure_reason=None,
                          session_id=session_id, language=language, judged=judged, summary=resumo,
                          bot_voice_heard=bot_voice)
        if req.speech_profile_id and ev["profile_in_effect"] != req.speech_profile_id:
            logger.error("speech-check %s: pedido o perfil %r, o gateway aplicou %r — o resultado NAO e do perfil",
                         req.check_id, req.speech_profile_id, ev["profile_in_effect"])
        logger.info("speech-check %s concluida: %d/%d frases certas, %d alucinacao(oes), perfil em vigor %r",
                    req.check_id, ev["phrases_correct"], ev["phrases_total"], ev["hallucinations"],
                    ev["profile_in_effect"])
        return ev
    except CheckFailed as exc:
        return falhou(exc)
    except Exception as exc:  # noqa: BLE001 — erro não previsto também vira resultado, dito
        logger.exception("speech-check %s: erro nao previsto", req.check_id)
        return falhou(CheckFailed("call_failed", repr(exc)))
    finally:
        if call is not None:
            try:
                await call.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("speech-check %s: fechar a chamada falhou: %s", req.check_id, exc)
        if listener is not None:
            try:
                await listener.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("speech-check %s: parar o ouvinte falhou: %s", req.check_id, exc)
        if endpoint_id is not None:
            try:
                await deps.delete_endpoint(req.tenant_id, endpoint_id)
            except Exception as exc:  # noqa: BLE001 — endpoint órfão é dito, com o id para limpar
                logger.error("speech-check %s: endpoint temporario %s NAO apagado: %s", req.check_id, endpoint_id, exc)
