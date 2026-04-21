"""
stream_subscriber.py
Modelo híbrido — subscriber do stream canônico da sessão para entrega WebSocket.

Responsabilidade única:
  - XREAD bloqueante em session:{id}:stream (Redis Stream)
  - Filtra eventos com visibility="agents_only" — cliente nunca os vê
  - Mapeia StreamEventType → WebChatMessageType
  - Rastreia cursor (último event_id lido) para reconnect cursor-based
  - Sem estado além do cursor — stateless entre reconexões

Motivação (modelo híbrido):
  O stream canônico já é a fonte de verdade de todos os eventos da sessão.
  Subscribing diretamente elimina:
    - Buffer de reconnect separado (XRANGE session:{id}:stream {cursor} +)
    - Notificações extras de participant_joined/left (já estão no stream)
    - Dependência do Kafka conversations.outbound para entrega webchat

  Para entrega cross-instance: o stream Redis é compartilhado — qualquer
  instância do Channel Gateway pode fazer XREAD. Sem pub/sub de conteúdo.

  Typing indicators (efêmeros, não persistidos) continuam via Redis pub/sub
  separado (session:{id}:typing). Não passam pelo stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import redis.asyncio as aioredis

logger = logging.getLogger("plughub.channel-gateway.stream")

# Tipos de visibilidade que bloqueiam entrega ao cliente
_AGENTS_ONLY = "agents_only"

# Timeout do XREAD bloqueante (ms) — evita bloqueio infinito, permite checagem
# de cancelamento da task
_XREAD_BLOCK_MS = 2000


class StreamExpiredError(Exception):
    """
    Levantado quando o cliente tenta reconectar com um cursor mas o stream Redis
    não existe mais (TTL expirou após o encerramento da sessão).

    O chamador deve enviar conn.session_ended ao cliente e fechar a conexão.
    Não é um erro de runtime — é um estado esperado para sessões encerradas.
    """


class StreamSubscriber:
    """
    Subscriber XREAD do stream canônico de uma sessão.

    Uso:
        subscriber = StreamSubscriber(redis, session_id, cursor="0")
        async for ws_msg in subscriber.messages():
            await ws.send_json(ws_msg)

    O `cursor` inicial deve ser:
      - "0" para nova conexão (recebe todos os eventos desde o início da sessão)
      - event_id do último evento recebido para reconnect (XRANGE > cursor)

    A propriedade `cursor` é atualizada a cada evento processado — o chamador
    deve salvar e passar no reconnect para garantir entrega sem lacunas.
    """

    def __init__(
        self,
        redis:      aioredis.Redis,
        session_id: str,
        cursor:     str = "0",
    ) -> None:
        self._redis      = redis
        self._stream_key = f"session:{session_id}:stream"
        self._cursor     = cursor

    @property
    def cursor(self) -> str:
        """Último event_id processado — persiste no cliente para reconnect."""
        return self._cursor

    # ─── API pública ──────────────────────────────────────────────────────────

    async def messages(self) -> AsyncIterator[dict]:
        """
        AsyncIterator que produz mensagens prontas para envio via WebSocket.
        Para quando a task é cancelada (asyncio.CancelledError propagado).

        Lança StreamExpiredError antes do primeiro XREAD se o cliente veio com
        um cursor (reconexão) mas o stream não existe mais no Redis — o que indica
        que a sessão foi encerrada e o TTL do stream expirou.  Cursor "0" (nova
        conexão) não aciona a verificação, pois o stream pode ainda não ter eventos.
        """
        # ── Verificação de stream expirado (somente em reconexão) ──────────────
        if self._cursor != "0":
            try:
                exists = await self._redis.exists(self._stream_key)
            except asyncio.CancelledError:
                return
            except Exception:
                exists = 1  # se não der pra checar, presume que existe
            if not exists:
                raise StreamExpiredError(
                    f"stream {self._stream_key} not found — session may have ended"
                )

        while True:
            try:
                entries = await self._redis.xread(
                    {self._stream_key: self._cursor},
                    count=20,
                    block=_XREAD_BLOCK_MS,
                )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("StreamSubscriber XREAD error: %s — retrying", exc)
                await asyncio.sleep(1)
                continue

            if not entries:
                continue  # timeout do BLOCK — sem novos eventos

            for _key, events in entries:
                for event_id, fields in events:
                    # Atualiza cursor independente de entregar — não re-processa
                    self._cursor = (
                        event_id.decode() if isinstance(event_id, bytes) else event_id
                    )

                    ws_msg = self._map_event(fields)
                    if ws_msg is not None:
                        yield ws_msg

    # ─── Mapeamento stream → WebSocket ───────────────────────────────────────

    def _map_event(self, fields: dict) -> dict | None:
        """
        Mapeia um evento do stream canônico para o formato WebSocket do webchat.
        Retorna None para eventos que não devem ser entregues ao cliente.
        """
        decoded = _decode_fields(fields)

        event_type = decoded.get("type", "")
        visibility = decoded.get("visibility", "all")

        # ── Filtro de visibilidade ──────────────────────────────────────────
        # agents_only nunca chega ao cliente browser.
        # Lista de participant_ids: o cliente não tem participant_id no modelo
        # híbrido — só recebe "all".
        if visibility == _AGENTS_ONLY:
            return None
        if isinstance(visibility, list):
            # Mensagem privada entre agentes — cliente não recebe
            return None

        # ── Mapeamento por tipo ────────────────────────────────────────────
        if event_type == "message":
            return self._map_message(decoded)

        if event_type == "participant_joined":
            return self._map_participant_joined(decoded)

        if event_type == "participant_left":
            return self._map_participant_left(decoded)

        if event_type == "session_closed":
            return {
                "type":   "conn.session_ended",
                "reason": decoded.get("close_reason") or decoded.get("reason", "session_closed"),
            }

        if event_type == "interaction_request":
            payload = decoded.get("payload", {})
            return {
                "type":        "interaction.request",
                "menu_id":     payload.get("menu_id", ""),
                "interaction": payload.get("interaction", "text"),
                "prompt":      payload.get("prompt", ""),
                "options":     payload.get("options"),
                "fields":      payload.get("fields"),
            }

        # flow_step_completed, customer_identified, medium_transitioned
        # não são entregues ao cliente na fase 1
        return None

    def _map_message(self, decoded: dict) -> dict | None:
        """Mapeia evento 'message' para o tipo WebSocket correto."""
        payload  = decoded.get("payload", {})
        content  = payload.get("content", {})
        author   = decoded.get("author", {})

        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception:
                content = {"type": "text", "text": content}

        content_type = content.get("type", "text")

        base = {
            "message_id": decoded.get("event_id", ""),
            "author": {
                "role":         author.get("role", "primary") if isinstance(author, dict) else "primary",
                "participant_id": author.get("participant_id", "") if isinstance(author, dict) else "",
            },
            "timestamp": decoded.get("timestamp", ""),
        }

        if content_type == "text":
            return {**base, "type": "msg.text", "text": content.get("text", "")}

        if content_type == "image":
            return {
                **base,
                "type":          "msg.image",
                "file_id":       content.get("file_id", ""),
                "url":           content.get("url", ""),
                "mime_type":     content.get("mime_type", ""),
                "original_name": content.get("original_name", ""),
                "size_bytes":    content.get("size_bytes", 0),
                "width_px":      content.get("width_px"),
                "height_px":     content.get("height_px"),
                "caption":       content.get("caption"),
            }

        if content_type == "document":
            return {
                **base,
                "type":          "msg.document",
                "file_id":       content.get("file_id", ""),
                "url":           content.get("url", ""),
                "mime_type":     content.get("mime_type", ""),
                "original_name": content.get("original_name", ""),
                "size_bytes":    content.get("size_bytes", 0),
                "page_count":    content.get("page_count"),
                "caption":       content.get("caption"),
            }

        if content_type == "video":
            return {
                **base,
                "type":           "msg.video",
                "file_id":        content.get("file_id", ""),
                "url":            content.get("url", ""),
                "mime_type":      content.get("mime_type", ""),
                "original_name":  content.get("original_name", ""),
                "size_bytes":     content.get("size_bytes", 0),
                "duration_secs":  content.get("duration_secs"),
                "thumbnail_url":  content.get("thumbnail_url"),
                "caption":        content.get("caption"),
            }

        # Tipo de conteúdo não suportado na fase 1 — ignora silenciosamente
        logger.debug("StreamSubscriber: unsupported content type=%s", content_type)
        return None

    def _map_participant_joined(self, decoded: dict) -> dict | None:
        """participant_joined → presence.agent_joined (somente agentes, não customer)."""
        author = decoded.get("author", {})
        if not isinstance(author, dict):
            return None
        role = author.get("role", "")
        # Não notifica o cliente sobre si mesmo (customer) — só sobre agentes
        if role == "customer":
            return None
        return {
            "type":           "presence.agent_joined",
            "participant_id": author.get("participant_id", ""),
            "role":           role,
        }

    def _map_participant_left(self, decoded: dict) -> dict | None:
        """participant_left → presence.agent_left (somente agentes)."""
        author = decoded.get("author", {})
        if not isinstance(author, dict):
            return None
        role = author.get("role", "")
        if role == "customer":
            return None
        return {
            "type":           "presence.agent_left",
            "participant_id": author.get("participant_id", ""),
            "role":           role,
        }


# ─── Utils ────────────────────────────────────────────────────────────────────

def _decode_fields(fields: dict) -> dict:
    """Decodifica bytes→str e tenta parse JSON para cada campo."""
    result: dict = {}
    for k, v in fields.items():
        key = k.decode() if isinstance(k, bytes) else str(k)
        val = v.decode() if isinstance(v, bytes) else str(v)
        try:
            result[key] = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            result[key] = val
    return result
