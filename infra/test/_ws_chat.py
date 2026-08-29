"""
Cliente WEBCHAT scriptado — dirige um contato real pelo caminho de produção.

Por que existe: alguns fatos só nascem quando um CLIENTE conversa (o agente de
entrada resolve identidade, acha pendência e ramifica). Não há como exercitar esse
ramo por REST: o `POST /v1/channels/webhook/pool/{id}` cria sessão de WORKFLOW, não
de contato, e é justamente a diferença que está sob teste.

Roda DENTRO do container channel-gateway — é lá que `websockets` é dependência e é
de lá que o segredo do JWT é resolvido pela MESMA via do servidor (per-tenant no
Redis, com fallback para settings.jwt_secret). Assinar com um segredo hardcoded
faria o probe testar o segredo, não o fluxo.

Protocolo (webchat.py / survey_web.py):
  server → conn.hello                        cliente → conn.authenticate {token}
  server → conn.authenticated {session_id}
  server → interaction.request {menu_id, interaction, prompt, options[], fields[]}
  cliente → menu.submit {menu_id, interaction, result}

Uso:
  python3 /tmp/_ws_chat.py <tenant> <pool> <contact_id> <script_json> [hold_s]

`script_json` = lista de regras aplicadas NA ORDEM em que os prompts chegam:
  [{"match": "CPF", "answer": "529..."},
   {"match": "já tem um pedido", "answer": "consultar"},
   {"match": "dados de acesso",  "answer": {"email": "x@y", "senha": "s"}}]
`answer` escalar responde `text`/`button`/`list`; `answer` DICT responde
`interaction: form` (um valor por `field.id`) — ver a nota das TRÊS superfícies.
`match` é substring case-insensitive do prompt. Regra sem prompt correspondente NÃO
é usada — e o cliente imprime `UNMATCHED`, para o veredicto do shell distinguir
"o fluxo não chegou lá" de "o fluxo chegou e a resposta estava errada".

⚠️ **Duas superfícies de pergunta, não uma.** `menu` com `interaction: text` e sem
`masked_fields` NÃO vira `interaction.request`: o engine o entrega como mensagem comum
e espera `msg.text` de volta (`skill-flow-engine/src/steps/menu.ts:167-168`). As regras
são casadas contra as DUAS, e a resposta sai no formato de cada uma. Medido em
2026-08-25: a v1 deste cliente só respondia `interaction.request` e ficava muda na
pergunta do CPF — o probe acusou o fluxo por um defeito do instrumento.

Saída (uma linha por fato, para o shell ramificar):
  AUTHENTICATED session_id=<sid>
  PROMPT <menu_id> | <prompt truncado>
  ANSWER <menu_id> | <resposta>
  UNMATCHED <menu_id> | <prompt truncado>
  NOTIFY <texto truncado>
  CLOSED_BY_SERVER
  DONE authed=<0|1> answered=<n> unmatched=<n>
Código de saída: 0 = autenticou e nenhuma regra ficou sem uso · 3 = não autenticou
· 4 = autenticou mas houve prompt sem regra (INCONCLUSIVO do lado do shell).
"""
import asyncio
import json
import sys
import time
import uuid

import jwt as pyjwt
import websockets


async def resolve_secret(tenant_id: str) -> str:
    """Mesma resolução do servidor: per-tenant no Redis, senão settings.jwt_secret."""
    from plughub_channel_gateway.config import get_settings

    settings = get_settings()
    secret = settings.jwt_secret
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        per_tenant = await r.get(f"{tenant_id}:config:webchat:jwt_secret")
        if per_tenant:
            secret = per_tenant
        await r.aclose()
    except Exception:
        pass  # fallback é o caminho normal em single-tenant
    return secret


async def main() -> int:
    tenant     = sys.argv[1]
    pool       = sys.argv[2]
    contact_id = sys.argv[3]
    rules      = json.loads(sys.argv[4])
    hold_s     = float(sys.argv[5]) if len(sys.argv) > 5 else 60.0

    secret = await resolve_secret(tenant)
    token = pyjwt.encode(
        {"sub": contact_id, "tenant_id": tenant, "exp": int(time.time()) + 3600},
        secret,
        algorithm="HS256",
    )
    if not isinstance(token, str):
        token = token.decode()

    url       = f"ws://localhost:8010/ws/chat/{pool}"
    authed    = False
    answered  = 0
    unmatched = 0
    used      = [False] * len(rules)

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
                    await ws.send(json.dumps({"type": "conn.authenticate", "token": token}))
                elif mtype == "conn.authenticated":
                    authed = True
                    print(f"AUTHENTICATED session_id={msg.get('session_id', '')}", flush=True)
                elif mtype == "conn.ping":
                    await ws.send(json.dumps({"type": "conn.pong"}))
                elif mtype == "interaction.request":
                    prompt  = (msg.get("prompt") or "")
                    menu_id = msg.get("menu_id") or ""
                    print(f"PROMPT {menu_id} | {prompt[:110]}", flush=True)
                    hit = None
                    for i, rule in enumerate(rules):
                        if used[i]:
                            continue
                        if rule.get("match", "").lower() in prompt.lower():
                            hit = (i, rule)
                            break
                    if hit is None:
                        unmatched += 1
                        print(f"UNMATCHED {menu_id} | {prompt[:110]}", flush=True)
                        continue
                    i, rule = hit
                    used[i] = True
                    answered += 1
                    # ⚠️ TERCEIRA superfície de resposta: `interaction: form`.
                    # `WsMenuSubmit.result` é `str | list[str] | dict`
                    # (channel-gateway/models.py:61), e um form de N campos só é
                    # respondível como DICT {field_id: valor}. A v2 deste cliente
                    # mandava sempre string, então nenhum `interaction: form`
                    # jamais foi exercido por probe — inclusive o de masking POR
                    # CAMPO, que é onde nasceu o vazamento de 2026-08-29.
                    # `answer` dict/list viaja como está; string segue como antes.
                    _ans = rule["answer"]
                    _shown = _ans if isinstance(_ans, str) else json.dumps(_ans, ensure_ascii=False)
                    print(f"ANSWER {menu_id} | {_shown}", flush=True)
                    await ws.send(json.dumps({
                        "type":        "menu.submit",
                        "menu_id":     menu_id,
                        "interaction": msg.get("interaction") or "text",
                        "result":      _ans,
                    }))
                elif mtype in ("msg.text", "message.text"):
                    # ⚠️ Menu com `interaction: text` (sem masked_fields) NÃO vira
                    # `interaction.request` — o engine o entrega como MENSAGEM COMUM e
                    # espera texto livre de volta (`menu.ts:167-168`). Um cliente que só
                    # soubesse responder a `interaction.request` ficaria mudo na pergunta
                    # mais banal do fluxo, e o probe acusaria o fluxo. As regras é que
                    # protegem contra responder à saudação: sem `match`, nada é enviado.
                    body = (msg.get("text") or msg.get("content") or "")
                    if not body:
                        continue
                    print(f"NOTIFY {body[:110]}", flush=True)
                    hit = None
                    for i, rule in enumerate(rules):
                        if used[i]:
                            continue
                        if rule.get("match", "").lower() in body.lower():
                            hit = (i, rule)
                            break
                    if hit is None:
                        continue
                    i, rule = hit
                    used[i] = True
                    answered += 1
                    print(f"ANSWER <free-text> | {rule['answer']}", flush=True)
                    await ws.send(json.dumps({"type": "msg.text", "text": rule["answer"]}))
                elif mtype == "conn.session_closed":
                    print("CLOSED_BY_SERVER", flush=True)
                    break
    except Exception as exc:  # noqa: BLE001 — o shell precisa do motivo, não do traceback
        print(f"WS_ERROR {type(exc).__name__}: {exc}", flush=True)

    print(f"DONE authed={int(authed)} answered={answered} unmatched={unmatched}", flush=True)
    if not authed:
        return 3
    return 4 if unmatched else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
