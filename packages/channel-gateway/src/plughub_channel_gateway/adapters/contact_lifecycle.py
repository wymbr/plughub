"""
adapters/contact_lifecycle.py — o contrato de ciclo de vida de um contato por WebSocket,
numa casa só (VOZ-04).

POR QUE EXISTE
  O webchat e o webrtc são dois canais de cliente por WebSocket, e os dois precisam dizer à
  plataforma as MESMAS três coisas: "um contato abriu", "roteie-o para este pool" e "o contato
  fechou". O webchat dizia certo; o webrtc tinha a sua própria versão, escrita à mão, e ela não
  era aceita por ninguém — medido ao vivo em 2026-09-14:
    · o pedido de roteamento ia sem `started_at`, o `ConversationInboundEvent` do routing-engine
      o recusava ("Unrecognised inbound event") e o contato nunca era roteado;
    · o `contact_close` ia para `conversations.inbound`, onde nem o bridge nem o routing o
      reconhecem — o fim do contato não chegava a lugar nenhum;
    · a sessão gravava `session:{id}:meta` e nunca `ws_alive`, que é exatamente o que o watchdog
      do bridge lê como sessão ÓRFÃ e fecha.
  Contrato de payload mora ENTRE produtor e leitor; com dois produtores, a segunda cópia é a que
  diverge calada. Os dois adapters montam o que publicam por aqui.
"""
from __future__ import annotations

from typing import Any

# Motivo de TRANSPORTE (o `reason` do ContactClosedEvent, lido pelo bridge para decidir
# `customer_side`) → motivo de NEGÓCIO (domínio `close_reason` do CLAUDE.md). `agent_done`
# fica sem mapa de propósito: quem sabe se foi flow_complete ou agent_hangup é o bridge, e o
# evento dele vence no ClickHouse.
_BUSINESS_CLOSE_REASON: dict[str, str] = {
    "client_disconnect": "customer_disconnect",
    "timeout":           "session_timeout",
}


def routing_request(
    *,
    session_id:              str,
    tenant_id:               str,
    customer_id:             str,
    channel:                 str,
    pool_id:                 str,
    started_at:              str,
    customer_participant_id: str,
) -> dict[str, Any]:
    """
    O pedido de roteamento em `conversations.inbound`. Sem `type`: o routing-engine reconhece
    o evento pelo formato do `ConversationInboundEvent`, e `started_at` é obrigatório lá.
    """
    return {
        "session_id":              session_id,
        "tenant_id":               tenant_id,
        "customer_id":             customer_id,
        "channel":                 channel,
        "pool_id":                 pool_id,
        "started_at":              started_at,
        "elapsed_ms":              0,
        "customer_participant_id": customer_participant_id,
    }


def business_close_reason(transport_reason: str, *, customer_action: str | None = None) -> str | None:
    """
    `close_reason` de negócio para o ContactClosedEvent.

    `customer_action` é o que só o canal sabe: no webrtc o cliente DESLIGA a chamada
    (`customer_hangup`), fato que o transporte não distingue de uma queda.
    """
    if customer_action:
        return customer_action
    return _BUSINESS_CLOSE_REASON.get(transport_reason)


def ws_alive_ttl_s(ws_connection_timeout_s: float) -> int:
    """
    TTL de `session:{id}:ws_alive`: timeout de ociosidade + janela de ping + folga. O watchdog
    do bridge fecha a sessão quando a chave some — um TTL menor que a cadência de renovação
    fecharia contato vivo.
    """
    return int(float(ws_connection_timeout_s) + 120)
