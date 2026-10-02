"""
_att08_agent_attachment.py — corpo do `probe_att08_agent_attachment.sh` (ATT-08). Roda DENTRO do
container do channel-gateway (usa `websockets`, `redis`, `jwt` e os segredos que ele já tem).

O cliente abre um contato de webchat; o atendente manda um arquivo pelo caminho do Console
(`POST /api/agent_attachment/{sid}` pela borda do platform-ui, a mesma porta que o browser usa).
Quem ATENDE é o que o bridge grava ao ativar um humano: `session:{sid}:human_agents`. A probe
coloca ali a instância do atendente de teste — é a fixture de atendimento, não o caminho medido.

  A0 o contato abriu (sem ele, nada se mede — INCONCL)
  N1 CONTROLE: quem tem o grant mas NÃO atende → 403 `not_attending`, e nada vai ao stream
  A1 quem atende → 201 com `file_id`
  A2 a mensagem vai ao stream: `content.type=document`, indicador `[Anexo: …]`, attachment sem link
  A3 o CLIENTE recebe `msg.document` no WebSocket do chat, com URL assinada
  A4 a URL assinada devolve os MESMOS bytes
  N2 CONTROLE: arquivo que não é o que declara → recusa da esteira (415) e nada novo no stream

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
EDGE = os.environ.get("EDGE", "http://platform-ui:5174")
WEBCHAT_SECRET = os.environ["PLUGHUB_JWT_SECRET"]
AUTH_SECRET = os.environ["PLUGHUB_AUTH_JWT_SECRET"]
TENANT = os.environ["PLUGHUB_TENANT_ID"]
POOL = os.environ["POOL"]
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n" + b"att08-" + str(time.time()).encode()


def tok_cliente(sub):
    now = int(time.time())
    return jwt.encode({"sub": sub, "tenant_id": TENANT, "iat": now, "exp": now + 600},
                      WEBCHAT_SECRET, algorithm="HS256")


def tok_agente(sub):
    now = int(time.time())
    return jwt.encode({"sub": sub, "tenant_id": TENANT, "iat": now, "exp": now + 600, "roles": ["operator"],
                       "module_config": {"agent_assist": {"atender": {"access": "read_write", "scope": []}}}},
                      AUTH_SECRET, algorithm="HS256")


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


def envia(sid, token, nome, mime, dados, legenda=""):
    q = urllib.parse.urlencode({"file_name": nome, "mime_type": mime, "caption": legenda})
    req = urllib.request.Request(f"{EDGE}/api/agent_attachment/{sid}?{q}", data=dados, method="POST",
                                 headers={"Content-Type": "application/octet-stream",
                                          "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def interno(url):
    i = url.find("/webchat/")
    return HTTP + url[i:] if i >= 0 else url


async def anexos_no_stream(r, sid):
    out = []
    for _id, campos in await r.xrange(f"session:{sid}:stream", "-", "+"):
        if campos.get("type") != "message":
            continue
        try:
            c = json.loads(campos.get("payload") or "{}").get("content") or {}
        except ValueError:
            continue
        if isinstance(c, dict) and c.get("attachment"):
            out.append((campos.get("author_id"), c))
    return out


async def main():
    import urllib.parse  # noqa: F401 — usado em envia()
    r = aioredis.from_url(os.environ.get("PLUGHUB_REDIS_URL", "redis://redis:6379"), decode_responses=True)
    ok = falha = 0

    def veredito(cond, txt):
        nonlocal ok, falha
        print(("  OK     " if cond else "  FALHA  ") + txt, flush=True)
        ok, falha = ok + bool(cond), falha + (not cond)

    stamp = int(time.time())
    chat = await websockets.connect(f"{GW}/ws/chat/{POOL}")
    await recv_until(chat, {"conn.hello"})
    await chat.send(json.dumps({"type": "conn.authenticate", "token": tok_cliente(f"att08-c-{stamp}")}))
    m = await recv_until(chat, {"conn.authenticated"})
    if not m:
        print("  INCONCL A0 o chat nao autenticou")
        return 2
    sid = m["session_id"]
    meta = None
    for _ in range(20):
        meta = await r.get(f"session:{sid}:meta")
        if meta:
            break
        await asyncio.sleep(0.5)
    if not meta:
        print(f"  INCONCL A0 session:{sid}:meta nao apareceu")
        return 2
    print(f"  OK     A0 contato aberto session={sid} pool={POOL}", flush=True)

    atende, outro = f"att08-a-{stamp}", f"att08-o-{stamp}"
    chave = f"session:{sid}:human_agents"
    await r.sadd(chave, f"human-{atende}")
    await r.expire(chave, 900)
    try:
        # ── N1: grant sem atender ────────────────────────────────────────────
        st, corpo = await asyncio.to_thread(envia, sid, tok_agente(outro), "x.pdf", "application/pdf", PDF)
        antes = await anexos_no_stream(r, sid)
        veredito(st == 403 and corpo.get("error") == "not_attending" and not antes,
                 f"N1 quem nao atende recebe 403 not_attending e nada vai ao stream (http {st} {corpo})")

        # ── A1..A4: quem atende ──────────────────────────────────────────────
        st, corpo = await asyncio.to_thread(envia, sid, tok_agente(atende), "contrato att08.pdf",
                                            "application/pdf", PDF, "segue o contrato")
        fid = ((corpo or {}).get("attachment") or {}).get("file_id")
        veredito(st == 201 and bool(fid), f"A1 quem atende manda: http {st} file_id={fid}")
        if st != 201:
            return 1
        achou = None
        for _ in range(20):
            for autor, c in await anexos_no_stream(r, sid):
                if c["attachment"].get("file_id") == fid:
                    achou = (autor, c)
            if achou:
                break
            await asyncio.sleep(0.3)
        autor, c = achou or ("", {})
        veredito(bool(achou) and c.get("type") == "document" and autor == f"human-{atende}"
                 and str(c.get("text", "")).startswith("[Anexo: contrato att08.pdf]")
                 and not c["attachment"].get("url") and "contrato" not in json.dumps(c["attachment"]),
                 f"A2 stream: type={c.get('type')} autor={autor} texto={c.get('text')!r} "
                 f"attachment={c.get('attachment')}")

        doc = await recv_until(chat, {"msg.document"}, timeout=15)
        url = (doc or {}).get("url", "")
        veredito(bool(doc) and doc.get("file_id") == fid and "sig=" in url and "exp=" in url,
                 f"A3 o cliente recebe msg.document com URL assinada ({(doc or {}).get('file_id')}, "
                 f"assinada={'sig=' in url})")
        cst, bytes_ = await asyncio.to_thread(get, interno(url)) if url else (0, b"")
        veredito(cst == 200 and bytes_ == PDF, f"A4 a URL assinada devolve os mesmos bytes (http {cst}, {len(bytes_)} bytes)")

        # ── N2: a esteira recusa o que não é o que declara ───────────────────
        n_antes = len(await anexos_no_stream(r, sid))
        st2, corpo2 = await asyncio.to_thread(envia, sid, tok_agente(atende), "falso.pdf",
                                              "application/pdf", b"isto nao e um pdf " * 4)
        n_depois = len(await anexos_no_stream(r, sid))
        veredito(st2 == 415 and n_depois == n_antes,
                 f"N2 bytes que nao sao PDF: http {st2} {corpo2.get('error')} e o stream nao mudou ({n_antes}→{n_depois})")
    finally:
        await r.srem(chave, f"human-{atende}")
        await chat.send(json.dumps({"type": "msg.text", "text": "fim"}))
        await asyncio.sleep(0.5)
        await chat.close()
        await r.aclose()
    return 0 if falha == 0 else 1


sys.exit(asyncio.run(main()))
