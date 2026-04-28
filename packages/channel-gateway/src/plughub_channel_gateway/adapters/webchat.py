"""
adapters/webchat.py
WebSocket adapter for the web chat channel — hybrid stream model.
Spec: PlugHub v24.0 sections 3.5, 4.7

Protocol flow (new typed envelope):
  1. WS accept (no credentials in URL — safe for access logs)
  2. Server → client: conn.hello
  3. Client → server: conn.authenticate {token, cursor?}
  4. Server validates JWT → derives contact_id, session_id, tenant_id
  5. Server → client: conn.authenticated {contact_id, session_id, stream_cursor}
  6. Three concurrent tasks run until first exits:
       a. _receive_loop        — inbound messages from client
       b. _stream_delivery_loop— outbound events via XREAD on session stream
       c. _typing_listener     — ephemeral typing indicators via Redis pub/sub
  7. On first task exit → cancel others → _close(reason)

Upload lifecycle (within _receive_loop):
  client → upload.request  {id, file_name, mime_type, size_bytes}
  server → upload.ready    {request_id, file_id, upload_url}
  client → POST upload_url (binary, handled by upload_router.py)
  server → upload.committed{file_id, url, mime_type, size_bytes, content_type}
  client → msg.image|document|video {file_id, caption?}
  (adapter normalises → conversations.inbound)

Reconnect:
  Client passes cursor=<last_event_id> in conn.authenticate.
  StreamSubscriber resumes from that cursor — no missed events.

Backward compat:
  Legacy message types (message.text, pong) still accepted in _receive_loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import jwt as pyjwt
import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer
from fastapi import WebSocket, WebSocketDisconnect

from ..attachment_store import AttachmentStore, FilesystemAttachmentStore
from ..config import Settings
from ..context_reader import ContextReader
from ..models import (
    ContactClosedEvent,
    ContactOpenEvent,
    ContextSnapshot,
    MessageAuthor,
    MessageContent,
    NormalizedInboundEvent,
    WsAuthError,
    WsAuthenticate,
    WsAuthenticated,
    WsHello,
    WsMediaMessage,
    WsMenuSubmit,
    WsMessageText,
    WsPong,
    WsTypingStart,
    WsTypingStop,
    WsUploadReady,
    WsUploadRequest,
)
from ..session_registry import SessionRegistry
from ..stream_subscriber import StreamSubscriber, StreamExpiredError

logger = logging.getLogger("plughub.channel-gateway.webchat")

# JWT claims expected in customer tokens
_CLAIM_SUB        = "sub"           # contact_id
_CLAIM_SESSION    = "session_id"    # present on reconnect tokens
_CLAIM_TENANT     = "tenant_id"


class AuthError(Exception):
    """Raised during the auth handshake with a structured code."""
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code    = code
        self.message = message


class WebchatAdapter:
    """
    Handles a single WebSocket connection for the duration of a contact.
    One instance per active WebSocket connection.

    Args:
        ws:               Active FastAPI WebSocket connection.
        pool_id:          Service pool to route this contact to.
        producer:         Kafka producer for publishing normalised events.
        registry:         SessionRegistry for cross-instance delivery tracking.
        context_reader:   Reads per-session NLP context snapshot from Redis.
        settings:         Gateway settings.
        redis:            Async Redis client (for stream XREAD + pub/sub).
        attachment_store: Optional — enables file upload flow.  If None, upload.request
                          responses with an error.
        _token_validator: Optional override for JWT validation — used in tests to
                          bypass PyJWT.  Signature: (token: str) -> dict.
    """

    def __init__(
        self,
        *,
        ws:                WebSocket,
        pool_id:           str,
        producer:          AIOKafkaProducer,
        registry:          SessionRegistry,
        context_reader:    ContextReader,
        settings:          Settings,
        redis:             aioredis.Redis,
        attachment_store:  AttachmentStore | None = None,
        _token_validator:  Callable[[str], dict] | None = None,
    ) -> None:
        self._ws               = ws
        self._pool_id          = pool_id or settings.entry_point_pool_id
        self._producer         = producer
        self._registry         = registry
        self._context_reader   = context_reader
        self._settings         = settings
        self._redis            = redis
        self._attachment_store = attachment_store
        self._token_validator  = _token_validator

        # Set during _auth_handshake
        self._contact_id:     str = ""
        self._session_id:     str = ""
        self._initial_cursor: str = "0"
        self._started_at:     str = ""

        # Cache masked_fields per menu_id so _handle_menu_submit can redact values.
        # Populated when interaction.request with masked_fields is delivered to the client.
        self._pending_masked_fields: dict[str, list[str]] = {}

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def handle(self) -> None:
        """Entry point: full connection lifecycle from accept to close."""
        await self._ws.accept()

        # ── Auth handshake ─────────────────────────────────────────────────────
        try:
            await asyncio.wait_for(
                self._auth_handshake(),
                timeout=float(self._settings.ws_auth_timeout_s),
            )
        except asyncio.TimeoutError:
            logger.warning("auth_timeout — no conn.authenticate received")
            try:
                await self._ws.close(code=4001, reason="auth_timeout")
            except Exception:
                pass
            return
        except AuthError as exc:
            logger.warning("auth_error code=%s: %s", exc.code, exc.message)
            try:
                await self._ws.send_json(
                    WsAuthError(code=exc.code, message=exc.message).model_dump()
                )
                await self._ws.close(code=4003, reason=exc.code)
            except Exception:
                pass
            return

        # ── Session setup ──────────────────────────────────────────────────────
        tenant_id    = self._settings.tenant_id
        ttl          = self._settings.session_ttl_seconds
        self._started_at = datetime.now(timezone.utc).isoformat()

        await self._registry.register(self._contact_id, self._ws)

        await self._redis.setex(
            f"session:{self._session_id}:contact_id",
            ttl,
            self._contact_id,
        )
        await self._redis.setex(
            f"session:{self._session_id}:meta",
            ttl,
            json.dumps({
                "contact_id":  self._contact_id,
                "session_id":  self._session_id,
                "tenant_id":   tenant_id,
                "customer_id": self._contact_id,
                "channel":     "webchat",
                "pool_id":     self._pool_id,
                "started_at":  self._started_at,
            }),
        )

        await self._publish_event(
            ContactOpenEvent(
                contact_id=self._contact_id,
                session_id=self._session_id,
                started_at=self._started_at,
            ).model_dump()
        )
        logger.info(
            "contact_open contact_id=%s session_id=%s pool=%s cursor=%s",
            self._contact_id, self._session_id, self._pool_id, self._initial_cursor,
        )

        if self._pool_id:
            await self._publish_inbound({
                "session_id":  self._session_id,
                "tenant_id":   tenant_id,
                "customer_id": self._contact_id,
                "channel":     "webchat",
                "pool_id":     self._pool_id,
                "started_at":  self._started_at,
                "elapsed_ms":  0,
            })

        # ── Concurrent tasks ───────────────────────────────────────────────────
        receive_task  = asyncio.create_task(self._receive_loop(),         name="webchat_recv")
        delivery_task = asyncio.create_task(self._stream_delivery_loop(), name="webchat_deliver")
        typing_task   = asyncio.create_task(self._typing_listener(),      name="webchat_typing")

        close_reason = "client_disconnect"
        try:
            done, pending = await asyncio.wait(
                {receive_task, delivery_task, typing_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Infer close reason from the first finished task
            for task in done:
                if task.cancelled():
                    close_reason = "client_disconnect"
                elif task.exception() is not None:
                    exc = task.exception()
                    if isinstance(exc, asyncio.TimeoutError):
                        close_reason = "session_timeout"
                    elif isinstance(exc, WebSocketDisconnect):
                        close_reason = "client_disconnect"
                    else:
                        logger.error(
                            "unexpected error in task %s: %s",
                            task.get_name(), exc, exc_info=exc,
                        )
                        close_reason = "client_disconnect"
                break
        finally:
            for task in [receive_task, delivery_task, typing_task]:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

        await self._close(reason=close_reason)

    async def close_from_platform(self, reason: str = "agent_done") -> None:
        """Called externally (e.g. by OutboundConsumer) to close a session."""
        await self._close(reason=reason)
        try:
            await self._ws.close()
        except Exception:
            pass

    # ── Auth handshake ─────────────────────────────────────────────────────────

    async def _auth_handshake(self) -> None:
        """
        Sends conn.hello, waits for conn.authenticate, validates token,
        and sends conn.authenticated.  Sets self._contact_id / _session_id /
        _initial_cursor on success or raises AuthError.
        """
        await self._ws.send_json(WsHello().model_dump())

        raw = await self._ws.receive_text()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AuthError("bad_request", f"invalid JSON: {exc}") from exc

        if data.get("type") != "conn.authenticate":
            raise AuthError("bad_request", "expected conn.authenticate")

        auth_msg = WsAuthenticate.model_validate(data)

        try:
            claims = await self._decode_token(auth_msg.token)
        except pyjwt.ExpiredSignatureError:
            raise AuthError("token_expired", "token has expired")
        except pyjwt.InvalidTokenError as exc:
            raise AuthError("invalid_token", str(exc))

        contact_id = str(claims.get(_CLAIM_SUB) or "")
        if not contact_id:
            raise AuthError("invalid_token", "missing 'sub' claim")

        session_id = str(claims.get(_CLAIM_SESSION) or "") or str(uuid.uuid4())
        cursor     = auth_msg.cursor or "0"

        self._contact_id     = contact_id
        self._session_id     = session_id
        self._initial_cursor = cursor

        await self._ws.send_json(
            WsAuthenticated(
                contact_id    = contact_id,
                session_id    = session_id,
                stream_cursor = cursor,
            ).model_dump()
        )
        logger.debug(
            "conn.authenticated contact_id=%s session_id=%s cursor=%s",
            contact_id, session_id, cursor,
        )

    async def _decode_token(self, token: str) -> dict:
        """
        Decodes and validates the customer JWT.

        Multi-tenant secret resolution (fase 2):
          1. Decode without signature verification to read tenant_id from claims.
          2. Look up per-tenant secret from Redis key
             `{tenant_id}:config:webchat:jwt_secret` (written by config-api).
          3. Fall back to settings.jwt_secret if no per-tenant secret is configured.
          4. Re-decode with full HS256 verification using the resolved secret.

        This allows each tenant to rotate their JWT secret independently without
        redeploying the gateway.  Single-tenant deployments (no per-tenant Redis
        key) continue to use settings.jwt_secret with zero config changes.

        Override via _token_validator constructor param (used in tests).
        """
        if self._token_validator is not None:
            return self._token_validator(token)

        # Step 1 — read tenant_id from unverified payload
        try:
            unverified = pyjwt.decode(
                token,
                options={"verify_signature": False},
                algorithms=["HS256"],
            )
        except pyjwt.DecodeError as exc:
            raise pyjwt.InvalidTokenError(f"malformed token: {exc}") from exc

        tenant_id = str(unverified.get(_CLAIM_TENANT) or "")

        # Step 2 — resolve per-tenant secret (async Redis lookup, ~0.5ms)
        secret = await self._resolve_jwt_secret(tenant_id)

        # Step 3 — validate algorithm header explicitly before full verification
        try:
            header = pyjwt.get_unverified_header(token)
        except pyjwt.DecodeError as exc:
            raise pyjwt.InvalidTokenError(f"cannot read token header: {exc}") from exc

        if header.get("alg") != "HS256":
            raise pyjwt.InvalidTokenError(
                f"unsupported algorithm: {header.get('alg')!r} — only HS256 is accepted"
            )

        # Step 4 — full verification
        return pyjwt.decode(token, secret, algorithms=["HS256"])

    async def _resolve_jwt_secret(self, tenant_id: str) -> str:
        """
        Returns the JWT secret for the given tenant.
        Checks Redis key `{tenant_id}:config:webchat:jwt_secret` first;
        falls back to the instance-level settings.jwt_secret.
        """
        if not tenant_id:
            return self._settings.jwt_secret
        try:
            per_tenant = await self._redis.get(
                f"{tenant_id}:config:webchat:jwt_secret"
            )
            if per_tenant:
                return per_tenant if isinstance(per_tenant, str) else per_tenant.decode()
        except Exception:
            pass  # Redis unavailable — fall back to instance secret
        return self._settings.jwt_secret

    # ── Inbound receive loop ───────────────────────────────────────────────────

    async def _receive_loop(self) -> None:
        """
        Reads inbound WebSocket messages and dispatches them.
        Raises WebSocketDisconnect on client disconnect.
        Raises asyncio.TimeoutError if a heartbeat ping goes unanswered.
        """
        timeout = float(self._settings.ws_connection_timeout_s)
        while True:
            try:
                raw = await asyncio.wait_for(
                    self._ws.receive_text(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                # No activity — send a ping to check liveness
                try:
                    await self._ws.send_json({"type": "conn.ping"})
                    raw = await asyncio.wait_for(
                        self._ws.receive_text(),
                        timeout=10.0,
                    )
                except asyncio.TimeoutError:
                    raise  # propagate → session_timeout

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("invalid JSON from contact_id=%s", self._contact_id)
                continue

            msg_type = data.get("type", "")

            if msg_type in ("msg.text", "message.text"):       # new + legacy
                await self._handle_text(data)
            elif msg_type in ("msg.image", "msg.document", "msg.video"):
                await self._handle_media(data)
            elif msg_type == "upload.request":
                await self._handle_upload_request(data)
            elif msg_type == "menu.submit":
                await self._handle_menu_submit(WsMenuSubmit.model_validate(data))
            elif msg_type == "conn.ping":
                await self._ws.send_json(WsPong().model_dump())
            elif msg_type in ("conn.pong", "pong"):
                pass  # heartbeat response — no-op
            else:
                logger.warning(
                    "unknown message type=%s contact_id=%s", msg_type, self._contact_id
                )

    # ── Outbound stream delivery ───────────────────────────────────────────────

    async def _stream_delivery_loop(self) -> None:
        """
        Subscribes to the session's canonical Redis Stream and delivers events
        to the client via WebSocket.  Cursor-based — survives reconnects without
        missing events.

        StreamExpiredError: raised by StreamSubscriber when the client reconnects
        with a cursor but the stream no longer exists (session ended, TTL expired).
        In this case the client is notified with conn.session_ended and the loop
        returns normally so the connection can be closed cleanly.
        """
        subscriber = StreamSubscriber(
            redis      = self._redis,
            session_id = self._session_id,
            cursor     = self._initial_cursor,
        )
        try:
            async for msg in subscriber.messages():
                # Cache masked_fields so _handle_menu_submit can redact sensitive values
                # before they are stored in the conversation history visible to agents.
                if (
                    msg.get("type") == "interaction.request"
                    and msg.get("masked_fields")
                    and msg.get("menu_id")
                ):
                    self._pending_masked_fields[msg["menu_id"]] = list(msg["masked_fields"])

                try:
                    await self._ws.send_json(msg)
                except Exception:
                    # WebSocket closed — stop delivery
                    return
        except StreamExpiredError:
            logger.info(
                "stream expired session_id=%s cursor=%s — sending session_ended",
                self._session_id, self._initial_cursor,
            )
            try:
                await self._ws.send_json({
                    "type":   "conn.session_ended",
                    "reason": "session_expired",
                })
            except Exception:
                pass

    # ── Typing indicators ──────────────────────────────────────────────────────

    async def _typing_listener(self) -> None:
        """
        Subscribes to the session's ephemeral typing pub/sub channel and
        forwards typing start/stop notifications to the client.

        Channel key: session:{session_id}:typing
        Expected message payload: {"type": "typing_start"|"typing_stop",
                                    "participant_id": "...", "role": "..."}
        """
        channel = f"session:{self._session_id}:typing"
        pubsub  = self._redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                try:
                    payload = json.loads(msg["data"])
                except (json.JSONDecodeError, TypeError):
                    continue
                typing_type = payload.get("type")
                try:
                    if typing_type == "typing_start":
                        await self._ws.send_json(
                            WsTypingStart(
                                participant_id = payload.get("participant_id", ""),
                                role           = payload.get("role", "primary"),
                            ).model_dump()
                        )
                    elif typing_type == "typing_stop":
                        await self._ws.send_json(
                            WsTypingStop(
                                participant_id = payload.get("participant_id", ""),
                            ).model_dump()
                        )
                except Exception:
                    return  # WS closed
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass

    # ── Inbound handlers ───────────────────────────────────────────────────────

    async def _handle_text(self, data: dict) -> None:
        """Normalises a text message and publishes to conversations.inbound."""
        text     = data.get("text", "")
        msg_id   = data.get("id") or str(uuid.uuid4())
        snapshot = await self._context_reader.get_snapshot(self._session_id)
        event    = NormalizedInboundEvent(
            message_id       = msg_id,
            contact_id       = self._contact_id,
            session_id       = self._session_id,
            author           = MessageAuthor(type="customer"),
            content          = MessageContent(type="text", text=text),
            context_snapshot = snapshot,
        )
        await self._registry.append_message(
            session_id = self._session_id,
            message_id = event.message_id,
            author     = "customer",
            text       = text,
            timestamp  = event.timestamp,
        )
        await self._publish_inbound(event.model_dump())
        logger.info(
            "inbound text contact_id=%s session_id=%s turn=%d",
            self._contact_id, self._session_id, snapshot.turn_number,
        )

    async def _handle_media(self, data: dict) -> None:
        """
        Validates that the referenced file_id is committed and publishes a
        media inbound event to conversations.inbound.
        """
        file_id  = data.get("file_id", "")
        msg_type = data.get("type", "")

        # Map WebSocket message type → media_type string
        content_type_map = {
            "msg.image":    "image",
            "msg.document": "document",
            "msg.video":    "video",
        }
        media_type = content_type_map.get(msg_type, "document")

        # Validate that the file was committed (if store is wired)
        if self._attachment_store is not None:
            meta = await self._attachment_store.resolve(
                file_id   = file_id,
                tenant_id = self._settings.tenant_id,
            )
            if meta is None:
                logger.warning(
                    "media msg references unknown file_id=%s contact_id=%s",
                    file_id, self._contact_id,
                )
                return
            if meta.deleted_at is not None:
                logger.warning(
                    "media msg references expired file_id=%s contact_id=%s",
                    file_id, self._contact_id,
                )
                return

        snapshot = await self._context_reader.get_snapshot(self._session_id)
        event    = NormalizedInboundEvent(
            contact_id       = self._contact_id,
            session_id       = self._session_id,
            author           = MessageAuthor(type="customer"),
            content          = MessageContent(
                type    = "media",
                payload = {
                    "media_type": media_type,
                    "file_id":    file_id,
                    "caption":    data.get("caption"),
                },
            ),
            context_snapshot = snapshot,
        )
        await self._publish_inbound(event.model_dump())
        logger.info(
            "inbound media type=%s file_id=%s contact_id=%s",
            media_type, file_id, self._contact_id,
        )

    async def _handle_upload_request(self, data: dict) -> None:
        """
        Reserves an upload slot in the AttachmentStore and replies with
        upload.ready so the client can POST the binary.
        """
        if self._attachment_store is None:
            await self._ws.send_json(
                WsAuthError(
                    code    = "upload_not_supported",
                    message = "file upload is not enabled on this gateway",
                ).model_dump()
            )
            return

        try:
            req = WsUploadRequest.model_validate(data)
        except Exception as exc:
            await self._ws.send_json(
                WsAuthError(code="bad_request", message=str(exc)).model_dump()
            )
            return

        err = FilesystemAttachmentStore.validate_mime(req.mime_type, req.size_bytes)
        if err is not None:
            await self._ws.send_json(
                WsAuthError(code="upload_rejected", message=err).model_dump()
            )
            return

        expires_at = datetime.now(timezone.utc) + timedelta(
            days=self._settings.attachment_expiry_days
        )
        try:
            file_id, upload_url = await self._attachment_store.reserve(
                tenant_id  = self._settings.tenant_id,
                session_id = self._session_id,
                file_name  = req.file_name,
                mime_type  = req.mime_type,
                size_bytes = req.size_bytes,
                expires_at = expires_at,
            )
        except Exception as exc:
            logger.error("attachment reserve failed: %s", exc)
            await self._ws.send_json(
                WsAuthError(code="upload_error", message="could not reserve upload slot").model_dump()
            )
            return

        await self._ws.send_json(
            WsUploadReady(
                request_id = req.id,
                file_id    = file_id,
                upload_url = upload_url,
            ).model_dump()
        )
        logger.debug(
            "upload.ready file_id=%s contact_id=%s", file_id, self._contact_id
        )

    async def _handle_menu_submit(self, msg: WsMenuSubmit) -> None:
        """Normalises a menu/form submission and publishes to conversations.inbound."""
        snapshot = await self._context_reader.get_snapshot(self._session_id)
        event    = NormalizedInboundEvent(
            contact_id       = self._contact_id,
            session_id       = self._session_id,
            author           = MessageAuthor(type="customer"),
            content          = MessageContent(
                type    = "menu_result",
                payload = {
                    "menu_id":     msg.menu_id,
                    "interaction": msg.interaction,
                    "result":      msg.result,
                },
            ),
            context_snapshot = snapshot,
        )

        # Build the agent-visible summary of the form submission.
        # Masked fields (senha, PIN, OTP, etc.) must NEVER appear in the session stream
        # or conversation history visible to agents — replace with "••••••".
        #
        # Primary source: SessionRegistry._menu_masked_fields, populated by
        # OutboundConsumer when the menu.payload arrived from Kafka (correct path).
        # Fallback: self._pending_masked_fields, populated by _stream_delivery_loop
        # (legacy / edge-case path for menus that reach clients via stream).
        masked_fields = (
            self._registry.pop_menu_masked_fields(self._contact_id, msg.menu_id)
            or self._pending_masked_fields.get(msg.menu_id, [])
        )
        masked_set    = set(masked_fields)

        if msg.interaction == "form" and masked_set:
            # Form interaction: redact individual masked fields, keep the rest visible.
            try:
                result_dict: dict = (
                    json.loads(msg.result)
                    if isinstance(msg.result, str)
                    else dict(msg.result) if isinstance(msg.result, dict) else {}
                )
            except (json.JSONDecodeError, TypeError):
                result_dict = {}

            redacted = {
                k: ("••••••" if k in masked_set else v)
                for k, v in result_dict.items()
            }
            agent_label   = "Formulário"
            agent_summary = json.dumps(redacted, ensure_ascii=False)

        elif masked_set:
            # Non-form masked interaction (text, button, list with masked:true).
            # The entire result is sensitive — replace with placeholder.
            # Uses the implicit field id (output_as or step.id) as label hint.
            field_hint    = next(iter(masked_set), "entrada")
            agent_label   = f"Entrada mascarada ({field_hint})"
            agent_summary = "••••••"

        else:
            agent_label   = "Resposta"
            agent_summary = (
                msg.result
                if isinstance(msg.result, str)
                else json.dumps(msg.result, ensure_ascii=False)
            )

        await self._registry.append_message(
            session_id = self._session_id,
            message_id = event.message_id,
            author     = "customer",
            text       = f"[{agent_label}: {agent_summary}]",
            timestamp  = event.timestamp,
        )
        await self._publish_inbound(event.model_dump())

        # Evict fallback cache entry (if any) — pop_menu_masked_fields already
        # cleared the primary SessionRegistry entry above.
        self._pending_masked_fields.pop(msg.menu_id, None)

        logger.debug(
            "menu_submit interaction=%s masked=%s contact_id=%s",
            msg.interaction, list(masked_set), self._contact_id,
        )

    # ── Close ──────────────────────────────────────────────────────────────────

    async def _close(self, reason: str) -> None:
        started_at = await self._registry.unregister(self._contact_id)
        await self._publish_event(
            ContactClosedEvent(
                contact_id = self._contact_id,
                session_id = self._session_id,
                reason     = reason,  # type: ignore[arg-type]
                started_at = started_at or self._started_at,
            ).model_dump()
        )
        logger.info("contact_closed contact_id=%s reason=%s", self._contact_id, reason)

    # ── Kafka helpers ──────────────────────────────────────────────────────────

    async def _publish_inbound(self, payload: dict) -> None:
        await self._producer.send(
            self._settings.kafka_topic_inbound,
            value=json.dumps(payload).encode(),
        )

    async def _publish_event(self, payload: dict) -> None:
        await self._producer.send(
            self._settings.kafka_topic_events,
            value=json.dumps(payload).encode(),
        )
