# -*- coding: utf-8 -*-
"""Exercicio do IDN-10 DENTRO do container do gateway.

Lido por stdin: `docker exec -i <gw> python - [censo|exercicio] [--mutar]`.

  censo      varre o indice Redis do tenant e compara o dono de cada ancora com o
             que o CADASTRO registra. Imprime {redis_ancoras, sem_linha_pg,
             concordam, divergem, exemplos}.

  exercicio  roda o codigo da IMAGEM contra Postgres e Redis reais:
    progressiva_respeita   ancora fria no Redis, com dono no cadastro, NAO e anexada
                           ao prospect que venceu pelo Redis
    progressiva_controle   ancora sem dono no cadastro, na mesma chamada, E anexada
    reidratacao_respeita   no caminho `durable`, a ancora do cliente que PERDEU nao e
                           apontada para o vencedor
    reidratacao_controle   ancora sem dono, na mesma chamada, e apontada para o vencedor

    empate_frio_ambiguo    IDN-11: ancoras de DOIS clientes no mesmo score, com o
                           indice frio -> `ambiguous`, e nada escrito no indice
                           (antes: vencia a ORDEM das ancoras na chamada)

  `--mutar` troca a regra de escrita (`_pode_apontar` -> sempre True): os dois
  `*_respeita` TEM de reprovar e os dois controles seguem verdes.
  `--mutar-empate` volta a regra de empate antiga do caminho frio: o
  `empate_frio_ambiguo` TEM de reprovar.

Limpa sempre o que criou (por hash e por id).
"""
import asyncio
import json
import os
import sys
import uuid

import asyncpg
import redis.asyncio as aioredis

from plughub_channel_gateway.config import get_settings
from plughub_channel_gateway.identity import index as idx_mod
from plughub_channel_gateway.identity.index import IdentityIndex
from plughub_channel_gateway.identity.normalize import hash_anchor

MODO = sys.argv[1] if len(sys.argv) > 1 else "exercicio"
MUTAR = "--mutar" in sys.argv
MUTAR_EMPATE = "--mutar-empate" in sys.argv


async def censo(s, r, db):
    t = s.tenant_id
    pg = {(row["kind"], row["value_hash"]): row["customer_id"] for row in await db.fetch(
        "SELECT kind, value_hash, customer_id FROM identity.customer_secondary_keys WHERE tenant_id=$1", t)}
    out = {"redis_ancoras": 0, "sem_linha_pg": 0, "concordam": 0, "divergem": 0, "exemplos": []}
    async for k in r.scan_iter(match="%s:identity:*" % t, count=500):
        partes = k.decode().split(":")
        if len(partes) != 4:
            continue
        got = idx_mod._decode_index(await r.get(k))
        if not got:
            continue
        out["redis_ancoras"] += 1
        dono = pg.get((partes[2], partes[3]))
        if dono is None:
            out["sem_linha_pg"] += 1
        elif dono == got[0]:
            out["concordam"] += 1
        else:
            out["divergem"] += 1
            if len(out["exemplos"]) < 5:
                out["exemplos"].append({"kind": partes[2], "redis": got[0], "cadastro": dono})
    return out


async def censo_mutado(s, r, db):
    """Injeta UMA divergencia, roda o censo e RESTAURA — no mesmo processo.

    ⚠️ A primeira versao fazia injecao e restauracao em duas chamadas, passando o
    valor anterior pelo shell; o helper de extracao imprimia `None` para JSON nulo
    e a restauracao GRAVOU a string "None" como dono da ancora de um cliente real.
    Aqui o valor e o TTL originais nunca saem do processo, e a restauracao esta
    num `finally`.
    """
    row = await db.fetchrow("SELECT kind, value_hash FROM identity.customer_secondary_keys "
                            "WHERE tenant_id=$1 LIMIT 1", s.tenant_id)
    if not row:
        return {"sem_amostra": True}
    k = "%s:identity:%s:%s" % (s.tenant_id, row["kind"], row["value_hash"])
    antes = await r.get(k)
    ttl_ms = await r.pttl(k)
    try:
        await r.set(k, idx_mod._encode_index("cus_probe_idn10_injetado", "claimed"))
        return await censo(s, r, db)
    finally:
        if antes is None:
            await r.delete(k)
        else:
            await r.set(k, antes)
            if ttl_ms and ttl_ms > 0:
                await r.pexpire(k, ttl_ms)


async def exercicio(s, r, db):
    t = s.tenant_id
    salt = os.getenv("PLUGHUB_IDENTITY_SALT", "plughub_identity_demo_salt")
    idx = IdentityIndex(redis=r, salt=salt, db_pool=db)
    sx = "%06d" % (uuid.uuid4().int % 1000000)
    ids = {n: "cus_probe_idn10_%s_%s" % (n, sx) for n in ("dono", "prospect", "venc", "outro")}
    anc = {
        "p_phone": ("phone", "+55110010" + sx[:5]),   # dono no cadastro = dono
        "p_email": ("email", "idn10p%s@probe.local" % sx),
        "p_livre": ("phone", "+55110020" + sx[:5]),   # sem dono
        "r_cpf":   ("cpf", "100" + sx + "01"),          # dono = venc
        "r_email": ("email", "idn10r%s@probe.local" % sx),  # dono = outro
        "r_livre": ("phone", "+55110030" + sx[:5]),
    }
    h = {n: hash_anchor(salt, k, v) for n, (k, v) in anc.items()}
    key = lambda n: idx._identity_key(t, anc[n][0], h[n])
    out = {"mutar": MUTAR, "casos": {}}
    c = out["casos"]
    try:
        # fixtures no cadastro (pelo escritor de produto), com o Redis delas FRIO
        await idx.attach_anchor(t, ids["dono"], *anc["p_phone"], persist_durable=True, provenance="declared")
        await idx.attach_anchor(t, ids["venc"], *anc["r_cpf"], persist_durable=True, provenance="declared")
        await idx.attach_anchor(t, ids["outro"], *anc["r_email"], persist_durable=True, provenance="declared")
        for n in ("p_phone", "r_cpf", "r_email"):
            await r.delete(key(n))
        await r.set(key("p_email"), idx_mod._encode_index(ids["prospect"], "claimed"))

        if MUTAR:
            # A regra de ESCRITA no indice, e so ela. A versao anterior desta
            # mutacao calava o cadastro (`_pg_key_owner` -> None); desde a IDN-11 o
            # cadastro tambem DECIDE o vencedor das ancoras frias, e cala-lo matava o
            # caminho `durable` inteiro — os controles reprovariam por outro motivo.
            idx_mod._pode_apontar = lambda dono, winner: True
        if MUTAR_EMPATE:
            # IDN-11: a regra de empate antiga do caminho frio — o primeiro maior vence.
            def _primeiro_maior(candidates):
                if not candidates:
                    return []
                top = max(c[0] for c in candidates.values())
                return [next(cid for cid, c in candidates.items() if c[0] == top)]
            idx_mod._vencedores = _primeiro_maior

        # ── IDN-11: empate entre clientes, com o indice FRIO ─────────────────
        a1, a2 = ("phone", "+55110040" + sx[:5]), ("phone", "+55110050" + sx[:5])
        extra = ("email", "idn11x%s@probe.local" % sx)
        for n, (k, v) in (("e_a", a1), ("e_b", a2), ("e_x", extra)):
            anc[n] = (k, v)
            h[n] = hash_anchor(salt, k, v)
        await idx.attach_anchor(t, ids["dono"], *a1, persist_durable=True, provenance="declared")
        await idx.attach_anchor(t, ids["outro"], *a2, persist_durable=True, provenance="declared")
        for n in ("e_a", "e_b"):
            await r.delete(key(n))
        ref = await idx.resolve_or_provision(t, [{"kind": k, "value": v} for (k, v) in (a1, a2, extra)],
                                             provision=False)
        escreveu = [n for n in ("e_a", "e_b", "e_x") if await r.get(key(n)) is not None]
        out["empate_frio"] = {"matched_by": ref.matched_by, "escreveu": escreveu}
        c["empate_frio_ambiguo"] = ref.matched_by == "ambiguous" and not escreveu and ref.customer_id == ""

        ref = await idx.resolve_or_provision(t, [
            {"kind": anc["p_email"][0], "value": anc["p_email"][1]},
            {"kind": anc["p_phone"][0], "value": anc["p_phone"][1]},
            {"kind": anc["p_livre"][0], "value": anc["p_livre"][1]},
        ])
        out["progressiva_vencedor"] = ref.customer_id
        got = idx_mod._decode_index(await r.get(key("p_phone")))
        c["progressiva_respeita"] = ref.customer_id == ids["prospect"] and (got is None or got[0] != ids["prospect"])
        got = idx_mod._decode_index(await r.get(key("p_livre")))
        c["progressiva_controle"] = bool(got and got[0] == ids["prospect"])

        ref = await idx.resolve_or_provision(t, [
            {"kind": anc["r_cpf"][0], "value": anc["r_cpf"][1]},
            {"kind": anc["r_email"][0], "value": anc["r_email"][1]},
            {"kind": anc["r_livre"][0], "value": anc["r_livre"][1]},
        ], provision=False)
        out["reidratacao"] = {"matched_by": ref.matched_by, "vencedor": ref.customer_id}
        perdedora = "r_email" if ref.customer_id == ids["venc"] else "r_cpf"
        got = idx_mod._decode_index(await r.get(key(perdedora)))
        c["reidratacao_respeita"] = (ref.matched_by == "durable"
                                     and ref.customer_id in (ids["venc"], ids["outro"])
                                     and (got is None or got[0] != ref.customer_id))
        got = idx_mod._decode_index(await r.get(key("r_livre")))
        c["reidratacao_controle"] = bool(got and got[0] == ref.customer_id)
        return out
    finally:
        async with db.acquire() as conn:
            for n, (k, _v) in anc.items():
                await conn.execute(
                    "DELETE FROM identity.customer_secondary_keys WHERE tenant_id=$1 AND kind=$2 AND value_hash=$3",
                    t, k, h[n])
            await conn.execute("DELETE FROM identity.customers WHERE customer_id = ANY($1::text[])", list(ids.values()))
        for n in anc:
            await r.delete(key(n))


async def main():
    s = get_settings()
    r = aioredis.from_url(s.redis_url)
    db = await asyncpg.create_pool(s.database_url, min_size=1, max_size=2)
    try:
        if MODO == "censo":
            return await censo(s, r, db)
        if MODO == "censo-mutado":
            return await censo_mutado(s, r, db)
        return await exercicio(s, r, db)
    finally:
        await db.close()
        await r.aclose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), default=str))
