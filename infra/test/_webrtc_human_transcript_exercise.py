"""
_webrtc_human_transcript_exercise.py — exercício do `probe_webrtc_human_transcript.sh` (VOZ-05 fatia 4).

Roda DENTRO da imagem do channel-gateway, na rede do compose. Uma chamada de verdade atendida por
HUMANO: o cliente fala o protocolo do widget com o gateway; o agente fala o protocolo do Console
com o mcp-server (`/agent/ws`) e pede o token pelo nginx do platform-ui; os dois entram na sala e
FALAM (frases sintetizadas pelo `speaches`). As respostas são perguntadas a quem as consome: o SFU,
o stream da sessão, o socket do Console, o socket do cliente e o ClickHouse.

  H0 CONTROLE do instrumento: o `speaches` transcreve as duas frases sem o gateway (senão INCONCL)
  H1 o OUVINTE entra na chamada de humano: `bot-…` oculto e sem publicar; a VOZ `voz-…` não entra
  H2 a fala do CLIENTE vira mensagem dele no stream, marcada `audio_transcript`
  H3 a fala do HUMANO vira mensagem DELE (`human-{sub}`), marcada, visibilidade `all`
  H4 cada falante no seu canal: as palavras de um não aparecem na transcrição do outro
  H5 o Console NÃO recebe a fala transcrita do cliente — e recebe o texto DIGITADO (controle)
  H6 o cliente NÃO recebe a fala transcrita do humano — e recebe o texto DIGITADO (controle)
  H7 o texto digitado pelo humano fica `text` no stream (o classificador distingue, não marca tudo)
  H8 ClickHouse `messages`: as duas falas com `content_type = audio_transcript`, o digitado `text`
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import unicodedata
import uuid

import httpx
import jwt
import redis.asyncio as aioredis
import websockets
from livekit import api, rtc

POOL     = os.environ["POOL"]
TENANT   = os.environ["PLUGHUB_TENANT_ID"]
CSEC     = os.environ["PLUGHUB_JWT_SECRET"]
ASEC     = os.environ["PLUGHUB_AUTH_JWT_SECRET"]
LK_URL   = os.environ["PLUGHUB_WEBRTC_LIVEKIT_URL"]
LK_KEY   = os.environ["PLUGHUB_WEBRTC_LIVEKIT_API_KEY"]
LK_SEC   = os.environ["PLUGHUB_WEBRTC_LIVEKIT_API_SECRET"]
SPEACHES = os.environ.get("PLUGHUB_WEBRTC_SPEACHES_URL", "http://speaches:8000")
TTS_MODEL = os.environ.get("PLUGHUB_WEBRTC_TTS_MODEL", "")
TTS_VOICE = os.environ.get("PLUGHUB_WEBRTC_TTS_VOICE", "")
STT_MODEL = os.environ.get("PLUGHUB_WEBRTC_STT_MODEL", "")
CH       = "http://clickhouse:8123/?user=plughub&password=plughub&database=plughub_demo"
UI       = "http://platform-ui:5174"

FRASE_CLIENTE = "Eu quero falar sobre a minha fatura de energia."
FRASE_AGENTE  = "Vou verificar o seu cadastro agora mesmo."
P_CLIENTE = {"fatura", "energia"}
P_AGENTE  = {"verificar", "cadastro"}


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


def norm(t: str) -> set[str]:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return set("".join(c for c in t if c.isalnum() or c.isspace()).split())


def ctoken(sub: str) -> str:
    n = int(time.time())
    return jwt.encode({"sub": sub, "tenant_id": TENANT, "channel": "webrtc", "iat": n, "exp": n + 900},
                      CSEC, algorithm="HS256")


def atoken(sub: str) -> str:
    n = int(time.time())
    mc = {"agent_assist": {"atender": {"access": "read_write", "scope": [f"pool:{POOL}"]}}}
    return jwt.encode({"sub": sub, "tenant_id": TENANT, "module_config": mc, "iat": n, "exp": n + 900},
                      ASEC, algorithm="HS256")


async def recv_json(ws, secs: float):
    return json.loads(await asyncio.wait_for(ws.recv(), secs))


async def tts(http: httpx.AsyncClient, frase: str) -> bytes:
    r = await http.post(f"{SPEACHES}/v1/audio/speech", json={
        "model": TTS_MODEL, "input": frase, "voice": TTS_VOICE, "response_format": "pcm", "sample_rate": 48000})
    r.raise_for_status()
    return r.content


async def stt(http: httpx.AsyncClient, pcm48: bytes) -> str:
    import io, wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000); w.writeframes(pcm48)
    r = await http.post(f"{SPEACHES}/v1/audio/transcriptions",
                        files={"file": ("f.wav", buf.getvalue(), "audio/wav")},
                        data={"model": STT_MODEL, "language": "pt"})
    r.raise_for_status()
    return r.json().get("text", "")


async def falar(source: rtc.AudioSource, pcm48: bytes, silencio_s: float) -> None:
    passo = 480 * 2
    pcm48 = pcm48 + b"\x00" * int(48000 * 2 * silencio_s)
    t0 = time.monotonic()
    for i, off in enumerate(range(0, len(pcm48) - passo + 1, passo)):
        await source.capture_frame(rtc.AudioFrame(data=pcm48[off:off + passo], sample_rate=48000,
                                                  num_channels=1, samples_per_channel=480))
        atraso = t0 + (i + 1) * 0.01 - time.monotonic()
        if atraso > 0:
            await asyncio.sleep(atraso)


async def participante(token: str, nome: str) -> tuple[rtc.Room, rtc.AudioSource]:
    room = rtc.Room()
    await asyncio.wait_for(room.connect(LK_URL, token), 25)
    source = rtc.AudioSource(48000, 1)
    track = rtc.LocalAudioTrack.create_audio_track(nome, source)
    await asyncio.wait_for(room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)), 20)
    return room, source


async def ouvir(ws, caixa: list, ping: bool) -> None:
    while True:
        try:
            caixa.append(await recv_json(ws, 5))
        except asyncio.TimeoutError:
            if ping:
                await ws.send(json.dumps({"type": "pong"}))
        except Exception:
            return


async def falas_no_stream(r, sid: str) -> list[dict]:
    out = []
    for _id, f in await r.xrange(f"session:{sid}:stream"):
        if f.get("type") != "message":
            continue
        try:
            p = json.loads(f.get("payload") or "{}")
        except Exception:
            continue
        c = p.get("content") if isinstance(p.get("content"), dict) else {}
        out.append({"author_id": f.get("author_id"), "author_role": f.get("author_role"),
                    "visibility": f.get("visibility"), "type": c.get("type"), "text": c.get("text") or p.get("text") or ""})
    return out


async def main() -> None:
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    user = "u-voz05h-" + uuid.uuid4().hex[:6]
    instance = f"human-{user}"
    sid = ""
    rooms: list[rtc.Room] = []
    tarefas: list[asyncio.Task] = []
    try:
        async with httpx.AsyncClient(timeout=120) as http:
            pcm_c, pcm_a = await tts(http, FRASE_CLIENTE), await tts(http, FRASE_AGENTE)
            ctl_c, ctl_a = await stt(http, pcm_c), await stt(http, pcm_a)
            if not (P_CLIENTE <= norm(ctl_c) and P_AGENTE <= norm(ctl_a)):
                emit("INCONCL", "H0", f"speaches nao transcreve as frases sem o gateway: {ctl_c!r} / {ctl_a!r}")
                return
            emit("OK", "H0", f"CONTROLE speaches: {ctl_c!r} / {ctl_a!r}")

            async with websockets.connect(f"ws://channel-gateway:8010/ws/webrtc/{POOL}") as cws:
                await recv_json(cws, 10)
                await cws.send(json.dumps({"type": "conn.hello", "version": "1"}))
                await cws.send(json.dumps({"type": "conn.authenticate", "token": ctoken("c-" + user)}))
                sid = (await recv_json(cws, 10)).get("session_id", "")
                print(f"SID {sid}", flush=True)
                url = (f"ws://mcp-server-plughub:3100/agent/ws?pool={POOL}&user_id={user}"
                       f"&user_login={user}@probe.local&max_concurrent=1")
                async with websockets.connect(url, subprotocols=["plughub.bearer", atoken(user)]) as aws:
                    assigned = None
                    fim = time.monotonic() + 40
                    while time.monotonic() < fim and assigned is None:
                        try:
                            m = await recv_json(aws, 5)
                        except asyncio.TimeoutError:
                            await aws.send(json.dumps({"type": "pong"}))
                            continue
                        if m.get("type") == "conversation.assigned" and m.get("session_id") == sid:
                            assigned = m
                    if assigned is None:
                        emit("INCONCL", "H1", "o humano nao recebeu o contato — nada a medir")
                        return

                    tok = None
                    for _ in range(30):
                        resp = await http.get(f"{UI}/webrtc/token/{sid}?role=agent",
                                              headers={"Authorization": f"Bearer {atoken(user)}"})
                        if resp.status_code == 200:
                            tok = resp.json()
                            break
                        await asyncio.sleep(0.5)
                    ready = None
                    fim = time.monotonic() + 15
                    while time.monotonic() < fim and ready is None:
                        try:
                            m = await recv_json(cws, 2)
                        except asyncio.TimeoutError:
                            continue
                        if m.get("type") in ("webrtc.ready", "webrtc.media") and m.get("token"):
                            ready = m
                    if not (tok and ready):
                        emit("INCONCL", "H1", f"sem token do agente ({bool(tok)}) ou do cliente ({bool(ready)})")
                        return
                    sala_a, fonte_a = await participante(tok["token"], "mic-agente")
                    sala_c, fonte_c = await participante(ready["token"], "mic-cliente")
                    rooms += [sala_a, sala_c]

                    caixa_console: list[dict] = []
                    caixa_cliente: list[dict] = []
                    tarefas.append(asyncio.ensure_future(ouvir(aws, caixa_console, True)))
                    tarefas.append(asyncio.ensure_future(ouvir(cws, caixa_cliente, False)))

                    # H1 — ouvinte na sala, oculto e mudo; voz ausente
                    ouvinte, voz = None, "?"
                    async with api.LiveKitAPI(LK_URL, LK_KEY, LK_SEC) as lk:
                        for _ in range(30):
                            try:
                                ouvinte = await lk.room.get_participant(api.RoomParticipantIdentity(
                                    room=ready["room_name"], identity=f"bot-{sid[:8]}"))
                                break
                            except Exception:
                                await asyncio.sleep(0.5)
                        await asyncio.sleep(1.0)          # tempo para o ouvinte assinar as trilhas
                        ps = await lk.room.list_participants(api.ListParticipantsRequest(room=ready["room_name"]))
                        voz = [p.identity for p in ps.participants if p.identity.startswith("voz-")]
                    ok1 = (ouvinte is not None and ouvinte.permission.hidden and not ouvinte.permission.can_publish
                           and not voz)
                    emit("OK" if ok1 else "FALHA", "H1",
                         f"ouvinte bot-{sid[:8]} na sala={ouvinte is not None} "
                         f"hidden={getattr(getattr(ouvinte, 'permission', None), 'hidden', None)} "
                         f"publica={getattr(getattr(ouvinte, 'permission', None), 'can_publish', None)}; voz={voz}")

                    # as duas falas, em sequência (cada uma no seu microfone)
                    await falar(fonte_c, pcm_c, 1.5)
                    await falar(fonte_a, pcm_a, 1.5)

                    cli = hum = None
                    fim = time.monotonic() + 30
                    while time.monotonic() < fim and not (cli and hum):
                        for e in await falas_no_stream(r, sid):
                            if e["type"] != "audio_transcript":
                                continue
                            if e["author_role"] == "customer" and P_CLIENTE <= norm(e["text"]):
                                cli = e
                            if e["author_id"] == instance and P_AGENTE <= norm(e["text"]):
                                hum = e
                        await asyncio.sleep(0.5)
                    falas = [e for e in await falas_no_stream(r, sid) if e["type"] == "audio_transcript"]
                    emit("OK" if cli else "FALHA", "H2",
                         f"fala do cliente no stream: {cli!r} (falas marcadas: {falas})")
                    ok3 = bool(hum and hum["author_role"] == "primary" and json.loads(hum["visibility"]) == "all")
                    emit("OK" if ok3 else "FALHA", "H3", f"fala do humano no stream: {hum!r}")
                    mist_c = [e["text"] for e in falas if e["author_role"] == "customer" and norm(e["text"]) & P_AGENTE]
                    mist_h = [e["text"] for e in falas if e["author_id"] == instance and norm(e["text"]) & P_CLIENTE]
                    emit("OK" if (cli and hum and not mist_c and not mist_h) else "FALHA", "H4",
                         f"cada um no seu canal: palavras do humano no cliente={mist_c} · do cliente no humano={mist_h}")

                    # controles digitados, nos dois sentidos
                    dig_c = "voz05h-cliente-" + uuid.uuid4().hex[:8]
                    dig_a = "voz05h-agente-" + uuid.uuid4().hex[:8]
                    await cws.send(json.dumps({"type": "webrtc.message", "text": dig_c}))
                    await aws.send(json.dumps({"type": "message.text", "session_id": sid, "text": dig_a}))
                    fim = time.monotonic() + 15
                    while time.monotonic() < fim:
                        if (any(dig_c in json.dumps(m) for m in caixa_console)
                                and any(dig_a in json.dumps(m) for m in caixa_cliente)):
                            break
                        await asyncio.sleep(0.3)
                    await asyncio.sleep(1.0)

                    console_digitado = any(dig_c in json.dumps(m) for m in caixa_console)
                    console_fala = [m for m in caixa_console
                                    if m.get("type") == "message.text" and norm(m.get("text", "")) & P_CLIENTE]
                    emit("OK" if (console_digitado and not console_fala) else "FALHA", "H5",
                         f"Console: recebeu o digitado do cliente={console_digitado}; falas transcritas recebidas={console_fala}")
                    cliente_digitado = any(dig_a in json.dumps(m) for m in caixa_cliente)
                    cliente_fala = [m for m in caixa_cliente
                                    if m.get("type") == "webrtc.message" and norm(m.get("text", "")) & P_AGENTE]
                    emit("OK" if (cliente_digitado and not cliente_fala) else "FALHA", "H6",
                         f"cliente: recebeu o digitado do agente={cliente_digitado}; falas do humano recebidas={cliente_fala}")
                    dig_stream = [e for e in await falas_no_stream(r, sid) if dig_a in e["text"]]
                    emit("OK" if (dig_stream and all(e["type"] == "text" for e in dig_stream)) else "FALHA", "H7",
                         f"digitado do humano no stream: {dig_stream}")

                    # H8 — ClickHouse (o consumidor do analytics tem atraso)
                    linhas = []
                    fim = time.monotonic() + 45
                    q = ("SELECT author_role, author_id, content_type, content FROM messages FINAL "
                         f"WHERE session_id = '{sid}' FORMAT JSONEachRow")
                    while time.monotonic() < fim:
                        try:
                            resp = await http.post(CH, content=q)
                            linhas = [json.loads(l) for l in resp.text.splitlines() if l.strip().startswith("{")]
                        except Exception as exc:
                            linhas = [{"erro": str(exc)}]
                        tipos = {(l.get("author_id") == instance, l.get("content_type")) for l in linhas
                                 if norm(l.get("content") or "") & (P_CLIENTE | P_AGENTE) or dig_a in (l.get("content") or "")}
                        if {(False, "audio_transcript"), (True, "audio_transcript"), (True, "text")} <= tipos:
                            break
                        await asyncio.sleep(2)
                    def tipo_de(pred):
                        return sorted({l.get("content_type") for l in linhas if pred(l)})
                    tc = tipo_de(lambda l: l.get("author_role") == "customer" and norm(l.get("content") or "") & P_CLIENTE)
                    th = tipo_de(lambda l: l.get("author_id") == instance and norm(l.get("content") or "") & P_AGENTE)
                    td = tipo_de(lambda l: dig_a in (l.get("content") or ""))
                    ok8 = tc == ["audio_transcript"] and th == ["audio_transcript"] and td == ["text"]
                    emit("OK" if ok8 else "FALHA", "H8",
                         f"ClickHouse content_type: fala do cliente={tc} · fala do humano={th} · digitado do humano={td}")

                    for t in tarefas:
                        t.cancel()
                    for room in rooms:
                        try:
                            await asyncio.wait_for(room.disconnect(), 10)
                        except Exception:
                            pass
                await cws.send(json.dumps({"type": "webrtc.hangup"}))
        closed = gone = False
        for _ in range(30):
            closed = bool(await r.exists(f"session:{sid}:closed"))
            if closed:
                break
            await asyncio.sleep(0.5)
        for _ in range(30):
            gone = not await r.exists(f"{TENANT}:instance:{instance}") and \
                not await r.sismember(f"{TENANT}:pool:{POOL}:instances", instance)
            if gone:
                break
            await asyncio.sleep(0.5)
        emit("OK" if (closed and gone) else "FALHA", "LIMPEZA",
             f"sessao {sid} fechada={closed}; instancia {instance} removida={gone}")
    finally:
        await r.aclose()


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
