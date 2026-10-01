"""
_voz28_call_upload.py — corpo do `probe_voz28_call_upload.sh` (VOZ-28). Roda DENTRO do container do
channel-gateway (usa `websockets`, `redis` e o segredo de webchat que ele já tem).

O cliente de um contato de CHAT abre a chamada (`/ws/call`, WCH-01) e, com ela de pé, sobe um
arquivo de verdade pelo caminho de anexos do chat: `upload.request` → `upload.ready` → POST binário
→ `upload.committed` → `msg.document`. Mede:

  C0 a chamada abriu (`webrtc.ready` no `/ws/call`) — sem ela não se mede nada (INCONCL)
  U1 `upload.ready` com `file_id` e `upload_url`
  U2 POST 204 e `upload.committed` no WebSocket do CHAT, com o `file_id`
  U3 o `msg.document` vira MENSAGEM do stream: indicador `[Anexo: …]` e `content.attachment` com
     o `file_id` e o link (antes da VOZ-28 o bridge descartava `media` e este ramo ficava vermelho)
  U4 o arquivo servido devolve os MESMOS bytes
  U5 a chamada segue de pé depois do upload (o `/ws/call` responde ping, sem encerramento)
  N1 CONTROLE: PDF declarado com bytes que não são PDF → 415 (a validação de conteúdo roda)

Saída: 0 verde · 1 alguma falha · 2 não mediu.
"""
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request

import jwt
import redis.asyncio as aioredis
import websockets

GW = "ws://localhost:8010"
HTTP = "http://localhost:8010"
SECRET = os.environ["PLUGHUB_JWT_SECRET"]
TENANT = os.environ["PLUGHUB_TENANT_ID"]
POOL = os.environ["POOL"]
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n" + b"voz28-" + str(time.time()).encode()


def tok(sub):
    now = int(time.time())
    return jwt.encode({"sub": sub, "tenant_id": TENANT, "iat": now, "exp": now + 600},
                      SECRET, algorithm="HS256")


async def recv_until(ws, types, timeout=15):
    fim = time.time() + timeout
    while time.time() < fim:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.1, fim - time.time())))
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            return None
        if m.get("type") in types:
            return m
    return None


def post(url, data):
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def interno(url):
    """O `upload_url`/`url` é o endereço PÚBLICO do gateway; aqui dentro, o mesmo caminho em localhost."""
    i = url.find("/webchat/")
    return HTTP + url[i:] if i >= 0 else url


async def reserva(chat, nome, mime, tamanho):
    await chat.send(json.dumps({"type": "upload.request", "id": nome, "file_name": nome,
                                "mime_type": mime, "size_bytes": tamanho}))
    return await recv_until(chat, {"upload.ready", "conn.error"})


async def main():
    r = aioredis.from_url(os.environ.get("PLUGHUB_REDIS_URL", "redis://redis:6379"), decode_responses=True)
    ok = falha = 0

    def veredito(cond, txt):
        nonlocal ok, falha
        print(("  OK     " if cond else "  FALHA  ") + txt, flush=True)
        ok, falha = ok + bool(cond), falha + (not cond)

    sub = f"voz28-{int(time.time())}"
    token = tok(sub)
    chat = await websockets.connect(f"{GW}/ws/chat/{POOL}")
    await recv_until(chat, {"conn.hello"})
    await chat.send(json.dumps({"type": "conn.authenticate", "token": token}))
    m = await recv_until(chat, {"conn.authenticated"})
    if not m:
        print("  INCONCL o chat nao autenticou")
        return 2
    sid = m["session_id"]
    print(f"chat aberto session={sid} pool={POOL}", flush=True)
    await asyncio.sleep(4)          # roteamento para a IA

    call = await websockets.connect(f"{GW}/ws/call")
    await recv_until(call, {"conn.ready"})
    await call.send(json.dumps({"type": "conn.hello", "version": "1"}))
    await call.send(json.dumps({"type": "conn.authenticate", "token": token, "session_id": sid}))
    ready = await recv_until(call, {"webrtc.ready", "conn.error"}, timeout=40)
    if not ready or ready.get("type") != "webrtc.ready":
        print(f"  INCONCL C0 a chamada nao abriu: {ready}")
        return 2
    print(f"  OK     C0 chamada aberta publish={ready.get('publish')}", flush=True)

    # ── upload com a chamada de pé ───────────────────────────────────────────
    up = await reserva(chat, "voz28.pdf", "application/pdf", len(PDF))
    veredito(bool(up and up.get("type") == "upload.ready" and up.get("file_id") and up.get("upload_url")),
             f"U1 upload.ready com slot ({(up or {}).get('type')}, file_id={(up or {}).get('file_id')})")
    if not up or up.get("type") != "upload.ready":
        return 1
    fid = up["file_id"]
    st = await asyncio.to_thread(post, interno(up["upload_url"]), PDF)
    com = await recv_until(chat, {"upload.committed"}, timeout=10)
    veredito(st == 204 and bool(com) and com.get("file_id") == fid,
             f"U2 POST {st} e upload.committed no chat ({(com or {}).get('content_type')})")
    await chat.send(json.dumps({"type": "msg.document", "file_id": fid, "caption": "voz28"}))
    achou = None
    for _ in range(20):
        for _id, campos in await r.xrange(f"session:{sid}:stream", "-", "+"):
            if campos.get("type") != "message":
                continue
            try:
                c = json.loads(campos.get("payload") or "{}").get("content") or {}
            except ValueError:
                continue
            if (c.get("attachment") or {}).get("file_id") == fid:
                achou = c
        if achou:
            break
        await asyncio.sleep(0.5)
    att = (achou or {}).get("attachment") or {}
    veredito(bool(achou) and str(achou.get("text", "")).startswith("[Anexo:") and bool(att.get("url")),
             f"U3 o documento virou mensagem do stream com indicador e link "
             f"(texto={(achou or {}).get('text')!r} link={'sim' if att.get('url') else 'NAO'})")
    cst, corpo = await asyncio.to_thread(get, interno((com or {}).get("url", "")))
    veredito(cst == 200 and corpo == PDF, f"U4 o arquivo servido devolve os mesmos bytes (http {cst}, {len(corpo)} bytes)")
    try:
        pong = await call.ping()
        await asyncio.wait_for(pong, 5)
        vivo = True
    except Exception:  # noqa: BLE001
        vivo = False
    fechou = await recv_until(call, {"webrtc.session_closed", "webrtc.call_ended", "conn.error"}, timeout=2)
    veredito(vivo and fechou is None, f"U5 a chamada segue de pe depois do upload (ping={vivo}, encerramento={fechou})")

    # ── controle ─────────────────────────────────────────────────────────────
    falso = b"isto nao e um pdf " * 4
    up2 = await reserva(chat, "falso.pdf", "application/pdf", len(falso))
    st2 = await asyncio.to_thread(post, interno(up2["upload_url"]), falso) if up2 and up2.get("upload_url") else None
    veredito(st2 == 415, f"N1 PDF declarado com bytes que nao sao PDF recusado (http {st2})")

    await call.close()
    await chat.send(json.dumps({"type": "msg.text", "text": "fim"}))
    await asyncio.sleep(1)
    await chat.close()
    await r.aclose()
    return 0 if falha == 0 else 1


sys.exit(asyncio.run(main()))
