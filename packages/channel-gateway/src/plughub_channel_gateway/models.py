"""
models.py
Channel Gateway data models.
Spec: PlugHub v24.0 sections 3.5, 4.7m

Evolução (modelo híbrido):
  - Novos tipos de mensagem: media (image, document, video), upload lifecycle,
    auth flow (conn.*), presença (presence.*)
  - Tipos legados mantidos para backward compat com OutboundConsumer
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field
import uuid


# ══════════════════════════════════════════════════════════════════════════════
# WebSocket — cliente → servidor
# ══════════════════════════════════════════════════════════════════════════════

class WsAuthenticate(BaseModel):
    """Primeira mensagem do cliente após conn.hello."""
    type:   Literal["conn.authenticate"]
    token:  str
    # ID do último evento do stream recebido (para reconnect sem perda).
    # Omitido em conexões novas → servidor usa cursor "0" (início da sessão).
    cursor: str | None = None


class WsMessageText(BaseModel):
    """Mensagem de texto simples do cliente."""
    type: Literal["msg.text"]
    id:   str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str


class WsUploadRequest(BaseModel):
    """Cliente solicita slot de upload antes de enviar o arquivo via HTTP."""
    type:       Literal["upload.request"]
    id:         str = Field(default_factory=lambda: str(uuid.uuid4()))
    file_name:  str
    mime_type:  str
    size_bytes: int


class WsMediaMessage(BaseModel):
    """Mensagem de mídia após upload.committed — referencia o file_id."""
    type:    Literal["msg.image", "msg.document", "msg.video"]
    id:      str = Field(default_factory=lambda: str(uuid.uuid4()))
    file_id: str
    caption: str | None = None


class WsMenuSubmit(BaseModel):
    """Cliente submete resultado de interação (menu/carousel/formulário)."""
    type:        Literal["menu.submit"]
    menu_id:     str
    interaction: Literal["text", "button", "list", "checklist", "form"]
    result:      str | list[str] | dict[str, Any]


class WsPing(BaseModel):
    type: Literal["conn.ping"]


# ── Legado (mantido para backward compat) ─────────────────────────────────────
class WsMessageTextLegacy(BaseModel):
    """Formato legado — type='message.text' (antes do envelope tipado)."""
    type: Literal["message.text"]
    text: str


# ══════════════════════════════════════════════════════════════════════════════
# WebSocket — servidor → cliente
# ══════════════════════════════════════════════════════════════════════════════

class WsHello(BaseModel):
    """Enviado pelo servidor imediatamente após aceitar a conexão WebSocket."""
    type:           Literal["conn.hello"] = "conn.hello"
    server_version: str                   = "1.0"


class WsAuthenticated(BaseModel):
    """Confirmação de autenticação bem-sucedida."""
    type:       Literal["conn.authenticated"] = "conn.authenticated"
    contact_id: str
    session_id: str
    # Cursor atual do stream — cliente usa para reconnect
    stream_cursor: str = "0"


class WsAuthError(BaseModel):
    """Falha de autenticação."""
    type:    Literal["conn.error"] = "conn.error"
    code:    str
    message: str


class WsUploadReady(BaseModel):
    """Slot de upload reservado — cliente pode fazer POST no upload_url."""
    type:            Literal["upload.ready"] = "upload.ready"
    request_id:      str    # id da WsUploadRequest correspondente
    file_id:         str
    upload_url:      str
    expires_in_secs: int = 300


class WsUploadCommitted(BaseModel):
    """Upload processado e salvo — cliente pode enviar a mensagem de mídia."""
    type:          Literal["upload.committed"] = "upload.committed"
    file_id:       str
    url:           str
    mime_type:     str
    size_bytes:    int
    content_type:  Literal["image", "document", "video"]  # para o cliente saber qual msg.* enviar


class WsPong(BaseModel):
    type: Literal["conn.pong"] = "conn.pong"


class WsSessionEnded(BaseModel):
    type:   Literal["conn.session_ended"] = "conn.session_ended"
    reason: str


class WsTypingStart(BaseModel):
    type:           Literal["presence.typing_start"] = "presence.typing_start"
    participant_id: str
    role:           str


class WsTypingStop(BaseModel):
    type:           Literal["presence.typing_stop"] = "presence.typing_stop"
    participant_id: str


# ── Legado (mantido para backward compat com OutboundConsumer) ────────────────

class WsConnectionAccepted(BaseModel):
    type:       Literal["connection.accepted"] = "connection.accepted"
    contact_id: str
    session_id: str


class WsMessageOutbound(BaseModel):
    # type "msg.text" — matches webchat client case 'msg.text'
    type:       Literal["msg.text"] = "msg.text"
    message_id: str
    author:     dict[str, Any]
    text:       str
    timestamp:  str


class WsMenuRender(BaseModel):
    # type "interaction.request" — matches webchat client case 'interaction.request'
    type:        Literal["interaction.request"] = "interaction.request"
    menu_id:     str
    interaction: Literal["text", "button", "list", "checklist", "form"]
    prompt:      str
    options:     list[dict[str, str]] | None = None
    fields:      list[dict[str, Any]] | None = None


class WsAgentTyping(BaseModel):
    type:        Literal["agent.typing"] = "agent.typing"
    author_type: str


class WsSessionClosed(BaseModel):
    # type "conn.session_ended" — matches webchat client case 'conn.session_ended'
    type:   Literal["conn.session_ended"] = "conn.session_ended"
    reason: str


# ── Platform — normalised inbound event (published to conversations.inbound) ─

class ContextSnapshot(BaseModel):
    intent: str | None = None
    sentiment_score: float | None = None
    turn_number: int = 0


class MessageAuthor(BaseModel):
    type: Literal["customer", "agent_human", "agent_ai", "system"]
    id: str | None = None
    display_name: str | None = None


class MessageContent(BaseModel):
    type: Literal["text", "menu_result", "system_event", "media"]
    text: str | None = None
    payload: dict[str, Any] | None = None


class NormalizedInboundEvent(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    contact_id: str
    session_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    direction: Literal["inbound"] = "inbound"
    channel: Literal["webchat"] = "webchat"
    author: MessageAuthor
    content: MessageContent
    context_snapshot: ContextSnapshot = Field(default_factory=ContextSnapshot)


# ── Platform — contact lifecycle events (conversations.events) ─────────────

class ContactOpenEvent(BaseModel):
    event_type: Literal["contact_open"] = "contact_open"
    contact_id: str
    session_id: str
    channel: Literal["webchat"] = "webchat"
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ContactClosedEvent(BaseModel):
    event_type: Literal["contact_closed"] = "contact_closed"
    contact_id: str
    session_id: str
    channel: Literal["webchat"] = "webchat"
    reason: Literal["agent_done", "client_disconnect", "timeout"]
    started_at: str
    ended_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ── Platform — outbound event (consumed from conversations.outbound) ────────

class OutboundTextMessage(BaseModel):
    type: Literal["message.text"]
    contact_id: str
    session_id: str
    message_id: str
    channel: str
    author: dict[str, Any]
    text: str
    timestamp: str


class MenuPayload(BaseModel):
    type: Literal["menu.payload"]
    contact_id: str
    session_id: str
    menu_id: str
    channel: str
    interaction: Literal["text", "button", "list", "checklist", "form"]
    prompt: str
    options: list[dict[str, str]] | None = None
    fields: list[dict[str, Any]] | None = None


class AgentTypingPayload(BaseModel):
    type: Literal["agent.typing"]
    contact_id: str
    session_id: str
    channel: str
    author_type: str


class SessionClosedPayload(BaseModel):
    type: Literal["session.closed"]
    contact_id: str
    session_id: str
    channel: str
    reason: str
