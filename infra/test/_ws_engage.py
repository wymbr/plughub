"""
Cliente WebSocket mínimo do WEBCHAT — usado por `probe_spawn_reason_collect.sh`.

Por que ele existe: `handle_collect_engage` (GET /survey/{token}) NÃO publica
`conversations.inbound`. Ele só semeia o ctx da sessão-filha e cunha o JWT — quem
publica o inbound é a PÁGINA, ao conectar o WS com esse JWT (webchat.py:304, logo
após `conn.authenticated`). Um `curl` na página, portanto, produz ctx e NÃO produz
linha em `analytics.sessions`: mediria a metade errada e o zero pareceria "produtor
mudo" mais uma vez.

Roda DENTRO do container channel-gateway (é lá que `websockets` é dependência).

Uso:  python3 /tmp/_ws_engage.py <jwt> <pool_id> [hold_s]
Saída (stdout, uma linha por fato, para o shell ramificar):
  HELLO
  AUTHENTICATED session_id=<sid>
  CLOSED_BY_SERVER            (opcional — o flow completou e o servidor fechou)
  DONE
Código de saída: 0 = autenticou · 3 = não autenticou (o shell trata como INCONCLUSIVO).
"""
import asyncio
import json
import sys

import websockets


async def main() -> int:
    jwt    = sys.argv[1]
    pool   = sys.argv[2]
    hold_s = float(sys.argv[3]) if len(sys.argv) > 3 else 25.0

    url = f"ws://localhost:8010/ws/chat/{pool}"
    authed = False
    try:
        async with websockets.connect(url, open_timeout=15) as ws:
            deadline = asyncio.get_event_loop().time() + hold_s
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                except websockets.exceptions.ConnectionClosed:
                    print("CLOSED_BY_SERVER", flush=True)
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                mtype = msg.get("type", "")
                if mtype == "conn.hello":
                    print("HELLO", flush=True)
                    await ws.send(json.dumps({"type": "conn.authenticate", "token": jwt}))
                elif mtype == "conn.authenticated":
                    authed = True
                    print(f"AUTHENTICATED session_id={msg.get('session_id', '')}", flush=True)
                elif mtype == "conn.session_closed":
                    print("CLOSED_BY_SERVER", flush=True)
                    break
    except Exception as exc:  # noqa: BLE001 — o shell precisa do motivo, não do traceback
        print(f"WS_ERROR {type(exc).__name__}: {exc}", flush=True)

    print("DONE", flush=True)
    return 0 if authed else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
