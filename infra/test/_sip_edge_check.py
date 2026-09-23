"""
_sip_edge_check.py — ajudante do probe_sip_edge_surface.sh (VOZ-32). Roda DENTRO da rede do
compose, com a imagem do gateway (SDK LiveKit) e `infra/test` montado.

  python _sip_edge_check.py trunks
      → uma linha por tronco de ENTRADA no SFU: `TRUNK <nome> auth=<sim|nao> numeros=<a,b>`
  python _sip_edge_check.py invite <dnis> <usuario> <senha>
      → `FINAL <status> respostas=<lista>` · `FINAL none …` sem resposta final no prazo.
        Chamada ATENDIDA (200) é desligada na hora: o probe mede a autenticação, não o contato.
  python _sip_edge_check.py mk-open <nome> <numero> · rm <nome>
      → SÓ a bateria de mutação: cria/apaga um tronco SEM autenticação (população do ramo C).
"""
from __future__ import annotations

import asyncio
import os
import sys

from _sip_ua import SipUA


async def _trunks() -> int:
    from livekit import api
    from livekit.protocol import sip as lsip
    lk = api.LiveKitAPI(os.environ["LK_URL"], os.environ["LK_KEY"], os.environ["LK_SECRET"])
    try:
        itens = (await lk.sip.list_sip_inbound_trunk(lsip.ListSIPInboundTrunkRequest())).items
    finally:
        await lk.aclose()
    for t in itens:
        print(f"TRUNK {t.name} auth={'sim' if t.auth_username else 'nao'} "
              f"numeros={','.join(t.numbers) or '<todos>'}", flush=True)
    print(f"TOTAL {len(itens)}", flush=True)
    return 0


async def _open_trunk(acao: str, nome: str, numero: str = "") -> int:
    """SÓ para a bateria de mutação: cria (ou apaga) um tronco SEM autenticação, o estado que o
    seed passou a recusar — é a população que o ramo C precisa para poder reprovar."""
    from livekit import api
    from livekit.protocol import sip as lsip
    lk = api.LiveKitAPI(os.environ["LK_URL"], os.environ["LK_KEY"], os.environ["LK_SECRET"])
    try:
        if acao == "mk-open":
            t = await lk.sip.create_sip_inbound_trunk(lsip.CreateSIPInboundTrunkRequest(
                trunk=lsip.SIPInboundTrunkInfo(name=nome, numbers=[numero])))
            print(f"CRIADO {t.sip_trunk_id}", flush=True)
        else:
            for t in (await lk.sip.list_sip_inbound_trunk(lsip.ListSIPInboundTrunkRequest())).items:
                if t.name == nome:
                    await lk.sip.delete_sip_trunk(lsip.DeleteSIPTrunkRequest(sip_trunk_id=t.sip_trunk_id))
                    print(f"APAGADO {t.sip_trunk_id}", flush=True)
    finally:
        await lk.aclose()
    return 0


async def _invite(dnis: str, user: str, senha: str) -> int:
    ua = SipUA(os.environ.get("SIP_HOST", "livekit-sip"), user=user, senha=senha,
               ani="+5511900000032", log=lambda *_: None)
    ch = await ua.ligar(dnis, espera_atender_s=float(os.environ.get("ESPERA", "15")))
    print(f"FINAL {ch.status_final if ch.status_final else 'none'} respostas={ch.respostas}", flush=True)
    if ch.status_final == 200:
        await ua.desligar()
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "trunks":
        sys.exit(asyncio.run(_trunks()))
    if modo in ("mk-open", "rm") and len(sys.argv) >= 3:
        sys.exit(asyncio.run(_open_trunk(modo, *sys.argv[2:4])))
    if modo == "invite" and len(sys.argv) == 5:
        sys.exit(asyncio.run(_invite(*sys.argv[2:5])))
    print(__doc__)
    sys.exit(2)
