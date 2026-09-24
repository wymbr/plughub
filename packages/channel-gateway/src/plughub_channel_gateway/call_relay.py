"""WCH-12 — a chamada tem DONA, e o que é dela chega a ela.

Uma chamada (browser `webrtc`, telefone SIP, ou a chamada presa a um contato de chat) vive na
MEMÓRIA de uma instância do gateway: a sala, o bot leg, a fila de fala, a coleta. Duas entradas
não escolhem instância:

  · `conversations.outbound` é consumido por UM grupo Kafka — a mensagem cai em qualquer réplica;
  · o webhook do SFU (`/v1/livekit/webhook`) cai em qualquer réplica atrás do balanceador.

Com N réplicas, a fala da IA, o menu, o `session.closed` e o `participant_left` iam parar numa
instância que não tem a chamada — e a IA ficava muda, sem erro em lugar nenhum.

O contrato aqui é o do `SessionRegistry` do webchat, com uma diferença: a chave é da SESSÃO e o
canal é da INSTÂNCIA.

  · `channel:call:{sid}:owner = instance_id` — gravada quando a instância assume a chamada,
    renovada no keepalive, apagada (só se ainda for dela) quando a chamada acaba;
  · `call:deliver:{instance_id}` — cada instância ouve o SEU canal e só ele.

⚠️ ORDEM. Os leitores de saída enfileiram a fala ANTES do primeiro `await` (VOZ-05 fatia 3:
a ordem entre as tasks do consumidor só é a do Kafka até a primeira suspensão). Perguntar ao
Redis quem é a dona é um `await` — por isso o envio é ENCADEADO por sessão (`send_later`): cada
envio espera o anterior da mesma sessão, e a dona recebe na ordem em que o Kafka entregou. Na
dona, o ouvinte abre as tasks na ordem de chegada, que é o mesmo contrato do consumidor.

⚠️ Dona que não ouve (`publish` com 0 receptores) é instância MORTA com a chave ainda viva: a
chamada morreu junto. É ERROR nomeado, nunca entrega local — entregar aqui seria o mesmo silêncio
que esta peça existe para acabar.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from plughub_tasks import disparar

logger = logging.getLogger("plughub.channel_gateway.call_relay")

# Apaga só se a chave ainda for desta instância: numa re-anexação da chamada em OUTRA réplica,
# o `release` atrasado da antiga não pode apagar a posse da nova.
_RELEASE_IF_MINE = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) "
    "else return 0 end"
)

Handler = Callable[[dict], Awaitable[None]]


def owner_key(session_id: str) -> str:
    return f"channel:call:{session_id}:owner"


def deliver_channel(instance_id: str) -> str:
    return f"call:deliver:{instance_id}"


class CallRelay:
    def __init__(self, redis: Any, instance_id: str, ttl: int) -> None:
        self._redis = redis
        self.instance_id = instance_id
        self._ttl = ttl
        self._handlers: dict[str, Handler] = {}
        self._tail: dict[str, asyncio.Task] = {}

    # ── posse ─────────────────────────────────────────────────────────────

    async def claim(self, session_id: str) -> None:
        """A chamada passa a ser desta instância. A última a assumir vence (re-anexação)."""
        await self._redis.set(owner_key(session_id), self.instance_id, ex=self._ttl)

    async def renew(self, session_id: str) -> None:
        await self._redis.expire(owner_key(session_id), self._ttl)

    async def release(self, session_id: str) -> None:
        try:
            await self._redis.eval(_RELEASE_IF_MINE, 1, owner_key(session_id), self.instance_id)
        except Exception as exc:  # noqa: BLE001
            # Não apagar deixa a chave até o TTL: a saída seguiria indo a esta instância, que já
            # não tem a chamada — e entregaria no vazio. Dito.
            logger.error("call_relay: posse de %s NAO liberada (%s) — ate o TTL, a saida desta "
                         "sessao vem para esta instancia sem chamada", session_id, exc)

    async def owner(self, session_id: str) -> str:
        return str(await self._redis.get(owner_key(session_id)) or "")

    # ── envio ─────────────────────────────────────────────────────────────

    def on(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    def send_later(
        self, session_id: str, envelope: dict,
        unowned: Callable[[], Awaitable[None]] | None = None,
    ) -> asyncio.Task:
        """Encaminha `envelope` à dona da chamada de `session_id`, em ordem com os envios
        anteriores da mesma sessão. SÍNCRONO até enfileirar — chame antes de qualquer `await`.

        Sem dona, ou dona = esta instância: roda `unowned` (a entrega local de sempre), também
        em ordem."""
        prev = self._tail.get(session_id)
        task = disparar(self._send_after(prev, session_id, envelope, unowned),
                        nome=f"call-relay-{session_id[:8]}")
        self._tail[session_id] = task

        def _limpa(t: asyncio.Task) -> None:
            if self._tail.get(session_id) is t:
                self._tail.pop(session_id, None)
        task.add_done_callback(_limpa)
        return task

    async def _send_after(
        self, prev: asyncio.Task | None, session_id: str, envelope: dict,
        unowned: Callable[[], Awaitable[None]] | None,
    ) -> None:
        if prev is not None:
            await asyncio.wait({prev})
        try:
            dona = await self.owner(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("call_relay: dona de %s ILEGIVEL (%s) — %s entregue localmente, e so chega "
                         "se a chamada for daqui", session_id, exc, envelope.get("kind"))
            dona = ""
        if dona and dona != self.instance_id:
            await self.forward(dona, session_id, envelope)
            return
        if unowned is not None:
            await unowned()

    async def forward(self, instance_id: str, session_id: str, envelope: dict) -> bool:
        body = json.dumps({**envelope, "session_id": session_id})
        ouvintes = await self._redis.publish(deliver_channel(instance_id), body)
        if not ouvintes:
            logger.error(
                "call_relay: a dona %s da chamada %s NAO ouve (instancia morta com a posse viva) — "
                "%s PERDIDO; a chamada morreu com ela", instance_id, session_id, envelope.get("kind"),
            )
            return False
        logger.info("call_relay: %s de %s encaminhado a dona %s", envelope.get("kind"),
                    session_id, instance_id)
        return True

    # ── recebimento ───────────────────────────────────────────────────────

    async def listen(self) -> None:
        """Ouve o canal DESTA instância. Uma task por envelope, aberta na ordem de chegada — a
        entrega local enfileira a fala antes do primeiro `await`, como no consumidor."""
        pubsub = self._redis.pubsub()
        canal = deliver_channel(self.instance_id)
        await pubsub.subscribe(canal)
        logger.info("call_relay: ouvindo %s", canal)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            self.dispatch_raw(message.get("data"))

    def dispatch_raw(self, data: Any) -> None:
        try:
            envelope = json.loads(data)
        except (TypeError, ValueError) as exc:
            logger.error("call_relay: envelope ilegivel descartado: %s", exc)
            return
        kind = envelope.get("kind", "")
        handler = self._handlers.get(kind)
        if handler is None:
            logger.error("call_relay: envelope %r sem tratador nesta instancia — descartado (session=%s)",
                         kind, envelope.get("session_id"))
            return
        disparar(handler(envelope), nome=f"call-relay-in-{kind}")
