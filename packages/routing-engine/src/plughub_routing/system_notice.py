"""
system_notice.py — o aviso da plataforma ao cliente também é fato do stream canônico (ALW-18).

O aviso de espera da fila muda sai por `conversations.outbound` direto ao canal, com autor
`system`, e até 2026-09-28 não deixava rastro no `session:{sid}:stream`: vivia só na lista
`session:{sid}:messages` que o webchat mantinha para o histórico do Console. O histórico passou a
ser PROJEÇÃO do stream (uma casa), e o aviso que só existisse fora dele sumiria da tela.

Tipo `system_notice`, nunca `message` — ver `@plughub/schemas/stream.ts`. Gravado ANTES da
publicação no Kafka e com o MESMO `message_id`, para o histórico e o canal falarem da mesma
mensagem. Falha aqui não impede o aviso ao cliente: é dita, e o que se perde é só a linha
no histórico do Console.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("plughub.routing.system_notice")


async def record_system_notice(
    redis,
    session_id: str,
    message_id: str,
    text:       str,
    timestamp:  str,
    author_id:  str = "routing-engine",
) -> bool:
    if not (session_id and message_id and text):
        return False
    autor = {"participant_id": author_id, "instance_id": author_id, "role": "system"}
    try:
        await redis.xadd(f"session:{session_id}:stream", {
            "event_id":    message_id,
            "type":        "system_notice",
            "timestamp":   timestamp,
            "author_id":   author_id,
            "author_role": "system",
            "author":      json.dumps(autor),
            "visibility":  json.dumps("all"),
            "segment_id":  "",
            "payload":     json.dumps({
                "message_id": message_id,
                "content":    {"type": "text", "text": text},
            }, ensure_ascii=False),
        })
        return True
    except Exception as exc:
        logger.warning(
            "ALW-18: aviso de sistema NAO gravado no stream session=%s — o cliente recebe, mas o "
            "historico do Console fica sem esta linha: %s", session_id, exc,
        )
        return False
