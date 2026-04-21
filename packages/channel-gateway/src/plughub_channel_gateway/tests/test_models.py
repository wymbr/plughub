"""
test_models.py
Unit tests for Channel Gateway Pydantic models.
Verifies serialization round-trips and field defaults.
"""

from __future__ import annotations
import json
import pytest

from plughub_channel_gateway.models import (
    WsMessageText, WsMessageTextLegacy, WsMenuSubmit,
    WsConnectionAccepted, WsMessageOutbound, WsMenuRender,
    WsAgentTyping, WsSessionClosed,
    ContextSnapshot, MessageAuthor, MessageContent,
    NormalizedInboundEvent, ContactOpenEvent, ContactClosedEvent,
)


# ── Client → server ───────────────────────────────────────────────────────────

class TestClientMessages:
    def test_ws_message_text_valid(self):
        """New msg.text typed envelope."""
        raw = {"type": "msg.text", "text": "Olá, quero cancelar meu plano"}
        msg = WsMessageText.model_validate(raw)
        assert msg.type == "msg.text"
        assert msg.text == "Olá, quero cancelar meu plano"

    def test_ws_message_text_serializes(self):
        msg = WsMessageText(type="msg.text", text="teste")
        d = msg.model_dump()
        assert d["type"] == "msg.text"
        assert d["text"] == "teste"

    def test_ws_message_text_legacy_accepted(self):
        """Legacy message.text still parses via WsMessageTextLegacy."""
        raw = {"type": "message.text", "text": "legacy format"}
        msg = WsMessageTextLegacy.model_validate(raw)
        assert msg.type == "message.text"
        assert msg.text == "legacy format"

    def test_ws_menu_submit_button(self):
        raw = {
            "type": "menu.submit",
            "menu_id": "menu_abc",
            "interaction": "button",
            "result": "opt_1",
        }
        msg = WsMenuSubmit.model_validate(raw)
        assert msg.interaction == "button"
        assert msg.result == "opt_1"

    def test_ws_menu_submit_checklist(self):
        raw = {
            "type": "menu.submit",
            "menu_id": "menu_xyz",
            "interaction": "checklist",
            "result": ["opt_a", "opt_b"],
        }
        msg = WsMenuSubmit.model_validate(raw)
        assert isinstance(msg.result, list)
        assert len(msg.result) == 2

    def test_ws_menu_submit_form(self):
        raw = {
            "type": "menu.submit",
            "menu_id": "menu_form",
            "interaction": "form",
            "result": {"name": "João", "cpf": "000.000.000-00"},
        }
        msg = WsMenuSubmit.model_validate(raw)
        assert isinstance(msg.result, dict)
        assert msg.result["name"] == "João"


# ── Server → client ───────────────────────────────────────────────────────────

class TestServerMessages:
    def test_connection_accepted_defaults(self):
        msg = WsConnectionAccepted(contact_id="c1", session_id="s1")
        assert msg.type == "connection.accepted"
        d = msg.model_dump()
        assert d["type"] == "connection.accepted"
        assert d["contact_id"] == "c1"

    def test_ws_message_outbound(self):
        msg = WsMessageOutbound(
            message_id="m1",
            author={"type": "agent_ai"},
            text="Posso te ajudar?",
            timestamp="2024-01-01T10:00:00Z",
        )
        assert msg.type == "message.text"
        d = msg.model_dump()
        assert d["author"]["type"] == "agent_ai"

    def test_ws_menu_render_with_options(self):
        msg = WsMenuRender(
            menu_id="m1",
            interaction="button",
            prompt="Escolha uma opção:",
            options=[{"id": "opt_1", "label": "Sim"}, {"id": "opt_2", "label": "Não"}],
        )
        assert msg.type == "menu.render"
        assert len(msg.options) == 2

    def test_ws_menu_render_no_options(self):
        msg = WsMenuRender(
            menu_id="m1",
            interaction="text",
            prompt="Digite sua mensagem:",
        )
        assert msg.options is None
        assert msg.fields is None

    def test_ws_agent_typing(self):
        msg = WsAgentTyping(author_type="agent_ai")
        assert msg.type == "agent.typing"

    def test_ws_session_closed(self):
        msg = WsSessionClosed(reason="agent_done")
        assert msg.type == "session.closed"
        assert msg.reason == "agent_done"


# ── NormalizedInboundEvent ────────────────────────────────────────────────────

class TestNormalizedInboundEvent:
    def test_defaults_populated(self):
        event = NormalizedInboundEvent(
            contact_id="c1",
            session_id="s1",
            author=MessageAuthor(type="customer"),
            content=MessageContent(type="text", text="Oi"),
        )
        assert event.direction == "inbound"
        assert event.channel == "webchat"
        assert event.message_id  # uuid generated
        assert event.timestamp   # iso timestamp generated
        assert event.context_snapshot.turn_number == 0

    def test_serializes_to_dict(self):
        event = NormalizedInboundEvent(
            contact_id="c1",
            session_id="s1",
            author=MessageAuthor(type="customer"),
            content=MessageContent(type="text", text="Oi"),
        )
        d = event.model_dump()
        assert d["channel"] == "webchat"
        assert d["author"]["type"] == "customer"
        assert d["content"]["type"] == "text"

    def test_menu_result_content(self):
        event = NormalizedInboundEvent(
            contact_id="c1",
            session_id="s1",
            author=MessageAuthor(type="customer"),
            content=MessageContent(
                type="menu_result",
                payload={"menu_id": "m1", "interaction": "button", "result": "opt_1"},
            ),
        )
        assert event.content.type == "menu_result"
        assert event.content.payload["result"] == "opt_1"

    def test_context_snapshot_with_data(self):
        snap = ContextSnapshot(intent="portability_check", sentiment_score=0.8, turn_number=3)
        event = NormalizedInboundEvent(
            contact_id="c1",
            session_id="s1",
            author=MessageAuthor(type="customer"),
            content=MessageContent(type="text", text="test"),
            context_snapshot=snap,
        )
        assert event.context_snapshot.intent == "portability_check"
        assert event.context_snapshot.turn_number == 3


# ── Contact lifecycle events ──────────────────────────────────────────────────

class TestLifecycleEvents:
    def test_contact_open_event(self):
        ev = ContactOpenEvent(contact_id="c1", session_id="s1", started_at="2024-01-01T10:00:00Z")
        assert ev.event_type == "contact_open"
        assert ev.channel == "webchat"

    def test_contact_closed_event(self):
        ev = ContactClosedEvent(
            contact_id="c1",
            session_id="s1",
            reason="client_disconnect",
            started_at="2024-01-01T10:00:00Z",
        )
        assert ev.event_type == "contact_closed"
        assert ev.reason == "client_disconnect"
        assert ev.ended_at  # auto-generated

    def test_contact_closed_invalid_reason(self):
        with pytest.raises(Exception):
            ContactClosedEvent(
                contact_id="c1",
                session_id="s1",
                reason="invalid_reason",
                started_at="2024-01-01T10:00:00Z",
            )
