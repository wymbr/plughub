"""
adapters/webrtc_provider.py
Provider abstraction for the WebRTC channel (LiveKit SFU).

Architecture: docs/arcos/arc15-webrtc.md

One Protocol interface:
  IWebRTCProvider — room lifecycle + JWT token generation + egress recording

Concrete implementations:
  LiveKitProvider  — livekit-api SDK (livekit.api package)
  MockWebRTCProvider — in-memory stub for unit tests (no network I/O)

Token model:
  All LiveKit tokens are signed by Channel Gateway using LIVEKIT_API_SECRET.
  Tokens are NEVER returned to the browser directly — the browser receives a
  short-lived URL+token bundle served by /webrtc/token/{session_id}.

Egress (Phase D):
  start_egress / stop_egress stubs are present in Phase A so the interface
  is stable when Phase D wires the recording logic.

Adding a new SFU provider (mediasoup, Janus):
  1. Implement IWebRTCProvider Protocol
  2. Set PLUGHUB_WEBRTC_PROVIDER=mediasoup env var
  3. Register in WebRTCAdapter._build_provider()
"""

from __future__ import annotations

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


class LiveKitProvider:
    """
    LiveKit SFU integration via livekit-api Python SDK.

    Required package: pip install livekit-api
    (lighter than full 'livekit' SDK — no track pub/sub, server API only)

    Phase A: room CRUD + token generation
    Phase D: egress start/stop — IMPLEMENTADO. Em dev_mode (sem api_key/secret) devolve
             um egress_id mock (`EG_dev_…`) sem I/O de rede, e `stop_egress` é no-op.
             (Docstring corrigida em 2026-08-03: ainda anunciava *"stubs raise
             NotImplementedError until then"*, e havia dois testes cobrando essa
             promessa — o código andou, a doc ficou, e o teste ficou do lado da doc.)
    """

    def __init__(
        self,
        url:        str,
        api_key:    str,
        api_secret: str,
    ) -> None:
        self._url        = url
        self._api_key    = api_key
        self._api_secret = api_secret
        self._dev_mode   = not api_key or not api_secret

    def generate_token(self, grants: TokenGrants) -> str:
        """
        Generate a signed LiveKit JWT token.

        In dev mode (no api_key/secret), returns a placeholder token string
        so the adapter can be tested without a real LiveKit server.
        """
        if self._dev_mode:
            logger.debug("LiveKit dev mode: returning placeholder token")
            return f"dev-token-{grants.identity}-{grants.room_name}"

        try:
            from livekit.api import AccessToken, VideoGrants as LKVideoGrants
        except ImportError:
            logger.warning(
                "livekit-api not installed — pip install livekit-api. "
                "Returning placeholder token."
            )
            return f"missing-sdk-token-{grants.identity}"

        at = (
            AccessToken(self._api_key, self._api_secret)
            .with_identity(grants.identity)
            .with_name(grants.display_name or grants.identity)
            .with_ttl(grants.ttl_seconds)
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
        if self._dev_mode:
            logger.debug("LiveKit dev mode: mock create_room %s", room_name)
            return RoomInfo(
                room_name     = room_name,
                room_sid      = f"RM_dev_{room_name[:8]}",
                creation_time = int(time.time()),
            )
        try:
            from livekit.api import LiveKitAPI
            from livekit.api.room_service import CreateRoomRequest
        except ImportError:
            logger.warning("livekit-api not installed — returning mock room")
            return RoomInfo(room_name=room_name, room_sid=f"RM_mock_{uuid.uuid4().hex[:8]}")

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
        if self._dev_mode:
            logger.debug("LiveKit dev mode: mock delete_room %s", room_name)
            return
        try:
            from livekit.api import LiveKitAPI
            from livekit.api.room_service import DeleteRoomRequest
        except ImportError:
            return

        try:
            async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
                await lkapi.room.delete_room(DeleteRoomRequest(room=room_name))
        except Exception as exc:
            logger.warning("delete_room failed (%s): %s", room_name, exc)

    async def get_room(self, room_name: str) -> RoomInfo | None:
        if self._dev_mode:
            return None
        try:
            from livekit.api import LiveKitAPI
            from livekit.api.room_service import ListRoomsRequest
        except ImportError:
            return None

        try:
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
        except Exception as exc:
            logger.warning("get_room failed (%s): %s", room_name, exc)
            return None

    async def list_participants(self, room_name: str) -> list[ParticipantInfo]:
        if self._dev_mode:
            return []
        try:
            from livekit.api import LiveKitAPI
            from livekit.api.room_service import ListParticipantsRequest
        except ImportError:
            return []

        try:
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
        except Exception as exc:
            logger.warning("list_participants failed (%s): %s", room_name, exc)
            return []

    async def start_egress(
        self,
        room_name:    str,
        output_url:   str,
        layout:       str  = "speaker",
        dual_channel: bool = True,
    ) -> str:
        """
        Start a composite egress recording for *room_name*.

        output_url can be:
          - a local filesystem path (shared volume between LiveKit and Gateway):
              "/var/plughub/webrtc-recordings/session_id/segment_id.mp4"
          - an S3 URL handled by the caller (Phase D uses local file mode).

        Returns the LiveKit egress_id string.

        Fails gracefully:
          - dev_mode (no api_key/secret) → returns a mock egress_id, no network I/O.
          - ImportError (livekit-api not installed) → returns mock egress_id + warning.
          - All other exceptions propagate so the caller can log and skip recording.
        """
        if self._dev_mode:
            eid = f"EG_dev_{uuid.uuid4().hex[:8]}"
            logger.debug(
                "LiveKit dev mode: mock start_egress room=%s output=%s → %s",
                room_name, output_url, eid,
            )
            return eid

        try:
            from livekit.api import LiveKitAPI
            from livekit.api.egress_service import (  # type: ignore[import]
                StartRoomCompositeEgressRequest,
                EncodedFileOutput,
            )
        except ImportError:
            eid = f"EG_missing_sdk_{uuid.uuid4().hex[:8]}"
            logger.warning(
                "livekit-api not installed — returning mock egress_id. "
                "Install with: pip install livekit-api"
            )
            return eid

        async with LiveKitAPI(self._url, self._api_key, self._api_secret) as lkapi:
            file_output = EncodedFileOutput(filepath=output_url)
            req = StartRoomCompositeEgressRequest(
                room_name = room_name,
                layout    = layout,
                file      = file_output,
            )
            egress_info = await lkapi.egress.start_room_composite_egress(req)
            egress_id   = egress_info.egress_id
            logger.info(
                "LiveKit egress started: room=%s egress_id=%s output=%s",
                room_name, egress_id, output_url,
            )
            return egress_id

    async def stop_egress(self, egress_id: str) -> None:
        """
        Stop a running LiveKit egress and wait for it to flush its output file.

        Gracefully handles dev_mode and missing SDK — both are no-ops (the mock
        egress_id produced by start_egress in those modes is never sent to LiveKit).
        """
        if self._dev_mode:
            logger.debug("LiveKit dev mode: mock stop_egress egress_id=%s", egress_id)
            return

        try:
            from livekit.api import LiveKitAPI
            from livekit.api.egress_service import StopEgressRequest  # type: ignore[import]
        except ImportError:
            return

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
