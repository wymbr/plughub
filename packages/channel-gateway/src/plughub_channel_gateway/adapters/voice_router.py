"""
voice_router.py — a saída do canal `voice` vai para quem ABRIU a sessão (VOZ-02).

O canal `voice` significa *o cliente chegou por telefonia* (ADR `adr-voice-media-plane.md` V2), e
hoje ele tem duas pernas com donos diferentes:

  * a perna SIP (serviço SIP do SFU): o chamador está numa SALA, e quem tem a sala, o bot leg e a
    fala é o adapter WebRTC — a mídia é a mesma da chamada de browser (V3);
  * o legado Twilio (TwiML + Media Streams), que o ADR rebaixa a UM provedor entre outros.

O `OutboundConsumer` escolhe adapter pelo CANAL do payload, e os dois são `voice`. Sem este
roteador, a fala do agente de IA numa chamada SIP iria para o adapter Twilio — que não conhece a
sessão — e ninguém ouviria nada, sem erro. O discriminador é a SESSÃO, nunca um palpite: é do
SIP a sessão que o adapter WebRTC diz ter aberto; o resto segue para o legado como antes.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("plughub.channel-gateway.voice_router")


class VoiceChannelRouter:
    channel = "voice"

    def __init__(self, sip_owner: Any | None, legacy: Any) -> None:
        self._sip = sip_owner       # WebRTCAdapter (tem `is_sip_session`), ou None com o canal desligado
        self._legacy = legacy       # VoiceAdapter (Twilio)

    def _alvo(self, payload: dict) -> Any:
        sid = str(payload.get("session_id") or "")
        if self._sip is not None and sid and self._sip.is_sip_session(sid):
            return self._sip
        return self._legacy

    async def deliver_text(self, payload: dict) -> None:
        await self._alvo(payload).deliver_text(payload)

    async def deliver_menu(self, payload: dict) -> None:
        alvo = self._alvo(payload)
        if alvo is self._legacy and (payload.get("masked") or payload.get("masked_fields")):
            # NIV-07: `voice` declara `masked_input` pela perna SIP (pausa de mídia, tecla fora de
            # banda). O legado Twilio não tem controle nenhum disso — e nem renderiza menu (NIV-16):
            # entregar seria prometer uma coleta protegida que ele não faz. O menu sai pelo prazo.
            logger.error(
                "voice: menu MASCARADO %s RECUSADO na perna Twilio (sem pausa de midia nem garantia de "
                "tecla fora de banda — so a perna SIP coleta dado protegido) session=%s",
                payload.get("menu_id"), payload.get("session_id"),
            )
            return
        await alvo.deliver_menu(payload)

    async def deliver_typing(self, payload: dict) -> None:
        await self._alvo(payload).deliver_typing(payload)

    async def deliver_session_closed(self, payload: dict) -> None:
        await self._alvo(payload).deliver_session_closed(payload)

    def __getattr__(self, nome: str) -> Any:
        # Tudo que não é entrega de saída (ex.: `handle_collect_event`, procurado por `hasattr`) segue
        # sendo do legado, exatamente como antes deste roteador existir.
        return getattr(self._legacy, nome)
