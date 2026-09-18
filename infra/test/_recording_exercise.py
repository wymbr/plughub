"""VOZ-06 — a chamada é GRAVADA quando o pool pede, em partes, sem o bloco mascarado, com a recusa
honrada? Roda numa imagem do channel-gateway, na rede do compose (SDK, Redis, store, speaches).

MODE=grava     o cliente ACEITA (tecla 1). Esperado: DUAS partes guardadas como `call_recording`
               — antes e depois do PIN mascarado —, cada uma com a voz da plataforma DENTRO
               (transcrita pelo mesmo serviço de fala), nenhuma com o pedido do PIN, a retenção
               da classe carimbada, e a porta pública de anexos respondendo 404 para elas.
MODE=recusa    o cliente RECUSA (tecla 2) — o FLUXO grava `core.contact.recording_opt_out`. Esperado:
               a parte em curso DESCARTADA, nenhuma guardada, nenhuma nova depois do PIN.
MODE=controle  pool SEM `recording`. Esperado: nenhum evento de gravação.

Saída: `OK|FALHA|INCONCL|INFO <ramo> <texto>` e `SID <id>` para o shell.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import unicodedata
from datetime import datetime, timezone

import asyncpg
import httpx
import redis.asyncio as aioredis

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _sip_inbound_exercise import _stream_txt, menu_no_ar, sessao_do_chamador  # noqa: E402
from _sip_ua import SipUA  # noqa: E402

MODE = os.environ.get("MODE", "grava")
DNIS = os.environ["DNIS"]
ANI = os.environ["ANI"]
POOL = os.environ.get("POOL", "")
TENANT = os.environ.get("PLUGHUB_TENANT_ID", "tenant_demo")
SIP_HOST = os.environ.get("SIP_HOST", "livekit-sip")
SIP_USER = os.environ.get("SIP_USER", "plughub_demo")
SIP_PASS = os.environ.get("SIP_PASS", "")
REDIS_URL = os.environ.get("PLUGHUB_REDIS_URL", "redis://redis:6379")
SPEACHES = os.environ.get("PLUGHUB_WEBRTC_SPEACHES_URL", "http://speaches:8000")
STT_MODEL = os.environ.get("PLUGHUB_WEBRTC_STT_MODEL", "")
TTS_MODEL = os.environ.get("PLUGHUB_WEBRTC_TTS_MODEL", "")
TTS_VOICE = os.environ.get("PLUGHUB_WEBRTC_TTS_VOICE", "")
# A palavra que SÓ o pedido do PIN tem. "PIN" sozinho não serve: medido em 2026-09-18, a transcrição
# pode perdê-lo ("Digite e termine com o jogo da velha") — e o Whisper também ALUCINA a última frase
# no silêncio do fim do arquivo, então a frase comum aos menus não distingue nada.
PIN_PROMPT = "Senha confidencial. Digite o PIN e termine com jogo da velha."
PIN_MARCA = "confidencial"
GW = os.environ.get("GW_URL", "http://channel-gateway:8010")
PIN = "7391"
RETENCAO_ESPERADA = int(os.environ.get("RETENCAO_DIAS", "30"))


def emit(st: str, ramo: str, txt: str) -> None:
    print(f"{st} {ramo} {txt}", flush=True)


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


async def marcador(rd, sid: str, alvo: str, limite_s: float) -> bool:
    prazo = time.monotonic() + limite_s
    while time.monotonic() < prazo:
        if alvo in await _stream_txt(rd, sid):
            return True
        await asyncio.sleep(0.5)
    return False


async def eventos_de_gravacao(rd, sid: str, espera_s: float, parar_quando=None) -> list[dict]:
    """Os `recording.*` do stream; espera até `parar_quando(evts)` ou o prazo — a parte final é
    guardada DEPOIS do fim da chamada (o egress leva segundos para fechar o arquivo)."""
    prazo = time.monotonic() + espera_s
    while True:
        evts = [c for _, c in await rd.xrange(f"session:{sid}:stream", "-", "+")
                if str(c.get("type", "")).startswith("recording.")]
        if (parar_quando and parar_quando(evts)) or time.monotonic() >= prazo:
            return evts
        await asyncio.sleep(1.0)


async def sintetiza_wav(texto: str) -> bytes:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{SPEACHES}/v1/audio/speech", json={
            "model": TTS_MODEL, "input": texto, "voice": TTS_VOICE, "response_format": "wav"})
    r.raise_for_status()
    return r.content


async def transcreve(dados: bytes, nome: str = "parte.ogg", mime: str = "audio/ogg") -> str:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{SPEACHES}/v1/audio/transcriptions",
                         data={"model": STT_MODEL, "language": "pt"},
                         files={"file": (nome, dados, mime)})
    r.raise_for_status()
    return r.json().get("text", "")


async def chamada(rd) -> tuple[str, SipUA | None]:
    ua = SipUA(SIP_HOST, user=SIP_USER, senha=SIP_PASS, ani=ANI, log=lambda m: None)
    t0 = time.monotonic()
    ch = await ua.ligar(DNIS, espera_atender_s=45)
    if ch.status_final != 200:
        emit("INCONCL", "C0", f"chamada NAO atendida ({ch.status_final} {ch.motivo}) — nada medido")
        await ua.desligar()
        return "", None
    if ch.te_pt is None:
        emit("INCONCL", "C0", "sem telephone-event negociado — as teclas do roteiro nao chegariam")
        await ua.desligar()
        return "", None
    sid = await sessao_do_chamador(rd, ANI, t0)
    print(f"SID {sid}", flush=True)
    return sid, ua


async def roteiro(rd, ua, sid: str, tecla_gravacao: str) -> bool:
    """O cliente responde o que o fluxo pergunta. Devolve se chegou ao fim."""
    if not await menu_no_ar(rd, ua, sid, "Tecle 1 para aceitar"):
        emit("FALHA", "C1", "o menu de consentimento nao chegou em 40 s")
        return False
    ua.teclar(tecla_gravacao)
    esperado = "false" if tecla_gravacao == "1" else "true"
    if not await marcador(rd, sid, f"rec-optout={esperado}", 30):
        emit("FALHA", "C1", f"a escolha {tecla_gravacao} nao virou rec-optout={esperado}")
        return False
    if not await menu_no_ar(rd, ua, sid, "Digite cinco"):
        emit("FALHA", "C2", "o menu de espera nao chegou")
        return False
    await asyncio.sleep(3.0)          # a parte 1 grava a sala com o prompt que acabou de tocar
    ua.teclar("5#")
    if not await menu_no_ar(rd, ua, sid, "Senha confidencial"):
        emit("FALHA", "C3", "o menu mascarado nao chegou")
        return False
    ua.teclar(PIN + "#")
    if not await marcador(rd, sid, "rec-pin-recebido", 30):
        emit("FALHA", "C3", "o PIN nao foi recebido pelo fluxo")
        return False
    if not await menu_no_ar(rd, ua, sid, "digite oito"):
        emit("FALHA", "C4", "o menu final nao chegou")
        return False
    await asyncio.sleep(3.0)          # a parte 2 grava a sala com o prompt final
    ua.teclar("8#")
    if not await marcador(rd, sid, "rec-fim", 40):
        emit("FALHA", "C4", "o fluxo nao chegou ao fim")
        return False
    await ua.esperar_bye(40)
    return True


async def grava(rd) -> None:
    sid, ua = await chamada(rd)
    if not sid:
        return
    fim = await roteiro(rd, ua, sid, "1")
    await ua.desligar()
    if not fim:
        return
    evts = await eventos_de_gravacao(
        rd, sid, 90, lambda e: sum(1 for x in e if x["type"] in ("recording.completed", "recording.failed")) >= 2)
    done = sorted((e for e in evts if e["type"] == "recording.completed"), key=lambda e: int(e["part"]))
    ruins = [e for e in evts if e["type"] in ("recording.failed", "recording.skipped", "recording.discarded")]
    emit("OK" if len(done) == 2 and not ruins else "FALHA", "G1",
         f"{len(done)} parte(s) guardada(s) (esperado 2: antes e depois do PIN); outros eventos: "
         f"{[(e['type'], e.get('reason', '')) for e in ruins] or '-'}")
    if not done:
        return
    from plughub_channel_gateway.config import Settings
    from plughub_channel_gateway.main import _create_attachment_store
    s = Settings()
    pool = await asyncpg.create_pool(s.database_url, min_size=1, max_size=2)
    store = _create_attachment_store(s, pool)
    textos = []
    try:
        for e in done:
            fid = e["file_id"]
            meta = await store.resolve(file_id=fid, tenant_id=TENANT)
            if meta is None:
                emit("FALHA", "G2", f"parte {e['part']}: file_id {fid} NAO existe no store")
                continue
            dias = (meta.expires_at - datetime.now(timezone.utc)).total_seconds() / 86400
            ok_meta = (meta.artifact_class == "call_recording" and meta.mime_type == "audio/ogg"
                       and abs(dias - RETENCAO_ESPERADA) < 1 and e.get("pools") == POOL)
            emit("OK" if ok_meta else "FALHA", "G2",
                 f"parte {e['part']}: classe={meta.artifact_class} mime={meta.mime_type} expira em "
                 f"{dias:.1f} d (esperado {RETENCAO_ESPERADA}) pools={e.get('pools')} "
                 f"{e.get('duration_ms')} ms {e.get('size_bytes')} bytes")
            dados = b"".join([c async for c in await store.stream_bytes(file_id=fid, tenant_id=TENANT)])
            try:
                txt = await transcreve(dados)
            except Exception as exc:  # noqa: BLE001
                emit("INCONCL", "G3", f"parte {e['part']}: transcricao indisponivel ({exc}) — conteudo nao medido")
                txt = None
            textos.append((e["part"], txt))
            async with httpx.AsyncClient(timeout=10) as c:
                st = (await c.get(f"{GW}/webchat/v1/attachments/{fid}")).status_code
            emit("OK" if st == 404 else "FALHA", "G5",
                 f"parte {e['part']}: porta publica de anexos respondeu {st} (esperado 404)")
    finally:
        await pool.close()
    # G3 — o CONTEÚDO: a voz da plataforma está dentro, e o pedido do PIN em lugar nenhum
    medidos = [(p, t) for p, t in textos if t is not None]
    if medidos:
        emit("INFO", "G3", " | ".join(f"parte {p}: {t[:140]!r}" for p, t in medidos))
        tem_voz = [p for p, t in medidos if len(_norm(t).split()) >= 3]
        emit("OK" if len(tem_voz) == len(medidos) else "FALHA", "G3",
             f"partes com fala da chamada dentro: {tem_voz} de {[p for p, _ in medidos]} "
             f"(controle de que o arquivo nao e silencio)")
        # CONTROLE do G4: o mesmo pedido, sintetizado pela mesma voz e transcrito pelo mesmo
        # serviço, TEM a palavra — sem isto, "a palavra não está na gravação" poderia ser só surdez
        try:
            ctrl = await transcreve(await sintetiza_wav(PIN_PROMPT), "ctrl.wav", "audio/wav")
        except Exception as exc:  # noqa: BLE001
            ctrl = None
            emit("INCONCL", "G4", f"controle do pedido do PIN nao sintetizado/transcrito ({exc})")
        if ctrl is not None and PIN_MARCA not in _norm(ctrl):
            emit("INCONCL", "G4", f"o STT nao reconhece '{PIN_MARCA}' nem no controle ({ctrl!r}) — nada medido")
        elif ctrl is not None:
            pin_prompt = [p for p, t in medidos if PIN_MARCA in _norm(t)]
            pin_valor = [p for p, t in medidos if PIN in t.replace(" ", "")]
            emit("OK" if not pin_prompt and not pin_valor else "FALHA", "G4",
                 f"o bloco mascarado NAO esta em parte nenhuma: pedido do PIN em {pin_prompt or '-'}, "
                 f"valor em {pin_valor or '-'} (controle: o pedido sintetizado transcreve {ctrl!r})")


async def recusa(rd) -> None:
    sid, ua = await chamada(rd)
    if not sid:
        return
    fim = await roteiro(rd, ua, sid, "2")
    await ua.desligar()
    if not fim:
        return
    evts = await eventos_de_gravacao(rd, sid, 45, lambda e: any(x["type"] == "recording.discarded" for x in e))
    await asyncio.sleep(5)
    evts = await eventos_de_gravacao(rd, sid, 0)
    done = [e for e in evts if e["type"] == "recording.completed"]
    desc = [e for e in evts if e["type"] == "recording.discarded"]
    emit("OK" if not done and desc else "FALHA", "R1",
         f"recusa pelo fluxo: {len(done)} parte(s) guardada(s) (esperado 0), descartadas={len(desc)} "
         f"({[e.get('reason') for e in desc]})")
    raw = await rd.hget(f"{TENANT}:ctx:{sid}", "core.contact.recording_opt_out")
    emit("INFO", "R1", f"a tag que o fluxo gravou: {raw}")


async def controle(rd) -> None:
    sid, ua = await chamada(rd)
    if not sid:
        return
    fim = await roteiro(rd, ua, sid, "1")
    await ua.desligar()
    if not fim:
        return
    evts = await eventos_de_gravacao(rd, sid, 15)
    emit("OK" if not evts else "FALHA", "N1",
         f"pool sem `recording`: {len(evts)} evento(s) de gravacao (esperado 0) "
         f"{[e['type'] for e in evts] or ''}")


async def main() -> None:
    rd = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await {"grava": grava, "recusa": recusa, "controle": controle}[MODE](rd)
    finally:
        await rd.aclose()


asyncio.run(main())
