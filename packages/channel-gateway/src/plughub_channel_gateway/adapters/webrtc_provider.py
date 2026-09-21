"""
adapters/webrtc_provider.py
Provider abstraction for the WebRTC channel (LiveKit SFU).

Architecture: docs/arcos/arc15-webrtc.md

One Protocol interface:
  IWebRTCProvider — room lifecycle + JWT token generation + egress recording

Concrete implementations:
  LiveKitProvider  — livekit-api SDK (livekit.api package)
  MockWebRTCProvider — in-memory stub for unit tests (no network I/O)

SEM CREDENCIAL, O PROVIDER RECUSA — NÃO DEGRADA (VOZ-01, ADR voice-media-plane V6)
  Até 2026-09-14 `LiveKitProvider` ligava `_dev_mode` quando key/secret vinham vazios,
  e devolvia token `dev-token-…`, sala `RM_dev_…` e egress `EG_dev_…`; com o SDK ausente
  devolvia `missing-sdk-token-…`. Nada disso ficava vermelho — um token bem-formado e
  falso é o valor plausível na forma mais cara, e foi assim que o Arc 15 pareceu pronto
  por meses sem SFU em ambiente algum. Hoje a construção levanta
  `WebRTCProviderUnavailable`, que NOMEIA o que falta (env e/ou SDK). Modo sem
  infraestrutura existe só por escolha explícita: injetar `MockWebRTCProvider`.

Token model:
  All LiveKit tokens are signed by Channel Gateway using LIVEKIT_API_SECRET.
  Tokens are NEVER returned to the browser directly — the browser receives a
  short-lived URL+token bundle served by /webrtc/token/{session_id}.

Egress (VOZ-06):
  start_egress grava a sala só em ÁUDIO misturado (decisão do dono), em OGG, num caminho do
  volume que o `livekit-egress` e o gateway montam no MESMO lugar. `wait_egress` espera o
  egress TERMINAR e devolve o arquivo — medido em 2026-09-18: o `stop_egress` responde com o
  egress ainda em ENDING, e o arquivo só existe depois; o `sleep(5)` de antes adivinhava.
  ⚠️ Medido: `audio_only` NÃO dispensa o Chrome do egress (`sourceType WEB`) — misturar a sala
  é composição, e composição é o navegador; ativo em ~2-3 s.

Adding a new SFU provider (mediasoup, Janus):
  1. Implement IWebRTCProvider Protocol
  2. Set PLUGHUB_WEBRTC_PROVIDER=mediasoup env var
  3. Register in WebRTCAdapter._build_provider()
"""

from __future__ import annotations

import datetime
import importlib.util
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("plughub.channel-gateway.webrtc.provider")


# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class RoomInfo:
    """Minimal LiveKit room metadata."""
    room_name:    str
    room_sid:     str
    num_participants: int = 0
    creation_time:    int = 0   # Unix timestamp


@dataclass
class ParticipantInfo:
    """LiveKit participant metadata."""
    identity:     str
    sid:          str
    state:        str   # "JOINING" | "JOINED" | "ACTIVE" | "DISCONNECTED"
    can_publish:  bool = True
    can_subscribe: bool = True
    is_hidden:    bool = False


@dataclass
class TokenGrants:
    """
    Permission grants for a LiveKit token.

    role shortcuts:
      "customer"   → can_publish=True, can_subscribe=True, hidden=False
      "agent"      → can_publish=True, can_subscribe=True, hidden=False
      "supervisor" → can_publish=False, can_subscribe=True, hidden=True
      "recorder"   → internal egress participant (managed by LiveKit, not here)
    """
    room_name:          str
    identity:           str
    display_name:       str       = ""
    can_publish:        bool      = True
    can_subscribe:      bool      = True
    can_publish_data:   bool      = True    # DataChannel (text fallback)
    hidden:             bool      = False   # supervisor mode
    ttl_seconds:        int       = 3600
    # VOZ-09 — fontes que o participante PODE publicar (`microphone`, `camera`).
    # None = sem recorte por fonte (caminho legado). ⚠️ No LiveKit uma lista VAZIA em
    # `can_publish_sources` significa TODAS as fontes; por isso lista vazia aqui vira
    # `can_publish=False` no provider — "nada" nunca pode virar "tudo".
    can_publish_sources: tuple[str, ...] | None = None


@dataclass
class EgressResult:
    """Como um egress TERMINOU. `status` é o nome do estado do SFU (`EGRESS_COMPLETE`, …);
    `filename` vazio = não há arquivo, e `error` diz por quê."""
    egress_id:   str
    status:      str
    error:       str   = ""
    filename:    str   = ""
    duration_ms: int   = 0
    size_bytes:  int   = 0

    @property
    def complete(self) -> bool:
        return self.status == "EGRESS_COMPLETE" and bool(self.filename)


# ── Protocol interface ────────────────────────────────────────────────────────


@runtime_checkable
class IWebRTCProvider(Protocol):
    """LiveKit SFU operations — room lifecycle, token issuance, egress."""

    def generate_token(self, grants: TokenGrants) -> str:
        """
        Sign and return a LiveKit JWT token for a participant.
        Called synchronously — no network I/O.
        """
        ...

    async def create_room(
        self,
        room_name:         str,
        empty_timeout_s:   int = 300,
        max_participants:  int = 50,
    ) -> RoomInfo:
        """
        Create a LiveKit room.  Returns RoomInfo with room_sid.
        Idempotent: if room already exists, returns existing room.
        """
        ...

    async def delete_room(self, room_name: str) -> None:
        """Delete a LiveKit room and disconnect all participants."""
        ...

    async def get_room(self, room_name: str) -> RoomInfo | None:
        """Return RoomInfo if room exists, else None."""
        ...

    async def list_participants(self, room_name: str) -> list[ParticipantInfo]:
        """Return current participants in a room."""
        ...

    async def start_egress(self, room_name: str, filepath: str) -> str:
        """Grava a sala só em ÁUDIO misturado (OGG) em `filepath`. Devolve o egress_id; falha
        levanta — quem chama diz que a gravação NÃO começou."""
        ...

    async def stop_egress(self, egress_id: str) -> None:
        """Pede o fim do egress. Não espera o arquivo — isso é `wait_egress`."""
        ...

    async def wait_egress(self, egress_id: str, timeout_s: float) -> EgressResult:
        """Espera o egress TERMINAR (completo, falho ou abortado) e devolve como terminou.
        Estourado o prazo, devolve o último estado visto com `error` dizendo que não terminou."""
        ...

    async def update_participant_permission(
        self,
        room_name:           str,
        identity:            str,
        can_publish_sources: tuple[str, ...],
        can_subscribe:       bool = True,
        can_publish_data:    bool = True,
    ) -> bool:
        """
        Troca a permissão de um participante JÁ na sala (VOZ-09).

        True = aplicada. False = o participante não está na sala (ainda não entrou, ou
        saiu) — nada a aplicar, e quem chama entrega um token novo ao cliente. Qualquer
        outro erro PROPAGA: permissão que não se sabe se foi aplicada não é "False".
        """
        ...

    async def remove_participant(self, room_name: str, identity: str) -> bool:
        """
        Tira um participante da SALA de mídia — não da sessão (NIV-07: pausa de mídia do
        humano durante a coleta mascarada no telefone).

        True = removido. False = não estava na sala. Qualquer outro erro PROPAGA: quem pediu a
        remoção precisa saber que ela não aconteceu, porque é ela que impede o PIN de chegar lá.
        """
        ...


# ── LiveKitProvider ───────────────────────────────────────────────────────────


class WebRTCProviderUnavailable(RuntimeError):
    """
    O plano de mídia não pode ser usado — e a mensagem diz POR QUÊ.

    `missing` lista o que falta, com o nome que o operador procura: a variável de
    ambiente (`PLUGHUB_WEBRTC_LIVEKIT_API_KEY`, …) ou o pacote do SDK. Quem captura
    repassa `str(exc)` ao log e à resposta; um *"WebRTC indisponível"* genérico seria
    a frase que ninguém lê (§ Configuration, corolário de 2026-08-25).
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = list(missing)
        super().__init__(
            "plano de mídia WebRTC indisponível — falta: " + ", ".join(self.missing)
        )


# Nome da env de cada credencial, para a recusa nomear o que falta.
_ENV_URL    = "PLUGHUB_WEBRTC_LIVEKIT_URL"
_ENV_KEY    = "PLUGHUB_WEBRTC_LIVEKIT_API_KEY"
_ENV_SECRET = "PLUGHUB_WEBRTC_LIVEKIT_API_SECRET"


def _sdk_present() -> bool:
    try:
        return importlib.util.find_spec("livekit.api") is not None
    except ModuleNotFoundError:     # o pacote-pai `livekit` inteiro ausente
        return False


class LiveKitProvider:
    """
    LiveKit SFU integration via livekit-api Python SDK (server API only).

    Construção RECUSA (`WebRTCProviderUnavailable`) quando falta URL, key, secret ou o
    SDK — não há mais `_dev_mode` nem retorno *mock* por `ImportError`. Ver o cabeçalho
    do módulo.

    Falha de rede NÃO vira valor plausível: `get_room` e `list_participants` propagam
    a exceção em vez de responder *"sala não existe"* / *"ninguém na sala"* quando o
    SFU não respondeu. As duas únicas que engolem são `delete_room` e `stop_egress`,
    caminhos de LIMPEZA, e logam `warning` nomeando a sala/egress.
    """

    def __init__(
        self,
        url:        str,
        api_key:    str,
        api_secret: str,
    ) -> None:
        missing = [
            env for env, value in (
                (_ENV_URL, url), (_ENV_KEY, api_key), (_ENV_SECRET, api_secret),
            ) if not value
        ]
        if not _sdk_present():
            missing.append("SDK livekit-api (pacote `livekit-api` no pyproject)")
        if missing:
            raise WebRTCProviderUnavailable(missing)
        self._url        = url
        self._api_key    = api_key
        self._api_secret = api_secret

    def generate_token(self, grants: TokenGrants) -> str:
        """Sign a LiveKit JWT token (no network I/O)."""
        from livekit.api import AccessToken, VideoGrants as LKVideoGrants

        sources = grants.can_publish_sources
        can_publish = grants.can_publish if sources is None else (grants.can_publish and bool(sources))
        extra = {} if sources is None else {"can_publish_sources": list(sources)}
        at = (
            AccessToken(self._api_key, self._api_secret)
            .with_identity(grants.identity)
            .with_name(grants.display_name or grants.identity)
            # ⚠️ `timedelta`, não `int`. Passava `grants.ttl_seconds` cru, e o SDK soma o
            # TTL a um `datetime` → `TypeError` no `to_jwt()`. Esta linha NUNCA tinha
            # rodado: `_dev_mode` ou o ramo de `ImportError` a pulavam sempre. Foi o
            # primeiro SFU real (VOZ-01) que a executou.
            .with_ttl(datetime.timedelta(seconds=grants.ttl_seconds))
            .with_grants(
                LKVideoGrants(
                    room_join         = True,
                    room              = grants.room_name,
                    can_publish       = can_publish,
                    can_subscribe     = grants.can_subscribe,
                    can_publish_data  = grants.can_publish_data,
                    hidden            = grants.hidden,
                    **extra,
                )
            )
        )
        return at.to_jwt()

    async def create_room(
        self,
        room_name:        str,
        empty_timeout_s:  int = 300,
        max_participants: int = 50,
    ) -> RoomInfo:
        from livekit.api import CreateRoomRequest, LiveKitAPI

        async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
            room = await lkapi.room.create_room(
                CreateRoomRequest(
                    name              = room_name,
                    empty_timeout     = empty_timeout_s,
                    max_participants  = max_participants,
                )
            )
            return RoomInfo(
                room_name     = room.name,
                room_sid      = room.sid,
                num_participants = room.num_participants,
                creation_time = room.creation_time,
            )

    async def delete_room(self, room_name: str) -> None:
        from livekit.api import DeleteRoomRequest, LiveKitAPI

        try:
            async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
                await lkapi.room.delete_room(DeleteRoomRequest(room=room_name))
        except Exception as exc:
            logger.warning("delete_room failed (%s): %s", room_name, exc)

    async def get_room(self, room_name: str) -> RoomInfo | None:
        """None só quando o SFU RESPONDEU que a sala não existe; erro de rede propaga."""
        from livekit.api import ListRoomsRequest, LiveKitAPI

        async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
            resp = await lkapi.room.list_rooms(ListRoomsRequest(names=[room_name]))
            if not resp.rooms:
                return None
            r = resp.rooms[0]
            return RoomInfo(
                room_name    = r.name,
                room_sid     = r.sid,
                num_participants = r.num_participants,
                creation_time = r.creation_time,
            )

    async def list_inbound_trunks(self) -> list[tuple[str, list[str]]]:
        """(nome, números) de cada tronco SIP de entrada no SFU (VOZ-41). Lista vazia só quando o
        SFU RESPONDEU; erro de rede propaga — "não sei" e "não há tronco" não podem ter a mesma cara."""
        from livekit.api import LiveKitAPI
        from livekit.protocol import sip as lsip

        async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
            resp = await lkapi.sip.list_inbound_trunk(lsip.ListSIPInboundTrunkRequest())
            return [(t.name, list(t.numbers)) for t in resp.items]

    async def list_participants(self, room_name: str) -> list[ParticipantInfo]:
        """Lista vazia só quando o SFU RESPONDEU; erro de rede propaga."""
        from livekit.api import ListParticipantsRequest, LiveKitAPI

        async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
            resp = await lkapi.room.list_participants(
                ListParticipantsRequest(room=room_name)
            )
            return [
                ParticipantInfo(
                    identity  = p.identity,
                    sid       = p.sid,
                    state     = str(p.state),
                )
                for p in resp.participants
            ]

    async def start_egress(self, room_name: str, filepath: str) -> str:
        """Grava a sala em ÁUDIO misturado, OGG, em `filepath` — caminho que o egress e o
        gateway montam no mesmo lugar. Exceção propaga: sem serviço de egress, falha ALTO
        (antes do VOZ-01 devolvia `EG_dev_…` e o chamador registrava gravação iniciada)."""
        from livekit.api import (
            EncodedFileOutput,
            EncodedFileType,
            LiveKitAPI,
            RoomCompositeEgressRequest,
        )

        async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
            req = RoomCompositeEgressRequest(
                room_name    = room_name,
                audio_only   = True,
                file_outputs = [EncodedFileOutput(file_type=EncodedFileType.OGG, filepath=filepath)],
            )
            egress_info = await lkapi.egress.start_room_composite_egress(req)
            logger.info("LiveKit egress started: room=%s egress_id=%s output=%s",
                        room_name, egress_info.egress_id, filepath)
            return egress_info.egress_id

    async def wait_egress(self, egress_id: str, timeout_s: float) -> EgressResult:
        import asyncio as _asyncio
        from livekit.api import EgressStatus, LiveKitAPI, ListEgressRequest

        finais = {EgressStatus.EGRESS_COMPLETE, EgressStatus.EGRESS_FAILED,
                  EgressStatus.EGRESS_ABORTED, EgressStatus.EGRESS_LIMIT_REACHED}
        visto = None
        fim = time.monotonic() + timeout_s
        async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
            while True:
                resp = await lkapi.egress.list_egress(ListEgressRequest(egress_id=egress_id))
                visto = resp.items[0] if resp.items else None
                if visto is not None and visto.status in finais:
                    break
                if time.monotonic() >= fim:
                    break
                await _asyncio.sleep(0.5)
        if visto is None:
            return EgressResult(egress_id, "DESCONHECIDO", error="o SFU nao conhece este egress")
        status = EgressStatus.Name(visto.status)
        arq = visto.file_results[0] if visto.file_results else None
        erro = visto.error or ("" if visto.status in finais else f"nao terminou em {timeout_s:.0f} s")
        return EgressResult(
            egress_id   = egress_id,
            status      = status,
            error       = erro,
            filename    = arq.filename if arq else "",
            duration_ms = int((arq.duration if arq else 0) / 1_000_000),
            size_bytes  = int(arq.size if arq else 0),
        )

    async def stop_egress(self, egress_id: str) -> None:
        """Stop a running LiveKit egress (cleanup path: failure is logged, not raised)."""
        from livekit.api import LiveKitAPI, StopEgressRequest

        try:
            async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
                await lkapi.egress.stop_egress(StopEgressRequest(egress_id=egress_id))
            logger.info("LiveKit egress stopped: egress_id=%s", egress_id)
        except Exception as exc:
            logger.warning("stop_egress failed (egress_id=%s): %s", egress_id, exc)

    async def update_participant_permission(
        self,
        room_name:           str,
        identity:            str,
        can_publish_sources: tuple[str, ...],
        can_subscribe:       bool = True,
        can_publish_data:    bool = True,
    ) -> bool:
        from livekit.api import (
            LiveKitAPI, ParticipantPermission, RoomParticipantIdentity, UpdateParticipantRequest,
        )
        from livekit.api.twirp_client import TwirpError
        from livekit.protocol.models import TrackSource

        perm = ParticipantPermission(
            can_subscribe       = can_subscribe,
            # lista vazia no LiveKit = TODAS as fontes: teto vazio desliga o publish
            can_publish         = bool(can_publish_sources),
            can_publish_data    = can_publish_data,
            # ⚠️ Medido: aqui o protobuf exige o ENUM (`MICROPHONE`), enquanto o token JWT
            # aceita a string minúscula (`microphone`). Passar a string levanta
            # `ValueError: unknown enum label "microphone"`.
            can_publish_sources = [TrackSource.Value(s.upper()) for s in can_publish_sources],
        )
        async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
            # ⚠️ Pergunta-se ANTES, e isso é medição: `update_participant` sobre participante
            # ausente responde `unavailable`/503 ("no response from servers") — a mesma cara
            # de SFU fora do ar. `get_participant` responde `not_found`/404, limpo.
            try:
                await lkapi.room.get_participant(
                    RoomParticipantIdentity(room=room_name, identity=identity)
                )
            except TwirpError as exc:
                if exc.code == "not_found":
                    return False
                raise
            await lkapi.room.update_participant(UpdateParticipantRequest(
                room=room_name, identity=identity, permission=perm,
            ))
            return True

    async def remove_participant(self, room_name: str, identity: str) -> bool:
        from livekit.api import LiveKitAPI, RoomParticipantIdentity
        from livekit.api.twirp_client import TwirpError

        async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
            try:
                await lkapi.room.remove_participant(
                    RoomParticipantIdentity(room=room_name, identity=identity)
                )
            except TwirpError as exc:
                if exc.code == "not_found":
                    return False
                raise
            return True


# ── MockWebRTCProvider ────────────────────────────────────────────────────────


class MockWebRTCProvider:
    """
    In-memory stub for unit tests — no network I/O, no LiveKit SDK required.

    Records all calls for assertion:
      rooms_created, rooms_deleted, tokens_generated,
      egresses_started, egresses_stopped, participants
    """

    def __init__(self, verify_result: bool = True) -> None:
        self.rooms_created:    list[dict]  = []
        self.rooms_deleted:    list[str]   = []
        self.tokens_generated: list[dict]  = []
        self.egresses_started: list[dict]  = []
        self.egresses_stopped: list[str]   = []
        self._rooms:           dict[str, RoomInfo] = {}
        self._egress_counter:  int = 0
        # VOZ-06 — o que o egress "grava" no mock, e as duas falhas que o teste pode plantar
        self.egress_bytes:       bytes = b"OggS" + b"\x00" * 60
        self.egress_start_error: Exception | None = None
        self.egress_end_error:   str = ""
        self.permission_updates: list[dict] = []
        self.joined:           set[str] = set()   # identidades "na sala", para o teste
        self.participants_removed: list[tuple[str, str]] = []
        # VOZ-41 — troncos SIP de entrada "no SFU"; `inbound_trunks_error` planta a falha de rede
        self.inbound_trunks:       list[tuple[str, list[str]]] = []
        self.inbound_trunks_error: Exception | None = None

    async def list_inbound_trunks(self) -> list[tuple[str, list[str]]]:
        if self.inbound_trunks_error is not None:
            raise self.inbound_trunks_error
        return list(self.inbound_trunks)

    def generate_token(self, grants: TokenGrants) -> str:
        token = (
            f"mock-token-{grants.identity}-{grants.room_name}"
            f"-{'hidden' if grants.hidden else 'visible'}"
        )
        self.tokens_generated.append({
            "grants": grants,
            "token":  token,
        })
        return token

    async def create_room(
        self,
        room_name:        str,
        empty_timeout_s:  int = 300,
        max_participants: int = 50,
    ) -> RoomInfo:
        info = RoomInfo(
            room_name     = room_name,
            room_sid      = f"RM_mock_{len(self.rooms_created):04d}",
            creation_time = int(time.time()),
        )
        self._rooms[room_name] = info
        self.rooms_created.append({
            "room_name":        room_name,
            "empty_timeout_s":  empty_timeout_s,
            "max_participants": max_participants,
            "sid":              info.room_sid,
        })
        return info

    async def delete_room(self, room_name: str) -> None:
        self._rooms.pop(room_name, None)
        self.rooms_deleted.append(room_name)

    async def get_room(self, room_name: str) -> RoomInfo | None:
        return self._rooms.get(room_name)

    async def list_participants(self, room_name: str) -> list[ParticipantInfo]:
        # quem o teste pôs em `joined` está na sala (vazio por padrão, como antes)
        return [ParticipantInfo(identity=i, sid=f"PA_{i}", state="ACTIVE") for i in sorted(self.joined)]

    async def start_egress(self, room_name: str, filepath: str) -> str:
        if self.egress_start_error is not None:
            raise self.egress_start_error
        self._egress_counter += 1
        egress_id = f"EG_mock_{self._egress_counter:04d}"
        self.egresses_started.append({
            "room_name": room_name, "filepath": filepath, "egress_id": egress_id,
        })
        return egress_id

    async def stop_egress(self, egress_id: str) -> None:
        self.egresses_stopped.append(egress_id)

    async def wait_egress(self, egress_id: str, timeout_s: float) -> EgressResult:
        """O mock "grava" o que o teste pôs em `egress_bytes` (padrão: cabeçalho OGG) no
        caminho pedido — o resto do caminho (ler, guardar, apagar) roda de verdade."""
        inicio = next((e for e in self.egresses_started if e["egress_id"] == egress_id), None)
        if inicio is None:
            return EgressResult(egress_id, "DESCONHECIDO", error="egress nunca iniciado")
        if self.egress_end_error:
            return EgressResult(egress_id, "EGRESS_FAILED", error=self.egress_end_error)
        import pathlib
        p = pathlib.Path(inicio["filepath"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.egress_bytes)
        return EgressResult(egress_id, "EGRESS_COMPLETE", filename=str(p), duration_ms=1000,
                            size_bytes=len(self.egress_bytes))

    async def update_participant_permission(
        self,
        room_name:           str,
        identity:            str,
        can_publish_sources: tuple[str, ...],
        can_subscribe:       bool = True,
        can_publish_data:    bool = True,
    ) -> bool:
        """Registra a troca; `joined` controla se o participante 'está na sala'."""
        self.permission_updates.append({
            "room_name": room_name, "identity": identity,
            "can_publish_sources": tuple(can_publish_sources),
        })
        return identity in self.joined

    async def remove_participant(self, room_name: str, identity: str) -> bool:
        """Registra a remoção; `joined` controla se o participante 'está na sala'."""
        self.participants_removed.append((room_name, identity))
        presente = identity in self.joined
        self.joined.discard(identity)
        return presente


# ── Helpers ───────────────────────────────────────────────────────────────────


def build_room_name(session_id: str) -> str:
    """Canonical LiveKit room name for a PlugHub session."""
    return f"plughub-{session_id}"


# LÁPIDE — `negotiate_medium` e `MEDIUM_PRIORITY` saíram em 2026-09-14 (VOZ-09). Escolhiam
# UM meio (`video|voice|text`) para a sessão inteira a partir do `media_capabilities` do
# agente, campo sem produtor (sempre `[]` → sempre `text`). O substituto é o teto POR
# PARTICIPANTE de `media_policy.py`.
