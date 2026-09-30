"""
voice_router.py — a saída do canal `voice` vai para a perna SIP, e só para ela (VOZ-02/VOZ-03).

O canal `voice` significa *o cliente chegou por telefonia* (ADR `adr-voice-media-plane.md` V2). Ele
tem UMA perna: o serviço SIP do SFU põe o chamador numa SALA, e quem tem a sala, o bot leg e a fala
é o adapter WebRTC — a mídia é a mesma da chamada de browser (V3).

Até 2026-09-28 havia uma segunda, o legado Twilio (TwiML + Media Streams), e este roteador escolhia
entre as duas pela SESSÃO. A VOZ-03 aposentou o legado: o Twilio continua só como operadora do
tronco SIP. Sobrou o que o roteador tinha de não óbvio — o `OutboundConsumer` escolhe adapter pelo
CANAL do payload, e o adapter WebRTC também atende `webrtc`; aqui a saída de `voice` chega a ele.

O que NÃO é sessão SIP não tem destino e é DITO, nunca entregue a um palpite:
  * texto, menu e digitação → WARNING nomeando a sessão (conteúdo perdido);
  * `session_closed` → INFO: é o caso normal de a chamada já ter desligado antes do aviso chegar.

Coleta ativa pela perna SIP (ligação SAINTE) é a VOZ-33. O consumidor de `collect.events`,
que recusava `voice` nomeando, saiu com o tópico na WFL-02 (2026-09-30).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("plughub.channel-gateway.voice_router")


class VoiceChannelRouter:
    channel = "voice"

    def __init__(self, sip_owner: Any | None) -> None:
        self._sip = sip_owner       # WebRTCAdapter (tem `is_sip_session`), ou None com o canal desligado

    def _alvo(self, payload: dict, o_que: str) -> Any | None:
        sid = str(payload.get("session_id") or "")
        if self._sip is not None and sid and self._sip.is_sip_session(sid):
            return self._sip
        motivo = "canal WebRTC desligado" if self._sip is None else "sessao nao e da perna SIP"
        if o_que == "session_closed":
            logger.info("voice: session_closed sem chamada SIP viva (%s) session=%s", motivo, sid)
        else:
            logger.warning(
                "voice: %s NAO entregue — %s; o legado Twilio foi aposentado (VOZ-03) session=%s",
                o_que, motivo, sid,
            )
        return None

    async def deliver_text(self, payload: dict) -> None:
        alvo = self._alvo(payload, "texto")
        if alvo is not None:
            await alvo.deliver_text(payload)

    async def deliver_menu(self, payload: dict) -> None:
        alvo = self._alvo(payload, "menu")
        if alvo is not None:
            await alvo.deliver_menu(payload)

    async def deliver_typing(self, payload: dict) -> None:
        alvo = self._alvo(payload, "digitacao")
        if alvo is not None:
            await alvo.deliver_typing(payload)

    async def deliver_session_closed(self, payload: dict) -> None:
        alvo = self._alvo(payload, "session_closed")
        if alvo is not None:
            await alvo.deliver_session_closed(payload)
