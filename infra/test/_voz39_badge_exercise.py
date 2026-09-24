"""
_voz39_badge_exercise.py — exercício do `probe_voz39_recording_badge.sh` (VOZ-39).

Roda DENTRO da imagem do channel-gateway, na rede do compose. O cliente é um WebSocket que fala o
protocolo do widget de demo (`webrtc-widget.html`), num pool de IA só-WebRTC que roda a fixture
`skill_probe_voz39_v1`. Registra TODOS os frames na ordem em que chegam e julga:

  B1 com pool que GRAVA: chega `webrtc.recording {state: recording}`, e DEPOIS do aviso de gravação
     (a mensagem de sistema que o precede) — faixa antes do aviso seria gravar sem avisar
  B2 o `stopped` chega ANTES do `webrtc.session_closed` — a faixa apaga pelo servidor, não por
     o widget adivinhar
  B3 sem estado repetido e sem `paused` (não há bloco mascarado neste fluxo)
  E1 (VOZ-44, MODE=aborta) sem mídia publicada o egress fica em STARTING (medido: a parte inteira) —
     a faixa NÃO acende; antes ela dizia "Gravando" enquanto nada gravava
  (MODE=grava) o cliente ENTRA na sala e publica um tom, como o widget com microfone: é o que faz o
  SFU confirmar o egress, e só então a faixa acende
  N1 CONTROLE, pool que NÃO grava: nenhum `webrtc.recording` — a faixa não mente para o outro lado

Uso: MODE=grava|aborta|nao_grava. Imprime `SID <id>` e as linhas de veredicto.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import jwt
import math
import struct

POOL   = os.environ["POOL"]
MODE   = os.environ["MODE"]
TENANT = os.environ["PLUGHUB_TENANT_ID"]
SECRET = os.environ["PLUGHUB_JWT_SECRET"]
GW     = f"ws://channel-gateway:8010/ws/webrtc/{POOL}"
ABORT_WAIT_S = float(os.environ.get("ABORT_WAIT_S", "45"))
LK_URL = os.environ["PLUGHUB_WEBRTC_LIVEKIT_URL"]


async def _tom(source) -> None:
    """440 Hz contínuo, em quadros de 10 ms — o microfone de um cliente que fala."""
    from livekit import rtc
    n, i = 480, 0
    while True:
        pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * (i + k) / 48000)))
                       for k in range(n))
        i += n
        await source.capture_frame(rtc.AudioFrame(data=pcm, sample_rate=48000, num_channels=1,
                                                  samples_per_channel=n))
        await asyncio.sleep(0.01)


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


async def main() -> None:
    import websockets
    now = int(time.time())
    token = jwt.encode({"sub": "c-voz39-" + uuid.uuid4().hex[:6], "tenant_id": TENANT, "channel": "webrtc",
                        "iat": now, "exp": now + 900}, SECRET, algorithm="HS256")
    frames: list[dict] = []
    async with websockets.connect(GW) as ws:
        async def recv(prazo: float) -> dict | None:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), prazo))
            except (asyncio.TimeoutError, websockets.ConnectionClosed):
                return None
            frames.append(m)
            return m

        async def ate(pred, prazo: float) -> dict | None:
            fim = time.monotonic() + prazo
            while time.monotonic() < fim:
                m = await recv(max(0.1, fim - time.monotonic()))
                if m is None:
                    return None
                if pred(m):
                    return m
            return None

        if not await ate(lambda m: m.get("type") == "conn.ready", 10):
            emit("INCONCL", "B0", "gateway nao mandou conn.ready")
            return
        await ws.send(json.dumps({"type": "conn.hello", "version": "1"}))
        await ws.send(json.dumps({"type": "conn.authenticate", "token": token}))
        auth = await ate(lambda m: m.get("type") == "conn.authenticated", 15)
        if not auth:
            emit("INCONCL", "B0", "cliente nao autenticou")
            return
        print(f"SID {auth.get('session_id', '')}", flush=True)
        sala = tom = None
        if MODE == "grava":
            ready = await ate(lambda m: m.get("type") == "webrtc.ready", 60)
            if not ready or not ready.get("token"):
                emit("INCONCL", "B0", "sem webrtc.ready com token de midia — cliente nao entra na sala")
                return
            from livekit import rtc
            sala = rtc.Room()
            await asyncio.wait_for(sala.connect(LK_URL, ready["token"]), 20)
            fonte = rtc.AudioSource(48000, 1)
            trilha = rtc.LocalAudioTrack.create_audio_track("mic-cliente", fonte)
            await asyncio.wait_for(sala.local_participant.publish_track(
                trilha, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)), 20)
            tom = asyncio.create_task(_tom(fonte))
        # o menu `text` chega como MENSAGEM (o prompt), não como `webrtc.interaction`
        menu = await ate(lambda m: m.get("type") in ("webrtc.interaction", "webrtc.message")
                         and "Digite qualquer coisa" in (m.get("prompt") or m.get("text") or ""), 60)
        if not menu:
            emit("INCONCL", "B0", "o menu do fluxo nao chegou em 60 s (sem agente de IA?)")
            return
        if MODE == "grava":
            # o aviso FALADO espera o cliente na sala (15 s) antes de a parte começar — responder
            # antes encerraria o fluxo sem gravação nenhuma
            if not await ate(lambda m: m.get("type") == "webrtc.recording", 40):
                emit("FALHA", "B1", "nenhum webrtc.recording em 40 s com o pool que grava")
                return
        else:
            await ate(lambda m: False, 20)      # a mesma janela: se fosse gravar, gravaria aqui
        if MODE == "aborta":
            # VOZ-44: sem mídia, o egress nunca confirma — a faixa não pode acender
            acendeu = await ate(lambda m: m.get("type") == "webrtc.recording" and m.get("state") == "recording",
                                ABORT_WAIT_S)
            emit("FALHA" if acendeu else "OK", "E1",
                 f"sem midia publicada, a faixa {'ACENDEU' if acendeu else 'nao acendeu'} em "
                 f"{ABORT_WAIT_S:.0f} s (egress pedido e nao confirmado)")
        await asyncio.sleep(2)                  # a parte corre um pouco com o menu na tela
        await ws.send(json.dumps({"type": "webrtc.message", "text": "fim"}))
        fechou = await ate(lambda m: m.get("type") == "webrtc.session_closed", 40)
        if tom is not None:
            tom.cancel()
        if sala is not None:
            await sala.disconnect()
        if not fechou:
            emit("INCONCL", "B0", "o contato nao fechou em 40 s depois da resposta")
            return

    tipos = [m.get("type") for m in frames]
    estados = [(i, m.get("state")) for i, m in enumerate(frames) if m.get("type") == "webrtc.recording"]
    i_fim = tipos.index("webrtc.session_closed")
    print("INFO SEQ " + " > ".join(
        f"{m.get('type')}{'=' + str(m.get('state')) if m.get('type') == 'webrtc.recording' else ''}"
        for m in frames if m.get("type") not in ("conn.ping",)), flush=True)

    if MODE == "aborta":
        return
    if MODE == "nao_grava":
        emit("OK" if not estados else "FALHA", "N1",
             f"pool sem gravacao: {len(estados)} frame(s) webrtc.recording (esperado 0)")
        return

    gravando = [i for i, s in estados if s == "recording"]
    if not gravando:
        emit("FALHA", "B1", f"nenhum webrtc.recording=recording chegou (estados: {[s for _, s in estados]})")
        return
    i_rec = gravando[0]
    aviso = [i for i, m in enumerate(frames[:i_rec])
             if m.get("type") == "webrtc.message" and m.get("author") == "system"]
    emit("OK" if aviso else "FALHA", "B1",
         f"faixa 'recording' no frame {i_rec}, depois do aviso de sistema: "
         f"{repr((frames[aviso[-1]].get('text') or '')[:70]) if aviso else 'NENHUM AVISO ANTES'}")
    parados = [i for i, s in estados if s == "stopped"]
    emit("OK" if parados and parados[-1] < i_fim else "FALHA", "B2",
         f"'stopped' antes do session_closed: stopped={parados} session_closed={i_fim}")
    seq = [s for _, s in estados]
    repetido = any(a == b for a, b in zip(seq, seq[1:]))
    emit("OK" if seq == ["recording", "stopped"] and not repetido else "FALHA", "B3",
         f"sequencia de estados {seq} (esperado ['recording', 'stopped'], sem repeticao nem paused)")


asyncio.run(main())
