"""
_agent_ws_credential_exercise.py — exercício do `probe_agent_ws_credential.sh` (CAP-19).

Roda DENTRO da imagem do channel-gateway, na rede do compose. Fala com o `/agent/ws` do
mcp-server como o Console fala — e como um estranho falaria.

  W1 sem credencial → recusado, e nenhuma instância nasce para o `user_id` da query
  W2 JWT com assinatura errada → recusado
  W3 JWT válido SEM `agent_assist.atender` → recusado
  W4 `atender` recortado a OUTRO pool → recusado
  W5 `sub` do token ≠ `user_id` da query → recusado, sem instância para o `user_id` pedido
  W6 CONTROLE POSITIVO: token do Console, recortado ao pool → aceito, instância `human-{sub}` nasce
  W7 `?session_id=` de sessão em que o agente NÃO está: não lê o que é publicado para ela e não
     escreve no stream dela — com o controle W7b: sessão em que ele ESTÁ anexado, escreve
  W8 pela borda (nginx do platform-ui, `/agent-ws`) sem credencial → recusado
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

import jwt
import redis.asyncio as aioredis
import websockets

POOL   = os.environ["POOL"]
OUTRO  = os.environ.get("OTHER_POOL", "probe_voz04_webrtc")
TENANT = os.environ["PLUGHUB_TENANT_ID"]
SECRET = os.environ["PLUGHUB_AUTH_JWT_SECRET"]
MCP    = "ws://mcp-server-plughub:3100/agent/ws"
EDGE   = "ws://platform-ui:5174/agent-ws"
GRACE  = 2.5


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


def token(sub: str, scope: list[str] | None = None, *, grant: bool = True, secret: str = SECRET) -> str:
    n = int(time.time())
    mc = {"agent_assist": {"atender": {"access": "read_write", "scope": scope or []}}} if grant else \
         {"contacts": {"visualizar": {"access": "read_only", "scope": []}}}
    return jwt.encode({"sub": sub, "tenant_id": TENANT, "module_config": mc, "iat": n, "exp": n + 600},
                      secret, algorithm="HS256")


def url(base: str, user: str, **extra: str) -> str:
    q = f"pool={POOL}&user_id={user}&user_login={user}@probe.local&max_concurrent=1"
    for k, v in extra.items():
        q += f"&{k}={v}"
    return f"{base}?{q}"


async def tentar(u: str, tok: str | None, *, fica: float = 1.2):
    """(aceito, detalhe, ws|None). Aceito = recebeu `connection.accepted` e seguiu aberto."""
    kw = {"subprotocols": ["plughub.bearer", tok]} if tok else {}
    try:
        ws = await websockets.connect(u, open_timeout=8, **kw)
    except websockets.exceptions.InvalidStatus as exc:
        return False, f"http {exc.response.status_code}", None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:60]}", None
    try:
        primeira = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if primeira.get("type") != "connection.accepted":
            await ws.close()
            return False, f"primeira mensagem {primeira.get('type')!r}", None
        await asyncio.sleep(fica)
        if ws.close_code is not None:
            return False, f"fechado {ws.close_code} {ws.close_reason!r}", None
        return True, "aceito", ws
    except websockets.exceptions.ConnectionClosed as exc:
        return False, f"fechado {exc.rcvd.code if exc.rcvd else '?'} {exc.rcvd.reason if exc.rcvd else ''!r}", None


async def instancia(r, user: str) -> bool:
    return bool(await r.sismember(f"{TENANT}:pool:{POOL}:instances", f"human-{user}")) or \
        bool(await r.exists(f"{TENANT}:instance:human-{user}"))


async def fechar(ws) -> None:
    if ws is not None:
        await ws.close()
    await asyncio.sleep(GRACE + 1.5)   # AGH-01: ninguém entra no pool dentro da janela do outro


async def main() -> None:
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    usados: list[str] = []
    novo = lambda: usados.append("u-cap19-" + uuid.uuid4().hex[:6]) or usados[-1]
    lixo: list[str] = []
    try:
        u = novo()
        ok, det, ws = await tentar(url(MCP, u), None)
        inst = await instancia(r, u)
        emit("OK" if not ok and not inst else "FALHA", "W1", f"sem credencial: {det}; instancia_nasceu={inst}")
        await fechar(ws)

        u = novo()
        ok, det, ws = await tentar(url(MCP, u), token(u, secret="outro-segredo-qualquer-32-chars!!"))
        emit("OK" if not ok else "FALHA", "W2", f"assinatura errada: {det}; instancia_nasceu={await instancia(r, u)}")
        await fechar(ws)

        u = novo()
        ok, det, ws = await tentar(url(MCP, u), token(u, grant=False))
        emit("OK" if not ok else "FALHA", "W3", f"sem agent_assist.atender: {det}; instancia_nasceu={await instancia(r, u)}")
        await fechar(ws)

        u = novo()
        ok, det, ws = await tentar(url(MCP, u), token(u, [f"pool:{OUTRO}"]))
        emit("OK" if not ok else "FALHA", "W4", f"atender so em {OUTRO}: {det}; instancia_nasceu={await instancia(r, u)}")
        await fechar(ws)

        u, outro_sub = novo(), novo()
        ok, det, ws = await tentar(url(MCP, u), token(outro_sub, [f"pool:{POOL}"]))
        inst = await instancia(r, u)
        emit("OK" if not ok and not inst else "FALHA", "W5",
             f"token de {outro_sub} pedindo user_id={u}: {det}; instancia_do_pedido_nasceu={inst}")
        await fechar(ws)

        # W6 — controle positivo
        u = novo()
        ok, det, ws = await tentar(url(MCP, u), token(u, [f"pool:{POOL}"]), fica=2.0)
        inst = await instancia(r, u)
        emit("OK" if ok and inst else "FALHA", "W6", f"token do Console recortado ao pool: {det}; instancia human-{u}={inst}")
        await fechar(ws)

        # W7 — sessão em que o agente NÃO está / W7b — sessão em que está
        u = novo()
        alheia, minha = str(uuid.uuid4()), str(uuid.uuid4())
        lixo += [f"session:{alheia}:stream", f"session:{minha}:stream", f"session:{minha}:human_agents"]
        # Fixture da decisão, não do produto: pertencimento é o que o bridge escreve ao atribuir.
        await r.sadd(f"session:{minha}:human_agents", f"human-{u}")
        tok = token(u, [f"pool:{POOL}"])
        ok_a, det_a, wa = await tentar(url(MCP, u, session_id=alheia), tok)
        marca = uuid.uuid4().hex
        leu = escreveu = False
        if ok_a:
            await r.publish(f"agent:events:{alheia}", json.dumps({"type": "message.text", "session_id": alheia, "text": marca}))
            try:
                while True:
                    m = await asyncio.wait_for(wa.recv(), 2)
                    leu = leu or marca in m
            except Exception:
                pass
            await wa.send(json.dumps({"type": "message.text", "session_id": alheia, "text": "cap19-" + marca}))
            await asyncio.sleep(2)
            escreveu = await r.xlen(f"session:{alheia}:stream") > 0
        emit("OK" if not leu and not escreveu else "FALHA", "W7",
             f"session_id alheio ({det_a}): leu_eventos={leu} escreveu_no_stream={escreveu}")
        await fechar(wa)
        ok_m, det_m, wm = await tentar(url(MCP, u, session_id=minha), tok)
        escreveu_m = False
        if ok_m:
            await wm.send(json.dumps({"type": "message.text", "session_id": minha, "text": "cap19-controle"}))
            await asyncio.sleep(2)
            escreveu_m = await r.xlen(f"session:{minha}:stream") > 0
        emit("OK" if escreveu_m else "FALHA", "W7b", f"controle: sessao em que esta anexado ({det_m}) escreve={escreveu_m}")
        await fechar(wm)

        u = novo()
        ok, det, ws = await tentar(url(EDGE, u), None)
        emit("OK" if not ok else "FALHA", "W8", f"pela borda 5174 sem credencial: {det}; instancia_nasceu={await instancia(r, u)}")
        await fechar(ws)

        # W9 — controle do W8: o MESMO caminho com a credencial do Console abre. Sem ele, um nginx
        # que descartasse o subprotocolo deixaria o W8 verde e o Console inteiro fora do ar.
        u = novo()
        ok, det, ws = await tentar(url(EDGE, u), token(u, [f"pool:{POOL}"]), fica=2.0)
        inst = await instancia(r, u)
        emit("OK" if ok and inst else "FALHA", "W9", f"pela borda 5174 COM credencial: {det}; instancia human-{u}={inst}")
        await fechar(ws)

        # Limpeza pelo produto: quem ficou instância sai conectando com o próprio token e fechando.
        for u in usados:
            if await instancia(r, u):
                _, _, ws = await tentar(url(MCP, u), token(u, [f"pool:{POOL}"]), fica=0.5)
                await fechar(ws)
        for k in lixo:
            await r.delete(k)
        restos = [u for u in usados if await instancia(r, u)]
        emit("OK" if not restos else "FALHA", "LIMPEZA", f"instancias do probe restantes: {restos}")
    finally:
        await r.aclose()


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
