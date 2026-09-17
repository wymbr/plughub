"""
_webrtc_keypad_exercise.py — exercício do `probe_webrtc_keypad.sh` (VOZ-05 fatia 5c).

Roda DENTRO da imagem do channel-gateway, na rede do compose. O cliente é um WebSocket com o
protocolo do widget, numa chamada de IA com a fixture `skill_probe_keypad_v1`, respondendo PELA
TELA (o teclado do widget é medido no roteiro assistido, com navegador):

  F1 o frame do menu de botões leva a visão da coleta (`input: [dtmf]`) — é dela que o widget tira
     o teclado; sem ela, o teclado não existe
  F2 o frame do PIN leva o domínio e os tamanhos (4 a 6, terminador #) e o campo mascarado
  S2 PIN longo demais pela tela → recusado (`collect_invalid`, com a mensagem do fluxo) e NÃO chega ao menu
  S3 CONTROLE PIN válido com terminador → chega ao menu (`voz05c-pin-recebido`)

Imprime `SID <id>` e `PIN <valor>` para o .sh conferir o log.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
import uuid

import jwt
import websockets

POOL   = os.environ["POOL"]
TENANT = os.environ["PLUGHUB_TENANT_ID"]
SECRET = os.environ["PLUGHUB_JWT_SECRET"]
GW = f"ws://channel-gateway:8010/ws/webrtc/{POOL}"


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


class Cliente:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.pend: list[dict] = []

    async def espera(self, pred, prazo_s: float) -> dict | None:
        for m in self.pend:
            if pred(m):
                self.pend.remove(m)
                return m
        fim = time.monotonic() + prazo_s
        while time.monotonic() < fim:
            try:
                m = json.loads(await asyncio.wait_for(self.ws.recv(), max(0.1, fim - time.monotonic())))
            except (asyncio.TimeoutError, websockets.ConnectionClosed):
                break
            if pred(m):
                return m
            self.pend.append(m)
        return None


def interacao(trecho):
    return lambda m: m.get("type") == "webrtc.interaction" and trecho in (m.get("prompt") or "")


def marca(m):
    return m.get("type") == "webrtc.message" and (m.get("text") or "").startswith("voz05c-") \
        and m.get("text") != "voz05c-inicio"


async def main() -> None:
    # PIN aleatório de 6 dígitos que não se confunde com hora nem id no log
    pin = "".join(random.choice("123456789") for _ in range(6))
    # a tentativa INVÁLIDA também é distintiva (7 dígitos, acima do máximo): o L1 procura as duas no
    # log — com uma tentativa de 2 dígitos, um valor recusado vazando no log passava (medido na LM6)
    ruim = "".join(random.choice("123456789") for _ in range(7))
    print(f"PIN {pin}", flush=True)
    print(f"RUIM {ruim}", flush=True)
    sub = "c-voz05c-" + uuid.uuid4().hex[:6]
    n = int(time.time())
    tok = jwt.encode({"sub": sub, "tenant_id": TENANT, "channel": "webrtc", "iat": n, "exp": n + 600},
                     SECRET, algorithm="HS256")
    async with websockets.connect(GW) as ws:
        c = Cliente(ws)
        await c.espera(lambda m: m.get("type") == "conn.ready", 10)
        await ws.send(json.dumps({"type": "conn.hello", "version": "1"}))
        await ws.send(json.dumps({"type": "conn.authenticate", "token": tok}))
        auth = await c.espera(lambda m: m.get("type") == "conn.authenticated", 15)
        if not auth:
            emit("INCONCL", "F1", "cliente nao autenticou")
            return
        print(f"SID {auth['session_id']}", flush=True)

        m1 = await c.espera(interacao("fatura"), 45)
        if not m1:
            emit("INCONCL", "F1", "o fluxo nao mandou o menu de botoes")
            return
        v1 = m1.get("collect") or {}
        emit("OK" if v1.get("input") == ["dtmf"] else "FALHA", "F1", f"menu de botoes leva a coleta: {m1.get('collect')!r}")
        await ws.send(json.dumps({"type": "webrtc.menu_submit", "menu_id": m1["menu_id"],
                                  "interaction": "button", "result": "email"}))
        val = await c.espera(marca, 20)
        m2 = await c.espera(interacao("PIN"), 30)
        if not (val and m2):
            emit("INCONCL", "F2", f"nao chegou ao PIN (valor={val and val.get('text')!r})")
            return
        v2 = m2.get("collect") or {}
        quer = {"input": ["dtmf"], "domain": "digits", "min_digits": 4, "max_digits": 6, "terminator": "#"}
        emit("OK" if v2 == quer and m2.get("masked_fields") else "FALHA", "F2",
             f"PIN leva dominio e tamanhos e e mascarado: collect={v2!r} masked_fields={m2.get('masked_fields')!r}")

        await ws.send(json.dumps({"type": "webrtc.menu_submit", "menu_id": m2["menu_id"],
                                  "interaction": "text", "result": ruim}))
        erro = await c.espera(lambda m: m.get("type") == "conn.error", 10)
        cedo = await c.espera(marca, 4)
        emit("OK" if (erro and erro.get("code") == "collect_invalid" and erro.get("message") == "PIN inválido."
                      and not cedo) else "FALHA", "S2",
             f"PIN longo demais recusado pela tela: erro={erro!r} e o fluxo mandou {cedo and cedo.get('text')!r}")

        await ws.send(json.dumps({"type": "webrtc.menu_submit", "menu_id": m2["menu_id"],
                                  "interaction": "text", "result": pin + "#"}))
        fim = await c.espera(marca, 20)
        emit("OK" if fim and fim.get("text") == "voz05c-pin-recebido" else "FALHA", "S3",
             f"CONTROLE PIN valido com terminador: o fluxo mandou {fim and fim.get('text')!r}")
        await ws.send(json.dumps({"type": "webrtc.hangup"}))
        await asyncio.sleep(1)


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
