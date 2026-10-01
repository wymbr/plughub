"""
_voz11_degradation_exercise.py — exercício do `probe_voz11_media_degradation.sh` (VOZ-11 b/d).

Roda DENTRO da imagem do channel-gateway, na rede do compose. O cliente fala o protocolo do widget
de demo (`webrtc-widget.html`) num pool de IA nativa que roda `skill_probe_voz39_v1`, ENTRA na sala
e publica o microfone (um tom, como o widget). Imprime `SID <id>`, `FRAMES <tipos>` e, por aviso,
`NOTICE <kind> <reason>`; o probe julga com o stream da sessão.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import struct
import time
import uuid

import jwt

POOL   = os.environ["POOL"]
TENANT = os.environ["PLUGHUB_TENANT_ID"]
SECRET = os.environ["PLUGHUB_JWT_SECRET"]
GW     = f"ws://channel-gateway:8010/ws/webrtc/{POOL}"
LK_URL = os.environ["PLUGHUB_WEBRTC_LIVEKIT_URL"]


async def _tom(source) -> None:
    from livekit import rtc
    n, i = 480, 0
    while True:
        pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * (i + k) / 48000)))
                       for k in range(n))
        i += n
        await source.capture_frame(rtc.AudioFrame(data=pcm, sample_rate=48000, num_channels=1,
                                                  samples_per_channel=n))
        await asyncio.sleep(0.01)


async def main() -> None:
    import websockets
    now = int(time.time())
    token = jwt.encode({"sub": "c-voz11-" + uuid.uuid4().hex[:6], "tenant_id": TENANT, "channel": "webrtc",
                        "iat": now, "exp": now + 900}, SECRET, algorithm="HS256")
    frames: list[dict] = []
    async with websockets.connect(GW) as ws:
        async def ate(pred, prazo: float) -> dict | None:
            fim = time.monotonic() + prazo
            while time.monotonic() < fim:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), max(0.1, fim - time.monotonic())))
                except (asyncio.TimeoutError, websockets.ConnectionClosed):
                    return None
                frames.append(m)
                if pred(m):
                    return m
            return None

        if not await ate(lambda m: m.get("type") == "conn.ready", 10):
            print("INCONCL gateway nao mandou conn.ready", flush=True)
            return
        await ws.send(json.dumps({"type": "conn.hello", "version": "1"}))
        await ws.send(json.dumps({"type": "conn.authenticate", "token": token}))
        auth = await ate(lambda m: m.get("type") == "conn.authenticated", 15)
        if not auth:
            print("INCONCL cliente nao autenticou", flush=True)
            return
        print(f"SID {auth.get('session_id', '')}", flush=True)
        ready = await ate(lambda m: m.get("type") == "webrtc.ready", 60)
        if not ready or not ready.get("token"):
            print("INCONCL sem webrtc.ready com token de midia", flush=True)
            return
        from livekit import rtc
        sala = rtc.Room()
        await asyncio.wait_for(sala.connect(LK_URL, ready["token"]), 20)
        fonte = rtc.AudioSource(48000, 1)
        trilha = rtc.LocalAudioTrack.create_audio_track("mic-cliente", fonte)
        await asyncio.wait_for(sala.local_participant.publish_track(
            trilha, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)), 20)
        tom = asyncio.create_task(_tom(fonte))
        await ate(lambda m: m.get("type") in ("webrtc.interaction", "webrtc.message")
                  and "Digite qualquer coisa" in (m.get("prompt") or m.get("text") or ""), 60)
        await ate(lambda m: False, 4)           # deixa o webhook da trilha chegar
        await ws.send(json.dumps({"type": "webrtc.message", "text": "fim"}))
        await ate(lambda m: m.get("type") == "webrtc.session_closed", 40)
        tom.cancel()
        await sala.disconnect()

    print("FRAMES " + " ".join(m.get("type", "?") for m in frames if m.get("type") != "conn.ping"), flush=True)
    for m in frames:
        if m.get("type") == "webrtc.notice":
            print(f"NOTICE {m.get('kind')} {m.get('reason')} {bool(m.get('text'))}", flush=True)


asyncio.run(main())
