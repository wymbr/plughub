"""
webrtc_call.py — a chamada que se PRENDE a um contato de chat (WCH-01, 2026-09-21).

Decisão (`docs/adr/adr-chat-call-as-medium.md`): o contato do site é `webchat`, e a chamada é MEIO
que ele ganha. O texto continua pela conexão do webchat — stream canônico, cursor, anexos, árvore
de opções —, e esta conexão carrega SÓ o que é de chamada: sala, teto de mídia, gravação.

Diferenças para a conexão `webrtc` de canal (`WebRTCAdapter.handle_ws`), todas de propósito:

  * **não abre contato nem roteia** — a sessão já existe e já tem (ou terá) atendente; a chamada
    lê os atendentes do stream desde o início (`routing.assigned` / `participant_left`);
  * **não tem caminho de texto** — `webrtc.message` e `webrtc.menu_submit` são recusados DITOS;
  * **a queda derruba só a chamada** — nunca publica `contact_closed`, e o `ws_alive` do contato
    é do webchat: tocá-lo daqui manteria viva uma conversa cujo chat caiu;
  * **o fim do contato encerra a chamada** (`session_closed` no stream);
  * **a política do pool é lida AQUI, e só quando há chamada** — o bridge não a lê para `webchat`
    (`media_policy_source=not_webrtc`) porque seria uma chamada HTTP por ativação de todo chat,
    quase todas sem chamada nenhuma (custo e escala decidiram o ADR);
  * **sem bot leg nesta fatia** — nem transcrição nem voz de IA. Numa chamada de chat, a IA segue
    falando por texto; a conversão fica para a WCH-02, e o motivo vai ao estado de mídia.

A presença da chamada é anunciada como `media.call` (`started` | `ended`): no stream (registro
durável do trecho, `agents_only`) e em `agent:events:{sid}`, que é por onde o Console fica sabendo
que precisa montar a mídia num contato de chat.
"""
from __future__ import annotations

import asyncio
import json
import time
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import jwt as pyjwt
from fastapi import WebSocket, WebSocketDisconnect

from . import media_policy

logger = logging.getLogger("plughub.channel-gateway.webrtc.call")

CALL_CHANNELS = frozenset({"webchat"})     # contatos que podem ganhar chamada
NO_BOT_LEG_REASON = "chamada de contato de chat: sem transcricao nem voz de IA (WCH-02)"


def _stream_id(raw: object) -> tuple[int, int]:
    """`"ms-seq"` → `(ms, seq)` para comparar. Ilegível vira `(-1, -1)`: nunca é "depois" de nada."""
    s = raw.decode() if isinstance(raw, bytes) else str(raw)
    ms, _, seq = s.partition("-")
    return (int(ms), int(seq)) if ms.isdigit() and seq.isdigit() else (-1, -1)


class CallRefused(Exception):
    """Recusa da conexão de chamada, com o código que o widget lê."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CallAttachMixin:
    """Métodos da chamada anexada. Usa o estado do `WebRTCAdapter` (sala, teto, gravação)."""

    _attached: set[str]
    _call_started: set[str]
    _call_end_reason: dict[str, str]

    # ── Porta ────────────────────────────────────────────────────────────────

    async def handle_call_ws(self, ws: WebSocket) -> None:
        await ws.accept()
        if self._provider is None:
            # A mesma recusa da conexão de canal (VOZ-01): sem plano de mídia não há chamada — e o
            # chat do cliente segue intacto, que é o ponto de a chamada ser meio.
            await self._ws_error(ws, "media_plane_unavailable", str(self._provider_unavailable))
            return
        await self._ws_send(ws, {"type": "conn.ready"})

        from ..attachment_store import resolve_ws_auth_timeout_s
        timeout_s = await resolve_ws_auth_timeout_s(self._redis, self._settings.tenant_id, 30)
        try:
            session_id = await asyncio.wait_for(self._call_handshake(ws), timeout=float(timeout_s))
        except asyncio.TimeoutError:
            await self._ws_error(ws, "auth_timeout", "Authentication timed out")
            return
        except CallRefused as exc:
            logger.warning("webrtc chamada RECUSADA code=%s: %s", exc.code, exc.message)
            await self._ws_error(ws, exc.code, exc.message)
            return
        except WebSocketDisconnect:
            return

        self._connections[session_id] = ws
        self._attached.add(session_id)
        await self._ws_send(ws, {"type": "conn.authenticated", "session_id": session_id,
                                 "mode": "call"})
        logger.info("webrtc chamada anexada ao contato de chat session=%s", session_id)

        tasks = [
            asyncio.create_task(self._call_stream_watcher(ws, session_id),
                                name=f"webrtc-call-stream-{session_id[:8]}"),
            asyncio.create_task(self._call_receive_loop(ws, session_id),
                                name=f"webrtc-call-receive-{session_id[:8]}"),
            asyncio.create_task(self._call_keepalive(session_id),
                                name=f"webrtc-call-keepalive-{session_id[:8]}"),
        ]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await self._end_attached_call(
            session_id, self._call_end_reason.pop(session_id, "customer_disconnect"))

    async def _call_handshake(self, ws: WebSocket) -> str:
        """`conn.hello` → `conn.authenticate {token, session_id}`. Devolve o `session_id`.

        O token é o do CHAT (mesmo segredo por tenant); a sessão tem de ser `webchat`, do mesmo
        cliente, aberta, e sem outra chamada. Sessão alheia e inexistente recebem a MESMA recusa
        — não se confirma a existência de contato de outro cliente."""
        msg = json.loads(await ws.receive_text())
        if msg.get("type") != "conn.hello":
            raise CallRefused("bad_message", f"Expected conn.hello, got: {msg.get('type')}")
        msg = json.loads(await ws.receive_text())
        if msg.get("type") != "conn.authenticate":
            raise CallRefused("bad_message", f"Expected conn.authenticate, got: {msg.get('type')}")
        token = msg.get("token") or ""
        if not token:
            raise CallRefused("missing_token", "conn.authenticate requires 'token'")
        try:
            claims = pyjwt.decode(token, await self._resolve_jwt_secret(self._settings.tenant_id),
                                  algorithms=["HS256"])
        except pyjwt.ExpiredSignatureError:
            raise CallRefused("token_expired", "Customer token has expired")
        except pyjwt.InvalidTokenError as exc:
            raise CallRefused("invalid_token", f"Customer token invalid: {exc}")
        contact_id = str(claims.get("sub") or "")
        if not contact_id:
            raise CallRefused("missing_sub", "Token must contain 'sub' claim (contact_id)")

        claimed = str(claims.get("session_id") or "")
        session_id = str(msg.get("session_id") or "") or claimed
        if not session_id:
            raise CallRefused("missing_session", "conn.authenticate requires 'session_id'")
        if claimed and claimed != session_id:
            raise CallRefused("session_not_found", "no chat contact for this customer")

        raw = await self._redis.get(f"session:{session_id}:meta")
        try:
            meta = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        if (str(meta.get("tenant_id") or "") != self._settings.tenant_id
                or str(meta.get("contact_id") or "") != contact_id):
            raise CallRefused("session_not_found", "no chat contact for this customer")
        channel = str(meta.get("channel") or "")
        if channel not in CALL_CHANNELS:
            raise CallRefused("not_a_chat_contact",
                              f"call attaches to a chat contact; this one is {channel or '?'}")
        if await self._redis.exists(f"session:{session_id}:closed_recorded"):
            raise CallRefused("contact_closed", "the chat contact already ended")
        if session_id in self._connections:
            raise CallRefused("call_already_active", "this contact already has a call")
        return session_id

    # ── Recepção e keepalive ─────────────────────────────────────────────────

    async def _call_receive_loop(self, ws: WebSocket, session_id: str) -> None:
        try:
            async for raw in ws.iter_text():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                kind = msg.get("type", "")
                if kind == "webrtc.hangup":
                    self._call_end_reason[session_id] = "customer_hangup"
                    return
                if kind == "conn.ping":
                    await self._ws_send(ws, {"type": "conn.pong"})
                elif kind in ("webrtc.message", "webrtc.menu_submit", "msg.text", "menu.submit"):
                    # O texto tem UMA casa: a conexão do chat. Recebê-lo aqui seria o segundo
                    # caminho que o ADR elimina.
                    await self._ws_send(ws, {
                        "type": "conn.error", "code": "text_goes_through_chat",
                        "message": "this connection carries the call only; send text over the chat",
                    })
                else:
                    logger.debug("webrtc chamada: frame ignorado type=%s session=%s", kind, session_id)
        except WebSocketDisconnect:
            self._call_end_reason.setdefault(session_id, "customer_disconnect")

    async def _call_keepalive(self, session_id: str) -> None:
        from .webrtc import _KEEPALIVE_INTERVAL
        ttl = self._settings.session_ttl_seconds
        while True:
            await asyncio.sleep(_KEEPALIVE_INTERVAL)
            try:
                await self._redis.expire(f"channel:webrtc:{session_id}:room_name", ttl)
                await self._redis.expire(self._media_key(session_id), ttl)
            except Exception as exc:  # noqa: BLE001
                logger.debug("webrtc chamada keepalive (session=%s): %s", session_id, exc)

    # ── Política do pool, lida só quando há chamada ──────────────────────────

    # ── WCH-07: o atendente desliga a CHAMADA, não o contato ──────────────────

    CALL_END_REQUESTED = "media.call.end_requested"

    async def last_call_state(self, session_id: str) -> str:
        """Estado do último `media.call` do stream (`started`/`ended`), ou "" se nunca houve.

        Lê de trás para frente, em páginas: o stream é o registro durável do trecho, e é o
        mesmo fato que o Console e a supervisão leem — nenhuma segunda casa para "há chamada"."""
        key, end = f"session:{session_id}:stream", "+"
        for _ in range(20):                                 # teto: 4 000 entradas
            page = await self._redis.xrevrange(key, max=end, min="-", count=200)
            if not page:
                return ""
            for entry_id, fields in page:
                if fields.get("type") == "media.call":
                    return fields.get("state") or str(self._json_field(fields, "payload").get("state") or "")
            last = page[-1][0]
            ms, _, seq = str(last).partition("-")
            if not ms.isdigit() or not seq.isdigit():
                return ""
            end = f"({ms}-{seq}"                           # exclusivo: a página seguinte
        logger.warning("webrtc chamada: media.call nao achado nas ultimas 4000 entradas (session=%s)",
                       session_id)
        return ""

    async def request_agent_hangup(self, session_id: str, instance_id: str) -> None:
        """Grava no stream o pedido de desligar. Quem encerra é o observador da chamada — na
        instância que segura o WS do cliente, qualquer que seja —, e o autor fica registrado."""
        now = datetime.now(timezone.utc).isoformat()
        await self._redis.xadd(f"session:{session_id}:stream", {
            "type": self.CALL_END_REQUESTED, "event_id": str(uuid.uuid4()),
            "session_id": session_id, "timestamp": now, "visibility": "agents_only",
            "author_id": instance_id, "author_role": "agent",
            "author": json.dumps({"type": "agent_human", "id": instance_id}),
            "payload": json.dumps({"requested_by": instance_id}),
        })
        logger.info("webrtc chamada: desligar pedido por %s (session=%s)", instance_id, session_id)

    async def _stream_tail_id(self, stream_key: str) -> str:
        """Última entrada do stream AGORA. Pedido de desligar só vale depois dela: o observador
        relê o stream desde o início, e o pedido de uma chamada anterior do mesmo contato
        encerraria a chamada nova na hora."""
        try:
            last = await self._redis.xrevrange(stream_key, max="+", min="-", count=1)
            if isinstance(last, list) and last and isinstance(last[0][0], (str, bytes)):
                return last[0][0].decode() if isinstance(last[0][0], bytes) else last[0][0]
            if isinstance(last, list) and not last:
                return "0-0"
        except Exception as exc:  # noqa: BLE001
            logger.warning("webrtc chamada: fim do stream ilegivel (%s) — pedidos de desligar valem "
                           "a partir de agora pelo relogio", exc)
        return f"{int(time.time() * 1000)}-0"

    async def _attached_pool_field(self, session_id: str, fields: dict) -> dict:
        """Completa o campo `pool` do `routing.assigned` de um contato de chat com a política do
        pool, lida FRESCA do registry. Registry fora ou pool ilegível → `registry_unavailable`,
        que o `media_policy` já trata como "não oferece nada" — nunca se supõe."""
        pool = self._json_field(fields, "pool")
        if pool.get("media_policy_source") != "not_webrtc" or not pool.get("pool_id"):
            return fields
        pool_id = str(pool["pool_id"])
        s = self._settings
        headers = {"x-tenant-id": s.tenant_id}
        if s.agent_registry_service_token:
            headers["x-service-token"] = s.agent_registry_service_token
        novo: dict[str, Any] = {"pool_id": pool_id}
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{s.agent_registry_url.rstrip('/')}/v1/pools/{pool_id}", headers=headers)
            if r.status_code == 200:
                novo["media_policy"] = r.json().get("media_policy")
                novo["media_policy_source"] = "registry"
            else:
                novo["media_policy_source"] = "registry_unavailable"
                logger.error("webrtc chamada: politica do pool %s NAO lida (HTTP %s) — o atendente "
                             "nao oferece midia (session=%s)", pool_id, r.status_code, session_id)
        except Exception as exc:  # noqa: BLE001
            novo["media_policy_source"] = "registry_unavailable"
            logger.error("webrtc chamada: registry inalcancavel para a politica do pool %s (%s) — "
                         "o atendente nao oferece midia (session=%s)", pool_id, exc, session_id)
        return {**fields, "pool": json.dumps(novo)}

    # ── Os atendentes de AGORA, e a sala quando algum oferece mídia ──────────

    async def _call_stream_watcher(self, ws: WebSocket, session_id: str) -> None:
        """Lê o stream DESDE O INÍCIO e mantém o conjunto de atendentes de agora.

        ⚠️ Não é o `_stream_watcher` do canal, que monta a sala no PRIMEIRO `routing.assigned`:
        num contato de chat em curso, o primeiro pode ser um agente de fila que já saiu, e o teto
        dele decidiria a chamada inteira. Aqui a sala só nasce quando o conjunto ATUAL oferece
        mídia ao cliente; enquanto não oferece, o widget recebe `webrtc.call_pending` com as
        procedências. Depois da sala, entradas e saídas seguem pelos mesmos handlers do canal."""
        from .webrtc import _STREAM_BLOCK_MS, _STREAM_WATCHER_SLEEP
        stream_key = f"session:{session_id}:stream"
        hangup_after = _stream_id(await self._stream_tail_id(stream_key))
        last_id = "0-0"
        ready = False
        pending_sent: list[str] | None = None
        state = await self._load_media_state(session_id)
        state["attendants"] = {}        # reconstruído do stream: estado de chamada anterior não vale
        while True:
            try:
                results = await self._redis.xread({stream_key: last_id}, count=50, block=_STREAM_BLOCK_MS)
            except Exception as exc:  # noqa: BLE001
                logger.warning("webrtc chamada: leitura do stream falhou (session=%s): %s", session_id, exc)
                await asyncio.sleep(_STREAM_WATCHER_SLEEP)
                continue
            changed = False
            lidos = 0
            for _, entries in results or []:
                lidos += len(entries)
                for entry_id, fields in entries:
                    last_id = entry_id
                    kind = fields.get("type", "")
                    if kind in ("session_closed", "session.closed"):
                        self._call_end_reason[session_id] = "contact_closed"
                        await self._ws_send(ws, {"type": "webrtc.call_ended", "reason": "contact_closed"})
                        return
                    if kind == self.CALL_END_REQUESTED and _stream_id(entry_id) > hangup_after:
                        self._call_end_reason[session_id] = "agent_hangup"
                        await self._ws_send(ws, {"type": "webrtc.call_ended", "reason": "agent_hangup"})
                        return
                    if kind == "routing.assigned":
                        fields = await self._attached_pool_field(session_id, fields)
                        if ready:
                            await self._on_routing_renegotiate(ws, session_id, fields, self._settings)
                        else:
                            instance_id, record = self._attendant_record(fields, session_id)
                            state["attendants"][instance_id] = record
                            changed = True
                    elif kind == "participant_left":
                        if ready:
                            await self._on_attendant_left(ws, session_id, fields)
                        else:
                            who = (fields.get("author_id", "")
                                   or self._json_field(fields, "payload").get("participant_id", ""))
                            if state["attendants"].pop(who, None) is not None:
                                changed = True
            if ready or not (changed or pending_sent is None) or lidos >= 50:
                continue            # lote cheio = replay ainda não alcançou o fim do stream
            if await self._attached_setup(ws, session_id, state):
                ready = True
                continue
            sources = sorted(media_policy.policy_sources(state["attendants"]))
            if sources != pending_sent:
                pending_sent = sources
                logger.info("webrtc chamada aguardando: nenhum atendente atual oferece midia "
                            "(session=%s atendentes=%s fontes=%s)",
                            session_id, sorted(state["attendants"]), sources)
                await self._ws_send(ws, {"type": "webrtc.call_pending",
                                         "reason": "no_attendant_offers_media",
                                         "policy_sources": sources})

    async def _attached_setup(self, ws: WebSocket, session_id: str, state: dict) -> bool:
        """Monta a sala quando o conjunto atual oferece mídia. Devolve se montou."""
        publish = self._ceiling(state, session_id)
        if not publish:
            return False
        state["customer"] = self._customer_state(state, publish, "call_attached", session_id)
        self._decide_voice(session_id, state)          # registra a ausência de voz, dita
        self._customer_media[session_id] = publish
        room = self._room_of(session_id)
        # a chave ANTES da sala (VOZ-02): sem ela, o `police_room` apaga a sala ao nascer
        await self._redis.setex(f"channel:webrtc:{session_id}:room_name",
                                self._settings.session_ttl_seconds, room)
        try:
            await self._provider.create_room(room)
        except Exception as exc:  # noqa: BLE001
            logger.warning("webrtc chamada: create_room falhou (session=%s room=%s): %s", session_id, room, exc)
        identity = await self._customer_identity(session_id)
        token = self._provider.generate_token(self._customer_grants(room, identity, publish))
        await self._save_media_state(session_id, state)
        await self._ws_send(ws, {
            "type": "webrtc.ready", "livekit_url": self._client_livekit_url(), "token": token,
            "room_name": room, "publish": state["customer"]["publish"],
            "policy_sources": state["customer"]["policy_sources"],
        })
        await self._announce_call(session_id, "started")
        self._recording_follow(session_id, state)
        logger.info("webrtc chamada pronta session=%s publish=%s fontes=%s room=%s", session_id,
                    state["customer"]["publish"], state["customer"]["policy_sources"], room)
        return True

    # ── Fim ──────────────────────────────────────────────────────────────────

    async def _end_attached_call(self, session_id: str, reason: str) -> None:
        """Encerra a CHAMADA, nunca o contato. Idempotente."""
        if session_id not in self._attached:
            return
        self._attached.discard(session_id)
        room = self._room_of(session_id)
        await self._recorder.close(session_id)          # a parte em curso fecha e é guardada
        await self._stop_bot_leg(session_id)
        started = session_id in self._call_started
        self._call_started.discard(session_id)
        if started:
            try:
                await self._provider.delete_room(room)
            except Exception as exc:  # noqa: BLE001
                logger.warning("webrtc chamada: sala %s nao encerrada (%s) session=%s", room, exc, session_id)
        try:
            await self._redis.delete(f"channel:webrtc:{session_id}:room_name",
                                     self._media_key(session_id),
                                     f"channel:webrtc:{session_id}:media_hold")
        except Exception as exc:  # noqa: BLE001
            logger.warning("webrtc chamada: chaves da sala nao apagadas (%s) session=%s", exc, session_id)
        ws = self._connections.pop(session_id, None)
        self._customer_media.pop(session_id, None)
        if started:
            await self._announce_call(session_id, "ended", reason)
        if ws is not None:
            try:
                await ws.close(code=1000)
            except Exception:  # noqa: BLE001
                pass
        logger.info("webrtc chamada encerrada session=%s reason=%s (o contato de chat segue)",
                    session_id, reason)

    async def _announce_call(self, session_id: str, state: str, reason: str = "") -> None:
        """`media.call` no stream (registro durável do trecho) e em `agent:events` (Console)."""
        now = datetime.now(timezone.utc).isoformat()
        if state == "started":
            self._call_started.add(session_id)
        fields = {"type": "media.call", "event_id": str(uuid.uuid4()), "session_id": session_id,
                  "timestamp": now, "visibility": "agents_only",
                  "author": json.dumps({"type": "system", "id": "channel-gateway"}),
                  "state": state, "reason": reason,
                  "payload": json.dumps({"state": state, "reason": reason})}
        try:
            await self._redis.xadd(f"session:{session_id}:stream", fields)
        except Exception as exc:  # noqa: BLE001
            logger.error("webrtc chamada: media.call %s NAO foi ao stream (%s) session=%s",
                         state, exc, session_id)
        try:
            await self._redis.publish(f"agent:events:{session_id}", json.dumps({
                "type": "media.call", "session_id": session_id, "state": state,
                "reason": reason, "timestamp": now}))
        except Exception as exc:  # noqa: BLE001
            logger.error("webrtc chamada: media.call %s NAO chegou ao Console (%s) session=%s",
                         state, exc, session_id)
