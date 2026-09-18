"""
adapters/webrtc.py
WebRTC channel adapter — LiveKit SFU signaling, STT/TTS pipeline, session lifecycle.

Architecture: docs/arcos/arc15-webrtc.md

WebSocket endpoint: /ws/webrtc/{pool_id}
Token endpoint:     GET /webrtc/token/{session_id}

WebSocket protocol (per-connection state machine):

  Client → Server:
    {"type": "conn.hello",        "version": "1"}
    {"type": "conn.authenticate", "token": "<customer_JWT>"}
    {"type": "webrtc.hangup"}                    — customer ends call
    {"type": "webrtc.message",    "text": "..."}  — DataChannel text (medium=text)
    {"type": "webrtc.menu_submit",
     "menu_id": "...", "interaction": "form|text|button|list|checklist",
     "result": "..."|[...]|{...}}                 — resposta de menu (VOZ-05)
    {"type": "conn.ping"}                         — keepalive probe

  Server → Client:
    {"type": "conn.ready"}
    {"type": "conn.authenticated", "session_id": "...", "participant_id": "..."}
    {"type": "webrtc.ready",
     "livekit_url": "wss://...", "token": "...", "room_name": "plughub-{session_id}",
     "publish": ["audio","video"], "policy_sources": ["pool:video_humano"]}
    {"type": "webrtc.media",                       — o TETO do cliente mudou (VOZ-09)
     "token": "...", "room_name": "...", "publish": [...], "policy_sources": [...],
     "reason": "attendant_joined:human|attendant_left:human|..."}

  ⚠️ `publish` é TETO, não ordem: o cliente liga microfone e câmera por ESCOLHA, dentro
     dele, e o SFU recusa o que estiver fora. Até 2026-09-14 o servidor mandava UM meio
     (`negotiated_medium`) para a sessão inteira e `webrtc.renegotiate` o sobrescrevia a
     cada atribuição — um especialista de texto rebaixava o cliente de uma chamada de vídeo.
    {"type": "webrtc.message",   "text": "...", "author": "agent", "ts": "..."}
    {"type": "webrtc.interaction", "menu_id": "...", "interaction": "...", "prompt": "...",
     "options": [...], "fields": [...], "masked_fields": [...],
     "collect": {"input", "domain", "min_digits", "max_digits", "terminator"} | null}   — teclado (5c)
    {"type": "conn.error", "code": "collect_invalid", "menu_id": "...", "message": "..."}  — tela fora
                                                  do domínio/tamanho da coleta (VOZ-05 5c)
    {"type": "webrtc.typing",    "active": true|false}
    {"type": "webrtc.session_closed", "reason": "..."}
    {"type": "conn.pong"}
    {"type": "conn.error",       "code": "...", "message": "..."}

Token endpoint (agent / supervisor joining LiveKit room):
  GET /webrtc/token/{session_id}?role=agent|supervisor
  Authorization: Bearer <agent_JWT>
  Response: {"token", "livekit_url", "room_name", "publish", "customer_publish",
             "hidden", "policy_sources"}

Phase C — STT/TTS pipeline (Arc 15):
  - LiveKitRoomClient connects as bot, subscribes to customer audio track
  - resample_pcm_48_to_8() converts 48kHz PCM → 8kHz μ-law for Deepgram
  - STT finals published to conversations.inbound (content_type=audio_transcript)
  - TTS: MP3 bytes → PCM → LocalAudioTrack injection (when the customer ceiling carries audio)
  - DataChannel text (webrtc.message) → Kafka conversations.inbound (medium=text)
  - Menu reply (webrtc.menu_submit) → conversations.inbound `menu_result`, como o webchat;
    durante coleta mascarada, fala transcrita e texto livre NÃO são publicados (VOZ-05)

Security invariants (from arc15-webrtc.md):
  - LiveKit tokens are signed exclusively by Channel Gateway.
  - LIVEKIT_API_SECRET is never exposed to browsers.
  - Supervisor tokens always have hidden=True, can_publish=False.
  - Text (WS/DataChannel) is always available; audio/video are per-participant ceilings
    from `media_policy` (VOZ-09).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import struct
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import jwt as pyjwt
import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer
from fastapi import WebSocket, WebSocketDisconnect

from ..collect_core import (
    DIGIT_WORDS,
    CollectNotApplicable,
    CollectPlan,
    CollectSession,
    Done,
    Echo,
    Retry,
)
from ..config import Settings
from ..context_reader import ContextReader
from ..models import (
    ContactClosedEvent,
    ContactOpenEvent,
    MessageAuthor,
    MessageContent,
    NormalizedInboundEvent,
)
from ..session_registry import SessionRegistry
from . import contact_lifecycle
from .base import ChannelAdapter
from .speaches_provider import SpeachesSTTProvider, SpeachesTTSProvider, pcm16_48k_to_16k, rms
from .voice_provider import (
    SpeechSegmentation,
    SpeechStats,
    SpeechTuning,
    ISTTProvider,
    ITTSProvider,
    DeepgramSTTProvider,
    ElevenLabsTTSProvider,
)
from .webrtc_provider import (
    IWebRTCProvider,
    LiveKitProvider,
    MockWebRTCProvider,
    TokenGrants,
    WebRTCProviderUnavailable,
    build_room_name,
)
from . import media_policy
from .webchat import menu_result_history_text
from .sip_leg import SipCall, is_sip_room, parse_sip_participant
from .webrtc_room_client import (
    AGENT_IDENTITY_PREFIX,
    CUSTOMER_IDENTITY_PREFIX,
    CUSTOMER_PREFIXES,
    IWebRTCRoomClient,
    LiveKitRoomClient,
    mp3_to_pcm,
    resample_pcm_48_to_8,
)
from plughub_tasks import disparar
from .. import speech_metrics

logger = logging.getLogger("plughub.channel-gateway.webrtc")

# ── Constants ─────────────────────────────────────────────────────────────────

_SESSION_TTL        = 14_400   # 4h — matches ws_contact_max_duration_s default
_AUTH_TIMEOUT_S     = 30       # seconds to receive conn.authenticate after hello
_KEEPALIVE_INTERVAL = 20       # seconds between server-side ping probes
_STREAM_BLOCK_MS    = 5_000    # ms to wait on XREAD before looping
_STREAM_WATCHER_SLEEP = 1.0    # seconds to sleep on stream watcher error

# ── Coleta mascarada (VOZ-05, fatia A) ────────────────────────────────────────
#
# O valor protegido entra por UM caminho: o campo protegido do widget (`webrtc.menu_submit`).
# Enquanto um menu mascarado espera, a fala transcrita e o texto livre do cliente NÃO são
# publicados: o bridge entrega ao menu que espera qualquer resposta do cliente, e um
# `text` durante a espera viraria o valor do formulário — em claro no histórico, porque
# a redação por campo só reconhece `menu_result`.
#
# A espera é fato do MOTOR (`menu:waiting:{sid}`); o adapter só cobre as duas bordas que
# aquela chave não vê: a fala em curso quando o menu chega (antes do HSET) e a que
# termina de ser transcrita logo depois da submissão (depois do HDEL). A folga cobre o
# fim de fala (700 ms de silêncio) mais a transcrição, com margem para CPU.
_MASKED_SPEECH_GRACE_S = 5.0

# ── Fala do agente (VOZ-05, fatia 3) ──────────────────────────────────────────
#
# Barge-in: voz do cliente contínua por este tempo, enquanto o agente fala, corta a fala e
# descarta o que estava na fila. Curto o bastante para responder rápido, longo o bastante
# para um estalo ou uma tosse não interromper.
_BARGE_IN_MIN_MS = 200
# A primeira fala da IA chega ao gateway ANTES do `routing.assigned` que traz o bot (medido ao
# vivo: `message.text` e `menu.payload` 4 ms antes do `webrtc ready`) — e, no browser, antes de o
# cliente autorizar o microfone e entrar. Sem espera, o aviso inicial e o prompt do primeiro menu
# eram descartados. Cada mensagem espera bot e cliente na sala por este tempo, contado da
# CHEGADA dela; depois é descartada DITA (o texto já está no widget).
_SPEECH_WAIT_ROOM_S = 15.0
# Causa de "sem voz" que NÃO é degradação: a chamada não tem agente de IA de áudio.
_NO_AI_AUDIO_ATTENDANT = "nenhum agente de IA de audio atende a chamada"
_SENTENCE_MIN_CHARS = 25
# VOZ-02: quanto a despedida da plataforma pode tocar antes de a chamada telefônica ser derrubada.
_SIP_FAREWELL_MAX_S = 10.0

# ── Coleta por teclado e fala (VOZ-05, fatia 5b) ──────────────────────────────
#
# A semântica mora em `collect_core` (casa única); aqui fica a APRESENTAÇÃO (prompt falado com as
# teclas, eco, bipe) e a CAPTURA (DTMF do ouvinte, fala final do STT). O laço de cada coleta roda
# em passos de `_COLLECT_TICK_S` e confere a espera do motor a cada `_COLLECT_WAITING_CHECK_S`:
# menu respondido por outro caminho ou vencido pela guarda do motor libera a coleta.
_COLLECT_TICK_S          = 0.1
_COLLECT_WAITING_CHECK_S = 1.0
_BEEP_RATE, _BEEP_HZ, _BEEP_S = 24_000, 1_000, 0.12


def _beep_pcm(rate: int = _BEEP_RATE) -> bytes:
    n = int(rate * _BEEP_S)
    return b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * _BEEP_HZ * i / rate)))
                    for i in range(n))


@dataclass
class _ActiveCollect:
    """Uma coleta em curso numa sessão: o plano, o estado, o laço e o fim do prompt falado."""
    plan:          CollectPlan
    session:       CollectSession
    played:        asyncio.Event
    task:          asyncio.Task | None = None
    seen_waiting:  bool = False
    next_check:    float = 0.0
    outcomes:      list[Done] = field(default_factory=list)
    started_at:    float = field(default_factory=time.monotonic)
    metrics_sent:  bool = False          # VOZ-22: um evento por coleta, venha o fim por onde vier


def speech_sentences(text: str) -> list[str]:
    """
    O texto que o agente escreveu, como FRASES faláveis. Tira o que o TTS leria errado (emoji,
    marcação) e parte em frases: a primeira começa a tocar sem esperar a síntese da mensagem
    inteira, e o barge-in descarta as seguintes sem sintetizá-las. Pedaço curto demais junta
    com o próximo — frase de três palavras sozinha soa picotada.
    """
    limpo = "".join(
        " " if unicodedata.category(ch) in ("So", "Sk", "Cs", "Co") or ch in "*_`#~>|" else ch
        for ch in text
    )
    limpo = re.sub(r"\s+", " ", limpo).strip()
    if not limpo:
        return []
    partes = [p for p in re.split(r"(?<=[.!?…;:])\s+", limpo) if p.strip()]
    frases: list[str] = []
    acumulado = ""
    for parte in partes:
        acumulado = f"{acumulado} {parte}".strip()
        if len(acumulado) >= _SENTENCE_MIN_CHARS:
            frases.append(acumulado)
            acumulado = ""
    if acumulado:
        if frases:
            frases[-1] = f"{frases[-1]} {acumulado}"
        else:
            frases.append(acumulado)
    return frases
MASKED_CAPTURE_NOTICE = (
    "Por segurança, durante o preenchimento protegido a sua fala e o texto livre não são "
    "registrados. Use o campo protegido."
)


# ── WebRTCAdapter ──────────────────────────────────────────────────────────────


class WebRTCAdapter(ChannelAdapter):
    """
    WebRTC channel adapter singleton.

    One instance is created at startup and shared across all active WebSocket
    connections.  Per-connection state is tracked internally via:
      - _connections:  session_id → WebSocket  (for outbound delivery)
      - _customer_media: session_id → teto de mídia do cliente (espelho do Redis)

    The adapter implements the full ChannelAdapter interface so it is
    dispatched by OutboundConsumer the same way as SMS/WhatsApp/Email.
    Additionally, handle_ws() drives the WS signaling lifecycle and
    get_token() issues LiveKit JWT tokens for agents joining the room.
    """

    channel = "webrtc"

    def __init__(
        self,
        *,
        producer:          AIOKafkaProducer,
        redis:             aioredis.Redis,
        settings:          Settings,
        registry:          SessionRegistry,
        context_reader:    ContextReader,
        webrtc_provider:   IWebRTCProvider | None = None,
        stt_provider:      ISTTProvider    | None = None,
        tts_provider:      ITTSProvider    | None = None,
        attachment_store:  Any | None = None,   # AttachmentStore | None
    ) -> None:
        self._producer          = producer
        self._redis             = redis
        self._settings          = settings
        # VOZ-04: histórico da conversa e snapshot de contexto são os MESMOS do webchat — uma
        # mensagem de cliente é a mesma coisa para o bridge, venha do canal que vier.
        self._registry          = registry
        self._context_reader    = context_reader
        # VOZ-04: fatos de ciclo de vida por sessão, que o fechamento precisa e o socket não
        # carrega: {contact_id, pool_id, started_at}. E o conjunto de sessões cujo fechamento
        # já foi publicado — hangup seguido de disconnect publicaria DOIS contact_closed.
        self._sessions:     dict[str, dict[str, str]] = {}
        self._close_fired:  set[str] = set()
        # VOZ-01: sem credencial/SDK o provider RECUSA. O adapter guarda o MOTIVO e fecha
        # a porta do canal nomeando-o (`handle_ws`, `get_token`) — em vez de derrubar o
        # boot do gateway inteiro, que levaria webchat/WhatsApp junto por um canal só.
        self._provider_unavailable: WebRTCProviderUnavailable | None = None
        self._provider: IWebRTCProvider | None
        if webrtc_provider is not None:
            self._provider = webrtc_provider
        else:
            try:
                self._provider = self._build_provider()
            except WebRTCProviderUnavailable as exc:
                self._provider = None
                self._provider_unavailable = exc
                logger.error("webrtc: canal FECHADO — %s", exc)
        self._stt               = stt_provider or self._build_stt_provider()
        self._tts               = tts_provider or self._build_tts_provider()
        # VOZ-05: o bot leg TRANSCREVE toda chamada com áudio (pede STT) e CONVERTE para o
        # agente de IA, que lê texto (pede STT e TTS). O que falta fica aqui, para o estado de
        # mídia e o log o NOMEAREM. Antes, sem chave, o STT virava um mock que entrava na sala,
        # consumia o áudio e não transcrevia nada, sem uma linha de log.
        self._stt_unavailable: str | None = self._stt_reason()
        self._tts_unavailable: str | None = (
            None if self._tts is not None else
            "sem provedor de TTS (PLUGHUB_WEBRTC_SPEECH_PROVIDER=speaches com "
            "PLUGHUB_WEBRTC_SPEACHES_URL, ou PLUGHUB_VOICE_ELEVENLABS_API_KEY)")
        if self._stt_unavailable or self._tts_unavailable:
            logger.warning(
                "webrtc: bot leg INCOMPLETO — %s — chamada com audio %s; agente de IA %s",
                "; ".join(x for x in (self._stt_unavailable, self._tts_unavailable) if x),
                "NAO e transcrita" if self._stt_unavailable else "e transcrita",
                "atende por texto" if (self._stt_unavailable or self._tts_unavailable) else "fala por voz",
            )
        self._attachment_store  = attachment_store

        # Active WebSocket connections keyed by session_id.
        # Populated in handle_ws(); removed on close.
        self._connections: dict[str, WebSocket] = {}

        # VOZ-09: espelho em memória do TETO do cliente por sessão (fonte: Redis
        # `channel:webrtc:{sid}:media`). Substitui `_mediums`, que guardava UM meio para a
        # sessão inteira e era sobrescrito a cada atribuição.
        self._customer_media: dict[str, frozenset[str]] = {}

        # O bot leg são DOIS papéis na sala, nenhum deles participante da SESSÃO (VOZ-05 fatia 4):
        #   _room_clients   OUVINTE — oculto, só assina; é a única entrada de STT da chamada.
        #   _voice_clients  VOZ     — visível, só publica; existe só com agente de IA de áudio.
        # Oculto assina normalmente (medido contra o SFU em 2026-09-16); o que oculto não faz é
        # ser OUVIDO (fatia 3). Separar os papéis evita reconectar oculto↔visível quando uma IA
        # entra ou sai de uma chamada de humano, com buraco na transcrição.
        self._room_clients:  dict[str, IWebRTCRoomClient] = {}
        self._stt_tasks:     dict[str, asyncio.Task]       = {}
        self._voice_clients: dict[str, IWebRTCRoomClient] = {}
        # Decisão sobre a VOZ por sessão, tomada no `routing.assigned`: presente = a voz entra
        # (ou já entrou). Ausente com a atribuição já conhecida = ninguém vai falar, e a fala da
        # IA é descartada NA HORA, com o motivo — não depois de esperar 15 s por uma voz que
        # não vem, culpando a sala.
        self._voice_wanted:  set[str] = set()
        self._voice_absent_why: dict[str, str] = {}

        # Phase D: active egress recordings.
        # _session_egress: session_id → { segment_id → egress_id }
        self._session_egress: dict[str, dict[str, str]] = {}

        # VOZ-05 (fatia A): campos mascarados de cada menu entregue, por sessão
        # (session_id → menu_id → field_ids), e o fim da folga da coleta mascarada
        # (session_id → time.monotonic()).
        self._menu_masked:         dict[str, dict[str, list[str]]] = {}
        self._masked_grace_until:  dict[str, float] = {}

        # VOZ-05 (fatia 3): fala do agente por sessão — UMA fila e UM tocador, para duas
        # mensagens seguidas não se sobreporem; `_speaking` é o que o barge-in consulta, e
        # `_speech_cancel` diz ao tocador para largar as frases que faltam da mensagem atual.
        self._speech_queues:  dict[str, asyncio.Queue[tuple[str, float]]] = {}
        self._speech_tasks:   dict[str, asyncio.Task]       = {}
        self._speaking:       set[str] = set()
        self._speech_cancel:  set[str] = set()

        # VOZ-05 (fatia 5b): a coleta por teclado/fala em curso por sessão (no máximo uma: o menu
        # novo substitui o anterior) e o leitor de DTMF do ouvinte.
        self._collects:   dict[str, _ActiveCollect] = {}
        self._dtmf_tasks: dict[str, asyncio.Task]   = {}
        # VOZ-05 (fatia 5c): o plano de coleta de cada menu entregue (session_id → menu_id → plano),
        # para validar a resposta que chega pela TELA — inclusive a de campo mascarado, que teclado
        # e fala não coletam — e as tentativas inválidas por essa via.
        self._menu_plans:      dict[str, dict[str, CollectPlan]] = {}
        # VOZ-18: ajuste da segmentação da fala do CLIENTE por sessão — lido a cada quadro pelo STT,
        # ligado pela coleta por voz que declara `end_silence_ms`/`max_speech_s`.
        self._speech_tuning:   dict[str, SpeechTuning] = {}
        # VOZ-21: segmentação da fala por tenant (config-api, namespace `webrtc`), resolvida quando
        # a chamada abre o STT; `main.py` invalida no `config.changed`
        from ..speech_config import SpeechProfiles, SpeechSegmentationConfig
        self.speech_config = SpeechSegmentationConfig(settings.config_api_url)
        # VOZ-25: perfis de fala do tenant (namespace `speech_profiles`), apontados pelo endpoint da
        # chamada; a resolução de cada sessão é feita UMA vez e compartilhada entre STT e TTS
        self.speech_profiles = SpeechProfiles(settings.config_api_url)
        self._speech_resolved: dict[str, asyncio.Task] = {}
        self._screen_invalids: dict[str, dict[str, int]]         = {}

        # VOZ-02: sessões cuja chamada entrou por TELEFONE (perna SIP). Não têm WebSocket: a sala
        # nasceu do serviço SIP ANTES da sessão e foi ADOTADA; o que o socket fazia por sessão
        # (vigiar o stream, manter a sessão viva, desligar) roda em tasks próprias.
        self._sip:         dict[str, SipCall]           = {}
        self._sip_by_room: dict[str, str]               = {}
        self._sip_tasks:   dict[str, list[asyncio.Task]] = {}

    # ── Provider factories ────────────────────────────────────────────────────

    def _build_provider(self) -> IWebRTCProvider:
        s = self._settings
        return LiveKitProvider(
            url        = s.webrtc_livekit_url,
            api_key    = s.webrtc_livekit_api_key,
            api_secret = s.webrtc_livekit_api_secret,
        )

    @property
    def provider_unavailable(self) -> WebRTCProviderUnavailable | None:
        """O motivo pelo qual o canal está fechado, ou None se o provider existe."""
        return self._provider_unavailable

    def _client_livekit_url(self) -> str:
        """
        URL do SFU que vai ao CLIENTE (browser/Console).

        A interna (`webrtc_livekit_url`) é endereço de dentro da rede do deploy — no
        compose, `ws://livekit:7880`, que browser nenhum resolve. Sem a pública
        configurada devolve a interna, e isso é DITO no log uma vez por processo.
        """
        s = self._settings
        if s.webrtc_livekit_public_url:
            return s.webrtc_livekit_public_url
        if not getattr(self, "_warned_public_url", False):
            self._warned_public_url = True
            logger.warning(
                "webrtc: PLUGHUB_WEBRTC_LIVEKIT_PUBLIC_URL ausente — o cliente recebe a "
                "URL INTERNA do SFU (%s); só funciona se ela for alcançável de fora",
                s.webrtc_livekit_url,
            )
        return s.webrtc_livekit_url

    def _build_stt_provider(self) -> ISTTProvider | None:
        """
        STT do bot leg, ou None — nunca um mock em produção (VOZ-05). Mock é injetado por
        teste; aqui ele fingia um provedor que não transcreve nada.
        """
        s = self._settings
        if not s.webrtc_stt_enabled:
            return None
        if s.webrtc_speech_provider == "speaches" and s.webrtc_speaches_url:
            return SpeachesSTTProvider(s.webrtc_speaches_url, s.webrtc_stt_model)
        if not s.voice_deepgram_api_key:
            return None
        return DeepgramSTTProvider(api_key=s.voice_deepgram_api_key)

    def _build_tts_provider(self) -> ITTSProvider | None:
        """TTS do bot leg, ou None — nunca um mock em produção (VOZ-05)."""
        s = self._settings
        if s.webrtc_speech_provider == "speaches" and s.webrtc_speaches_url:
            return SpeachesTTSProvider(s.webrtc_speaches_url, s.webrtc_tts_model, s.webrtc_tts_voice)
        if not s.voice_elevenlabs_api_key:
            return None
        return ElevenLabsTTSProvider(
            api_key  = s.voice_elevenlabs_api_key,
            voice_id = s.voice_elevenlabs_voice_id,
        )

    def _stt_reason(self) -> str | None:
        """Por que não há STT neste gateway; None quando há."""
        if not self._settings.webrtc_stt_enabled:
            return "STT desligado (PLUGHUB_WEBRTC_STT_ENABLED=false)"
        if self._stt is None:
            return ("sem provedor de STT (PLUGHUB_WEBRTC_SPEECH_PROVIDER=speaches com "
                    "PLUGHUB_WEBRTC_SPEACHES_URL, ou PLUGHUB_VOICE_DEEPGRAM_API_KEY)")
        return None

    def _convert_available(self) -> bool:
        return self._stt_unavailable is None and self._tts_unavailable is None

    def _bot_leg_should_run(self, state: dict, publish: frozenset[str]) -> bool:
        """O OUVINTE entra quando há atendente de áudio — humano ou IA — E há STT: toda chamada
        com áudio é transcrita, cada falante no seu canal (VOZ-05 fatia 4; até ali só entrava
        com agente de IA, porque a fala de chamada com humano não tinha destino). Segue os
        ATENDENTES, não o teto. `publish` fica na assinatura porque é o que o gatilho antigo
        lia (e a mutação do probe usa)."""
        return self._stt_unavailable is None and media_policy.bot_leg_needs(state["attendants"])["transcribe"]

    def _voice_should_run(self, state: dict) -> bool:
        """A VOZ entra quando há agente de IA de áudio e o bot converte (STT e TTS)."""
        return self._convert_available() and media_policy.bot_leg_needs(state["attendants"])["convert"]

    def _voice_absent_reason(self, state: dict) -> str:
        """Por que ninguém fala nesta chamada — a mesma causa que o estado de mídia nomeia."""
        if not media_policy.bot_leg_needs(state["attendants"])["convert"]:
            return _NO_AI_AUDIO_ATTENDANT
        return "; ".join(x for x in (self._stt_unavailable, self._tts_unavailable) if x) or "voz indisponivel"

    def _decide_voice(self, session_id: str, state: dict) -> bool:
        """Grava a decisão sobre a VOZ (`_voice_wanted`) e devolve se ela deve estar na sala."""
        wanted = self._voice_should_run(state)
        if wanted:
            self._voice_wanted.add(session_id)
            self._voice_absent_why.pop(session_id, None)
        else:
            # a causa fica guardada: `_speak` roda ANTES de qualquer `await` e não pode ir ao Redis
            self._voice_absent_why[session_id] = self._voice_absent_reason(state)
            if session_id in self._voice_wanted:
                logger.info("webrtc voz: sai da chamada (session=%s) — %s",
                            session_id, self._voice_absent_why[session_id])
            self._voice_wanted.discard(session_id)
        return wanted

    def _bot_leg_state(self, state: dict, session_id: str) -> dict:
        needs = media_policy.bot_leg_needs(state["attendants"])
        faltas = []
        if needs["transcribe"] and self._stt_unavailable:
            faltas.append(f"chamada NAO transcrita: {self._stt_unavailable}")
        if needs["convert"] and self._tts_unavailable:
            faltas.append(f"agente de IA sem voz: {self._tts_unavailable}")
        bot = {"transcribe": needs["transcribe"], "convert": needs["convert"],
               "available": not faltas}
        if faltas:
            bot["reason"] = "; ".join(faltas)
            logger.error("webrtc bot leg: %s (session=%s)", bot["reason"], session_id)
        return bot

    async def _stop_bot_leg(self, session_id: str) -> None:
        """Os dois papéis saem da sala: o ouvinte e a voz."""
        self._voice_wanted.discard(session_id)
        self._voice_absent_why.pop(session_id, None)
        await self._stop_voice(session_id)
        await self._stop_listener(session_id)

    async def _stop_listener(self, session_id: str) -> None:
        for task in (self._stt_tasks.pop(session_id, None), self._dtmf_tasks.pop(session_id, None)):
            if task and not task.done():
                task.cancel()
        room_client = self._room_clients.pop(session_id, None)
        if room_client is not None:
            try:
                await room_client.disconnect()
            except Exception as exc:
                logger.warning("webrtc bot leg: disconnect falhou (session=%s): %s", session_id, exc)
            logger.info("webrtc bot leg: ouvinte saiu da sala (session=%s)", session_id)

    async def _stop_voice(self, session_id: str) -> None:
        self._stop_speech(session_id)
        voice = self._voice_clients.pop(session_id, None)
        if voice is not None:
            try:
                await voice.disconnect()
            except Exception as exc:
                logger.warning("webrtc voz: disconnect falhou (session=%s): %s", session_id, exc)
            logger.info("webrtc voz: saiu da sala (session=%s)", session_id)

    # ── ChannelAdapter interface — outbound delivery ──────────────────────────

    async def deliver_text(self, payload: dict) -> None:
        """
        Deliver a text message to the WebRTC client.
        Called by OutboundConsumer for msg_type="message.text".

        Always sends webrtc.message over the WebSocket. Mensagem do agente de IA também é
        FALADA na sala quando o bot leg converte (VOZ-05 fatia 3) — ver `_speak`.
        """
        session_id = payload.get("session_id", "")
        if session_id in self._sip:
            # VOZ-02: telefone não tem tela. O que o agente de IA escreve é FALADO; o resto (texto
            # digitado pelo humano, aviso de sistema) não tem como chegar — e é dito, porque um
            # atendente digitando para um chamador que não lê é exatamente o silêncio que ninguém
            # veria.
            texto = payload.get("content", {}).get("text", "") or payload.get("text", "")
            autor = payload.get("author", {}).get("type", "agent")
            if texto and autor == "agent_ai":
                self._speak(session_id, texto)
            elif texto:
                logger.warning(
                    "webrtc sip: texto de %s NAO entregue — a chamada e telefonica e so ouve a fala "
                    "do agente de IA (session=%s)", autor, session_id,
                )
            return
        ws = self._connections.get(session_id)
        if not ws:
            logger.debug(
                "webrtc deliver_text: no active connection session=%s", session_id
            )
            return

        text   = (
            payload.get("content", {}).get("text", "")
            or payload.get("text", "")
        )
        author = payload.get("author", {}).get("type", "agent")
        # VOZ-04 (fatia 3): os dois produtores de `message.text` em `conversations.outbound`
        # (o socket do agente humano e o `notification_send`) escrevem `timestamp`. Este
        # leitor pedia `ts`, que ninguém escreve, e caía sempre na hora da ENTREGA — medido
        # ao vivo: a mensagem do Console chegava ao cliente com outra hora. Ausência vira
        # hora da entrega, mas dita: é contrato de produtor quebrado, não detalhe.
        ts = payload.get("timestamp") or payload.get("ts")
        if not ts:
            logger.warning(
                "webrtc deliver_text: payload sem `timestamp` session=%s — cliente recebe a "
                "hora da entrega no lugar da hora da mensagem", session_id,
            )
            ts = datetime.now(timezone.utc).isoformat()

        # VOZ-05 (fatia 3): só o agente de IA fala pela sala. O humano tem voz própria na
        # chamada, e o que ele DIGITA fica texto; aviso de sistema também. Até aqui qualquer
        # texto era candidato — e nenhum era falado, porque o flag nascia desligado.
        # ⚠️ ANTES de qualquer `await`: o consumidor de saída abre UMA task por mensagem, e a
        # ordem entre elas só é a do Kafka até a primeira suspensão. Medido ao vivo: com a fala
        # enfileirada depois do envio pelo WebSocket, o prompt do menu seguinte era falado
        # antes do aviso que o precede.
        if text and author == "agent_ai":
            self._speak(session_id, text)

        await self._ws_send(ws, {
            "type":   "webrtc.message",
            "text":   text,
            "author": author,
            "ts":     ts,
        })



    async def deliver_menu(self, payload: dict) -> None:
        """
        Deliver an interactive menu or form to the WebRTC client.
        Called by OutboundConsumer for msg_type="menu.payload".

        VOZ-05 (fatia A): o frame é PLANO, com as chaves do `menu.payload`. Até aqui ia
        aninhado em `payload` e o widget lia `menu_id`/`prompt`/`options` na raiz — nenhum
        menu chegava a ser respondido. `masked_fields` diz ao widget quais campos são
        protegidos; o adapter guarda a lista para redigir o histórico na submissão.
        """
        session_id = payload.get("session_id", "")
        menu_id    = payload.get("menu_id", "")
        masked     = [f for f in (payload.get("masked_fields") or []) if isinstance(f, str)]
        if masked and menu_id:
            self._menu_masked.setdefault(session_id, {})[menu_id] = masked
            # a fala em curso quando o menu chega ainda não tem `menu:waiting` no Redis
            self._masked_grace_until[session_id] = time.monotonic() + _MASKED_SPEECH_GRACE_S
        if session_id in self._sip:
            # VOZ-02: sem tela, o menu é o que se OUVE — coleta por fala/teclado quando o menu a
            # declara, senão o prompt falado (a resposta chega como fala transcrita do cliente).
            plan = self._plan_collect(session_id, payload, masked)
            if plan is not None:
                self._start_collect(session_id, plan)
            elif payload.get("prompt"):
                self._speak(session_id, payload["prompt"])
            return
        ws = self._connections.get(session_id)
        if not ws:
            logger.warning(
                "webrtc deliver_menu: sessao sem conexao — menu NAO entregue session=%s menu=%s",
                session_id, menu_id,
            )
            return

        # A TELA também precisa do plano (fatia 5c): valida o campo de dígitos e desenha o teclado —
        # inclusive no menu mascarado, que teclado e fala não coletam.
        try:
            tela = CollectPlan.from_menu(payload)
        except CollectNotApplicable:
            tela = None
        if tela is not None and menu_id:
            self._menu_plans.setdefault(session_id, {})[menu_id] = tela
        # VOZ-05 (fatia 5b): menu com coleta por teclado/fala ganha o prompt com as teclas e um
        # laço que produz UM desfecho. Sem `collect`, o prompt é fala do agente como qualquer aviso.
        plan = self._plan_collect(session_id, payload, masked)
        if plan is not None:
            self._start_collect(session_id, plan)
        elif payload.get("prompt"):
            self._speak(session_id, payload["prompt"])
        await self._ws_send(ws, {
            "type":          "webrtc.interaction",
            "menu_id":       menu_id,
            "interaction":   payload.get("interaction", ""),
            "prompt":        payload.get("prompt", ""),
            "options":       payload.get("options") or [],
            "fields":        payload.get("fields") or [],
            "masked_fields": masked,
            # o que o widget precisa para o teclado (domínio, tamanhos, terminador); ausente = sem coleta
            "collect":       tela.screen_view() if tela is not None else None,
        })

    async def deliver_typing(self, payload: dict) -> None:
        """
        Deliver a typing indicator to the WebRTC client.
        Called by OutboundConsumer for msg_type="agent.typing".
        Sends {"type": "webrtc.typing", "active": bool} over WS.
        """
        session_id = payload.get("session_id", "")
        ws = self._connections.get(session_id)
        if not ws:
            return

        is_typing = payload.get("typing", True)
        await self._ws_send(ws, {
            "type":   "webrtc.typing",
            "active": is_typing,
        })

    async def deliver_session_closed(self, payload: dict) -> None:
        """
        Notify the WebRTC client that the session has ended and close the WS.
        Called by OutboundConsumer for msg_type="session.closed".
        """
        session_id = payload.get("session_id", "")
        if session_id in self._sip:
            await self._sip_platform_close(session_id, payload)
            return
        ws = self._connections.get(session_id)
        if not ws:
            logger.debug(
                "webrtc deliver_session_closed: no active connection session=%s",
                session_id,
            )
            return

        # VOZ-16: este leitor pedia `close_reason` com default `session_timeout`, e nenhum
        # produtor de `session.closed` escrevia a chave — todo fechamento chegava ao cliente
        # como `session_timeout`, e o teste passava porque ELE a escrevia. Hoje o routing a
        # escreve; bridge e `conversation_end` mandam o motivo de negócio em `reason`. O
        # `agent_done` de `reason` é marcador de TRANSPORTE e não é motivo para o cliente.
        reason = payload.get("close_reason") or payload.get("reason") or ""
        if reason == "agent_done":
            reason = ""
        if not reason:
            logger.warning(
                "webrtc deliver_session_closed: payload sem motivo de negocio session=%s — "
                "cliente recebe o fechamento sem motivo", session_id,
            )
        # Render v2: o aviso da plataforma (teto de fila, sem recurso, outage) vai ANTES do
        # fechamento, pelo mesmo socket — o adapter de webchat já fazia; este o descartava.
        farewell = payload.get("farewell_text") or ""
        if farewell:
            await self._ws_send(ws, {
                "type":   "webrtc.message",
                "text":   farewell,
                "author": "system",
                "ts":     datetime.now(timezone.utc).isoformat(),
            })
        # A plataforma fechou: publica-se `agent_done` ANTES de derrubar o socket, senão o
        # disconnect que vem a seguir seria publicado como queda do cliente.
        await self._close_session(session_id, "agent_done")
        closed = {"type": "webrtc.session_closed", "reason": reason}
        if not reason:
            del closed["reason"]          # ausente, nunca string vazia com cara de motivo
        await self._ws_send(ws, closed)
        try:
            await ws.close(code=1000)
        except Exception:
            pass

        # Phase D: stop egress recordings
        await self._stop_all_egress(session_id)

        # Phase C: ouvinte e voz saem da sala
        await self._stop_bot_leg(session_id)

        self._end_collect(session_id, "sessao encerrada", "session_closed")
        self._connections.pop(session_id, None)
        self._customer_media.pop(session_id, None)
        logger.info(
            "webrtc session_closed delivered: session=%s reason=%s",
            session_id, reason,
        )

    # ── WebSocket lifecycle ───────────────────────────────────────────────────

    async def handle_ws(self, ws: WebSocket, pool_id: str) -> None:
        """
        Full WebSocket signaling lifecycle for one WebRTC connection.

        Flow:
          accept → send conn.ready
          → receive conn.hello (validate version)
          → receive conn.authenticate (validate JWT, open session, route)
          → send conn.authenticated
          → run 3 concurrent tasks:
              _stream_watcher  — routing.assigned → negotiate → create_room → webrtc.ready
              _receive_loop    — webrtc.hangup → _close_session; conn.ping → conn.pong
              _keepalive       — renew Redis TTL every _KEEPALIVE_INTERVAL seconds
        """
        await ws.accept()
        if self._provider is None:
            # VOZ-01: a porta fecha ANTES da autenticação e do roteamento — um contato
            # não pode ser admitido e roteado para um canal cujo plano de mídia não existe.
            await self._ws_error(
                ws, "media_plane_unavailable", str(self._provider_unavailable),
            )
            return
        await self._ws_send(ws, {"type": "conn.ready"})

        # ── Auth handshake ────────────────────────────────────────────────────
        session_id:     str = ""
        contact_id:     str = ""
        participant_id: str = ""

        # Auth timeout — config-api wins (Single Source); _AUTH_TIMEOUT_S is the
        # fallback default. Same config key as webchat (webchat.auth_timeout_s).
        from ..attachment_store import resolve_ws_auth_timeout_s
        auth_timeout_s = await resolve_ws_auth_timeout_s(
            self._redis, self._settings.tenant_id, _AUTH_TIMEOUT_S,
        )
        try:
            session_id, contact_id, participant_id = await asyncio.wait_for(
                self._auth_handshake(ws, pool_id),
                timeout=float(auth_timeout_s),
            )
        except asyncio.TimeoutError:
            logger.warning("webrtc auth_timeout — closing WS")
            await self._ws_error(ws, "auth_timeout", "Authentication timed out")
            return
        except _AuthError as exc:
            logger.warning("webrtc auth_error code=%s: %s", exc.code, exc.message)
            await self._ws_error(ws, exc.code, exc.message)
            return
        except WebSocketDisconnect:
            logger.debug("webrtc: client disconnected during auth handshake")
            return

        # ── Register connection ───────────────────────────────────────────────
        self._connections[session_id] = ws
        logger.info(
            "webrtc WS connected: session=%s contact=%s pool=%s participant=%s",
            session_id, contact_id, pool_id, participant_id,
        )

        # ── Concurrent tasks ──────────────────────────────────────────────────
        tasks = [
            asyncio.create_task(
                self._stream_watcher(ws, session_id, pool_id),
                name=f"webrtc-stream-{session_id[:8]}",
            ),
            asyncio.create_task(
                self._receive_loop(ws, session_id),
                name=f"webrtc-receive-{session_id[:8]}",
            ),
            asyncio.create_task(
                self._keepalive(session_id),
                name=f"webrtc-keepalive-{session_id[:8]}",
            ),
        ]

        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()

        # ── Cleanup ───────────────────────────────────────────────────────────
        # Qualquer saída do laço é o fim do socket do cliente. Sem este fechamento, uma task
        # que terminasse antes do `receive_loop` deixaria a sessão aberta até o watchdog. É
        # idempotente: hangup e disconnect já publicados não publicam de novo.
        await self._close_session(session_id, "customer_disconnect")
        self._connections.pop(session_id, None)
        self._customer_media.pop(session_id, None)
        logger.info("webrtc WS session complete: session=%s contact=%s", session_id, contact_id)

    # ── Auth handshake ────────────────────────────────────────────────────────

    async def _auth_handshake(
        self, ws: WebSocket, pool_id: str
    ) -> tuple[str, str, str]:
        """
        Exchange conn.hello / conn.authenticate with the client.

        Returns (session_id, contact_id, participant_id).
        Raises _AuthError on invalid token.
        Publishes contact_open (conversations.events) + routing request (conversations.inbound).
        """
        s = self._settings

        # Step 1: receive conn.hello
        raw = await ws.receive_text()
        msg = json.loads(raw)
        if msg.get("type") != "conn.hello":
            raise _AuthError("bad_message", f"Expected conn.hello, got: {msg.get('type')}")

        # Step 2: receive conn.authenticate
        raw = await ws.receive_text()
        msg = json.loads(raw)
        if msg.get("type") != "conn.authenticate":
            raise _AuthError("bad_message", f"Expected conn.authenticate, got: {msg.get('type')}")

        token = msg.get("token", "")
        if not token:
            raise _AuthError("missing_token", "conn.authenticate requires 'token'")

        # Validate customer JWT — per-tenant secret with Redis override
        jwt_secret = await self._resolve_jwt_secret(s.tenant_id)
        try:
            claims = pyjwt.decode(
                token,
                jwt_secret,
                algorithms=["HS256"],
            )
        except pyjwt.ExpiredSignatureError:
            raise _AuthError("token_expired", "Customer token has expired")
        except pyjwt.InvalidTokenError as exc:
            raise _AuthError("invalid_token", f"Customer token invalid: {exc}")

        contact_id = claims.get("sub", "")
        if not contact_id:
            raise _AuthError("missing_sub", "Token must contain 'sub' claim (contact_id)")

        # Resolve pool_id via Layer 2 (ChannelEndpoint lookup in agent-registry) — e o perfil de
        # fala que o endpoint aponta (VOZ-25)
        resolved_pool, speech_profile_id = await self._resolve_pool(pool_id, contact_id)

        session_id, participant_id = await self._open_session(
            contact_id, "webrtc", resolved_pool, speech_profile_id,
        )

        # Confirm authentication to client
        await self._ws_send(ws, {
            "type":           "conn.authenticated",
            "session_id":     session_id,
            "participant_id": participant_id,
        })

        logger.info(
            "webrtc auth ok: contact=%s session=%s pool=%s",
            contact_id, session_id, resolved_pool,
        )
        return session_id, contact_id, participant_id

    async def _open_session(
        self, contact_id: str, channel: str, resolved_pool: str, speech_profile_id: str | None,
    ) -> tuple[str, str]:
        """Abre o contato: chaves da sessão no Redis, abertura em `conversations.events` e pedido
        de roteamento. UMA casa para as duas portas de entrada — o widget (`webrtc`) e o telefone
        (`voice`, VOZ-02) —, porque para o bridge um contato é o mesmo fato venha de onde vier.
        Devolve `(session_id, participant_id)`."""
        s = self._settings
        # Assign session ID and participant ID
        session_id     = str(uuid.uuid4())
        participant_id = str(uuid.uuid4())
        started_at     = datetime.now(timezone.utc).isoformat()

        # Store session meta in Redis
        ttl = s.session_ttl_seconds
        await self._redis.setex(
            f"channel:webrtc:{contact_id}:session", ttl, session_id
        )
        await self._redis.setex(
            f"session:{session_id}:contact_id", ttl, contact_id
        )
        await self._redis.setex(
            f"session:{session_id}:meta",
            ttl,
            json.dumps({
                "contact_id":             contact_id,
                "session_id":             session_id,
                "tenant_id":              s.tenant_id,
                "customer_id":            contact_id,
                "channel":                channel,
                "pool_id":                resolved_pool,
                "started_at":             started_at,
                "customer_participant_id": participant_id,
            }),
        )
        await self._redis.setex(
            f"session:{session_id}:customer_participant_id", ttl, participant_id,
        )
        # Sem esta chave o watchdog do bridge lê a sessão (que tem `meta`) como ÓRFÃ e a fecha
        # no ciclo seguinte. Renovada no keepalive: numa chamada de voz o cliente pode não
        # mandar frame nenhum pelo WebSocket durante minutos.
        await self._touch_ws_alive(session_id)
        self._sessions[session_id] = {
            "contact_id": contact_id, "pool_id": resolved_pool, "started_at": started_at,
            "speech_profile_id": speech_profile_id, "channel": channel,
        }

        # O contrato é o do webchat (`contact_lifecycle`): abertura em `conversations.events`,
        # pedido de roteamento no formato que o routing-engine reconhece.
        await self._publish_event(ContactOpenEvent(
            contact_id = contact_id,
            session_id = session_id,
            tenant_id  = s.tenant_id,
            channel    = channel,
            started_at = started_at,
        ).model_dump())
        await self._publish_inbound(contact_lifecycle.routing_request(
            session_id              = session_id,
            tenant_id               = s.tenant_id,
            customer_id             = contact_id,
            channel                 = channel,
            pool_id                 = resolved_pool,
            started_at              = started_at,
            customer_participant_id = participant_id,
        ))
        return session_id, participant_id

    # ── Stream watcher — routing.assigned → webrtc.ready ─────────────────────

    async def _stream_watcher(
        self, ws: WebSocket, session_id: str, pool_id: str
    ) -> None:
        """
        Watch session:{id}:stream for routing.assigned events.

        On routing.assigned:
          1st → register attendant, compute the customer ceiling (`media_policy`),
                create_room, token recortado por fonte, webrtc.ready
          next → attendant SOMA ao conjunto e o teto é refeito (webrtc.media)
        On participant_left: attendant sai do conjunto e o teto é refeito.

        Continues watching after webrtc.ready to detect session.closed events
        (e.g. agent done, max duration exceeded) and stop the keepalive.
        """
        s          = self._settings
        stream_key = f"session:{session_id}:stream"
        last_id    = "0-0"
        ready_sent = False

        while True:
            try:
                results = await self._redis.xread(
                    {stream_key: last_id},
                    count=20,
                    block=_STREAM_BLOCK_MS,
                )
                if not results:
                    continue

                for _, entries in results:
                    for entry_id, fields in entries:
                        last_id    = entry_id
                        event_type = fields.get("type", "")

                        if event_type == "routing.assigned":
                            if not ready_sent:
                                # First assignment — full setup (room + token + ready)
                                await self._on_routing_assigned(
                                    ws, session_id, fields, s
                                )
                                ready_sent = True
                            else:
                                # Atendente a mais (transferência, especialista, hook):
                                # SOMA ao conjunto e refaz o teto do cliente.
                                await self._on_routing_renegotiate(
                                    ws, session_id, fields, s
                                )

                        elif event_type == "participant_left" and ready_sent:
                            await self._on_attendant_left(ws, session_id, fields)

                        elif event_type in ("session.closed", "agent_done"):
                            # Session ended from server side — stop watcher
                            logger.info(
                                "webrtc stream_watcher: %s for session=%s",
                                event_type, session_id,
                            )
                            return

            except Exception as exc:
                logger.debug(
                    "webrtc stream_watcher error (session=%s): %s",
                    session_id, exc,
                )
                await asyncio.sleep(_STREAM_WATCHER_SLEEP)

    # ── Mídia por PARTICIPANTE (VOZ-09) ───────────────────────────────────────
    #
    # O estado de mídia da sessão é `channel:webrtc:{sid}:media`, um JSON com DOIS fatos
    # de escopo diferente, e é por isso que são dois campos e não um meio:
    #   attendants  {instance_id: {framework, pool_id, customer_publish, agent_publish,
    #               policy_source}} — quem atende AGORA e o que o POOL dele oferece (entra no
    #               `routing.assigned`, sai no `participant_left`);
    #   customer    {publish, policy_sources, reason, updated_at} — o TETO do cliente,
    #               derivado dos atendentes pela `media_policy`.
    # Um único watcher por sessão processa o stream em ordem, então ler-modificar-gravar
    # aqui não concorre consigo mesmo.

    def _media_key(self, session_id: str) -> str:
        return f"channel:webrtc:{session_id}:media"

    async def _load_media_state(self, session_id: str) -> dict:
        raw = await self._redis.get(self._media_key(session_id))
        if not raw:
            return {"attendants": {}, "customer": None}
        try:
            state = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.error(
                "webrtc media: estado ILEGIVEL em %s — recomeçando SEM atendentes (teto vazio)",
                self._media_key(session_id),
            )
            return {"attendants": {}, "customer": None}
        state.setdefault("attendants", {})
        state.setdefault("customer", None)
        for iid, rec in list(state["attendants"].items()):
            if not isinstance(rec, dict):
                # Estado gravado antes da VOZ-10 guardava só o framework: não há de onde
                # tirar a política do pool, então o atendente passa a oferecer NADA — e diz.
                logger.warning(
                    "webrtc media: atendente %s em estado anterior a VOZ-10 (%r) — sem "
                    "politica de pool, contribui NADA ao teto (session=%s)", iid, rec, session_id,
                )
                state["attendants"][iid] = {
                    "framework": rec if isinstance(rec, str) else "", "pool_id": "",
                    "customer_publish": [], "agent_publish": [],
                    "policy_source": "estado_sem_politica",
                }
        return state

    async def attendant_ids(self, session_id: str) -> set[str]:
        """Quem ATENDE esta sessão agora, por `instance_id` (VOZ-15).

        Leitura pública do estado de mídia, para quem precisa decidir *"este chamador é um dos
        atendentes?"* — hoje a rota do token de mídia. Devolve conjunto VAZIO quando não há
        estado, estado ilegível ou nenhum atendente: quem pergunta trata ausência como "ainda
        não sei", e a decisão de falhar fechado é de lá, não daqui.
        """
        return set((await self._load_media_state(session_id)).get("attendants") or {})

    async def _save_media_state(self, session_id: str, state: dict) -> None:
        await self._redis.setex(
            self._media_key(session_id), self._settings.session_ttl_seconds, json.dumps(state),
        )

    async def _customer_identity(self, session_id: str) -> str:
        call = self._sip.get(session_id)
        if call is not None:
            return call.identity   # VOZ-02: quem o serviço SIP nomeou, não o que o gateway escolheria
        contact_id = await self._redis.get(f"session:{session_id}:contact_id") or session_id
        return f"{CUSTOMER_IDENTITY_PREFIX}{contact_id}"   # o bot leg assina SÓ esta identidade

    def _customer_grants(self, room_name: str, identity: str, publish: frozenset[str]) -> TokenGrants:
        return TokenGrants(
            room_name           = room_name,
            identity            = identity,
            display_name        = "Customer",
            can_publish         = True,
            can_subscribe       = True,
            can_publish_data    = True,
            hidden              = False,
            ttl_seconds         = self._settings.webrtc_token_ttl_s,
            can_publish_sources = tuple(media_policy.publish_sources(publish)),
        )

    def _attendant_record(self, fields: dict, session_id: str) -> tuple[str, dict]:
        """
        (instance_id, registro) do atendente de um `routing.assigned`: framework + a
        política do POOL que atende (VOZ-10). Toda recusa é DITA — framework
        desconhecido consome nada; política ausente, ilegível ou não lida oferece nada.
        """
        instance_id = fields.get("instance_id", "") or "?"
        framework   = fields.get("framework", "")
        if framework not in media_policy.CONSUMES_BY_FRAMEWORK:
            logger.warning(
                "webrtc media: routing.assigned sem framework reconhecido (%r) para "
                "instance=%s — tratado como atendente que NAO consome midia (session=%s)",
                framework, instance_id, session_id,
            )
        record, aviso = media_policy.attendant_from_pool_field(framework, self._json_field(fields, "pool"))
        if aviso:
            log = logger.error if record["policy_source"].startswith("registry_indisponivel") else logger.warning
            log("webrtc media: %s (instance=%s session=%s)", aviso, instance_id, session_id)
        return instance_id, record

    def _customer_state(self, state: dict, publish: frozenset[str], reason: str,
                        session_id: str = "") -> dict:
        return {
            "publish":        media_policy.kinds_list(publish),
            "policy_sources": media_policy.policy_sources(state["attendants"]),
            "reason":         reason,
            "bot_leg":        self._bot_leg_state(state, session_id),
            "updated_at":     datetime.now(timezone.utc).isoformat(),
        }

    def _ceiling(self, state: dict) -> frozenset[str]:
        return media_policy.customer_ceiling(
            state["attendants"], bot_leg_audio=self._convert_available())

    async def _on_routing_assigned(
        self,
        ws:         WebSocket,
        session_id: str,
        fields:     dict,
        s:          Settings,
    ) -> None:
        """
        PRIMEIRO `routing.assigned` da sessão: registra o atendente, calcula o teto do
        cliente, cria a sala e envia `webrtc.ready` com o token já recortado por fonte.
        """
        instance_id, record = self._attendant_record(fields, session_id)
        state = await self._load_media_state(session_id)
        state["attendants"][instance_id] = record
        publish = self._ceiling(state)
        state["customer"] = self._customer_state(
            state, publish, f"attendant_joined:{record['framework'] or 'unknown'}", session_id,
        )
        # A decisão da VOZ vem ANTES de `_customer_media`, sem `await` entre as duas: a primeira
        # fala da IA chega junto desta atribuição (fatia 3), e ao ver `_customer_media` sem a
        # decisão ela a lia como "sem voz" e era descartada. Medido ao vivo na fatia 4: aviso e
        # prompt do menu inicial mudos, as falas seguintes normais.
        run_voice = self._decide_voice(session_id, state)
        self._customer_media[session_id] = publish

        room_name = self._room_of(session_id)
        # A chave da sala ANTES de criá-la (VOZ-02). Com `auto_create` ligado, o gateway apaga a
        # sala `plughub-{sid}` que nasce SEM esta chave (`police_room`); gravar depois abriria a
        # corrida em que a sala legítima é apagada pelo próprio controle.
        ttl = s.session_ttl_seconds
        await self._redis.setex(f"channel:webrtc:{session_id}:room_name", ttl, room_name)
        if session_id in self._sip:
            # A sala da chamada telefônica JÁ existe (nasceu do serviço SIP) e o chamador já está
            # nela: não há sala a criar nem token a mandar — só o bot leg a pôr para dentro.
            await self._save_media_state(session_id, state)
            logger.info("webrtc sip: atendimento na sala da chamada session=%s room=%s atendente=%s",
                        session_id, room_name, instance_id)
            if self._bot_leg_should_run(state, publish):
                disparar(self._start_stt_pipeline(session_id, room_name),
                         nome=f"webrtc-stt-start-{session_id[:8]}")
            if run_voice:
                disparar(self._start_voice(session_id, room_name),
                         nome=f"webrtc-voz-start-{session_id[:8]}")
            return
        try:
            await self._provider.create_room(room_name)
        except Exception as exc:
            logger.warning(
                "webrtc: create_room failed (session=%s room=%s): %s — continuing",
                session_id, room_name, exc,
            )

        identity = await self._customer_identity(session_id)
        token = self._provider.generate_token(self._customer_grants(room_name, identity, publish))

        await self._save_media_state(session_id, state)

        await self._ws_send(ws, {
            "type":           "webrtc.ready",
            "livekit_url":    self._client_livekit_url(),
            "token":          token,
            "room_name":      room_name,
            "publish":        state["customer"]["publish"],
            "policy_sources": state["customer"]["policy_sources"],
        })
        logger.info(
            "webrtc ready: session=%s publish=%s sources=%s room=%s",
            session_id, state["customer"]["publish"], state["customer"]["policy_sources"], room_name,
        )

        if self._bot_leg_should_run(state, publish):
            disparar(
                self._start_stt_pipeline(session_id, room_name),
                nome=f"webrtc-stt-start-{session_id[:8]}",
            )
        if run_voice:
            disparar(
                self._start_voice(session_id, room_name),
                nome=f"webrtc-voz-start-{session_id[:8]}",
            )
        # LÁPIDE (VOZ-10, 2026-09-14) — aqui a gravação disparava se o pool trouxesse
        # `webrtc_recording`, campo que NÃO existia em lugar nenhum: leitor sem produtor,
        # logo a gravação nunca iniciava. O gatilho volta com a VOZ-06, junto do egress e
        # do campo que o controla — campo na tela sem efeito é o que se evita.

    async def _on_routing_renegotiate(
        self,
        ws:         WebSocket,
        session_id: str,
        fields:     dict,
        s:          Settings,
    ) -> None:
        """
        `routing.assigned` SUBSEQUENTE: o atendente SOMA ao conjunto (transferência,
        especialista, hook) — nunca substitui o anterior. Quem sai sai pelo
        `participant_left`.
        """
        instance_id, record = self._attendant_record(fields, session_id)
        state = await self._load_media_state(session_id)
        state["attendants"][instance_id] = record
        await self._apply_customer_ceiling(
            ws, session_id, state, f"attendant_joined:{record['framework'] or 'unknown'}",
        )

    async def _on_attendant_left(self, ws: WebSocket, session_id: str, fields: dict) -> None:
        """`participant_left` no stream: o atendente sai do conjunto e o teto é refeito."""
        who = fields.get("author_id", "") or self._json_field(fields, "payload").get("participant_id", "")
        state = await self._load_media_state(session_id)
        if who not in state["attendants"]:
            # Não se inventa quem saiu. Se ids de entrada e saída divergirem, o teto fica
            # MAIS PERMISSIVO do que devia — por isso o aviso nomeia os dois lados.
            logger.warning(
                "webrtc media: participant_left de %r, que nao e atendente registrado "
                "(atendentes=%s) — teto do cliente NAO recalculado (session=%s)",
                who, sorted(state["attendants"]), session_id,
            )
            return
        record = state["attendants"].pop(who)
        framework = record.get("framework", "") if isinstance(record, dict) else ""
        await self._apply_customer_ceiling(ws, session_id, state, f"attendant_left:{framework or 'unknown'}")

    async def _apply_customer_ceiling(
        self, ws: WebSocket, session_id: str, state: dict, reason: str,
    ) -> None:
        """
        Recalcula o teto do cliente e, se mudou, aplica NOS DOIS LADOS: permissão no SFU
        (se o cliente já está na sala) e `webrtc.media` com token novo ao cliente (se
        ainda não entrou, entra com o teto certo). Sem mudança, não faz nada.
        """
        publish = self._ceiling(state)
        previous = (state.get("customer") or {}).get("publish")
        new_list = media_policy.kinds_list(publish)
        # O bot leg segue os ATENDENTES, não o teto: entra com o primeiro atendente de áudio e
        # sai quando não resta nenhum.
        # Ouvinte e voz decidem cada um por si; a decisão da voz antes de qualquer `await`.
        run_voice = self._decide_voice(session_id, state)
        run_bot = self._bot_leg_should_run(state, publish)
        if run_bot and session_id not in self._room_clients:
            disparar(
                self._start_stt_pipeline(session_id, self._room_of(session_id)),
                nome=f"webrtc-stt-start-{session_id[:8]}",
            )
        elif not run_bot and session_id in self._room_clients:
            await self._stop_listener(session_id)
        if run_voice and session_id not in self._voice_clients:
            disparar(
                self._start_voice(session_id, self._room_of(session_id)),
                nome=f"webrtc-voz-start-{session_id[:8]}",
            )
        elif not run_voice and session_id in self._voice_clients:
            await self._stop_voice(session_id)
        if previous == new_list:
            state["customer"] = {**(state.get("customer") or {}),
                                 "policy_sources": media_policy.policy_sources(state["attendants"]),
                                 "bot_leg": self._bot_leg_state(state, session_id)}
            await self._save_media_state(session_id, state)
            logger.debug("webrtc media: teto inalterado %s (%s) session=%s", new_list, reason, session_id)
            return

        state["customer"] = self._customer_state(state, publish, reason, session_id)
        self._customer_media[session_id] = publish
        await self._save_media_state(session_id, state)

        if session_id in self._sip:
            # O chamador de telefone publica ÁUDIO porque o canal é esse; não há permissão de fonte
            # a recortar no SFU nem token a reemitir (VOZ-02). O teto vale para o bot leg, acima.
            logger.info("webrtc media: teto %s -> %s (%s) session=%s — chamada telefonica, nada a "
                        "aplicar no SFU", previous, new_list, reason, session_id)
            return

        room_name = self._room_of(session_id)
        identity  = await self._customer_identity(session_id)
        try:
            in_room = await self._provider.update_participant_permission(
                room_name, identity, tuple(media_policy.publish_sources(publish)),
            )
        except Exception as exc:
            # O watcher engoliria isto em `debug` e o evento já foi consumido: sem esta
            # linha, o SFU ficaria com a permissão velha e ninguém saberia.
            in_room = None
            logger.error(
                "webrtc media: FALHOU aplicar teto %s no SFU para %s (session=%s): %s — "
                "o cliente recebe o token novo, mas a permissao na sala segue a ANTERIOR",
                new_list, identity, session_id, exc,
            )
        token = self._provider.generate_token(self._customer_grants(room_name, identity, publish))
        await self._ws_send(ws, {
            "type":           "webrtc.media",
            "token":          token,
            "room_name":      room_name,
            "publish":        new_list,
            "policy_sources": state["customer"]["policy_sources"],
            "reason":         reason,
        })
        logger.info(
            "webrtc media: teto do cliente %s -> %s (%s; aplicado no SFU=%s) session=%s",
            previous, new_list, reason, in_room, session_id,
        )

    @staticmethod
    def _json_field(fields: dict, name: str) -> dict:
        raw = fields.get(name, "{}")
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    # ── Receive loop ─────────────────────────────────────────────────────────

    async def _receive_loop(self, ws: WebSocket, session_id: str) -> None:
        """
        Receive messages from the WebRTC client until disconnect or hangup.

        Handled message types:
          webrtc.hangup             → contact_closed em conversations.events, stop tasks
          webrtc.message            → DataChannel text → Kafka conversations.inbound
                                      (medium=text path — customer typing in text mode)
          webrtc.menu_submit        → resposta de menu/form → conversations.inbound
                                      (`menu_result`, valor real) + histórico redigido
          webrtc.interaction_reply  → aposentado (VOZ-05) → conn.error
          conn.ping                 → reply conn.pong
          (others)                  → logged and ignored
        """
        try:
            async for raw in ws.iter_text():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug(
                        "webrtc receive_loop: invalid JSON from session=%s", session_id
                    )
                    continue

                msg_type = msg.get("type", "")
                # Todo frame prova que o socket está vivo (mesma regra do webchat).
                await self._touch_ws_alive(session_id)

                if msg_type == "webrtc.hangup":
                    logger.info("webrtc hangup received: session=%s", session_id)
                    await self._close_session(session_id, "customer_hangup")
                    return

                elif msg_type == "webrtc.message":
                    # Texto do cliente. O formato é o do webchat: `text` plano.
                    text = msg.get("text", "")
                    if not isinstance(text, str) or not text.strip():
                        # Até 2026-09-14 o widget mandava `content.text` e isto virava "" —
                        # a mensagem sumia sem rastro. Formato errado é recusado DITO.
                        logger.warning(
                            "webrtc: webrtc.message sem `text` (chaves=%s) — descartada "
                            "(session=%s)", sorted(msg), session_id,
                        )
                        await self._ws_send(ws, {
                            "type": "conn.error", "code": "bad_message",
                            "message": "webrtc.message requires a non-empty 'text'",
                        })
                    elif await self._masked_capture_active(session_id):
                        # Texto livre durante coleta mascarada: seria entregue ao menu que
                        # espera como se fosse o valor, e ficaria em claro no histórico.
                        logger.info(
                            "webrtc: texto livre RECUSADO durante coleta mascarada session=%s "
                            "(o valor protegido entra pelo campo protegido)", session_id,
                        )
                        await self._ws_send(ws, {
                            "type": "conn.error", "code": "masked_capture_active",
                            "message": MASKED_CAPTURE_NOTICE,
                        })
                    elif self._collect_refuses_text(session_id):
                        # D5 (fatia 5b): o modo da coleta decide o que é resposta. O bridge
                        # entregaria o texto ao menu que espera como se fosse o valor.
                        plano = self._collects[session_id].plan
                        logger.info(
                            "webrtc: texto livre RECUSADO — o menu %s coleta por %s (session=%s)",
                            plano.menu_id, sorted(plano.inputs), session_id,
                        )
                        await self._ws_send(ws, {
                            "type": "conn.error", "code": "collect_input_not_accepted",
                            "message": "this menu is answered by keypad or voice",
                        })
                    else:
                        await self._publish_customer_text(session_id, text.strip(), msg.get("id"))

                elif msg_type == "webrtc.menu_submit":
                    await self._handle_menu_submit(ws, session_id, msg)

                elif msg_type == "webrtc.interaction_reply":
                    # Formato aposentado na VOZ-05: gravava um envelope JSON em
                    # `menu:result:{sid}` — chave e formato que o motor não lê (ele espera o
                    # valor cru na chave da instância, que o BRIDGE resolve). Recusado DITO.
                    logger.warning(
                        "webrtc: webrtc.interaction_reply RECUSADO (formato aposentado; use "
                        "webrtc.menu_submit) session=%s", session_id,
                    )
                    await self._ws_send(ws, {
                        "type": "conn.error", "code": "unsupported_message",
                        "message": "webrtc.interaction_reply was retired; send webrtc.menu_submit "
                                   "{menu_id, interaction, result}",
                    })

                elif msg_type == "conn.ping":
                    await self._ws_send(ws, {"type": "conn.pong"})

                else:
                    logger.debug(
                        "webrtc receive_loop: unhandled type=%s session=%s",
                        msg_type, session_id,
                    )

        except WebSocketDisconnect:
            logger.info("webrtc WS disconnected: session=%s", session_id)
            await self._close_session(session_id, "customer_disconnect")

        except Exception as exc:
            logger.warning(
                "webrtc receive_loop error (session=%s): %s", session_id, exc
            )

    # ── Keepalive ─────────────────────────────────────────────────────────────

    async def _keepalive(self, session_id: str) -> None:
        """
        Renew Redis TTL for WebRTC session keys every _KEEPALIVE_INTERVAL seconds.
        Prevents stale session metadata from expiring during long calls.
        """
        ttl = self._settings.session_ttl_seconds
        while True:
            await asyncio.sleep(_KEEPALIVE_INTERVAL)
            try:
                await self._redis.expire(
                    f"session:{session_id}:meta", ttl
                )
                await self._redis.expire(
                    f"channel:webrtc:{session_id}:room_name", ttl
                )
                await self._redis.expire(self._media_key(session_id), ttl)
                await self._touch_ws_alive(session_id)
            except Exception as exc:
                logger.debug(
                    "webrtc keepalive error (session=%s): %s", session_id, exc
                )

    # ── Token endpoint (for agents / supervisors) ─────────────────────────────

    async def get_token(
        self,
        session_id: str,
        role:       str,   # "agent" | "supervisor"
        identity:   str,   # agent_type_id or human agent user ID
    ) -> dict[str, str] | None:
        """
        Issue a LiveKit token for an agent or supervisor joining an active session.

        Called by the GET /webrtc/token/{session_id} HTTP endpoint after the
        agent's JWT is validated by the FastAPI route (Authorization: Bearer).

        Returns {token, livekit_url, room_name, publish, customer_publish, hidden,
        policy_sources} — `publish` é o teto DESTE participante; `customer_publish` é o do
        cliente, para a tela decidir o que mostrar. Não há mais `negotiated_medium`: um meio
        único para a sessão era o defeito (VOZ-09).
        Returns None if the session has no active LiveKit room yet.
        Raises WebRTCProviderUnavailable when the media plane is not configured.
        """
        if self._provider is None:
            raise self._provider_unavailable or WebRTCProviderUnavailable(["provider"])
        room_name = await self._redis.get(f"channel:webrtc:{session_id}:room_name")

        if not room_name:
            logger.debug(
                "webrtc get_token: room not yet ready for session=%s", session_id
            )
            return None

        state = await self._load_media_state(session_id)
        if role == "supervisor":
            policy = media_policy.role_policy(media_policy.SUPERVISOR)
            publish, subscribe, hidden = policy.publish, policy.subscribe, policy.hidden
        else:
            # O teto do atendente humano vem dos POOLS que o puseram na sala (VOZ-10).
            # Sem atendente humano registrado, ou com pool sem politica, e VAZIO — e o
            # SFU recebe `can_publish=False`, porque lista vazia la significa TUDO.
            publish, subscribe, hidden = media_policy.agent_ceiling(state["attendants"]), True, False
            if not publish:
                logger.warning(
                    "webrtc get_token: atendente %s sem teto de publicacao (fontes=%s) — "
                    "entra na sala so assistindo (session=%s)",
                    identity, media_policy.policy_sources(state["attendants"]), session_id,
                )
        grants = TokenGrants(
            room_name           = room_name,
            identity            = f"{role}-{identity}",
            display_name        = identity,
            can_publish         = True,
            can_subscribe       = subscribe,
            can_publish_data    = not hidden,
            hidden              = hidden,
            ttl_seconds         = self._settings.webrtc_token_ttl_s,
            can_publish_sources = tuple(media_policy.publish_sources(publish)),
        )

        # Sem try: assinar é local, e uma falha aqui é defeito. Antes ela virava `None`
        # e a rota respondia 404 *"room not ready"* — motivo plausível e falso.
        token = self._provider.generate_token(grants)

        return {
            "token":            token,
            "livekit_url":      self._client_livekit_url(),
            "room_name":        room_name,
            "publish":          media_policy.kinds_list(publish),
            "customer_publish": (state.get("customer") or {}).get("publish", []),
            "hidden":           hidden,
            "policy_sources":   media_policy.policy_sources(state["attendants"]),
        }

    # ── Perna SIP (VOZ-02) ────────────────────────────────────────────────────
    #
    # A chamada de telefone entra pelo serviço SIP do SFU, que põe o chamador numa sala NOVA antes
    # de existir sessão. O SFU avisa por webhook (`/v1/livekit/webhook`, assinado); daqui em diante
    # a sessão ADOTA a sala e a mídia é a mesma da chamada de browser.

    def is_sip_session(self, session_id: str) -> bool:
        """A sessão é uma chamada telefônica deste gateway (e a saída do canal `voice` é daqui)."""
        return session_id in self._sip

    def _room_of(self, session_id: str) -> str:
        """A sala da sessão: a que o serviço SIP criou, ou `plughub-{sid}` para o browser."""
        call = self._sip.get(session_id)
        return call.room if call is not None else build_room_name(session_id)

    async def on_livekit_event(self, event: str, room: str, participant: dict | None) -> None:
        """Um evento da sala, já com a assinatura conferida pela rota."""
        if event == "room_started":
            await self.police_room(room)
        elif event == "participant_joined" and participant:
            call = parse_sip_participant(room, participant)
            if call is not None:
                await self._sip_arrived(call)
        elif event == "participant_left" and participant:
            sid = self._sip_by_room.get(room)
            if sid and self._sip[sid].identity == participant.get("identity"):
                await self._sip_hangup(sid, "o chamador desligou")
        elif event == "room_finished":
            sid = self._sip_by_room.get(room)
            if sid:
                await self._sip_hangup(sid, "a sala da chamada terminou")

    async def police_room(self, room: str) -> bool:
        """CONTROLE COMPENSATÓRIO do `auto_create` (VOZ-02, decisão do dono em 2026-09-18).

        A VOZ-01 fixou `room.auto_create: false` para que um token assinado para um nome qualquer não
        criasse sala. O serviço SIP do SFU entra na sala por JOIN e não a cria — com `false`, 100% das
        chamadas levavam 486 (medido) —, então a criação no join foi ligada, e a garantia passou de
        prevenção para REAÇÃO: sala `plughub-{uuid}` que nasce sem a chave da sessão viva
        (`channel:webrtc:{sid}:room_name`, gravada ANTES do `create_room` e apagada no fechamento) é
        apagada na hora. O recorte é o espaço de nomes que os tokens do gateway alcançam: a sala do
        telefone (prefixo SIP) nasce legitimamente sem sessão, e sala fora do prefixo não é deste
        gateway. Devolve True quando apagou."""
        prefixo = build_room_name("")
        if not room.startswith(prefixo) or is_sip_room(room):
            return False
        sid = room[len(prefixo):]
        try:
            uuid.UUID(sid)
        except ValueError:
            return False
        if await self._redis.exists(f"channel:webrtc:{sid}:room_name"):
            return False
        if self._provider is None:
            logger.error("webrtc: sala %s nasceu SEM sessao viva e NAO pode ser apagada — %s",
                         room, self._provider_unavailable)
            return False
        try:
            await self._provider.delete_room(room)
        except Exception as exc:
            logger.error("webrtc: sala %s nasceu SEM sessao viva e a remocao FALHOU: %s", room, exc)
            return False
        logger.error(
            "webrtc: sala %s nasceu SEM sessao viva (criada no join, `auto_create`) — APAGADA. Um token "
            "de sessao encerrada ou forjado tentou abri-la", room,
        )
        return True

    async def _sip_arrived(self, call: SipCall) -> None:
        """O chamador entrou na sala: nasce o contato `voice`, endereçado pelo número DISCADO."""
        s = self._settings
        ttl = s.session_ttl_seconds
        # O webhook pode chegar repetido; a SALA é a identidade da chamada.
        if not await self._redis.set(f"channel:sip:room:{call.room}", "abrindo", nx=True, ex=ttl):
            logger.info("webrtc sip: participant_joined repetido para %s — ignorado", call.room)
            return
        if not call.dnis:
            await self._sip_refuse(call, "a chamada nao traz o numero discado (sip.trunkPhoneNumber)")
            return
        pool, perfil, motivo = await self._resolve_voice_endpoint(call.dnis)
        if not pool:
            await self._sip_refuse(call, motivo)
            return
        # Número escondido pela dispatch rule não vira um número inventado: o contato fica com o
        # id da chamada, que diz o que é.
        contact_id = call.ani or f"sip:{call.call_id or call.room}"
        session_id, _ = await self._open_session(contact_id, "voice", pool, perfil)
        self._sip[session_id] = call
        self._sip_by_room[call.room] = session_id
        await self._redis.set(f"channel:sip:room:{call.room}", session_id, ex=ttl)
        self._sip_tasks[session_id] = [
            disparar(self._stream_watcher(None, session_id, pool), nome=f"sip-stream-{session_id[:8]}"),
            disparar(self._keepalive(session_id), nome=f"sip-keepalive-{session_id[:8]}"),
        ]
        logger.info(
            "webrtc sip: chamada %s de %s para %s virou contato session=%s pool=%s perfil=%s room=%s",
            call.call_id, call.ani or "(numero oculto)", call.dnis, session_id, pool, perfil or "-", call.room,
        )

    async def _resolve_voice_endpoint(self, dnis: str) -> tuple[str, str | None, str]:
        """(pool, perfil de fala, motivo da recusa). Sem endpoint `voice` para o número, NÃO há
        pool: nem default nem adivinhação — telefone que toca num pool que ninguém escolheu é o
        fallback de endereço que a casa proíbe."""
        s = self._settings
        if not s.agent_registry_url:
            return "", None, "gateway sem PLUGHUB_AGENT_REGISTRY_URL — nao ha como resolver o numero"
        from ..endpoint_resolver import resolve_endpoint as _resolve
        ep = await _resolve(channel="voice", identifier=dnis, tenant_id=s.tenant_id,
                            agent_registry_url=s.agent_registry_url, cache_ttl_s=s.endpoint_cache_ttl_s)
        if ep.outcome == "unavailable":
            return "", None, f"registro de endpoints INALCANCAVEL ao resolver {dnis}"
        if not ep.pool_id:
            return "", None, (f"nenhum endpoint `voice` cadastrado para {dnis} ({ep.outcome}) — "
                              f"cadastre em Configuracao > Canais > Voz")
        perfil = ep.settings.get("speech_profile_id") if isinstance(ep.settings, dict) else None
        if perfil is not None and not isinstance(perfil, str):
            logger.error("webrtc sip: endpoint %s tem speech_profile_id nao-texto (%r) — ignorado", dnis, perfil)
            perfil = None
        return ep.pool_id, (perfil or None), ""

    async def _sip_refuse(self, call: SipCall, motivo: str) -> None:
        logger.error("webrtc sip: chamada %s de %s para %s RECUSADA — %s", call.call_id,
                     call.ani or "(numero oculto)", call.dnis or "(sem numero)", motivo)
        if self._provider is None:
            logger.error("webrtc sip: e a sala %s NAO pode ser encerrada — %s", call.room, self._provider_unavailable)
            return
        try:
            await self._provider.delete_room(call.room)     # derruba a chamada
        except Exception as exc:
            logger.error("webrtc sip: encerrar a sala %s FALHOU: %s — o chamador fica chamando ate o "
                         "tempo de toque do tronco", call.room, exc)

    async def _sip_hangup(self, session_id: str, porque: str) -> None:
        """O chamador saiu: o contato fecha como DESLIGAR do cliente."""
        if session_id not in self._sip:
            return
        logger.info("webrtc sip: %s (session=%s)", porque, session_id)
        await self._close_session(session_id, "customer_hangup")
        await self._sip_teardown(session_id)

    async def _sip_platform_close(self, session_id: str, payload: dict) -> None:
        """A plataforma encerrou: a despedida é FALADA (telefone não lê) e a chamada é derrubada."""
        farewell = payload.get("farewell_text") or ""
        if farewell:
            tocada = asyncio.Event()
            self._speak(session_id, farewell, tocada)
            try:
                await asyncio.wait_for(tocada.wait(), timeout=_SIP_FAREWELL_MAX_S)
            except asyncio.TimeoutError:
                logger.warning("webrtc sip: despedida nao terminou em %.0f s — desligando assim mesmo "
                               "(session=%s)", _SIP_FAREWELL_MAX_S, session_id)
        await self._close_session(session_id, "agent_done")
        await self._sip_teardown(session_id)
        logger.info("webrtc sip: chamada encerrada pela plataforma session=%s", session_id)

    async def _sip_teardown(self, session_id: str) -> None:
        call = self._sip.pop(session_id, None)
        if call is None:
            return
        self._sip_by_room.pop(call.room, None)
        atual = asyncio.current_task()
        for t in self._sip_tasks.pop(session_id, []):
            if t is not atual:
                t.cancel()
        await self._stop_bot_leg(session_id)
        self._end_collect(session_id, "chamada encerrada", "session_closed")
        self._customer_media.pop(session_id, None)
        if self._provider is not None:
            try:
                await self._provider.delete_room(call.room)     # derruba a perna SIP, se ainda estiver
            except Exception as exc:
                logger.debug("webrtc sip: delete_room %s: %s", call.room, exc)

    # ── Session close ─────────────────────────────────────────────────────────

    async def _close_session(self, session_id: str, reason: str) -> None:
        """
        Publish contact_closed (conversations.events, VOZ-04) and tear down
        Phase C resources (STT task + room client).

        The platform (Core) handles session bookkeeping and publishes
        session.closed to the stream, which eventually reaches deliver_session_closed.
        """
        # Phase D: stop egress recordings before Phase C cleanup
        await self._stop_all_egress(session_id)

        # Phase C: ouvinte e voz saem da sala
        await self._stop_bot_leg(session_id)

        if session_id in self._close_fired:
            logger.debug("webrtc: fechamento ja publicado session=%s (%s ignorado)", session_id, reason)
            return
        self._close_fired.add(session_id)
        self._end_collect(session_id, "sessao encerrada", "session_closed")
        info = self._sessions.pop(session_id, {})
        self._menu_masked.pop(session_id, None)
        self._menu_plans.pop(session_id, None)
        self._screen_invalids.pop(session_id, None)
        self._speech_tuning.pop(session_id, None)
        self._speech_resolved.pop(session_id, None)
        self._masked_grace_until.pop(session_id, None)
        try:
            # a chave da sala sai junto (VOZ-02): sem ela, a sala recriada por um token de sessão
            # encerrada é apagada ao nascer (`police_room`) — é o que substitui o `auto_create: false`
            await self._redis.delete(f"session:{session_id}:ws_alive",
                                     f"channel:webrtc:{session_id}:room_name")
        except Exception as exc:
            logger.debug("webrtc: delete ws_alive falhou (session=%s): %s", session_id, exc)
        if not info:
            # Sem os fatos da abertura não há ContactClosedEvent válido — e inventar
            # started_at/contact_id seria o valor plausível. Diz e sai.
            logger.error(
                "webrtc: fechamento de sessao sem registro de abertura (session=%s reason=%s) — "
                "contact_closed NAO publicado", session_id, reason,
            )
            return
        # `reason` do evento é o de TRANSPORTE que o bridge lê para `customer_side`; o cliente
        # desligar e a conexão cair são o mesmo fato para ele. O que o canal sabe a mais — que
        # foi um DESLIGAR — vai no `close_reason` de negócio. `agent_done` é a plataforma que
        # fechou (o bridge publica o evento enriquecido, que vence no ClickHouse).
        transport = "agent_done" if reason == "agent_done" else "client_disconnect"
        try:
            await self._publish_event(ContactClosedEvent(
                contact_id   = info["contact_id"],
                session_id   = session_id,
                tenant_id    = self._settings.tenant_id,
                channel      = info.get("channel") or "webrtc",
                reason       = transport,  # type: ignore[arg-type]
                started_at   = info["started_at"],
                pool_id      = info["pool_id"],
                customer_id  = info["contact_id"],
                close_reason = contact_lifecycle.business_close_reason(
                    transport,
                    customer_action = reason if reason == "customer_hangup" else None,
                ),
            ).model_dump())
            logger.info("webrtc: contact_closed publicado session=%s reason=%s", session_id, reason)
        except Exception as exc:
            logger.error(
                "webrtc: contact_closed publish failed (session=%s): %s",
                session_id, exc,
            )

    # ── Phase C: STT pipeline ─────────────────────────────────────────────────

    async def _start_stt_pipeline(self, session_id: str, room_name: str) -> None:
        """
        Connect a server-side LiveKit room client and start the STT pipeline.

        Called when the customer ceiling carries audio (VOZ-09)
        and webrtc_stt_enabled=True.
        """
        s = self._settings
        if not s.webrtc_stt_enabled:
            return

        # OUVINTE (VOZ-05 fatia 4): oculto e sem publicar — ninguém na sala o vê, e ele não tem
        # o que dizer. A voz da IA é outro participante (`_start_voice`).
        room_client = await self._join_room(
            session_id, room_name, identity=f"bot-{session_id[:8]}", display_name="Transcricao",
            publish=False, subscribe=True, hidden=True, papel="ouvinte",
        )
        if room_client is None:
            return

        self._room_clients[session_id] = room_client

        # Launch STT pipeline as background task
        task = asyncio.create_task(
            self._stt_pipeline(session_id, room_client),
            name=f"webrtc-stt-{session_id[:8]}",
        )
        self._stt_tasks[session_id] = task
        self._dtmf_tasks[session_id] = asyncio.create_task(
            self._dtmf_reader(session_id, room_client), name=f"webrtc-dtmf-{session_id[:8]}",
        )
        logger.info(
            "webrtc: STT pipeline started: session=%s room=%s", session_id, room_name
        )

    async def _join_room(
        self, session_id: str, room_name: str, *, identity: str, display_name: str,
        publish: bool, subscribe: bool, hidden: bool, papel: str,
    ) -> IWebRTCRoomClient | None:
        """Um papel do bot leg entra na sala. Falha fica DITA: sem ouvinte, a chamada não é
        transcrita; sem voz, o agente de IA não fala."""
        s = self._settings
        try:
            token = self._provider.generate_token(TokenGrants(
                room_name        = room_name,
                identity         = identity,
                display_name     = display_name,
                can_publish      = publish,
                can_subscribe    = subscribe,
                can_publish_data = False,
                hidden           = hidden,
                ttl_seconds      = s.webrtc_token_ttl_s,
            ))
        except Exception as exc:
            logger.warning("webrtc %s: token falhou (session=%s): %s — %s NAO entra na sala",
                           papel, session_id, exc, papel)
            return None
        client = LiveKitRoomClient()
        try:
            await client.connect(room_name=room_name, identity=identity, token=token,
                                 livekit_url=s.webrtc_livekit_url)
        except Exception as exc:
            logger.warning("webrtc %s: conexao falhou (session=%s): %s — %s NAO entra na sala",
                           papel, session_id, exc, papel)
            return None
        return client

    async def _start_voice(self, session_id: str, room_name: str) -> None:
        """VOZ (VOZ-05 fatia 4): visível, só publica. Oculta, ninguém a ouviria — medido na fatia
        3 contra o SFU: 0 s de áudio de participante `hidden` chega ao cliente."""
        voice = await self._join_room(
            session_id, room_name, identity=f"voz-{session_id[:8]}", display_name="Assistente virtual",
            publish=True, subscribe=False, hidden=False, papel="voz",
        )
        if voice is None:
            return
        if session_id not in self._voice_wanted or session_id in self._voice_clients:
            # a decisão mudou (ou outra entrada venceu) enquanto conectava
            await voice.disconnect()
            return
        self._voice_clients[session_id] = voice
        logger.info("webrtc voz: entrou na sala session=%s room=%s", session_id, room_name)

    async def _stt_pipeline(
        self, session_id: str, room_client: IWebRTCRoomClient
    ) -> None:
        """
        O OUVINTE da chamada (VOZ-05 fatia 4): um fluxo de STT POR FALANTE — o cliente e cada
        atendente humano, cada um no seu canal. Só a frase FINAL sai do gateway, como mensagem
        de texto do falante com `content_type="audio_transcript"`; detecção de voz, transcrição
        parcial e barge-in ficam aqui.
        """
        falas: set[asyncio.Task] = set()
        seg: SpeechSegmentation | None = None
        voz = None
        if getattr(self._stt, "supports_tuning", False):
            voz = await self._speech_settings(session_id)
            seg = voz.segmentation
            logger.info("webrtc stt: segmentacao da fala session=%s %s", session_id, seg.describe())
            logger.info("webrtc stt: voz da chamada session=%s perfil=%s %s", session_id,
                        voz.profile_id or "-", voz.describe_voice())
            if not getattr(self._stt, "supports_model_choice", False) and voz.provenance.get("stt_model", "env") != "env":
                logger.warning("webrtc stt: o provedor %s nao escolhe modelo por chamada — stt_model do perfil %s "
                               "NAO se aplica (session=%s)", type(self._stt).__name__, voz.profile_id, session_id)
        elif self._stt is not None:
            logger.info("webrtc stt: o provedor %s nao aceita segmentacao configurada — webrtc.stt_* do "
                        "config-api e o perfil de fala NAO se aplicam a esta chamada (session=%s)",
                        type(self._stt).__name__, session_id)
        try:
            async for identity, chunks in room_client.speakers():
                if identity.startswith(CUSTOMER_PREFIXES):
                    autor = None                                   # o cliente (browser ou telefone)
                elif identity.startswith(AGENT_IDENTITY_PREFIX):
                    autor = "human-" + identity[len(AGENT_IDENTITY_PREFIX):]
                else:
                    logger.info("webrtc stt: trilha de %r nao transcrita (session=%s)", identity, session_id)
                    continue
                logger.info("webrtc stt: transcrevendo %s (session=%s)", identity, session_id)
                t = asyncio.create_task(self._stt_speaker(session_id, identity, autor, chunks, seg, voz),
                                        name=f"webrtc-stt-{identity[:16]}")
                falas.add(t)
                t.add_done_callback(falas.discard)
            if falas:
                await asyncio.gather(*falas, return_exceptions=True)
        except asyncio.CancelledError:
            logger.debug("webrtc stt_pipeline cancelled: session=%s", session_id)
        finally:
            for t in falas:
                t.cancel()

    async def _stt_speaker(
        self, session_id: str, identity: str, autor: str | None, chunks: AsyncIterator[bytes],
        segmentation: SpeechSegmentation | None = None,
        voice: "SpeechSettings | None" = None,
    ) -> None:
        """Um falante: quadros → STT → frase final publicada. `autor` None = o cliente (o único
        que interrompe a fala da IA); senão, o `participant_id` do humano (`human-{sub}`).
        `voice` traz modelo e língua do perfil da chamada (VOZ-25)."""
        s = self._settings
        stats: SpeechStats | None = None
        pool_id = (self._sessions.get(session_id) or {}).get("pool_id")

        # O provedor diz em que taxa quer o áudio (VOZ-05): o auto-hospedado transcreve PCM a
        # 16 kHz; o Deepgram legado recebe μ-law a 8 kHz, que é o que este laço sempre mandou.
        stt_rate = getattr(self._stt, "input_sample_rate", None)

        # Barge-in (VOZ-05 fatia 3): o mesmo limiar de energia que o STT usa para achar fala.
        # Só no caminho a 16 kHz PCM — no legado (μ-law a 8 kHz) a energia não é medida aqui,
        # e não há barge-in. Só a voz do CLIENTE interrompe o agente de IA.
        limiar = (segmentation.energy_threshold if segmentation is not None
                  else float(getattr(self._stt, "_thr", 400.0)))
        interrompe = autor is None
        voz_ms = 0.0

        async def _audio_chunks():
            nonlocal voz_ms
            async for chunk in chunks:
                try:
                    if stt_rate == 16000:
                        pcm = pcm16_48k_to_16k(chunk)
                        if interrompe and rms(pcm) >= limiar:
                            voz_ms += len(pcm) / 32.0     # 16 kHz × 2 bytes = 32 bytes/ms
                            if (voz_ms >= _BARGE_IN_MIN_MS and session_id in self._speaking
                                    and self._voice_barge_allowed(session_id)):
                                self._barge_in(session_id)
                        else:
                            voz_ms = 0.0
                        yield pcm
                    else:
                        yield resample_pcm_48_to_8(chunk)
                except Exception as exc:
                    logger.warning(
                        "webrtc stt_pipeline: resample error (session=%s): %s",
                        session_id, exc,
                    )

        try:
            language = voice.stt_language if voice is not None else s.voice_stt_language
            kw = {"sample_rate": stt_rate} if stt_rate else {}
            if voice is not None and getattr(self._stt, "supports_model_choice", False):
                kw["model"] = voice.stt_model
            if segmentation is not None:
                kw["segmentation"] = segmentation
            if interrompe and getattr(self._stt, "supports_tuning", False):
                # só a fala do CLIENTE responde menu, logo só ela segue o ajuste da coleta
                kw["tuning"] = self._speech_tuning.setdefault(session_id, SpeechTuning())
                # VOZ-22: e só ela alimenta a telemetria da recalibragem (o ambiente do CLIENTE)
                stats = SpeechStats()
                kw["stats"] = stats
            async for result in self._stt.stream(_audio_chunks(), language=language, **kw):
                if not (result.is_final and result.transcript.strip()):
                    continue
                if autor is None:
                    await self._publish_transcript(
                        session_id  = session_id,
                        transcript  = result.transcript,
                        confidence  = result.confidence,
                        start_ms    = result.start_ms,
                        end_ms      = result.end_ms,
                    )
                else:
                    await self._publish_agent_transcript(
                        session_id, autor, result.transcript, result.confidence,
                        result.start_ms, result.end_ms,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "webrtc stt: transcricao de %s PAROU (session=%s): %s", identity, session_id, exc
            )
        finally:
            if stats is not None and stats.frames:
                # task própria: o fluxo costuma acabar CANCELADO (a chamada fechou), e um await
                # aqui dentro não sobreviveria ao cancelamento
                evento = speech_metrics.stream_summary(
                    tenant_id=s.tenant_id, session_id=session_id, pool_id=pool_id,
                    stt_provider=type(self._stt).__name__, stats=stats, segmentation=segmentation,
                    speech_profile_id=voice.profile_id if voice is not None else None,
                    stt_model=(voice.stt_model if voice is not None
                               and getattr(self._stt, "supports_model_choice", False)
                               else getattr(self._stt, "_model", None)))
                disparar(speech_metrics.publish(self._producer, evento), nome=f"speech-metrics-{session_id[:8]}")

    async def _publish_transcript(
        self,
        session_id: str,
        transcript: str,
        confidence: float,
        start_ms:   int,
        end_ms:     int,
    ) -> None:
        """
        Publish a final STT transcript to Kafka conversations.inbound.
        content_type="audio_transcript" distinguishes voice input from text input.
        """
        if await self._masked_capture_active(session_id):
            # VOZ-05 (fatia A): a fala durante coleta mascarada é, por presunção, o valor
            # protegido dito em voz alta. Não vai ao bridge (que a entregaria ao menu como
            # resposta), nem ao histórico, nem ao log — só a contagem sai.
            logger.info(
                "webrtc: fala transcrita DESCARTADA durante coleta mascarada session=%s "
                "(%d caracteres, texto nao registrado)", session_id, len(transcript),
            )
            ws = self._connections.get(session_id)
            if ws:
                await self._ws_send(ws, {
                    "type": "webrtc.message", "text": MASKED_CAPTURE_NOTICE,
                    "author": "system", "ts": datetime.now(timezone.utc).isoformat(),
                })
            return
        try:
            # O mesmo evento da mensagem digitada (VOZ-04): o formato solto de antes era
            # descartado pelo bridge. Confiança e janela vão no `payload` do conteúdo.
            # A fala é REGISTRO da chamada; responder menu é só pela coleta abaixo (fatia 5b).
            await self._publish_customer_text(
                session_id, transcript, content_type="audio_transcript",
                payload={"confidence": confidence, "start_ms": start_ms, "end_ms": end_ms},
            )
        except Exception as exc:
            logger.warning(
                "webrtc: transcript publish failed (session=%s): %s", session_id, exc
            )
        await self._collect_speech(session_id, transcript, confidence)

    async def _publish_agent_transcript(
        self, session_id: str, participant_id: str, transcript: str,
        confidence: float, start_ms: int, end_ms: int,
    ) -> None:
        """A frase final do ATENDENTE HUMANO como mensagem dele, marcada como fala (VOZ-05 fatia
        4). Vai para o bridge, que a grava na sessão sem entregá-la ao cliente — ele a ouviu —
        nem ao Console do próprio humano. Não entra no histórico de chat (`append_message`):
        é o histórico que o Console recarrega."""
        info = self._sessions.get(session_id)
        if not info:
            logger.error("webrtc stt: fala do atendente em sessao sem registro de abertura "
                         "(session=%s) — descartada", session_id)
            return
        event = NormalizedInboundEvent(
            contact_id   = info["contact_id"],
            session_id   = session_id,
            channel      = info.get("channel") or "webrtc",
            content_type = "audio_transcript",
            author       = MessageAuthor(type="agent_human", id=participant_id),
            content      = MessageContent(type="text", text=transcript, payload={
                "confidence": confidence, "start_ms": start_ms, "end_ms": end_ms}),
        )
        try:
            await self._publish_inbound(event.model_dump())
        except Exception as exc:
            logger.warning("webrtc stt: fala do atendente %s NAO publicada (session=%s): %s",
                           participant_id, session_id, exc)

    # ── Fala do agente (VOZ-05, fatia 3) ──────────────────────────────────────

    def _voice_decided_absent(self, session_id: str) -> bool:
        """A atribuição já chegou (`_customer_media`) e decidiu que NÃO há voz nesta chamada."""
        return session_id in self._customer_media and session_id not in self._voice_wanted

    def _can_speak(self, session_id: str) -> bool:
        """Há TTS, a sessão é deste gateway e a VOZ vai estar na sala. Antes do `routing.assigned`
        não se sabe — a fala da IA chega antes dele (fatia 3) —, e a espera de cada mensagem
        (`_wait_room_for_speech`) é que descobre. Depois dele, a decisão é conhecida: sem voz,
        não há por que enfileirar (VOZ-05 fatia 4 — antes esperava 15 s e culpava a sala)."""
        return (self._tts is not None
                and (session_id in self._voice_clients or session_id in self._sessions)
                and not self._voice_decided_absent(session_id))

    def _speak(self, session_id: str, text: str, played: asyncio.Event | None = None) -> None:
        """Enfileira `text` para ser falado na sala. Sem voz não fala; a causa já está NOMEADA
        no estado de mídia (`customer.bot_leg.reason`), então aqui é debug com a causa.
        `played` é marcado quando a mensagem termina — tocada, interrompida ou não falada: a
        coleta arma nele o prazo da primeira entrada (VOZ-05 fatia 5b)."""
        if not self._can_speak(session_id):
            if played is not None:
                played.set()
            # IA SEM agente de áudio (chamada de texto) é o normal e fica em debug; IA de áudio
            # sem voz é DEGRADAÇÃO e aparece — foi um descarte em debug que escondeu a corrida
            # da atribuição na fatia 4.
            degradou = (self._voice_decided_absent(session_id)
                        and self._voice_absent_why.get(session_id) != _NO_AI_AUDIO_ATTENDANT)
            logger.log(logging.INFO if degradou else logging.DEBUG,
                       "webrtc fala: texto nao falado (session=%s) — %s", session_id,
                       self._speech_absent_cause(session_id))
            return
        fila = self._speech_queues.get(session_id)
        if fila is None:
            fila = asyncio.Queue()
            self._speech_queues[session_id] = fila
            self._speech_tasks[session_id] = disparar(
                self._speech_worker(session_id, fila), nome=f"webrtc-fala-{session_id[:8]}",
            )
        fila.put_nowait((text, time.monotonic(), played))

    async def _speech_worker(
        self, session_id: str, fila: asyncio.Queue[tuple[str, float, asyncio.Event | None]],
    ) -> None:
        """UM tocador por sessão: mensagem a mensagem, frase a frase, esperando cada uma tocar."""
        while True:
            text, chegou, played = await fila.get()
            self._speech_cancel.discard(session_id)
            room_client = await self._wait_room_for_speech(session_id, chegou)
            if room_client is None or self._tts is None:
                if played is not None:
                    played.set()
                continue
            self._speaking.add(session_id)
            try:
                for frase in speech_sentences(text):
                    if session_id in self._speech_cancel:
                        break
                    pcm, rate = await self._synthesize_pcm(session_id, frase)
                    if not pcm or session_id in self._speech_cancel:
                        continue
                    await room_client.publish_audio(pcm, sample_rate=rate)
                if session_id not in self._speech_cancel:
                    await room_client.wait_audio_playout()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("webrtc fala: tocador falhou (session=%s): %s", session_id, exc)
            finally:
                self._speaking.discard(session_id)
                if played is not None:
                    played.set()

    def _speech_absent_cause(self, session_id: str) -> str:
        if self._tts is None:
            return self._tts_unavailable or "sem TTS"
        if self._voice_decided_absent(session_id):
            return "sem voz nesta chamada: " + self._voice_absent_why.get(session_id, "motivo nao registrado")
        return "sessao desconhecida neste gateway"

    async def _wait_room_for_speech(self, session_id: str, chegou: float) -> IWebRTCRoomClient | None:
        """A VOZ na sala E o cliente na sala, até `_SPEECH_WAIT_ROOM_S` depois de a mensagem
        chegar — ou até a atribuição decidir que não haverá voz, e então desiste na hora."""
        prazo = chegou + _SPEECH_WAIT_ROOM_S
        while True:
            voice = self._voice_clients.get(session_id)
            if voice is not None and voice.customer_present():
                return voice
            if self._voice_decided_absent(session_id):
                logger.info(
                    "webrtc fala: mensagem NAO falada (session=%s) — %s; o texto ja chegou ao widget",
                    session_id, self._speech_absent_cause(session_id),
                )
                return None
            if time.monotonic() >= prazo:
                logger.info(
                    "webrtc fala: mensagem NAO falada (session=%s) — %s; o texto ja chegou ao widget",
                    session_id,
                    (f"o cliente nao entrou na sala em {_SPEECH_WAIT_ROOM_S:.0f} s" if voice is not None
                     else f"a atribuicao nao chegou em {_SPEECH_WAIT_ROOM_S:.0f} s" if session_id not in self._customer_media
                     else f"a voz nao entrou na sala em {_SPEECH_WAIT_ROOM_S:.0f} s"),
                )
                return None
            await asyncio.sleep(0.05)

    def voice_defaults(self) -> dict[str, str]:
        """A ÚLTIMA camada de modelo, língua e voz: o env do gateway (VOZ-17). Acima dela vêm o
        namespace `webrtc` do tenant e o perfil da chamada — ver `speech_config.apply_profile`.
        Público porque a porta de escrita (`PUT /v1/speech-profiles/...`) confere contra o que a
        chamada REALMENTE usaria, e isso inclui esta camada."""
        s = self._settings
        return {"stt_model": s.webrtc_stt_model, "stt_language": s.voice_stt_language,
                "tts_model": s.webrtc_tts_model, "tts_voice": s.webrtc_tts_voice}

    async def _speech_settings(self, session_id: str) -> "SpeechSettings":
        """A fala da chamada (VOZ-25): segmentação do tenant sobreposta pelo perfil do endpoint, com
        modelo/língua/voz. Resolvida UMA vez por sessão e compartilhada entre STT e TTS — os dois
        têm de concordar sobre o perfil; `shield` porque cancelar quem espera não pode cancelar a
        resolução do outro."""
        from ..speech_config import resolve_session
        t = self._speech_resolved.get(session_id)
        if t is None:
            info = self._sessions.get(session_id) or {}
            t = asyncio.ensure_future(resolve_session(
                self.speech_config, self.speech_profiles, self._settings.tenant_id,
                info.get("speech_profile_id"), self.voice_defaults()))
            if session_id in self._sessions:
                self._speech_resolved[session_id] = t
        return await asyncio.shield(t)

    async def _synthesize_pcm(self, session_id: str, text: str) -> tuple[bytes, int]:
        try:
            if getattr(self._tts, "supports_model_choice", False):
                voz = await self._speech_settings(session_id)
                audio = await self._tts.synthesize(text, voz.tts_voice or None, model=voz.tts_model or None)
            else:
                audio = await self._tts.synthesize(text, None)
        except Exception as exc:
            logger.warning("webrtc fala: sintese falhou (session=%s): %s — frase NAO falada", session_id, exc)
            return b"", 0
        if not audio:
            return b"", 0
        # O auto-hospedado já devolve PCM (VOZ-05); só o legado devolve MP3 — e a imagem não
        # tem decodificador de MP3, então esse caminho loga e não fala.
        out_rate = getattr(self._tts, "output_sample_rate", None)
        if out_rate:
            return audio, out_rate
        return mp3_to_pcm(audio, target_sample_rate=24000), 24000

    def _barge_in(self, session_id: str) -> None:
        """O cliente falou por cima do agente: a fala para e o que estava na fila é descartado.
        O TEXTO dessas mensagens já chegou ao widget — perde-se a leitura, não o conteúdo."""
        fila = self._speech_queues.get(session_id)
        descartadas = 0
        while fila is not None and not fila.empty():
            _, _, played = fila.get_nowait()
            if played is not None:
                played.set()
            descartadas += 1
        self._speech_cancel.add(session_id)
        self._speaking.discard(session_id)
        # o OUVINTE percebeu o cliente; quem para é a VOZ — sinal local, no mesmo processo
        voice = self._voice_clients.get(session_id)
        if voice is not None:
            voice.interrupt_audio()
        logger.info(
            "webrtc: fala INTERROMPIDA pelo cliente session=%s (%d mensagem(ns) pendente(s) descartada(s))",
            session_id, descartadas,
        )

    def _stop_speech(self, session_id: str) -> None:
        task = self._speech_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
        fila = self._speech_queues.pop(session_id, None)
        while fila is not None and not fila.empty():
            _, _, played = fila.get_nowait()
            if played is not None:
                played.set()
        self._speaking.discard(session_id)
        self._speech_cancel.discard(session_id)

    # ── Coleta por teclado e fala (VOZ-05, fatia 5b) ──────────────────────────

    def _plan_collect(self, session_id: str, payload: dict, masked: list[str]) -> CollectPlan | None:
        """O plano da coleta, ou `None` quando o menu não é coletado por teclado/fala neste canal —
        com o motivo dito sempre que o menu PEDIU e não vai ter."""
        menu_id = payload.get("menu_id", "")
        try:
            plan = CollectPlan.from_menu(payload)
        except CollectNotApplicable as exc:
            logger.warning(
                "webrtc coleta: menu %s pede teclado/fala e NAO sera coletado assim — %s; "
                "responde pela tela (session=%s)", menu_id, exc, session_id,
            )
            return None
        if plan is None:
            return None
        if (masked or payload.get("masked")) and self.is_sip_session(session_id):
            # O telefone NÃO tem tela: a decisão 4 (campo protegido) não se aplica, e a coleta por
            # teclado de dado mascarado ainda não existe na perna SIP — exige `telephone-event`
            # negociado e a perna isolada durante o bloco (NIV-07). Dizer "vai ao campo protegido"
            # aqui seria a frase plausível e falsa; o menu fica sem coleta e sai pelo prazo dele.
            logger.error(
                "webrtc coleta: menu %s e MASCARADO numa chamada telefonica — coleta mascarada "
                "por teclado na perna SIP NAO existe (NIV-07); as teclas sao ignoradas sem valor no "
                "log e o menu sai pelo prazo (session=%s)", menu_id, session_id,
            )
            return None
        if masked or payload.get("masked"):
            # decisão 4: WebRTC no browser TEM tela — o dado protegido vai ao campo protegido
            logger.info(
                "webrtc coleta: menu %s e mascarado e vai ao campo protegido da tela (fatia A); "
                "teclado e fala nao o coletam (session=%s)", menu_id, session_id,
            )
            return None
        if session_id not in self._room_clients:
            logger.info(
                "webrtc coleta: menu %s coleta por %s e o ouvinte ainda nao esta na sala (%s) — "
                "responde o que chegar depois dele, e o prazo corre (session=%s)",
                menu_id, sorted(plan.inputs & {"dtmf", "voice"}),
                self._stt_unavailable or "entra com a atribuicao de um atendente de audio", session_id,
            )
        if ("voice" in plan.inputs and plan.min_confidence is not None
                and not getattr(self._stt, "measures_confidence", True)):
            logger.warning(
                "webrtc coleta: menu %s declara min_confidence=%s e o STT nao mede confianca — "
                "o limite NAO e aplicado (session=%s)", menu_id, plan.min_confidence, session_id,
            )
        if "voice" in plan.inputs and plan.speech_params and not getattr(self._stt, "supports_tuning", False):
            logger.warning(
                "webrtc coleta: menu %s declara %s e o STT nao ajusta a segmentacao por coleta — "
                "NAO aplicado (session=%s)", menu_id, ", ".join(plan.speech_params), session_id,
            )
        return plan

    def _start_collect(self, session_id: str, plan: CollectPlan) -> None:
        """Fala o prompt com as teclas e começa o laço. ANTES de qualquer `await`, como a fala
        de `deliver_text`: a ordem das falas é a do Kafka só até a primeira suspensão."""
        self._end_collect(session_id, f"substituida pelo menu {plan.menu_id}", "replaced")
        ac = _ActiveCollect(plan=plan, session=CollectSession(plan), played=asyncio.Event())
        self._collects[session_id] = ac
        self._tune_speech(session_id, plan)
        texto = plan.spoken_prompt()
        if texto:
            self._speak(session_id, texto, ac.played)
        else:
            ac.played.set()
        ac.task = disparar(self._run_collect(session_id, ac), nome=f"webrtc-coleta-{session_id[:8]}")
        logger.info(
            "webrtc coleta: menu %s por %s (%s, prazo %.0f s, eco %s, barge-in %s) session=%s",
            plan.menu_id, sorted(plan.inputs), plan.interaction, plan.first_timeout_s,
            plan.echo, plan.barge_in, session_id,
        )

    def _tune_speech(self, session_id: str, plan: CollectPlan | None) -> None:
        """Liga (plano com voz e parâmetros) ou desliga (None) o ajuste da fala do cliente."""
        tuning = self._speech_tuning.setdefault(session_id, SpeechTuning())
        if plan is not None and "voice" in plan.inputs and plan.speech_params:
            tuning.silence_ms = plan.end_silence_ms
            tuning.max_utterance_ms = plan.max_speech_ms
        else:
            tuning.clear()

    def _end_collect(self, session_id: str, why: str, reason: str = "released") -> None:
        """`reason` é o código do fim para a telemetria: `session_closed`, `replaced`, `screen`
        (respondido pela tela), `screen_invalid` (inválidos esgotados pela tela)."""
        ac = self._collects.pop(session_id, None)
        if ac is None:
            return
        self._tune_speech(session_id, None)
        if ac.session.done is None:
            logger.info("webrtc coleta: menu %s liberado sem desfecho — %s (session=%s)",
                        ac.plan.menu_id, why, session_id)
            if reason == "screen":
                self._emit_collect_metrics(session_id, ac, "value", "screen", None)
            elif reason == "screen_invalid":
                self._emit_collect_metrics(session_id, ac, "invalid", "screen", None)
            else:
                self._emit_collect_metrics(session_id, ac, "released", "", reason)
        if ac.task is not None and ac.task is not asyncio.current_task() and not ac.task.done():
            ac.task.cancel()

    async def _run_collect(self, session_id: str, ac: _ActiveCollect) -> None:
        """O relógio da coleta. O prazo da primeira entrada só arma quando o prompt termina."""
        try:
            while ac.session.done is None:
                now = time.monotonic()
                if ac.played.is_set():
                    ac.session.arm(now)
                await self._apply_collect(session_id, ac, ac.session.tick(now))
                if ac.session.done is not None:
                    break
                if now >= ac.next_check:
                    ac.next_check = now + _COLLECT_WAITING_CHECK_S
                    esperando = await self._menu_waiting_now(session_id)
                    if esperando:
                        ac.seen_waiting = True
                    elif esperando is False and ac.seen_waiting:
                        # respondido por outro caminho, ou a guarda absoluta do motor venceu
                        logger.info(
                            "webrtc coleta: o motor nao espera mais o menu %s — coleta liberada "
                            "(session=%s)", ac.plan.menu_id, session_id,
                        )
                        break
                await asyncio.sleep(_COLLECT_TICK_S)
        finally:
            if self._collects.get(session_id) is ac:
                self._collects.pop(session_id, None)
                self._tune_speech(session_id, None)
                if ac.session.done is None:
                    self._emit_collect_metrics(session_id, ac, "released", "", "engine_released")

    async def _menu_waiting_now(self, session_id: str) -> bool | None:
        """Há menu esperando no motor? `None` = não se sabe (a coleta segue: o prazo dela termina)."""
        try:
            return bool(await self._redis.hgetall(f"menu:waiting:{session_id}"))
        except Exception as exc:
            logger.debug("webrtc coleta: menu:waiting ilegivel (session=%s): %s", session_id, exc)
            return None

    async def _apply_collect(self, session_id: str, ac: _ActiveCollect, actions: list) -> None:
        for a in actions:
            if isinstance(a, Echo):
                if ac.plan.echo == "plain":
                    self._speak(session_id, DIGIT_WORDS[a.key])
                elif ac.plan.echo == "masked":
                    self._beep(session_id)
            elif isinstance(a, Retry):
                limite = f" de {ac.plan.max_invalid}" if ac.plan.max_invalid else " (sem limite: ate o prazo)"
                logger.info(
                    "webrtc coleta: entrada invalida %d%s no menu %s — %s (session=%s)",
                    a.count, limite, ac.plan.menu_id,
                    "mensagem ao cliente" if a.message else "ignorada sem eco", session_id,
                )
                if a.message:
                    self._speak(session_id, a.message)
                    ws = self._connections.get(session_id)
                    if ws:
                        await self._ws_send(ws, {
                            "type": "webrtc.message", "text": a.message, "author": "system",
                            "ts": datetime.now(timezone.utc).isoformat(),
                        })
            elif isinstance(a, Done):
                ac.outcomes.append(a)
                await self._publish_collect_done(session_id, ac.plan, a)
                self._emit_collect_metrics(session_id, ac, a.outcome, a.via, None)

    def _emit_collect_metrics(self, session_id: str, ac: "_ActiveCollect", outcome: str, via: str,
                              release_reason: str | None) -> None:
        """VOZ-22: um `collect_outcome` por coleta que aceita VOZ — só contagens, nunca o valor."""
        if ac.metrics_sent or "voice" not in ac.plan.inputs:
            return
        ac.metrics_sent = True
        p = ac.plan
        # VOZ-25: o perfil EM VIGOR, se a fala da chamada já foi resolvida (coleta por voz implica o
        # fluxo de STT aberto); sem resolução, None — nunca o id declarado, que pode não ter valido
        resolvida = self._speech_resolved.get(session_id)
        perfil = (resolvida.result().profile_id
                  if resolvida is not None and resolvida.done() and not resolvida.cancelled()
                  and resolvida.exception() is None else None)
        evento = speech_metrics.collect_outcome(
            tenant_id=self._settings.tenant_id, session_id=session_id,
            pool_id=(self._sessions.get(session_id) or {}).get("pool_id"),
            menu_id=p.menu_id, interaction=p.interaction, inputs=list(p.inputs), outcome=outcome,
            via=via, release_reason=release_reason, counters=ac.session.counters(),
            min_confidence=p.min_confidence, end_silence_ms=p.end_silence_ms, max_speech_ms=p.max_speech_ms,
            duration_ms=(time.monotonic() - ac.started_at) * 1000, speech_profile_id=perfil)
        disparar(speech_metrics.publish(self._producer, evento), nome=f"speech-metrics-{session_id[:8]}")

    async def _publish_collect_done(self, session_id: str, p: CollectPlan, done: Done) -> None:
        """O desfecho vira `menu_result` — com o valor, como a resposta pela tela, ou com
        `outcome`, que o bridge entrega ao menu como SINAL (fatia 5a)."""
        info = self._sessions.get(session_id)
        if not info:
            logger.error("webrtc coleta: desfecho %s do menu %s em sessao sem registro de abertura "
                         "(session=%s) — NAO publicado", done.outcome, p.menu_id, session_id)
            return
        # Payloads LITERAIS e só com chaves que o bridge lê: `probe_menu_result_contract` mede cada
        # produtor pelo literal e reprova chave sem leitor. Por onde veio (tecla/fala) fica no log.
        if done.outcome == "value":
            content = MessageContent(type="menu_result", payload={
                "menu_id": p.menu_id, "interaction": p.interaction, "result": done.value})
        else:
            content = MessageContent(type="menu_result", payload={
                "menu_id": p.menu_id, "outcome": done.outcome})
        event = NormalizedInboundEvent(
            contact_id       = info["contact_id"],
            session_id       = session_id,
            channel          = info.get("channel") or "webrtc",
            author           = MessageAuthor(type="customer"),
            content          = content,
            context_snapshot = await self._context_reader.get_snapshot(session_id),
        )
        if done.outcome == "value" and done.via == "dtmf":
            # a tecla não deixa rastro de texto como a fala (que é registro) ou o clique (que o
            # widget mostra): a linha de histórico é a da resposta pela tela
            await self._registry.append_message(
                session_id = session_id,
                message_id = event.message_id,
                author     = "customer",
                text       = menu_result_history_text(p.interaction, done.value, set()),
                timestamp  = event.timestamp,
            )
        try:
            await self._publish_inbound(event.model_dump())
        except Exception as exc:
            logger.error("webrtc coleta: desfecho %s do menu %s NAO publicado (session=%s): %s — "
                         "o menu fica ate o prazo do motor", done.outcome, p.menu_id, session_id, exc)
            return
        logger.info("webrtc coleta: menu %s -> %s%s (session=%s)", p.menu_id, done.outcome,
                    f" por {done.via}" if done.via else "", session_id)

    async def _screen_invalid(self, ws: WebSocket, session_id: str, plano: CollectPlan) -> None:
        """Resposta pela tela fora do domínio ou do tamanho: não vai ao menu. Esgotado
        `max_invalid`, o menu recebe o desfecho `invalid` — como pela tecla."""
        contagem = self._screen_invalids.setdefault(session_id, {})
        n = contagem[plano.menu_id] = contagem.get(plano.menu_id, 0) + 1
        logger.info(
            "webrtc coleta: resposta pela tela INVALIDA %d%s no menu %s (valor nao registrado) session=%s",
            n, f" de {plano.max_invalid}" if plano.max_invalid else "", plano.menu_id, session_id,
        )
        if plano.max_invalid is not None and n >= plano.max_invalid:
            self._end_collect(session_id, "invalidos esgotados pela tela", "screen_invalid")
            self._menu_plans.get(session_id, {}).pop(plano.menu_id, None)
            await self._publish_collect_done(session_id, plano, Done("invalid"))
            return
        await self._ws_send(ws, {
            "type": "conn.error", "code": "collect_invalid", "menu_id": plano.menu_id,
            "message": plano.invalid_message or "",
        })

    async def _dtmf_reader(self, session_id: str, room_client: IWebRTCRoomClient) -> None:
        """Teclas que o OUVINTE recebe. O SFU as entrega a todos na sala: só o cliente responde."""
        try:
            async for identity, digit in room_client.dtmf():
                if not identity.startswith(CUSTOMER_PREFIXES):
                    logger.info("webrtc dtmf: tecla de %r ignorada — so o cliente responde menu "
                                "(session=%s)", identity, session_id)
                    continue
                await self._collect_digit(session_id, digit)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("webrtc dtmf: leitor de teclas PAROU (session=%s): %s", session_id, exc)

    async def _collect_digit(self, session_id: str, digit: str) -> None:
        ac = self._collects.get(session_id)
        if ac is None or "dtmf" not in ac.plan.inputs:
            # o valor da tecla não vai ao log: pode ser dado do cliente
            logger.info("webrtc dtmf: tecla ignorada — %s (session=%s)",
                        "nenhuma coleta em curso" if ac is None else f"o menu {ac.plan.menu_id} nao coleta por teclado",
                        session_id)
            return
        if not ac.played.is_set():
            if not ac.plan.barge_in:
                logger.info("webrtc dtmf: tecla durante o prompt do menu %s ignorada (barge_in "
                            "desligado) session=%s", ac.plan.menu_id, session_id)
                return
            self._barge_in(session_id)
        await self._apply_collect(session_id, ac, ac.session.digit(digit, time.monotonic()))

    async def _collect_speech(self, session_id: str, transcript: str, confidence: float | None) -> None:
        """Fala final do cliente, já publicada como registro. Responde o menu só no modo `voice`."""
        ac = self._collects.get(session_id)
        if ac is None:
            return
        if "voice" not in ac.plan.inputs:
            logger.info("webrtc coleta: fala do cliente NAO responde o menu %s (coleta por %s) — fica "
                        "como registro (session=%s)", ac.plan.menu_id, sorted(ac.plan.inputs), session_id)
            return
        if not ac.played.is_set() and not ac.plan.barge_in:
            logger.info("webrtc coleta: fala durante o prompt do menu %s ignorada (barge_in desligado) "
                        "session=%s", ac.plan.menu_id, session_id)
            return
        if ac.plan.min_confidence is not None and confidence is None:
            logger.warning("webrtc coleta: confianca desta fala NAO medida — min_confidence=%s do menu %s "
                           "nao aplicado a ela (session=%s)", ac.plan.min_confidence, ac.plan.menu_id, session_id)
        elif ac.plan.min_confidence is not None and confidence < ac.plan.min_confidence:
            # o núcleo conta como tentativa inválida; o texto não vai ao log, a medida sim
            logger.info("webrtc coleta: fala abaixo da confianca minima no menu %s (%.3f < %s) — "
                        "tentativa invalida (session=%s)", ac.plan.menu_id, confidence,
                        ac.plan.min_confidence, session_id)
        await self._apply_collect(session_id, ac, ac.session.speech(transcript, confidence, time.monotonic()))

    def _voice_barge_allowed(self, session_id: str) -> bool:
        """A voz do cliente interrompe a fala? Sempre, fora do prompt de uma coleta; durante ele, só
        se a coleta aceita fala e barge-in (tossir num menu de teclado não corta as opções)."""
        ac = self._collects.get(session_id)
        if ac is None or ac.played.is_set():
            return True
        return ac.plan.barge_in and "voice" in ac.plan.inputs

    def _collect_refuses_text(self, session_id: str) -> bool:
        ac = self._collects.get(session_id)
        return ac is not None and "text" not in ac.plan.inputs

    def _beep(self, session_id: str) -> None:
        """Eco `masked`: um bipe por tecla, pela VOZ."""
        voice = self._voice_clients.get(session_id)
        if voice is None:
            logger.info("webrtc coleta: eco por bipe sem voz na sala — tecla aceita sem eco (session=%s)",
                        session_id)
            return
        # na taxa da fala: a trilha da voz nasce com a taxa do primeiro áudio e não a troca
        rate = getattr(self._tts, "output_sample_rate", None) or _BEEP_RATE
        disparar(voice.publish_audio(_beep_pcm(rate), sample_rate=rate), nome=f"webrtc-bipe-{session_id[:8]}")

    # ── Phase D: Egress Recording ─────────────────────────────────────────────

    async def _start_egress(
        self,
        session_id: str,
        segment_id: str,
        room_name:  str,
    ) -> None:
        """
        Announce LGPD notice then start a LiveKit composite egress for this
        session/segment.

        Guard against double-start: if the Redis key already exists (e.g. rapid
        re-trigger), the call is a no-op.  The egress_id is stored both in the
        in-process dict and in Redis so teardown works even after a Gateway
        restart (best-effort — restart gap leaves egress running, not crashed).

        Called as a fire-and-forget task from _on_routing_assigned() when
        a recording trigger. ⚠️ Sem chamador desde a VOZ-10: o gatilho lia um
        `pool.webrtc_recording` que ninguem produzia, e volta com a VOZ-06.
        """
        s = self._settings

        # Double-start guard
        rec_key = f"channel:webrtc:{session_id}:egress:{segment_id}"
        if await self._redis.exists(rec_key):
            logger.info(
                "webrtc egress: already recording session=%s segment=%s — skip",
                session_id, segment_id,
            )
            return

        # Claim the recording slot before any async work to prevent races
        await self._redis.set(rec_key, "starting", ex=_SESSION_TTL)

        # Aviso LGPD de gravação: SEMPRE por texto, e também falado quando há voz na chamada.
        # Só na fila de fala (como era) ele podia não chegar a ninguém — descartado depois da
        # espera, cortado pelo barge-in, ou entregue a um participante que ninguém ouve — e o log
        # diria "o texto ja chegou ao widget", falso para ele (VOZ-05 fatia 4). A gravação não
        # depende de o aviso ter sido FALADO; se deve esperar o fim da fala é decisão da VOZ-06.
        notice = s.webrtc_recording_notice
        self._speak(session_id, notice)
        ws = self._connections.get(session_id)
        if ws:
            await self._ws_send(ws, {
                "type": "webrtc.message",
                "text": notice,
                "author": "system",
                "ts": datetime.now(timezone.utc).isoformat(),
            })
        else:
            logger.warning(
                "webrtc egress: aviso de gravacao sem WebSocket do cliente session=%s — "
                "o texto NAO foi entregue (%s)", session_id,
                "so falado" if self._can_speak(session_id) else "nem falado",
            )

        # Natural pause after notice (mirrors voice channel behaviour)
        await asyncio.sleep(1.5)

        # Build output file path (shared volume between LiveKit and Gateway)
        output_dir = Path(s.webrtc_egress_output_dir) / session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"{segment_id}.mp4")

        try:
            egress_id = await self._provider.start_egress(
                room_name    = room_name,
                output_url   = output_path,
                layout       = "speaker",
                dual_channel = True,
            )
        except Exception as exc:
            logger.error(
                "webrtc egress: start_egress failed session=%s segment=%s: %s",
                session_id, segment_id, exc,
            )
            # Release the claim so a retry is possible
            await self._redis.delete(rec_key)
            return

        # Persist egress_id
        await self._redis.set(rec_key, egress_id, ex=_SESSION_TTL)
        if session_id not in self._session_egress:
            self._session_egress[session_id] = {}
        self._session_egress[session_id][segment_id] = egress_id

        logger.info(
            "webrtc egress started: session=%s segment=%s egress_id=%s output=%s",
            session_id, segment_id, egress_id, output_path,
        )

    async def _stop_all_egress(self, session_id: str) -> None:
        """
        Stop all active egress recordings for a session and commit them to the
        AttachmentStore.  Called from both _close_session() and
        deliver_session_closed() — idempotent (cleared from dict on first call).
        """
        active = self._session_egress.pop(session_id, {})
        if not active:
            return

        for segment_id, egress_id in active.items():
            disparar(
                self._stop_egress_and_store(session_id, segment_id, egress_id),
                nome=f"webrtc-egress-stop-{session_id[:8]}-{segment_id[:8]}",
            )

    async def _stop_egress_and_store(
        self,
        session_id: str,
        segment_id: str,
        egress_id:  str,
    ) -> None:
        """
        Stop the LiveKit egress, wait for file finalization, commit bytes to
        AttachmentStore, and write a recording.completed event to the session
        stream.

        Steps:
          1. stop_egress(egress_id)
          2. sleep(webrtc_egress_wait_s) — LiveKit flushes the MP4 container
          3. Read file from shared output path
          4. Commit to AttachmentStore (if configured) → file_id, serving_url
          5. XADD recording.completed to session stream
          6. Delete local temp file
        """
        s          = self._settings
        output_dir = Path(s.webrtc_egress_output_dir) / session_id
        output_path = output_dir / f"{segment_id}.mp4"

        # Step 1 — stop egress
        try:
            await self._provider.stop_egress(egress_id)
        except Exception as exc:
            logger.warning(
                "webrtc egress: stop_egress error session=%s egress=%s: %s",
                session_id, egress_id, exc,
            )

        # Step 2 — wait for LiveKit to flush the output file
        await asyncio.sleep(s.webrtc_egress_wait_s)

        # Step 3 — read recording bytes
        file_bytes: bytes = b""
        file_size  = 0
        try:
            file_bytes = output_path.read_bytes()
            file_size  = len(file_bytes)
        except FileNotFoundError:
            logger.warning(
                "webrtc egress: recording file not found session=%s path=%s",
                session_id, output_path,
            )
        except Exception as exc:
            logger.warning(
                "webrtc egress: could not read recording session=%s: %s",
                session_id, exc,
            )

        # Step 4 — commit to AttachmentStore
        file_id     = str(uuid.uuid4())
        serving_url = str(output_path)  # fallback: local path

        if file_bytes and self._attachment_store is not None:
            try:
                from ..attachment_store import resolve_attachment_expiry_days
                _expiry_days = await resolve_attachment_expiry_days(
                    self._redis, s.tenant_id, s.attachment_expiry_days
                )
                expires_at = datetime.now(timezone.utc) + timedelta(days=_expiry_days)
                file_id_reserved, _ = await self._attachment_store.reserve(
                    tenant_id  = s.tenant_id,
                    session_id = session_id,
                    file_name  = f"recording-{session_id[:8]}-{segment_id[:8]}.mp4",
                    mime_type  = "video/mp4",
                    size_bytes = file_size,
                    expires_at = expires_at,
                )
                meta = await self._attachment_store.commit(
                    file_id   = file_id_reserved,
                    tenant_id = s.tenant_id,
                    data      = file_bytes,
                )
                file_id     = meta.file_id
                serving_url = getattr(meta, "serving_url", serving_url)
                logger.info(
                    "webrtc egress: recording committed file_id=%s url=%s "
                    "session=%s segment=%s size=%d",
                    file_id, serving_url, session_id, segment_id, file_size,
                )
            except Exception as exc:
                logger.error(
                    "webrtc egress: AttachmentStore commit failed session=%s: %s",
                    session_id, exc,
                )

        # Step 5 — write recording.completed to session stream
        try:
            stream_key = f"session:{session_id}:stream"
            await self._redis.xadd(
                stream_key,
                {
                    "type":       "recording.completed",
                    "session_id": session_id,
                    "segment_id": segment_id,
                    "egress_id":  egress_id,
                    "file_id":    file_id,
                    "serving_url": serving_url,
                    "size_bytes": str(file_size),
                    "channel":    "webrtc",
                },
            )
            logger.info(
                "webrtc egress: recording.completed event written to stream "
                "session=%s segment=%s file_id=%s",
                session_id, segment_id, file_id,
            )
        except Exception as exc:
            logger.error(
                "webrtc egress: stream XADD failed session=%s: %s", session_id, exc
            )

        # Step 6 — clean up local temp file
        if file_bytes:
            try:
                output_path.unlink(missing_ok=True)
                # Remove dir if empty
                try:
                    output_dir.rmdir()
                except OSError:
                    pass
            except Exception as exc:
                logger.debug(
                    "webrtc egress: cleanup error session=%s path=%s: %s",
                    session_id, output_path, exc,
                )

        # Clear Redis egress key
        try:
            await self._redis.delete(
                f"channel:webrtc:{session_id}:egress:{segment_id}"
            )
        except Exception:
            pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _resolve_pool(self, pool_id: str, contact_id: str) -> tuple[str, str | None]:
        """
        Resolve pool_id via Layer 2 agent-registry lookup (ChannelEndpoint).
        Falls back to webrtc_default_pool_id when no endpoint record matches.

        Devolve também o `speech_profile_id` que o endpoint aponta em `settings` (VOZ-25) — só
        existe quando o endereço é um endpoint cadastrado; pool direto não tem perfil.
        """
        s = self._settings
        if pool_id and s.agent_registry_url:
            try:
                from ..endpoint_resolver import resolve_endpoint as _resolve
                ep = await _resolve(
                    channel            = "webrtc",
                    identifier         = pool_id,
                    tenant_id          = s.tenant_id,
                    agent_registry_url = s.agent_registry_url,
                    cache_ttl_s        = s.endpoint_cache_ttl_s,
                )
                if ep.pool_id:
                    perfil = ep.settings.get("speech_profile_id")
                    if perfil is not None and not isinstance(perfil, str):
                        logger.error("webrtc: endpoint %s tem speech_profile_id nao-texto (%r) — ignorado",
                                     pool_id, perfil)
                        perfil = None
                    return ep.pool_id, (perfil or None)
            except Exception as exc:
                logger.warning("webrtc pool resolve failed: %s", exc)
        return pool_id or s.webrtc_default_pool_id, None

    async def _resolve_jwt_secret(self, tenant_id: str) -> str:
        """
        Resolve JWT secret: Redis per-tenant override → env var default.
        Mirrors the webchat channel JWT secret resolution.
        """
        try:
            override = await self._redis.get(
                f"{tenant_id}:config:webchat:jwt_secret"
            )
            if override:
                return override
        except Exception:
            pass
        return self._settings.jwt_secret

    async def _publish_inbound(self, payload: dict) -> None:
        """Publish to conversations.inbound Kafka topic."""
        await self._producer.send(
            self._settings.kafka_topic_inbound,
            json.dumps(payload).encode(),
        )

    async def _publish_event(self, payload: dict) -> None:
        """Publish to conversations.events — abertura e fechamento de contato (VOZ-04)."""
        await self._producer.send(
            self._settings.kafka_topic_events,
            json.dumps(payload).encode(),
        )

    async def _touch_ws_alive(self, session_id: str) -> None:
        await self._redis.setex(
            f"session:{session_id}:ws_alive",
            contact_lifecycle.ws_alive_ttl_s(self._settings.ws_connection_timeout_s),
            "1",
        )

    # ── Coleta mascarada (VOZ-05, fatia A) ────────────────────────────────────

    async def _masked_capture_active(self, session_id: str) -> bool:
        """
        `True` enquanto o valor protegido está sendo coletado: um menu mascarado espera no
        motor (`menu:waiting:{sid}`, entrada com `masked` ou `masked_fields`), ou a folga das
        bordas não venceu. Leitura que FALHA conta como ativa — o restritivo vence, porque o
        permissivo aqui degrada para o valor em claro no histórico.
        """
        if time.monotonic() < self._masked_grace_until.get(session_id, 0.0):
            return True
        try:
            waiting = await self._redis.hgetall(f"menu:waiting:{session_id}")
        except Exception as exc:
            logger.warning(
                "webrtc: menu:waiting ilegivel (session=%s): %s — coleta mascarada tratada "
                "como ATIVA", session_id, exc,
            )
            return True
        for raw in (waiting or {}).values():
            try:
                meta = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning(
                    "webrtc: entrada de menu:waiting nao e JSON (session=%s) — coleta "
                    "mascarada tratada como ATIVA", session_id,
                )
                return True
            if isinstance(meta, dict) and (meta.get("masked") or meta.get("masked_fields")):
                return True
        return False

    async def _masked_fields_for(self, session_id: str, menu_id: str) -> set[str] | None:
        """
        Campos mascarados do menu submetido. Primeiro o que o `deliver_menu` guardou; sem
        isso (gateway reiniciou entre a entrega e a resposta), a UNIÃO do que o motor
        declara em `menu:waiting`, como o bridge faz. `None` = não se sabe.
        """
        known = self._menu_masked.get(session_id, {}).pop(menu_id, None)
        if known is not None:
            return set(known)
        try:
            waiting = await self._redis.hgetall(f"menu:waiting:{session_id}")
        except Exception:
            return None
        if not waiting:
            return None
        fields: set[str] = set()
        for raw in waiting.values():
            try:
                meta = json.loads(raw)
            except (TypeError, ValueError):
                return None
            if isinstance(meta, dict):
                if meta.get("masked") and not meta.get("masked_fields"):
                    return None
                fields.update(f for f in (meta.get("masked_fields") or []) if isinstance(f, str))
        return fields

    async def _handle_menu_submit(self, ws: WebSocket, session_id: str, msg: dict) -> None:
        """
        Resposta de menu/formulário do cliente — o mesmo evento do webchat: `menu_result` com
        o valor REAL em `conversations.inbound` (o bridge o entrega ao menu que espera e o
        redige para stream e analytics), e a linha de histórico REDIGIDA pela
        `menu_result_history_text`, a mesma casa do webchat.
        """
        menu_id     = msg.get("menu_id")
        interaction = msg.get("interaction")
        result      = msg.get("result")
        if (not isinstance(menu_id, str) or not menu_id
                or interaction not in ("text", "button", "list", "checklist", "form")
                or not isinstance(result, (str, list, dict))):
            logger.warning(
                "webrtc: webrtc.menu_submit malformado (chaves=%s) — descartado session=%s",
                sorted(msg), session_id,
            )
            await self._ws_send(ws, {
                "type": "conn.error", "code": "bad_message",
                "message": "webrtc.menu_submit requires menu_id, interaction and result",
            })
            return
        info = self._sessions.get(session_id)
        if not info:
            logger.error(
                "webrtc: menu_submit de sessao sem registro de abertura (session=%s) — descartado",
                session_id,
            )
            return

        plano = self._menu_plans.get(session_id, {}).get(menu_id)
        if plano is not None and plano.is_digit_field and interaction == "text":
            # 5c: a resposta pela tela passa pela MESMA regra da tecla. O valor nunca vai ao log.
            if not (isinstance(result, str) and plano.accepts_digits(result.strip())):
                await self._screen_invalid(ws, session_id, plano)
                return
            if isinstance(result, str) and plano.terminator and result.strip().endswith(plano.terminator):
                result = result.strip()[:-1]

        ativa = self._collects.get(session_id)
        if ativa is not None and ativa.plan.menu_id == menu_id:
            # a tela respondeu: a coleta por teclado/fala do mesmo menu termina sem desfecho próprio
            self._end_collect(session_id, "respondido pela tela", "screen")

        masked = await self._masked_fields_for(session_id, menu_id)
        if masked is None:
            # Nem o adapter nem o motor dizem o que é protegido: o histórico recebe só a
            # existência da resposta. Restritivo, e dito.
            logger.warning(
                "webrtc: campos mascarados do menu %s desconhecidos (session=%s) — resposta "
                "inteira redigida no historico", menu_id, session_id,
            )
            history = menu_result_history_text("text", result, {"resposta"})
        else:
            history = menu_result_history_text(interaction, result, masked)
        # a fala que ainda está sendo transcrita não pode chegar depois do HDEL do motor
        if masked is None or masked:
            self._masked_grace_until[session_id] = time.monotonic() + _MASKED_SPEECH_GRACE_S

        snapshot = await self._context_reader.get_snapshot(session_id)
        event = NormalizedInboundEvent(
            contact_id       = info["contact_id"],
            session_id       = session_id,
            channel          = "webrtc",
            author           = MessageAuthor(type="customer"),
            content          = MessageContent(
                type    = "menu_result",
                payload = {"menu_id": menu_id, "interaction": interaction, "result": result},
            ),
            context_snapshot = snapshot,
        )
        await self._registry.append_message(
            session_id = session_id,
            message_id = event.message_id,
            author     = "customer",
            text       = history,
            timestamp  = event.timestamp,
        )
        await self._publish_inbound(event.model_dump())
        logger.info(
            "webrtc: menu_submit publicado session=%s menu=%s interaction=%s mascarados=%s",
            session_id, menu_id, interaction,
            "desconhecidos" if masked is None else sorted(masked),
        )

    async def _publish_customer_text(
        self, session_id: str, text: str, message_id: str | None = None,
        *, content_type: str = "text", payload: dict | None = None,
    ) -> None:
        """
        Mensagem do cliente (digitada, ou transcrita pelo STT) como `NormalizedInboundEvent` —
        o evento que o bridge reconhece. O formato solto de antes (`content` sem `type`) era
        descartado lá como "Unknown content type".
        """
        info = self._sessions.get(session_id)
        if not info:
            logger.error(
                "webrtc: mensagem de sessao sem registro de abertura (session=%s) — descartada",
                session_id,
            )
            return
        snapshot = await self._context_reader.get_snapshot(session_id)
        event = NormalizedInboundEvent(
            message_id       = message_id or str(uuid.uuid4()),
            contact_id       = info["contact_id"],
            session_id       = session_id,
            channel          = info.get("channel") or "webrtc",
            content_type     = content_type,  # type: ignore[arg-type]
            author           = MessageAuthor(type="customer"),
            content          = MessageContent(type="text", text=text, payload=payload),
            context_snapshot = snapshot,
        )
        if content_type != "audio_transcript":
            # A fala transcrita NÃO entra no histórico de chat (VOZ-05 fatia 4): é a lista que o
            # Console recarrega, e o humano não deve ver como digitado o que acabou de ouvir. A
            # fala vai à sessão pelo bridge, com a marca de origem.
            await self._registry.append_message(
                session_id = session_id,
                message_id = event.message_id,
                author     = "customer",
                text       = text,
                timestamp  = event.timestamp,
            )
        await self._publish_inbound(event.model_dump())
        logger.debug("webrtc: texto do cliente publicado session=%s len=%d", session_id, len(text))

    @staticmethod
    async def _ws_send(ws: WebSocket, message: dict) -> None:
        """Send a JSON message over the WebSocket; silently ignore closed socket."""
        try:
            await ws.send_json(message)
        except Exception as exc:
            logger.debug("webrtc ws_send failed: %s", exc)

    @staticmethod
    async def _ws_error(ws: WebSocket, code: str, message: str) -> None:
        """Send conn.error and close the WebSocket."""
        await WebRTCAdapter._ws_send(ws, {
            "type":    "conn.error",
            "code":    code,
            "message": message,
        })
        try:
            await ws.close(code=4003)
        except Exception:
            pass


# ── Internal exceptions ───────────────────────────────────────────────────────


class _AuthError(Exception):
    """Raised during WebRTC auth handshake with a structured error code."""
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code    = code
        self.message = message
