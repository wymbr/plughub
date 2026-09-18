"""
_sip_inbound_exercise.py — exercício do `probe_voz02_sip_inbound.sh` (VOZ-02, fatia 1).

Roda DENTRO da imagem do channel-gateway, na rede do compose, contra o stack VIVO: o "telefone" é o
cliente SIP mínimo (`_sip_ua.py`), que liga para o serviço SIP do SFU com G.711 e autenticação digest,
fala uma frase sintetizada pelo próprio serviço de fala e escuta o que volta. Nada do gateway é
instanciado aqui — a pergunta é se o PRODUTO, como está no ar, atende o telefone.

MODE=atende (endpoint cadastrado):
  S0 CONTROLE do instrumento: a frase do chamador é sintetizada (senão INCONCLUSIVO).
  S1 a chamada é ATENDIDA (200) — o serviço SIP só atende quando há áudio na sala, então isto já
     prova que o bot leg entrou e falou.
  S2 nasceu um contato `voice` do chamador, no pool do número discado.
  S3 o chamador OUVE a IA: energia no RTP que volta, em G.711.
  S4 a fala do chamador chega ao fluxo: o menu por voz registra `sip-m0=atendente`.
  K1 (VOZ-31) `telephone-event` negociado na resposta do serviço SIP.
  K2 (VOZ-31) teclas FORA de banda (RFC 4733) respondem o menu de teclado: `sip-m1=<código>`.
  K3 (VOZ-31) PIN MASCARADO no telefone NÃO é coletado (NIV-07), e isso não é mudo: o menu é
     RECUSADO no envio (`notification_send`, canal `voice` sem `masked_input` — o probe lê a
     linha no mcp-server), o fluxo sai pelo `on_failure` sem nunca receber o PIN, e o PIN teclado
     assim mesmo não aparece no stream — o probe confere também o log do gateway.
  S5 o fluxo encerra e a PLATAFORMA derruba a chamada (BYE chega ao telefone).
  depois, uma 2ª chamada em que o CHAMADOR desliga — o probe confere o fechamento no log.
  B1 (VOZ-31, caracterização) 3ª chamada SEM `telephone-event`, tecla como TOM no áudio: o que o
     conversor faz. É fato medido, não veredicto — é a entrada do controle (1) da NIV-07.
  INFO codec da trilha do chamador na sala (a §8 do ADR supunha relay, sem transcodificar).

MODE=recusa (endpoint REMOVIDO pelo probe antes):
  R1 a chamada NÃO é atendida e nenhum contato nasce para ela.

Saída: linhas `OK|FALHA|INCONCL|INFO <ramo> <texto>` e `SID1 <id>` / `SID2 <id>` para o shell.
"""
from __future__ import annotations

import asyncio
import audioop  # noqa: DEP — Python 3.11 da imagem
import json
import os
import re
import sys
import time

import httpx
import redis.asyncio as aioredis
from livekit import api

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _sip_ua import SipUA, tons_dtmf_pcm8k  # noqa: E402

MODE = os.environ.get("MODE", "atende")
DNIS = os.environ["DNIS"]
ANI = os.environ["ANI"]
POOL = os.environ.get("POOL", "")
SIP_HOST = os.environ.get("SIP_HOST", "livekit-sip")
SIP_USER = os.environ.get("SIP_USER", "plughub_demo")
SIP_PASS = os.environ.get("SIP_PASS", "")
REDIS_URL = os.environ.get("PLUGHUB_REDIS_URL", "redis://redis:6379")
SPEACHES = os.environ.get("PLUGHUB_WEBRTC_SPEACHES_URL", "http://speaches:8000")
TTS_MODEL = os.environ.get("PLUGHUB_WEBRTC_TTS_MODEL", "")
TTS_VOICE = os.environ.get("PLUGHUB_WEBRTC_TTS_VOICE", "")
LK_URL = os.environ.get("PLUGHUB_WEBRTC_LIVEKIT_URL", "ws://livekit:7880").replace("ws://", "http://")
LK_KEY = os.environ.get("PLUGHUB_WEBRTC_LIVEKIT_API_KEY", "")
LK_SEC = os.environ.get("PLUGHUB_WEBRTC_LIVEKIT_API_SECRET", "")
FRASE = "Atendente."
CODIGO = "4821"          # teclado claro (m1)
PIN = "5566"             # teclado MASCARADO (m2) — não pode aparecer em lugar nenhum


def emit(st: str, ramo: str, txt: str) -> None:
    print(f"{st} {ramo} {txt}", flush=True)


async def fala_8k() -> bytes:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{SPEACHES}/v1/audio/speech", json={
            "model": TTS_MODEL, "input": FRASE, "voice": TTS_VOICE, "response_format": "pcm",
            "sample_rate": 16000})
    r.raise_for_status()
    pcm16k = r.content[: len(r.content) // 2 * 2]
    pcm8k, _ = audioop.ratecv(pcm16k, 2, 1, 16000, 8000, None)
    return pcm8k


async def sessao_do_chamador(rd, ani: str, desde: float, limite_s: float = 20.0) -> str:
    prazo = time.monotonic() + limite_s
    while time.monotonic() < prazo:
        async for k in rd.scan_iter("channel:sip:room:*"):
            sid = await rd.get(k)
            if not sid or sid == "abrindo":
                continue
            raw = await rd.get(f"session:{sid}:meta")
            meta = json.loads(raw) if raw else {}
            if meta.get("customer_id") == ani:
                return sid
        await asyncio.sleep(0.5)
    return ""


async def _stream_txt(rd, sid: str) -> str:
    return "\n".join(json.dumps(c, ensure_ascii=False) for _, c in await rd.xrange(f"session:{sid}:stream", "-", "+"))


async def marcador(rd, sid: str, limite_s: float, opcoes=("sip-m0=atendente", "sip-m0=cancelar",
                                                         "sip-m0-invalido", "sip-timeout")) -> str:
    prazo = time.monotonic() + limite_s
    while time.monotonic() < prazo:
        txt = await _stream_txt(rd, sid)
        for m in opcoes:
            if m in txt:
                return m
        await asyncio.sleep(0.5)
    return ""


async def menu_no_ar(rd, ua, sid: str, prompt: str, limite_s: float = 40) -> bool:
    """Espera o menu chegar ao stream (a coleta nasce junto) e o prompt ser FALADO até o fim —
    tecla antes disso cairia em `nenhuma coleta em curso` e o ramo mediria a pressa do teste."""
    prazo = time.monotonic() + limite_s
    while time.monotonic() < prazo:
        if prompt in await _stream_txt(rd, sid):
            t = time.monotonic()
            await ua.silencio_do_outro_lado(depois_de=t - 1.0, calmo_s=1.2, limite_s=max(1.0, prazo - t))
            return True
        await asyncio.sleep(0.3)
    return False


async def codec_do_chamador(ani: str) -> str:
    if not LK_KEY:
        return "sem credencial do SFU no env"
    lk = api.LiveKitAPI(LK_URL, LK_KEY, LK_SEC)
    try:
        for r in (await lk.room.list_rooms(api.ListRoomsRequest())).rooms:
            for p in (await lk.room.list_participants(api.ListParticipantsRequest(room=r.name))).participants:
                if p.identity == f"sip_{ani}":
                    return ",".join(t.mime_type for t in p.tracks) or "(sem trilha)"
        return "(chamador nao encontrado na sala)"
    finally:
        await lk.aclose()


async def atende() -> None:
    rd = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        pcm = await fala_8k()
    except Exception as exc:
        emit("INCONCL", "S0", f"frase do chamador nao sintetizada ({type(exc).__name__}: {exc}) — nada medido")
        return
    if len(pcm) < 8000:
        emit("INCONCL", "S0", f"frase sintetizada curta demais ({len(pcm)} bytes) — nada medido")
        return
    emit("OK", "S0", f"CONTROLE: frase do chamador sintetizada ({len(pcm) / 16000:.1f} s a 8 kHz)")

    ua = SipUA(SIP_HOST, user=SIP_USER, senha=SIP_PASS, ani=ANI, log=lambda m: emit("INFO", "UA", m))
    t0 = time.monotonic()
    ch = await ua.ligar(DNIS, espera_atender_s=45)
    if ch.status_final != 200:
        emit("FALHA", "S1", f"chamada NAO atendida: respostas={ch.respostas} final={ch.status_final} ({ch.motivo})")
        await ua.desligar()
        return
    emit("OK", "S1", f"chamada ATENDIDA em {ch.atendida_em - t0:.1f} s (o servico SIP so atende com audio na sala)")
    sid = await sessao_do_chamador(rd, ANI, t0)
    print(f"SID1 {sid}", flush=True)
    if not sid:
        emit("FALHA", "S2", f"nenhum contato do chamador {ANI} no Redis")
    else:
        meta = json.loads(await rd.get(f"session:{sid}:meta") or "{}")
        ok = meta.get("channel") == "voice" and (not POOL or meta.get("pool_id") == POOL)
        emit("OK" if ok else "FALHA", "S2", f"contato session={sid} canal={meta.get('channel')} "
             f"pool={meta.get('pool_id')} (esperado voice/{POOL or '-'})")
    emit("INFO", "CODEC", f"trilha do chamador na sala: {await codec_do_chamador(ANI)} · "
         f"negociado no SIP: {'PCMU' if 'PCMU' in ch.sdp_remoto else ch.sdp_remoto[:80]}")

    calou = await ua.silencio_do_outro_lado(depois_de=ch.atendida_em, calmo_s=2.0, limite_s=40)
    ouvido = ua.energia_desde(ch.atendida_em)
    emit("OK" if ouvido >= 1.0 else "FALHA", "S3",
         f"o chamador OUVIU a IA: {ouvido:.1f} s de audio em G.711 (pts={sorted(ch.payload_types)}, "
         f"{ch.rtp_recebidos} pacotes){'' if calou else ' — a fala nao parou em 40 s'}")

    dur = ua.falar_pcm8k(pcm)
    await asyncio.sleep(dur + 0.5)
    m = await marcador(rd, sid, 40) if sid else ""
    emit("OK" if m == "sip-m0=atendente" else "FALHA", "S4",
         f"a fala do chamador chegou ao fluxo: {m or 'nenhum marcador em 40 s'}")

    # ── VOZ-31: teclas ──
    emit("OK" if ch.te_pt is not None else "FALHA", "K1",
         f"telephone-event negociado na resposta do servico SIP: PT {ch.te_pt}" if ch.te_pt is not None
         else "a resposta do servico SIP NAO aceitou telephone-event — tecla fora de banda impossivel")
    if ch.te_pt is not None and m == "sip-m0=atendente":
        if await menu_no_ar(rd, ua, sid, "Digite o codigo"):
            ua.teclar(CODIGO + "#")
            k2 = await marcador(rd, sid, 30, (f"sip-m1={CODIGO}", "sip-m1=", "sip-m1-invalido", "sip-m1-timeout"))
            emit("OK" if k2 == f"sip-m1={CODIGO}" else "FALHA", "K2",
                 f"{len(CODIGO) + 1} teclas RFC 4733 ({ua.chamada.teclas_enviadas} eventos completos) -> "
                 f"{k2 or 'nenhum marcador em 30 s'} (esperado sip-m1={CODIGO})")
        else:
            emit("FALHA", "K2", "o menu de teclado (m1) nao chegou ao stream em 40 s")
        # O chamador tecla o PIN como teclaria diante de um pedido — com ou sem menu no ar.
        await asyncio.sleep(2.0)
        ua.teclar(PIN + "#")
        print(f"PIN {PIN}", flush=True)
        caiu_cedo = await ua.esperar_bye(40)
        txt = await _stream_txt(rd, sid)
        recebido = "sip-m2-recebido" in txt
        # fronteira de dígito: o ANI do chamador, que o stream carrega, pode conter a sequência
        vazou = re.search(rf"(?<![0-9]){PIN}(?![0-9])", txt) is not None
        emit("OK" if not recebido and not vazou else "FALHA", "K3",
             f"PIN mascarado no telefone: coletado={'SIM' if recebido else 'nao'} · PIN no stream="
             f"{'SIM — VAZOU' if vazou else 'nao'} · a chamada {'caiu' if caiu_cedo else 'NAO caiu em 40 s'} "
             f"(esperado: nao coletado, sem valor, fluxo saindo — a coleta mascarada na perna SIP e a NIV-07)")
    else:
        emit("INCONCL", "K2", "sem telephone-event ou sem o m0 respondido — as teclas nao foram medidas")

    caiu = ua.chamada.bye_recebido_em is not None or await ua.esperar_bye(40)
    emit("OK" if caiu else "FALHA", "S5",
         "a plataforma encerrou e o telefone recebeu BYE" if caiu else "o fluxo acabou e a chamada NAO caiu em 40 s")
    await ua.desligar()

    # 2ª chamada: o CHAMADOR desliga
    ani2 = ANI[:-1] + ("1" if ANI[-1] != "1" else "2")
    ua2 = SipUA(SIP_HOST, user=SIP_USER, senha=SIP_PASS, ani=ani2)
    t1 = time.monotonic()
    ch2 = await ua2.ligar(DNIS, espera_atender_s=45)
    if ch2.status_final != 200:
        emit("FALHA", "H1", f"2a chamada NAO atendida: {ch2.respostas} ({ch2.motivo})")
        await ua2.desligar()
        return
    sid2 = await sessao_do_chamador(rd, ani2, t1)
    print(f"SID2 {sid2}", flush=True)
    await asyncio.sleep(2.0)
    await ua2.desligar()
    emit("INFO", "H1", f"2a chamada atendida e desligada PELO CHAMADOR (session={sid2 or '?'})")

    # 3ª chamada: telefone SEM telephone-event, a tecla vai como TOM dentro do áudio
    ani3 = ANI[:-1] + ("3" if ANI[-1] != "3" else "4")
    ua3 = SipUA(SIP_HOST, user=SIP_USER, senha=SIP_PASS, ani=ani3, dtmf_fora_de_banda=False)
    t3 = time.monotonic()
    ch3 = await ua3.ligar(DNIS, espera_atender_s=45)
    if ch3.status_final != 200:
        emit("INFO", "B1", f"sem telephone-event a chamada NAO foi atendida: {ch3.respostas} ({ch3.motivo})")
        await ua3.desligar()
        await rd.aclose()
        return
    sid3 = await sessao_do_chamador(rd, ani3, t3)
    if sid3 and await menu_no_ar(rd, ua3, sid3, "Diga atendente"):
        ua3.falar_pcm8k(tons_dtmf_pcm8k("1"))            # 1 = cancelar
        b1 = await marcador(rd, sid3, 45)
        leitura = {"sip-m0=cancelar": "o tom VIROU tecla (deteccao dentro do audio existe)",
                   "sip-timeout": "o tom foi IGNORADO (sem deteccao dentro do audio; menu saiu pelo prazo)",
                   "sip-m0-invalido": "o tom virou fala INVALIDA (chegou ao STT, nao ao teclado)"}.get(b1, "?")
        emit("INFO", "B1", f"telefone sem telephone-event (resposta com PT {ch3.te_pt}), tecla 1 como TOM -> "
             f"{b1 or 'nenhum marcador em 45 s'}: {leitura}")
    else:
        emit("INFO", "B1", f"3a chamada atendida, mas o m0 nao apareceu (session={sid3 or '?'}) — nada medido")
    await ua3.desligar()
    await rd.aclose()


async def recusa() -> None:
    rd = aioredis.from_url(REDIS_URL, decode_responses=True)
    ua = SipUA(SIP_HOST, user=SIP_USER, senha=SIP_PASS, ani=ANI)
    t0 = time.monotonic()
    ch = await ua.ligar(DNIS, espera_atender_s=25)
    sid = await sessao_do_chamador(rd, ANI, t0, limite_s=5)
    ok = ch.status_final != 200 and not sid
    emit("OK" if ok else "FALHA", "R1",
         f"numero SEM endpoint: final={ch.status_final or '-'} ({ch.motivo or 'sem motivo'}), "
         f"contato={'nenhum' if not sid else sid}")
    await ua.desligar()
    await rd.aclose()


if __name__ == "__main__":
    asyncio.run(atende() if MODE == "atende" else recusa())
