# -*- coding: utf-8 -*-
"""Exercício da PID-02 DENTRO do container do gateway (a lib `plughub_contextstore` da IMAGEM).

Lido por stdin: `docker exec -i <gw> python - <modo> [--mutar-reserva]`.

  funil      o `write_context_tags` contra o Redis real, numa sessão sintética:
    raise_recusa       tag de evidência em modo `raise` levanta ContextTagReserved, e nada é gravado
    warn_descarta      em modo `warn` (corpo de webhook) a reservada some e a outra é gravada
    controle_core      `core.workflow.*` continua gravando
  Mutação: --mutar-reserva (predicado sempre False) — `warn_descarta` e `raise_recusa` têm de cair.

  evidencia  depois da jornada: procura, nas journeys do tenant, a evidência OTP gravada a partir
             de `--desde <iso>`: status verified, proven_in_session de uma sessão que existe.
"""
import asyncio
import json
import sys
import uuid

import redis.asyncio as aioredis

from plughub_channel_gateway.config import get_settings
import plughub_contextstore.writer as writer_mod
from plughub_contextstore.writer import ContextTagReserved, write_context_tags

MODO = sys.argv[1] if len(sys.argv) > 1 else "funil"
MUTAR = "--mutar-reserva" in sys.argv
TAG = "core.journey.identity.otp.status"


def _arg(nome):
    return sys.argv[sys.argv.index(nome) + 1] if nome in sys.argv else ""


async def _fetch(_url):
    return {"entries": {}}


async def funil(s, r):
    t = s.tenant_id
    sid = "probe-pid02-py-" + uuid.uuid4().hex[:8]
    chave = "%s:ctx:%s" % (t, sid)
    out = {"mutar": MUTAR, "casos": {}}
    c = out["casos"]
    if MUTAR:
        writer_mod.is_reserved_identity_tag = lambda tag: False
    kw = dict(fetch_json=_fetch, source="probe", updated_at="2026-09-13T00:00:00Z")
    try:
        try:
            await write_context_tags(r, t, sid, {"session.probe_a": "1", TAG: "verified"}, **kw)
            c["raise_recusa"] = False
        except ContextTagReserved:
            c["raise_recusa"] = not await r.exists(chave)
        except Exception as e:                      # sob mutação cai no ContextScopeRefused
            c["raise_recusa"] = False
            out["raise_outro_erro"] = type(e).__name__
        await r.delete(chave)
        await write_context_tags(r, t, sid, {"session.probe_b": "1", "core.identity.probe": "verified"},
                                 on_foreign_scope="warn", **kw)
        c["warn_descarta"] = bool(await r.hexists(chave, "session.probe_b")) and not await r.hexists(chave, "core.identity.probe")
        await write_context_tags(r, t, sid, {"core.workflow.probe_pid02": "x"}, **kw)
        c["controle_core"] = bool(await r.hexists(chave, "core.workflow.probe_pid02"))
        return out
    finally:
        await r.delete(chave)


async def evidencia(s, r):
    t = s.tenant_id
    desde = _arg("--desde")
    achados = []
    async for k in r.scan_iter("%s:ctx:journey:*" % t, count=1000):
        raw = await r.hget(k, TAG)
        if not raw:
            continue
        e = json.loads(raw)
        if str(e.get("updated_at", "")) < desde:
            continue
        prov = await r.hget(k, "core.journey.identity.otp.proven_in_session")
        prov_v = json.loads(prov)["value"] if prov else None
        src = await r.hget(k, "core.journey.identity.otp.source")
        vat = await r.hget(k, "core.journey.identity.otp.verified_at")
        existe = bool(prov_v) and (await r.exists("session:%s:meta" % prov_v) or await r.exists("session:%s:stream" % prov_v))
        achados.append({"journey": k.decode() if isinstance(k, bytes) else k, "status": e.get("value"),
                        "source_escritor": e.get("source"), "proven_in_session": prov_v,
                        "source": json.loads(src)["value"] if src else None, "verified_at": bool(vat),
                        "sessao_existe": bool(existe)})
    verif = [a for a in achados if a["status"] == "verified"]
    return {"achados": achados, "casos": {
        "evidencia_verified": bool(verif),
        "prova_completa": any(a["proven_in_session"] and a["verified_at"] and a["sessao_existe"]
                              and a["source_escritor"] == "identity:otp" for a in verif),
    }}


async def main():
    s = get_settings()
    r = aioredis.from_url(s.redis_url)
    try:
        return await (evidencia(s, r) if MODO == "evidencia" else funil(s, r))
    finally:
        await r.aclose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), default=str))
