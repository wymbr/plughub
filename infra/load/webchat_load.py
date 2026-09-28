"""
webchat_load.py — carga de WEBCHAT pelo caminho de produção (PRD-02).

Cada "worker" é um cliente de verdade: abre o WebSocket do gateway, autentica com
JWT HS256, conversa (responde menu, formulário e pergunta de texto) e, quando a
sessão termina, abre OUTRA com um contato novo. Assim a concorrência pedida
(`--concurrency`) fica sustentada por `--duration` segundos — é o regime de um
contact center, não uma rajada.

Protocolo (conferido em `channel-gateway/adapters/webchat.py` e `stream_subscriber.py`):
  S→C conn.hello                     C→S conn.authenticate {token}
  S→C conn.authenticated {session_id}
  C→S msg.text {text}  |  menu.submit {menu_id, interaction, result}
  S→C msg.text {author.role, text}  |  interaction.request {menu_id, interaction, options, fields}
  S→C conn.ping → C→S conn.pong      S→C conn.session_ended {reason}

⚠️ O stream devolve ao cliente as PRÓPRIAS mensagens dele: mensagem de autor
`customer` nunca conta como resposta.
⚠️ `menu` com `interaction: text` sem `masked_fields` chega como `msg.text` comum e
espera `msg.text` de volta. O driver responde por texto quando, depois de uma
mensagem do agente, nada mais chega em `--idle-reply-s`.

Métricas (ms): ws_connect, authenticate, first_response (da autenticação — o agente pode
falar primeiro — ou da abertura do cliente, até a 1ª saída do agente), turn_response (de cada envio do cliente à próxima saída do agente),
session_duration. Contadores: sessions_started, sessions_authenticated,
session_ended:<motivo>, error:<código>, no_first_response, turns.

Uso:
  python3 webchat_load.py --url ws://GATEWAY:8010/ws/chat/demo \\
      --secret "$PLUGHUB_JWT_SECRET" --tenant tenant_demo \\
      --concurrency 3000 --ramp-s 300 --duration 1800 --report webchat.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import time
import uuid

import websockets

from harness_common import Metrics, jwt_hs256, ramp_delay, write_report


class Rules:
    """Respostas por substring do prompt; sem regra, política padrão."""

    def __init__(self, path: str | None, default_text: str) -> None:
        self.rules: list[dict] = []
        if path:
            with open(path, encoding="utf-8") as fh:
                self.rules = json.load(fh)
        self.default_text = default_text

    def match(self, prompt: str):
        low = (prompt or "").lower()
        for r in self.rules:
            if r.get("match", "").lower() in low:
                return r.get("answer")
        return None


END_OPTION = re.compile(r"encerrar|finalizar|sair|end|close|nao_obrigado|tchau", re.I)


def _now_ms() -> float:
    return time.perf_counter() * 1000


async def one_session(args, rules: Rules, m: Metrics, run_id: str, seq: int) -> None:
    contact = f"load-{run_id}-{seq}"
    now = int(time.time())
    token = jwt_hs256(
        {"sub": contact, "tenant_id": args.tenant, "channel": "webchat",
         "iat": now, "exp": now + 3600},
        args.secret,
    )
    m.count("sessions_started")
    t0 = _now_ms()
    try:
        ws = await asyncio.wait_for(
            websockets.connect(args.url, open_timeout=args.timeout_s, max_size=2**22,
                               ping_interval=None),
            timeout=args.timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 — conta e segue: carga não para por uma conexão
        m.count(f"error:connect:{type(exc).__name__}")
        return
    m.timing("ws_connect", _now_ms() - t0)
    m.enter()
    started = time.monotonic()
    try:
        await _converse(ws, args, rules, m, token, started)
    except websockets.ConnectionClosed as exc:
        m.count(f"session_ended:ws_closed:{exc.code}")
    except asyncio.TimeoutError:
        m.count("error:timeout")
    except Exception as exc:  # noqa: BLE001
        m.count(f"error:{type(exc).__name__}")
    finally:
        m.leave()
        m.timing("session_duration", (time.monotonic() - started) * 1000)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


async def _recv(ws, timeout: float) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(raw)


async def _converse(ws, args, rules: Rules, m: Metrics, token: str, started: float) -> None:
    hello = await _recv(ws, args.timeout_s)
    if hello.get("type") != "conn.hello":
        m.count(f"error:unexpected_first:{hello.get('type')}")
        return
    t_auth = _now_ms()
    await ws.send(json.dumps({"type": "conn.authenticate", "token": token, "cursor": None}))
    auth = await _recv(ws, args.timeout_s)
    if auth.get("type") != "conn.authenticated":
        m.count(f"error:auth:{auth.get('code', auth.get('type'))}")
        return
    m.count("sessions_authenticated")
    m.timing("authenticate", _now_ms() - t_auth)

    turns = 0
    # O agente pode falar PRIMEIRO (o demo manda o menu 40 ms após autenticar). Então a
    # 1ª resposta conta desde a autenticação, e o cliente só abre a conversa se nada
    # chegar em `--greeting-wait-s`. Mandar "oi" por cima de uma saudação já enfileirada
    # fazia o driver cronometrar um quadro que chegou ANTES do envio (0,1 ms medido).
    pending_since: float | None = _now_ms()
    first_pending = True
    last_agent_text_at: float | None = None
    opened = False

    async def send_text(text: str) -> None:
        nonlocal pending_since
        await ws.send(json.dumps({"type": "msg.text", "id": uuid.uuid4().hex[:8], "text": text}))
        pending_since = _now_ms()

    while True:
        if time.monotonic() - started > args.max_session_s:
            m.count("session_ended:driver_max_session")
            return
        if turns >= args.max_turns:
            m.count("session_ended:driver_max_turns")
            return
        if not opened and first_pending:
            wait = args.greeting_wait_s
        elif last_agent_text_at:
            wait = args.idle_reply_s
        else:
            wait = args.response_timeout_s
        try:
            msg = await _recv(ws, wait)
        except asyncio.TimeoutError:
            if not opened and first_pending:
                # Agente não abriu: o cliente abre (e a 1ª resposta conta daqui).
                opened = True
                await asyncio.sleep(random.uniform(0, args.think_s))
                await send_text(args.opening)
                continue
            if last_agent_text_at is not None:
                # Agente falou e nada mais chegou: é pergunta de texto — responder.
                last_agent_text_at = None
                await asyncio.sleep(random.uniform(0, args.think_s))
                await send_text(rules.default_text)
                turns += 1
                m.count("turns")
                continue
            if first_pending:
                m.count("no_first_response")
            else:
                m.count("error:turn_no_response")
            return

        kind = msg.get("type")
        if kind == "conn.ping":
            await ws.send(json.dumps({"type": "conn.pong"}))
            continue
        if kind == "conn.session_ended":
            m.count(f"session_ended:{msg.get('reason', 'unknown')}")
            return
        if kind == "conn.error":
            m.count(f"error:server:{msg.get('code')}")
            return
        if kind not in ("msg.text", "interaction.request"):
            continue
        if kind == "msg.text" and (msg.get("author") or {}).get("role") == "customer":
            continue  # eco da própria mensagem

        if pending_since is not None:
            elapsed = _now_ms() - pending_since
            m.timing("first_response" if first_pending else "turn_response", elapsed)
            first_pending = False
            pending_since = None

        if kind == "msg.text":
            last_agent_text_at = _now_ms()
            continue

        # interaction.request — responder o menu/formulário
        last_agent_text_at = None
        await asyncio.sleep(random.uniform(0, args.think_s))
        answer = rules.match(msg.get("prompt", ""))
        interaction = msg.get("interaction") or "text"
        options = msg.get("options") or []
        fields = msg.get("fields") or []
        if interaction == "form":
            result = answer if isinstance(answer, dict) else {
                f.get("id"): rules.default_text for f in fields if f.get("id")}
        elif interaction == "checklist":
            result = [answer] if answer else ([options[0].get("id")] if options else [])
        elif options:
            ids = [o.get("id") for o in options if o.get("id")]
            ending = [i for i in ids if END_OPTION.search(i)]
            if answer in ids:
                result = answer
            elif ending and turns >= args.end_after_turns:
                # Sem isto, "primeira opção" anda em círculo num menu que volta ao início
                # (medido no demo: sac → info_plano → outra_coisa → sac…) e a sessão nunca
                # fecha pelo fluxo — mediria só sessão aberta, nunca o fechamento.
                result = ending[0]
            else:
                pool = [i for i in ids if i not in ending] or ids
                result = random.choice(pool) if args.random_option else pool[0]
        else:
            result = answer if isinstance(answer, str) else rules.default_text
        await ws.send(json.dumps({"type": "menu.submit", "menu_id": msg.get("menu_id", ""),
                                  "interaction": interaction, "result": result}))
        pending_since = _now_ms()
        turns += 1
        m.count("turns")


async def worker(idx: int, args, rules: Rules, m: Metrics, run_id: str, deadline: float) -> None:
    await asyncio.sleep(ramp_delay(idx, args.concurrency, args.ramp_s))
    seq = 0
    while time.monotonic() < deadline:
        await one_session(args, rules, m, run_id, idx * 100000 + seq)
        seq += 1
        if args.once:
            return
        await asyncio.sleep(args.pause_between_s)


async def reporter(m: Metrics, every: float, deadline: float) -> None:
    while time.monotonic() < deadline:
        await asyncio.sleep(every)
        c = m.counters
        print(f"[t+{time.time() - m.started_at:6.0f}s] ativas={m.active} "
              f"iniciadas={c['sessions_started']} autenticadas={c['sessions_authenticated']} "
              f"turnos={c['turns']} sem_1a_resposta={c['no_first_response']}", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="ws://GATEWAY:8010/ws/chat/<slug ou pool>")
    ap.add_argument("--secret", required=True, help="segredo HS256 do webchat do tenant")
    ap.add_argument("--tenant", default="tenant_demo")
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--ramp-s", type=float, default=30)
    ap.add_argument("--duration", type=float, default=300, help="segundos de carga sustentada")
    ap.add_argument("--once", action="store_true", help="cada worker faz UMA sessão (rajada)")
    ap.add_argument("--opening", default="oi")
    ap.add_argument("--answers", help="JSON [{match, answer}] como o de infra/test/_ws_chat.py")
    ap.add_argument("--default-text", default="sim")
    ap.add_argument("--random-option", action="store_true", help="opção aleatória em vez da primeira")
    ap.add_argument("--think-s", type=float, default=3.0, help="espera máxima antes de responder")
    ap.add_argument("--idle-reply-s", type=float, default=4.0)
    ap.add_argument("--greeting-wait-s", type=float, default=3.0,
                    help="espera pela saudação do agente antes de o cliente abrir")
    ap.add_argument("--end-after-turns", type=int, default=4,
                    help="a partir deste turno escolhe a opção de encerrar, se o menu tiver")
    ap.add_argument("--response-timeout-s", type=float, default=60.0)
    ap.add_argument("--timeout-s", type=float, default=15.0)
    ap.add_argument("--max-turns", type=int, default=12)
    ap.add_argument("--max-session-s", type=float, default=600)
    ap.add_argument("--pause-between-s", type=float, default=1.0)
    ap.add_argument("--report", help="arquivo JSON do relatório final")
    args = ap.parse_args()

    rules = Rules(args.answers, args.default_text)
    m = Metrics()
    run_id = uuid.uuid4().hex[:6]
    deadline = time.monotonic() + args.ramp_s + args.duration
    tasks = [asyncio.create_task(worker(i, args, rules, m, run_id, deadline))
             for i in range(args.concurrency)]
    rep = asyncio.create_task(reporter(m, 10, deadline))
    await asyncio.gather(*tasks)
    rep.cancel()
    write_report(args.report, {"driver": "webchat", "run_id": run_id, "args": vars(args),
                               **m.summary()})


if __name__ == "__main__":
    asyncio.run(main())
