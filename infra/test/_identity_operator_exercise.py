# -*- coding: utf-8 -*-
"""Exercicio da IDN-08 DENTRO do container do gateway.

Lido por stdin: `docker exec -i <gw> python - <modo> [--mutar-*]`.

  rota         o PORTAO da rota viva `POST /identity/operator/register` (8010), com
               JWTs cunhados pelo segredo do proprio servico e uma sessao sintetica:
    sem_bearer_401 · sem_campo_403 · escopo_outro_pool_403 · sessao_outro_tenant_404
    kind_invalido_422      `kind: "telefone"` (o que a aba mandava) recusa nomeando
    cria_200_operator      grant ESCOPADO ao pool da sessao passa; o cadastro nasce
                           duravel, as ancoras com procedencia `operator`, e o nome
    existente_200          o mesmo telefone de novo: `existing`, MESMO cliente, sem outro
    resolve_le_operator    `/identity/resolve` devolve `provenance: operator` (IDN-07)

  exercicio    a regra no codigo da IMAGEM contra Postgres e Redis reais:
    cria_operator          procedencia `operator`, claimed, no cadastro
    conflito_nao_move      ancora de outro cliente recusa e fica com o dono
    controle_existente     ancora do proprio cliente passa (a recusa nao e geral)
  Mutacoes: --mutar-procedencia (carimba `declared`) · --mutar-conflito (guarda desligada).

Limpa sempre o que criou (por hash, id e a sessao sintetica).
"""
import asyncio
import json
import os
import sys
import time
import uuid

import asyncpg
import httpx
import jwt as pyjwt
import redis.asyncio as aioredis

from plughub_channel_gateway.config import get_settings
from plughub_channel_gateway.identity import index as idx_mod
from plughub_channel_gateway.identity.index import IdentityIndex
from plughub_channel_gateway.identity.region import PhoneRegionConfig

MODO = sys.argv[1] if len(sys.argv) > 1 else "exercicio"
MUT_PROV = "--mutar-procedencia" in sys.argv
MUT_CONF = "--mutar-conflito" in sys.argv
BASE = "http://localhost:8010/v1/channels/webhook/identity"
POOL = "probe_pool_idn08"


def _token(s, tenant, module_config):
    return pyjwt.encode({"sub": "probe-idn08", "tenant_id": tenant, "module_config": module_config,
                         "exp": int(time.time()) + 600}, s.auth_jwt_secret, algorithm="HS256")


async def _linhas(db, t, cid):
    async with db.acquire() as conn:
        keys = await conn.fetch("SELECT kind, provenance, verification_class FROM identity.customer_secondary_keys "
                                "WHERE tenant_id=$1 AND customer_id=$2 ORDER BY kind", t, cid)
        cust = await conn.fetchrow("SELECT status, attributes FROM identity.customers WHERE customer_id=$1", cid)
    attrs = cust["attributes"] if cust else None
    if isinstance(attrs, str):
        attrs = json.loads(attrs)
    return [dict(k) for k in keys], (cust["status"] if cust else None), (attrs or {})


async def _limpa(r, db, t, idx, anchors, cids, sessions=()):
    hashes = []
    for kind, value in anchors:
        try:
            hashes.append((kind, await idx.anchor_hash(t, kind, value)))
        except ValueError:
            pass
    async with db.acquire() as conn:
        for kind, h in hashes:
            await conn.execute("DELETE FROM identity.customer_secondary_keys WHERE tenant_id=$1 AND kind=$2 "
                               "AND value_hash=$3", t, kind, h)
        await conn.execute("DELETE FROM identity.customers WHERE customer_id = ANY($1::text[])", [c for c in cids if c])
    for kind, h in hashes:
        await r.delete("%s:identity:%s:%s" % (t, kind, h))
    for sid in sessions:
        await r.delete("session:%s:meta" % sid)


async def rota(s, r, db, salt):
    t = s.tenant_id
    idx = IdentityIndex(redis=r, salt=salt, db_pool=db, phone_region=PhoneRegionConfig(s.config_api_url))
    sx = "%08d" % (uuid.uuid4().int % 100000000)
    sid = "probe-idn08-" + sx
    phone_nac, email = "(11) 9%s-%s" % (sx[:4], sx[4:]), "idn08-%s@probe.local" % sx
    await r.set("session:%s:meta" % sid, json.dumps({"tenant_id": t, "pool_id": POOL}), ex=300)
    ok_grant = {"agent_assist": {"atender": {"access": "read_write", "scope": ["pool:" + POOL]}}}
    tok_ok = _token(s, t, ok_grant)
    tok_sem = _token(s, t, {})
    tok_outro = _token(s, t, {"agent_assist": {"atender": {"access": "read_write", "scope": ["pool:outro_pool"]}}})
    tok_tenant = _token(s, "tenant_outro", ok_grant)
    corpo = {"session_id": sid, "anchors": [{"kind": "phone", "value": phone_nac}, {"kind": "email", "value": email}],
             "name": "Probe IDN-08"}
    out = {"casos": {}}
    c = out["casos"]
    cids = set()
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            post = lambda tok, body: http.post(BASE + "/operator/register", json=body,
                                               headers={"authorization": "Bearer " + tok} if tok else {})
            c["sem_bearer_401"] = (await post(None, corpo)).status_code == 401
            c["sem_campo_403"] = (await post(tok_sem, corpo)).status_code == 403
            c["escopo_outro_pool_403"] = (await post(tok_outro, corpo)).status_code == 403
            # o 404 tem de ser o da SESSÃO: rota inexistente também responde 404, e foi
            # assim que este caso passou no vermelho-primeiro, contra o gateway antigo
            rr = await post(tok_tenant, corpo)
            c["sessao_outro_tenant_404"] = rr.status_code == 404 and "sessao" in rr.text

            rr = await post(tok_ok, {**corpo, "anchors": [{"kind": "telefone", "value": phone_nac}]})
            j = rr.json()
            c["kind_invalido_422"] = rr.status_code == 422 and j.get("invalid") == ["telefone"]

            rr = await post(tok_ok, corpo)
            j = rr.json()
            out["cria"] = j
            cid = j.get("customer_id", "")
            cids.add(cid)
            keys, status, attrs = await _linhas(db, t, cid)
            out["linhas"] = keys
            c["cria_200_operator"] = (rr.status_code == 200 and j.get("outcome") == "created" and bool(cid)
                                      and [k["kind"] for k in keys] == ["email", "phone"]
                                      and all(k["provenance"] == "operator" and k["verification_class"] == "claimed"
                                              for k in keys)
                                      and attrs.get("nome") == "Probe IDN-08" and status == "identified")

            rr = await post(tok_ok, {**corpo, "anchors": [{"kind": "phone", "value": "+55 11 9" + sx}], "name": "Outro"})
            j = rr.json()
            _k, _s, attrs2 = await _linhas(db, t, cid)
            c["existente_200"] = (rr.status_code == 200 and j.get("outcome") == "existing"
                                  and j.get("customer_id") == cid and attrs2.get("nome") == "Probe IDN-08")

            rr = await http.post(BASE + "/resolve", json={"tenant_id": t, "provision": False,
                                                          "anchors": [{"kind": "phone", "value": phone_nac}]})
            c["resolve_le_operator"] = rr.status_code == 200 and rr.json().get("provenance") == "operator"
        return out
    finally:
        await _limpa(r, db, t, idx, [("phone", phone_nac), ("email", email)], cids, sessions=[sid])


async def exercicio(s, r, db, salt):
    t = s.tenant_id
    idx = IdentityIndex(redis=r, salt=salt, db_pool=db, phone_region=PhoneRegionConfig(s.config_api_url))
    sx = "%08d" % (uuid.uuid4().int % 100000000)
    outro = "cus_probe_idn08_outro_" + sx
    phone, email_outro, phone2 = "+55 11 9" + sx, "idn08x-%s@probe.local" % sx, "+55 21 9" + sx
    out = {"mutar": {"procedencia": MUT_PROV, "conflito": MUT_CONF}, "casos": {}}
    c = out["casos"]
    cids = {outro}
    try:
        await idx.attach_anchor(t, outro, "email", email_outro, persist_durable=True, provenance="declared")
        if MUT_PROV:
            idx_mod.PROVENANCE_OPERATOR = "declared"
        if MUT_CONF:
            idx_mod._ancora_de_outro = lambda dono, quente, cid: False

        res = await idx.register_by_operator(t, [{"kind": "phone", "value": phone}], name="X", operator="probe")
        cids.add(res.customer_id)
        keys, _st, _at = await _linhas(db, t, res.customer_id)
        c["cria_operator"] = (res.outcome == "created" and len(keys) == 1
                              and keys[0]["provenance"] == "operator" and keys[0]["verification_class"] == "claimed")

        # Conflito de verdade: o e-mail (0.80) identifica `outro`, e o telefone digitado
        # junto é do cliente recém-criado. Anexar moveria o telefone de um para o outro.
        # (Âncora SEM dono junto da de `outro` não é conflito: é `existing`, e anexa.)
        res2 = await idx.register_by_operator(t, [{"kind": "phone", "value": phone},
                                                  {"kind": "email", "value": email_outro}], operator="probe")
        cids.add(res2.customer_id)
        dono = (await _linhas(db, t, outro))[0]
        do_criado = (await _linhas(db, t, res.customer_id))[0]
        c["conflito_nao_move"] = (res2.outcome == "refused" and res2.conflicts == ["phone"]
                                  and [k["kind"] for k in dono] == ["email"]
                                  and [k["kind"] for k in do_criado] == ["phone"])
        out["conflito"] = {"outcome": res2.outcome, "conflicts": res2.conflicts}

        res3 = await idx.register_by_operator(t, [{"kind": "phone", "value": phone},
                                                  {"kind": "phone", "value": phone2}], operator="probe")
        c["controle_existente"] = res3.outcome == "existing" and res3.customer_id == res.customer_id
        return out
    finally:
        await _limpa(r, db, t, idx, [("phone", phone), ("phone", phone2), ("email", email_outro)], cids)


async def main():
    s = get_settings()
    r = aioredis.from_url(s.redis_url)
    db = await asyncpg.create_pool(s.database_url, min_size=1, max_size=2)
    salt = os.getenv("PLUGHUB_IDENTITY_SALT", "plughub_identity_demo_salt")
    try:
        if MODO == "rota":
            return await rota(s, r, db, salt)
        return await exercicio(s, r, db, salt)
    finally:
        await db.close()
        await r.aclose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), default=str))
