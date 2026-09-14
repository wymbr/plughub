"""
_webrtc_pool_media_policy_exercise.py — metade LEITORA do `probe_webrtc_pool_media_policy.sh` (VOZ-10).

Roda DENTRO da imagem do channel-gateway. Lê do Redis real as entradas `routing.assigned`
que a metade produtora (imagem do bridge) escreveu, e as entrega, em ordem, ao adapter DA
IMAGEM com o provider LiveKit real (os tokens são assinados de verdade e decodificados
aqui). As saídas de atendente são injetadas entre as entradas: quem as produz
(`participant_left`) não é a proposição.

  C0 o stream tem as 4 entradas, com procedência e política que o produtor prometeu
  C1 humano do pool P1   → cliente publica EXATAMENTE `customer_publish` de P1 (só vídeo)
  C2 token do atendente  → publica EXATAMENTE `agent_publish` de P1 (só áudio)
  C3 IA do mesmo pool    → leu P1, e mesmo assim não abre mídia ao cliente (consumo)
  C4 humano após a troca → P2 (só áudio): a mudança na tela vale sem reiniciar nada
  C5 registry fora       → a política é RECUSADA e a procedência diz por quê

MODE=mut_ignore_pool : o leitor ignora a política e oferece tudo → C1 TEM de reprovar.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from unittest.mock import MagicMock

import redis.asyncio as aioredis

from plughub_channel_gateway.adapters import media_policy
from plughub_channel_gateway.adapters.webrtc import WebRTCAdapter
from plughub_channel_gateway.config import Settings

MODE = os.environ.get("MODE", "full")
SID  = os.environ["SID"]
POOL = os.environ["POOL"]
P1   = json.loads(os.environ["P1"])
P2   = json.loads(os.environ["P2"])


def emit(status: str, ramo: str, texto: str) -> None:
    print(f"{status} {ramo} {texto}", flush=True)


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


def _sources(tok: str) -> tuple[bool | None, list[str]]:
    part = tok.split(".")[1]
    v = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("video", {})
    return v.get("canPublish"), list(v.get("canPublishSources") or [])


def _src_of(kinds: list[str]) -> list[str]:
    return media_policy.publish_sources(frozenset(kinds))


async def main() -> None:
    if MODE == "mut_ignore_pool":
        _orig = media_policy.attendant_from_pool_field

        def _ignora(framework, pool_field):
            rec, _ = _orig(framework, pool_field)
            rec.update(customer_publish=["audio", "video"], agent_publish=["audio", "video"])
            return rec, None
        media_policy.attendant_from_pool_field = _ignora

    s = Settings()
    s.webrtc_stt_enabled = False
    r = aioredis.from_url("redis://redis:6379", decode_responses=True)
    a = WebRTCAdapter(producer=MagicMock(), redis=r, settings=s)
    ws = FakeWS()
    state_key = f"channel:webrtc:{SID}:media"
    try:
        entries = [f for _id, f in await r.xrange(f"session:{SID}:stream")
                   if f.get("type") == "routing.assigned"]
        pools = [json.loads(f.get("pool") or "{}") for f in entries]
        esperado = [("registry", P1), ("registry", P1), ("registry", P2), ("registry_unavailable", None)]
        visto = [(p.get("media_policy_source"), p.get("media_policy")) for p in pools]
        emit("OK" if visto == esperado and [f.get("instance_id") for f in entries] == ["h1", "ia1", "h2", "h3"]
             else "FALHA", "C0", f"produtor escreveu (procedencia, politica)={visto}")
        if len(entries) != 4:
            return

        # ── C1 ──────────────────────────────────────────────────────────────
        await a._on_routing_assigned(ws, SID, entries[0], s)
        ready = ws.sent[-1]
        can, srcs = _sources(ready.get("token", "x.e30.x"))
        emit("OK" if (ready.get("type") == "webrtc.ready" and ready.get("publish") == P1["customer_publish"]
                      and srcs == _src_of(P1["customer_publish"]) and ready.get("policy_sources") == [f"pool:{POOL}"])
             else "FALHA", "C1",
             f"humano de {POOL}: publish={ready.get('publish')} token.fontes={srcs} "
             f"procedencia={ready.get('policy_sources')} (esperado {P1['customer_publish']})")

        # ── C2 ──────────────────────────────────────────────────────────────
        tok = await a.get_token(SID, "agent", "probe-voz10")
        can, srcs = _sources(tok["token"])
        emit("OK" if (tok["publish"] == P1["agent_publish"] and srcs == _src_of(P1["agent_publish"]))
             else "FALHA", "C2",
             f"token do atendente: publish={tok['publish']} fontes={srcs} (esperado {P1['agent_publish']})")

        # ── C3 ──────────────────────────────────────────────────────────────
        await a._on_routing_renegotiate(ws, SID, entries[1], s)
        n = len(ws.sent)
        await a._on_attendant_left(ws, SID, {"type": "participant_left", "author_id": "h1"})
        st = json.loads(await r.get(state_key))
        caiu = ws.sent[-1] if len(ws.sent) > n else {}
        emit("OK" if (st["attendants"]["ia1"]["customer_publish"] == P1["customer_publish"]
                      and caiu.get("type") == "webrtc.media" and caiu.get("publish") == [])
             else "FALHA", "C3",
             f"IA do mesmo pool leu {st['attendants']['ia1']['customer_publish']}; so ela na sala -> "
             f"msg={caiu.get('type')} publish={caiu.get('publish')} (IA de texto nao consome)")

        # ── C4 ──────────────────────────────────────────────────────────────
        await a._on_routing_renegotiate(ws, SID, entries[2], s)
        last = ws.sent[-1]
        can, srcs = _sources(last.get("token", "x.e30.x"))
        emit("OK" if (last.get("type") == "webrtc.media" and last.get("publish") == P2["customer_publish"]
                      and srcs == _src_of(P2["customer_publish"]))
             else "FALHA", "C4",
             f"politica trocada na API, humano novo: publish={last.get('publish')} fontes={srcs} "
             f"(esperado {P2['customer_publish']}, sem reiniciar nada)")

        # ── C5 ──────────────────────────────────────────────────────────────
        await a._on_attendant_left(ws, SID, {"type": "participant_left", "author_id": "h2"})
        await a._on_routing_renegotiate(ws, SID, entries[3], s)
        st = json.loads(await r.get(state_key))
        h3 = st["attendants"]["h3"]
        emit("OK" if (h3["policy_source"] == f"registry_indisponivel:{POOL}" and h3["customer_publish"] == []
                      and st["customer"]["publish"] == [] and "media_policy" not in pools[3])
             else "FALHA", "C5",
             f"registry fora: procedencia={h3['policy_source']} teto={st['customer']['publish']}")
    finally:
        await a._provider.delete_room(f"plughub-{SID}")
        await r.delete(f"session:{SID}:meta", f"session:{SID}:stream", f"session:{SID}:contact_id",
                       f"channel:webrtc:{SID}:room_name", state_key)
        await r.aclose()
        emit("OK", "LIMPEZA", f"sala e chaves da sessao {SID} apagadas por nome")


try:
    asyncio.run(main())
finally:
    sys.stdout.flush()
    os._exit(0)
