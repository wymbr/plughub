"""
_att07_upload_limit.py — corpo do `probe_att07_upload_limit.sh` (ATT-07). Roda DENTRO do
channel-gateway, depois que o shell gravou `webchat.upload_limits_mb.image = 1` pela API e o
gateway recarregou (config.changed). Fala com o gateway EM EXECUÇÃO pelo caminho do cliente:
WebSocket do chat (`upload.request`) e POST do binário.

  C1 CONTROLE: imagem declarada pequena → `upload.ready` (o teto do tenant deixa passar)
  R1 imagem DECLARADA com 2 MB → `upload_rejected` (o reserve lê o teto do tenant)
  R2 slot declarado pequeno, corpo REAL com 2 MB → 413 (o POST lê o teto do tenant)
  C2 CONTROLE: o corpo pequeno no slot do C1 → 204
  P1 PDF declarado com 2 MB → `upload.ready` (o teto de imagem não vaza para outro tipo)

Saída: 0 verde · 1 alguma falha · 2 não mediu.
"""
import asyncio
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

import jwt
import websockets
from PIL import Image

GW = "ws://localhost:8010"
HTTP = "http://localhost:8010"
SECRET = os.environ["PLUGHUB_JWT_SECRET"]
TENANT = os.environ["PLUGHUB_TENANT_ID"]
POOL = os.environ["POOL"]
MB = 1024 * 1024
_buf = io.BytesIO()
Image.new("RGB", (8, 8), (10, 20, 200)).save(_buf, format="PNG")
PNG = _buf.getvalue()


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
    i = url.find("/webchat/")
    url = HTTP + url[i:] if i >= 0 else url
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


async def main() -> int:
    falhas = []

    def ok(ramo, cond, txt):
        print(f"  {'✓' if cond else '✗'} {ramo} {txt}", flush=True)
        if not cond:
            falhas.append(ramo)

    chat = await websockets.connect(f"{GW}/ws/chat/{POOL}", max_size=None)
    await recv_until(chat, {"conn.hello"})
    await chat.send(json.dumps({"type": "conn.authenticate", "token": tok(f"att07-{int(time.time())}")}))
    if not await recv_until(chat, {"conn.authenticated"}):
        print("  INCONCL o chat nao autenticou")
        return 2

    async def reserva(nome, mime, tamanho):
        await chat.send(json.dumps({"type": "upload.request", "id": nome, "file_name": nome,
                                    "mime_type": mime, "size_bytes": tamanho}))
        return await recv_until(chat, {"upload.ready", "conn.error"}) or {}

    pequeno = await reserva("pequena.png", "image/png", len(PNG))
    ok("C1", pequeno.get("type") == "upload.ready", f"imagem pequena declarada → {pequeno.get('type')}")
    if pequeno.get("type") != "upload.ready":
        print("  INCONCL o controle não reservou — as recusas abaixo não provariam nada")
        return 2

    grande = await reserva("grande.png", "image/png", 2 * MB)
    ok("R1", grande.get("type") == "conn.error" and grande.get("code") == "upload_rejected",
       f"imagem DECLARADA com 2 MB → {grande.get('type')} {grande.get('code', '')} {grande.get('message', '')[:60]}")

    outro = await reserva("outra.png", "image/png", len(PNG))
    if outro.get("type") == "upload.ready":
        st = await asyncio.to_thread(post, outro["upload_url"], b"\x00" * (2 * MB))
        ok("R2", st == 413, f"slot pequeno, corpo REAL de 2 MB → {st}")
    else:
        ok("R2", False, f"não reservou o slot do R2: {outro}")

    st = await asyncio.to_thread(post, pequeno["upload_url"], PNG)
    ok("C2", st == 204, f"corpo pequeno no slot do controle → {st}")

    pdf = await reserva("doc.pdf", "application/pdf", 2 * MB)
    ok("P1", pdf.get("type") == "upload.ready", f"PDF declarado com 2 MB → {pdf.get('type')}")

    await chat.close()
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
