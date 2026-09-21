"""
seed_sip.py — semeia os troncos SIP de entrada e suas dispatch rules no serviço SIP do SFU
(VOZ-02, fatia 1). SEED-IF-ABSENT, pela API oficial (LiveKit), como todo provisionamento da casa.

Um arquivo por tronco em `SIP_DIR` (`infra/sip/*.json`). A identidade é o `name` do tronco e o da
regra: o que já existe com esse nome NÃO é tocado — nem se o arquivo mudou. Para o arquivo valer
de novo, `SIP_SEED_RECONCILE=true` apaga e recria. Isso segue a regra da casa (§ Configuration,
seed-if-absent): quem mudar o tronco pela API não perde a mudança num restart.

A dispatch rule é SEMPRE `individual` com o prefixo de `sip_leg.SIP_ROOM_PREFIX` — importado do
gateway, nunca repetido aqui: o gateway só adota como contato a sala que tem esse prefixo, e dois
lugares escrevendo o mesmo prefixo são como ele passa a divergir sem erro.

Saída: 0 tudo semeado ou já presente · 1 algum tronco falhou · 2 SFU inalcançável no prazo
· 3 diretório SEM tronco (VOZ-41 — `SIP_SEED_ALLOW_EMPTY=true` quando a instalação não tem SIP).
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import sys
import time

from livekit import api
from livekit.protocol import sip as lsip

from plughub_channel_gateway.adapters.sip_leg import SIP_ROOM_PREFIX

URL = os.environ.get("LIVEKIT_URL", "http://livekit:7880")
KEY = os.environ.get("LIVEKIT_API_KEY", "")
SECRET = os.environ.get("LIVEKIT_API_SECRET", "")
SIP_DIR = os.environ.get("SIP_DIR", "/sip")
MAX_WAIT = float(os.environ.get("SEED_MAX_WAIT", "120"))
RECONCILE = os.environ.get("SIP_SEED_RECONCILE", "").lower() in ("1", "true", "yes")


def log(msg: str) -> None:
    print(f"[sip-seed] {msg}", flush=True)


async def _espera_sfu(lk: api.LiveKitAPI) -> bool:
    prazo = time.monotonic() + MAX_WAIT
    ultimo = ""
    while time.monotonic() < prazo:
        try:
            await lk.sip.list_sip_inbound_trunk(lsip.ListSIPInboundTrunkRequest())
            return True
        except Exception as exc:  # noqa: BLE001 — o motivo vai no log final
            ultimo = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(3)
    log(f"SFU/servico SIP nao respondeu em {MAX_WAIT:.0f}s ({ultimo}) — NADA semeado")
    return False


async def _semeia(lk: api.LiveKitAPI, caminho: str) -> bool:
    try:
        spec = json.load(open(caminho, encoding="utf-8"))
        t = spec["trunk"]
        r = spec["dispatch_rule"]
    except Exception as exc:  # noqa: BLE001
        log(f"{caminho}: arquivo ilegivel ({exc}) — tronco NAO semeado")
        return False
    if r.get("kind") != "individual":
        log(f"{caminho}: dispatch_rule.kind={r.get('kind')!r} — so `individual` existe (uma sala por chamada)")
        return False
    senha = os.environ.get(t.get("auth_password_env") or "", "") if t.get("auth_username") else ""
    if t.get("auth_username") and not senha:
        # Tronco com usuário e sem senha aceitaria qualquer um que soubesse o usuário. Recusa alto.
        log(f"{caminho}: auth_username declarado e {t.get('auth_password_env')!r} vazio no env — "
            f"tronco NAO semeado (sem senha ele ficaria aberto)")
        return False

    troncos = (await lk.sip.list_sip_inbound_trunk(lsip.ListSIPInboundTrunkRequest())).items
    regras = (await lk.sip.list_sip_dispatch_rule(lsip.ListSIPDispatchRuleRequest())).items
    existente = next((x for x in troncos if x.name == t["name"]), None)
    regra = next((x for x in regras if x.name == r["name"]), None)

    if existente and RECONCILE:
        log(f"RECONCILE: apagando tronco {t['name']} ({existente.sip_trunk_id}) para recriar do arquivo")
        if regra:
            await lk.sip.delete_sip_dispatch_rule(lsip.DeleteSIPDispatchRuleRequest(
                sip_dispatch_rule_id=regra.sip_dispatch_rule_id))
            regra = None
        await lk.sip.delete_sip_trunk(lsip.DeleteSIPTrunkRequest(sip_trunk_id=existente.sip_trunk_id))
        existente = None

    if existente:
        log(f"tronco {t['name']} ja existe ({existente.sip_trunk_id}, numeros={list(existente.numbers)}) "
            f"— NAO tocado (seed-if-absent)")
        trunk_id = existente.sip_trunk_id
    else:
        info = lsip.SIPInboundTrunkInfo(
            name=t["name"], numbers=list(t.get("numbers") or []),
            auth_username=t.get("auth_username", ""), auth_password=senha,
        )
        if t.get("ringing_timeout_s"):
            info.ringing_timeout.FromSeconds(int(t["ringing_timeout_s"]))
        if t.get("max_call_duration_s"):
            info.max_call_duration.FromSeconds(int(t["max_call_duration_s"]))
        criado = await lk.sip.create_sip_inbound_trunk(lsip.CreateSIPInboundTrunkRequest(trunk=info))
        trunk_id = criado.sip_trunk_id
        log(f"tronco {t['name']} CRIADO ({trunk_id}, numeros={list(info.numbers)}, "
            f"auth={'digest' if senha else 'NENHUMA'})")

    if regra:
        log(f"dispatch rule {r['name']} ja existe ({regra.sip_dispatch_rule_id}) — NAO tocada")
        prefixo = regra.rule.dispatch_rule_individual.room_prefix
        if prefixo != SIP_ROOM_PREFIX:
            # Existe, mas põe as chamadas numa sala que o gateway não adota: o telefone tocaria e
            # nenhum contato nasceria. Não se corrige sozinho (seed-if-absent) — diz.
            log(f"ATENCAO: a regra existente usa room_prefix={prefixo!r} e o gateway so adota "
                f"{SIP_ROOM_PREFIX!r} — as chamadas deste tronco NAO viram contato. "
                f"SIP_SEED_RECONCILE=true recria.")
            return False
    else:
        criada = await lk.sip.create_sip_dispatch_rule(lsip.CreateSIPDispatchRuleRequest(
            name=r["name"], trunk_ids=[trunk_id],
            rule=lsip.SIPDispatchRule(dispatch_rule_individual=lsip.SIPDispatchRuleIndividual(
                room_prefix=SIP_ROOM_PREFIX)),
        ))
        log(f"dispatch rule {r['name']} CRIADA ({criada.sip_dispatch_rule_id}, prefixo={SIP_ROOM_PREFIX})")
    return True


async def main() -> int:
    if not KEY or not SECRET:
        log("LIVEKIT_API_KEY/LIVEKIT_API_SECRET ausentes no env — NADA semeado")
        return 1
    arquivos = sorted(glob.glob(os.path.join(SIP_DIR, "*.json")))
    if not arquivos:
        # VOZ-41: diretório vazio é o bind mount que não montou, nunca "nada a fazer". O Redis do
        # SFU não persiste; sair 0 aqui deixou o SIP descartando TODA chamada em silêncio (2026-09-21).
        if os.environ.get("SIP_SEED_ALLOW_EMPTY", "").lower() == "true":
            log(f"nenhum tronco em {SIP_DIR} — SIP_SEED_ALLOW_EMPTY=true, nada semeado de propósito")
            return 0
        log(f"ERRO: nenhum tronco em {SIP_DIR} — o diretório não montou? Sem tronco, o serviço SIP "
            f"descarta toda chamada. Instalação sem SIP: SIP_SEED_ALLOW_EMPTY=true")
        return 3
    lk = api.LiveKitAPI(URL, KEY, SECRET)
    try:
        if not await _espera_sfu(lk):
            return 2
        resultados = [await _semeia(lk, a) for a in arquivos]
    finally:
        await lk.aclose()
    falhas = resultados.count(False)
    log(f"{len(resultados) - falhas} de {len(resultados)} tronco(s) ok")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
