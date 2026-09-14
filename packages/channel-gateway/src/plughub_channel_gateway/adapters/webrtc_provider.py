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

Egress (Phase D):
  start_egress / stop_egress falam com o serviço de egress do LiveKit, que NÃO está
  no compose (gravação é a VOZ-06). Contra o SFU atual a chamada falha alto.

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

    async def start_egress(
        self,
        room_name:    str,
        output_url:   str,           # s3://bucket/path or file:///path
        layout:       str = "speaker",   # "speaker" | "grid"
        dual_channel: bool = True,
    ) -> str:
        """
        Start a composite recording egress.
        Returns egress_id.  Phase D implementation.
        """
        ...

    async def stop_egress(self, egress_id: str) -> None:
        """Stop a running egress. Phase D implementation."""
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
                    can_publish       = grants.can_publish,
                    can_subscribe     = grants.can_subscribe,
                    can_publish_data  = grants.can_publish_data,
                    hidden            = grants.hidden,
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

    async def start_egress(
        self,
        room_name:    str,
        output_url:   str,
        layout:       str  = "speaker",
        dual_channel: bool = True,
    ) -> str:
        """
        Start a composite egress recording for *room_name*.

        output_url: local filesystem path on a volume shared between the egress
        worker and the Gateway, e.g. "/var/plughub/webrtc-recordings/{sid}/{seg}.mp4".

        Returns the LiveKit egress_id. Exceptions propagate so the caller logs and
        skips recording.

        ⚠️ O serviço de EGRESS não está no compose (VOZ-01 sobe só SFU + TURN; gravação
        é a VOZ-06). Contra o SFU atual esta chamada falha — e falha ALTO, que é o
        ponto: antes ela devolvia `EG_dev_…` e o chamador registrava gravação iniciada.
        """
        from livekit.api import (
            EncodedFileOutput,
            LiveKitAPI,
            RoomCompositeEgressRequest,
        )

        async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
            req = RoomCompositeEgressRequest(
                room_name   = room_name,
                layout      = layout,
                file_outputs = [EncodedFileOutput(filepath=output_url)],
            )
            egress_info = await lkapi.egress.start_room_composite_egress(req)
            egress_id   = egress_info.egress_id
            logger.info(
                "LiveKit egress started: room=%s egress_id=%s output=%s",
                room_name, egress_id, output_url,
            )
            return egress_id

    async def stop_egress(self, egress_id: str) -> None:
        """Stop a running LiveKit egress (cleanup path: failure is logged, not raised)."""
        from livekit.api import LiveKitAPI, StopEgressRequest

        try:
            async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
                await lkapi.egress.stop_egress(StopEgressRequest(egress_id=egress_id))
            logger.info("LiveKit egress stopped: egress_id=%s", egress_id)
        except Exception as exc:
            logger.warning("stop_egress failed (egress_id=%s): %s", egress_id, exc)


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
        return []

    async def start_egress(
        self,
        room_name:    str,
        output_url:   str,
        layout:       str  = "speaker",
        dual_channel: bool = True,
    ) -> str:
        self._egress_counter += 1
        egress_id = f"EG_mock_{self._egress_counter:04d}"
        self.egresses_started.append({
            "room_name":    room_name,
            "output_url":   output_url,
            "layout":       layout,
            "dual_channel": dual_channel,
            "egress_id":    egress_id,
        })
        return egress_id

    async def stop_egress(self, egress_id: str) -> None:
        self.egresses_stopped.append(egress_id)


# ── Helpers ───────────────────────────────────────────────────────────────────


def build_room_name(session_id: str) -> str:
    """Canonical LiveKit room name for a PlugHub session."""
    return f"plughub-{session_id}"


MEDIUM_PRIORITY: list[str] = ["video", "voice", "text"]


def negotiate_medium(
    agent_capabilities:  list[str],
    fallback_order:      list[str] | None = None,
) -> str:
    """
    Return the highest-tier medium the agent supports.

    Args:
        agent_capabilities: e.g. ["voice", "text"] from agent type config
        fallback_order: pool-level override, e.g. ["voice", "text"] for no-video pools

    Returns:
        "video" | "voice" | "text"  — always returns at least "text"
    """
    order = fallback_order or MEDIUM_PRIORITY
    for medium in order:
        if medium in agent_capabilities:
            return medium
    return "text"
