"""
menu_wake.py — avisa o bridge de que o agente de fila recebeu um sinal (DUR-01 F3).

O routing-engine sinaliza o agente de fila com `LPUSH menu:result:{sid}` (`__agent_available__`
quando um humano libera, `__queue_timeout__` quando o teto de espera vence). Com o pool de
fila em `menu_wait: park`, ninguém está bloqueado naquela lista: a conversa está ESTACIONADA
e só o bridge a acorda. Este aviso vai no tópico `menu.wake` (schema `MenuWakeEventSchema`,
`@plughub/schemas`), SEMPRE depois do `LPUSH`, com a sessão como chave de partição.

O agente de fila roda sem instância, então o campo do `menu:waiting` é `_default_`.
Falhar aqui não perde o sinal (ele está na lista), mas a conversa fica estacionada até a
próxima coisa que a acorde — por isso a falha é LOGADA com essa consequência.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("plughub.routing.menu_wake")

TOPIC_MENU_WAKE = "menu.wake"
QUEUE_AGENT_FIELD = "_default_"


async def publish_menu_wake(producer, tenant_id: str, session_id: str, reason: str) -> bool:
    try:
        await producer.send(
            TOPIC_MENU_WAKE,
            key=session_id.encode("utf-8"),
            value={
                "event_type": "menu_wake",
                "tenant_id":  tenant_id,
                "session_id": session_id,
                "field":      QUEUE_AGENT_FIELD,
                "reason":     reason,
                "timestamp":  datetime.now(timezone.utc).isoformat(),
            },
        )
        return True
    except Exception as exc:
        logger.warning(
            "menu.wake NÃO publicado (session=%s reason=%s): se o agente de fila estiver "
            "estacionado, ele não acorda com este sinal — %s", session_id, reason, exc,
        )
        return False
