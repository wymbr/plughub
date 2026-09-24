"""
_sip_rtp_gaps.py — instrumento da VOZ-35: a linha fica VIVA quando ninguém fala?

Liga para `DNIS`, fica calado por `DURACAO` segundos e mede o que o "telefone" RECEBE: quando a
chamada foi atendida, quantos pacotes RTP chegaram e o MAIOR intervalo entre dois pacotes. Operadora
e SBC costumam derrubar a chamada depois de ~30–60 s sem RTP — silêncio de verdade (pacotes com
áudio baixo) é diferente de pacote nenhum.

  python _sip_rtp_gaps.py <dnis> <usuario> <senha>
  → linhas `INFO <chave>=<valor>` e `FINAL <status>`; ESPERA (atender) e DURACAO (ouvir) por env.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

from _sip_ua import SipUA


async def main(dnis: str, user: str, senha: str) -> int:
    ua = SipUA(os.environ.get("SIP_HOST", "livekit-sip"), user=user, senha=senha,
               ani=os.environ.get("ANI", "+5511900000035"), log=lambda *_: None)
    t0 = time.monotonic()
    ch = await ua.ligar(dnis, espera_atender_s=float(os.environ.get("ESPERA", "30")))
    print(f"FINAL {ch.status_final or 'none'}", flush=True)
    if ch.status_final != 200:
        print(f"INFO respostas={ch.respostas}", flush=True)
        return 0
    print(f"INFO atendida_em_s={ch.atendida_em - t0:.1f}", flush=True)
    duracao = float(os.environ.get("DURACAO", "25"))
    await asyncio.sleep(duracao)
    marcas = [t for t, _ in ch.energia if t >= ch.atendida_em]
    gaps = [b - a for a, b in zip(marcas, marcas[1:])]
    primeiro = (marcas[0] - ch.atendida_em) if marcas else None
    print(f"INFO pacotes={len(marcas)} esperados_20ms={int(duracao / 0.02)}", flush=True)
    print(f"INFO primeiro_pacote_s={primeiro if primeiro is None else round(primeiro, 2)}", flush=True)
    print(f"INFO maior_intervalo_s={max(gaps):.2f}" if gaps else "INFO maior_intervalo_s=sem_pacotes", flush=True)
    longos = [round(g, 1) for g in gaps if g > 1.0]
    print(f"INFO intervalos_maiores_que_1s={longos}", flush=True)
    await ua.desligar()
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(*sys.argv[1:4])))
