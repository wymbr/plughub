"""
_webrtc_masked_keypad_exercise.py — exercício do `probe_webrtc_masked_keypad.sh` (VOZ-05, fatia A).

Roda DENTRO da imagem do channel-gateway, na rede do compose. O cliente é um WebSocket que fala
o protocolo do widget de demo, num pool de IA só-WebRTC que roda a fixture `skill_probe_masked_keypad_v1` (formulário
com `email` claro e `senha`/`codigo_2fa` mascarados), e também um participante LiveKit de verdade
que FALA durante a coleta.

  K1 o menu chega ao cliente PLANO, com `interaction=form` e `masked_fields` ⊇ {senha, codigo_2fa}
  K2 texto livre durante a coleta é RECUSADO (`masked_capture_active`)
  K3 a senha INVÁLIDA chega ao fluxo pelo campo protegido: o fluxo responde PROBE_PIN_INVALIDO
     e reabre o formulário (sem o valor chegar, o desfecho seria PROBE_FALHOU ou timeout)
  K4 a senha VÁLIDA chega: PROBE_PIN_VALIDO — os dois desfechos juntos provam que o fluxo LEU o
     valor, e não só que recebeu alguma resposta
  V1 a fala durante a coleta (depois da folga local, portanto decidida pelo `menu:waiting` do
     motor) foi TRANSCRITA e DESCARTADA — o log do gateway é conferido pelo .sh (V1)
  H* (no .sh) histórico, stream e logs sem os valores e sem a fala

Imprime `SID <id>` e `T_FALA <iso>` para o .sh julgar o que mora fora do container.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

import httpx
import jwt
import redis.asyncio as aioredis
from livekit import rtc

POOL   = os.environ["POOL"]
TENANT = os.environ["PLUGHUB_TENANT_ID"]
SECRET = os.environ["PLUGHUB_JWT_SECRET"]
LK_URL = os.environ["PLUGHUB_WEBRTC_LIVEKIT_URL"]
SPEACHES = os.environ.get("PLUGHUB_WEBRTC_SPEACHES_URL", "http://speaches:8000")
TTS_MODEL = os.environ.get("PLUGHUB_WEBRTC_TTS_MODEL", "")
TTS_VOICE = os.environ.get("PLUGHUB_WEBRTC_TTS_VOICE", "")
GW     = f"ws://channel-gateway:8010/ws/webrtc/{POOL}"
EMAIL        = os.environ["EMAIL"]
SENHA_ERRADA = os.environ["SENHA_ERRADA"]
SENHA_CERTA  = os.environ["SENHA_CERTA"]
CODIGO_2FA   = os.environ["CODIGO_2FA"]
TEXTO_LIVRE  = os.environ["TEXTO_LIVRE"]
FRASE_FALADA = os.environ["FRASE_FALADA"]


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


class Cliente:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.recebidas: list[dict] = []

    async def espera(self, pred, prazo_s: float) -> dict | None:
        fim = time.monotonic() + prazo_s
        for m in self.recebidas:
            if pred(m):
                self.recebidas.remove(m)
                return m
        while time.monotonic() < fim:
            try:
                m = json.loads(await asyncio.wait_for(self.ws.recv(), max(0.1, fim - time.monotonic())))
            except asyncio.TimeoutError:
                break
            if pred(m):
                return m
            self.recebidas.append(m)
        return None

    async def manda(self, m: dict) -> None:
        await self.ws.send(json.dumps(m))


def _form(m: dict) -> bool:
    return m.get("type") == "webrtc.interaction" and m.get("interaction") == "form"


def _texto(m: dict, trecho: str) -> bool:
    return m.get("type") == "webrtc.message" and trecho in (m.get("text") or "")


async def _falar(source: rtc.AudioSource, pcm48: bytes) -> None:
    passo = 480 * 2
    pcm48 = pcm48 + b"\x00" * int(48000 * 2 * 1.5)
    t0 = time.monotonic()
    for i, off in enumerate(range(0, len(pcm48) - passo + 1, passo)):
        frame = rtc.AudioFrame(data=pcm48[off:off + passo], sample_rate=48000, num_channels=1, samples_per_channel=480)
        await source.capture_frame(frame)
        atraso = t0 + (i + 1) * 0.01 - time.monotonic()
        if atraso > 0:
            await asyncio.sleep(atraso)


async def main() -> None:
    sub = "c-voz05m-" + uuid.uuid4().hex[:6]
    now = int(time.time())
    token = jwt.encode({"sub": sub, "tenant_id": TENANT, "channel": "webrtc", "iat": now, "exp": now + 900},
                       SECRET, algorithm="HS256")
    room = None
    async with websockets_connect(GW) as ws:
        c = Cliente(ws)
        pronto = await c.espera(lambda m: m.get("type") == "conn.ready", 10)
        if not pronto:
            emit("FALHA", "K1", "gateway nao mandou conn.ready")
            return
        await c.manda({"type": "conn.hello", "version": "1"})
        await c.manda({"type": "conn.authenticate", "token": token})
        auth = await c.espera(lambda m: m.get("type") == "conn.authenticated", 15)
        if not auth:
            emit("FALHA", "K1", "cliente nao autenticou")
            return
        sid = auth.get("session_id", "")
        print(f"SID {sid}", flush=True)
        try:
            ready = await c.espera(lambda m: m.get("type") == "webrtc.ready", 60)
            if not ready:
                emit("FALHA", "K1", "nenhum agente de IA atendeu em 60 s (webrtc.ready ausente)")
                return

            # ── K1 ────────────────────────────────────────────────────────────
            form = await c.espera(_form, 60)
            campos = set((form or {}).get("masked_fields") or [])
            ids = {f.get("id") for f in (form or {}).get("fields") or []}
            emit("OK" if (form and {"senha", "codigo_2fa"} <= campos and {"email", "senha", "codigo_2fa"} <= ids)
                 else "FALHA", "K1",
                 f"formulario chegou plano: menu_id={bool((form or {}).get('menu_id'))} campos={sorted(i for i in ids if i)} "
                 f"masked_fields={sorted(campos)} (chaves do frame={sorted(form or {})})")
            if not form:
                return
            t_form = time.monotonic()

            # ── K2 ────────────────────────────────────────────────────────────
            await c.manda({"type": "webrtc.message", "text": TEXTO_LIVRE})
            recusa = await c.espera(lambda m: m.get("type") == "conn.error", 5)
            emit("OK" if (recusa or {}).get("code") == "masked_capture_active" else "FALHA", "K2",
                 f"texto livre durante a coleta recusado: {(recusa or {}).get('code')!r}")

            # ── V1: fala durante a coleta, depois da folga local ──────────────
            if not (TTS_MODEL and ready.get("token")):
                emit("INCONCL", "V1", "sem modelo de TTS no env do gateway ou sem token de midia — fala nao exercida")
            else:
                async with httpx.AsyncClient(timeout=120) as http:
                    rr = await http.post(f"{SPEACHES}/v1/audio/speech", json={
                        "model": TTS_MODEL, "input": FRASE_FALADA, "voice": TTS_VOICE,
                        "response_format": "pcm", "sample_rate": 48000})
                    rr.raise_for_status()
                    voz = rr.content
                room = rtc.Room()
                await asyncio.wait_for(room.connect(LK_URL, ready["token"]), 20)
                src = rtc.AudioSource(48000, 1)
                trilha = rtc.LocalAudioTrack.create_audio_track("mic-cliente", src)
                await asyncio.wait_for(room.local_participant.publish_track(
                    trilha, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)), 20)
                await asyncio.sleep(3)   # o bot é participante OCULTO: o cliente não o vê; V1 prova no log
                espera = 7.0 - (time.monotonic() - t_form)   # passa da folga local (5 s)
                if espera > 0:
                    await asyncio.sleep(espera)
                print(f"T_FALA {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}", flush=True)
                await _falar(src, voz)
                await asyncio.sleep(4)
                emit("INFO", "V1", "fala emitida durante a coleta mascarada; o .sh confere o descarte no log")
                aviso = await c.espera(lambda m: _texto(m, "preenchimento protegido"), 4)
                emit("OK" if aviso else "FALHA", "V2",
                     f"cliente avisado de que a fala nao e registrada: {bool(aviso)}")

            # ── K3 ────────────────────────────────────────────────────────────
            await c.manda({"type": "webrtc.menu_submit", "menu_id": form["menu_id"], "interaction": "form",
                           "result": {"email": EMAIL, "senha": SENHA_ERRADA, "codigo_2fa": CODIGO_2FA}})
            invalida = await c.espera(lambda m: _texto(m, "PROBE_PIN_INVALIDO"), 30)
            form2 = await c.espera(_form, 30) if invalida else None
            emit("OK" if (invalida and form2) else "FALHA", "K3",
                 f"senha invalida chegou ao fluxo: 'PROBE_PIN_INVALIDO'={bool(invalida)} form reaberto={bool(form2)}")
            if not form2:
                return

            # ── K4 ────────────────────────────────────────────────────────────
            await c.manda({"type": "webrtc.menu_submit", "menu_id": form2["menu_id"], "interaction": "form",
                           "result": {"email": EMAIL, "senha": SENHA_CERTA, "codigo_2fa": CODIGO_2FA}})
            ok = await c.espera(lambda m: _texto(m, "PROBE_PIN_VALIDO"), 30)
            emit("OK" if ok else "FALHA", "K4", f"senha valida chegou ao fluxo: 'PROBE_PIN_VALIDO'={bool(ok)}")

            # ── H1/H2 antes do fechamento (o stream não sobrevive a ele) ─────
            await asyncio.sleep(2)
            r = aioredis.from_url("redis://redis:6379", decode_responses=True)
            try:
                hist = [json.loads(x) for x in await r.lrange(f"session:{sid}:messages", 0, -1)]
                stream = await r.xrange(f"session:{sid}:stream")
            finally:
                await r.aclose()
            cli = [h.get("text", "") for h in hist if h.get("author") == "customer"]
            forms = [t for t in cli if t.startswith("[Formulário: ")]
            redig = [t for t in forms if EMAIL in t and '"senha": "••••••"' in t and '"codigo_2fa": "••••••"' in t]
            emit("OK" if (len(forms) >= 2 and len(redig) == len(forms)) else "FALHA", "H1",
                 f"historico: {len(forms)} submissao(oes), {len(redig)} redigida(s) com e-mail visivel "
                 f"(esperado >=2, todas)")
            outras = [t for t in cli if not t.startswith("[Formulário: ")]
            emit("OK" if not outras else "FALHA", "H1",
                 f"historico: linhas do cliente fora das submissoes = {len(outras)} (texto livre e fala fora)")
            superficie = json.dumps(hist, ensure_ascii=False) + json.dumps(stream, ensure_ascii=False)
            segredos = [SENHA_ERRADA, SENHA_CERTA, CODIGO_2FA, TEXTO_LIVRE.split()[-1], "secreta"]
            vazou = [v for v in segredos if v.lower() in superficie.lower()]
            emit("OK" if (stream and not vazou) else "FALHA", "H2",
                 f"historico ({len(hist)}) + stream ({len(stream)} entradas) sem valor protegido, texto livre "
                 f"ou fala (vazou={vazou})")
        finally:
            if room is not None:
                try:
                    await asyncio.wait_for(room.disconnect(), 10)
                except Exception:
                    pass
            try:
                await c.manda({"type": "webrtc.hangup"})
                await asyncio.sleep(1)
            except Exception:
                pass


def websockets_connect(url: str):
    import websockets
    return websockets.connect(url)


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
