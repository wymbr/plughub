# -*- coding: utf-8 -*-
"""Exercicio da PID-10 DENTRO do container do gateway.

Lido por stdin: `docker exec -i <gw> python - [censo|exercicio] [--mutar-*]`.

  censo      o que o PARQUE declara e o que ja foi gravado:
               skills_vivos  — o snapshot do slot `current` de TODO pool (o que roda,
                               nunca o YAML) E o `flow` publicado de todo skill (o
                               que roda em pool sem slot, e o que o proximo promote
                               fotografa): cada `otp_challenge` com `kind` literal
                               entregavel e `customer_id` presente
               posse_nao_entregavel — ancoras `possessed` de kind que nao recebe codigo
                               (legado do desafio tautologico; INFORMACAO)

  exercicio  o codigo da IMAGEM contra Postgres e Redis reais. Fixture: um cliente
             IMPORTADO (phone+cpf `authoritative`), um phone `declared` do mesmo
             cliente, e um segundo cliente sem nada.
    cpf_autoritativo_recusa        cpf do cadastro -> undeliverable_kind
    declarado_recusa               phone declarado -> anchor_not_authoritative
    autoritativo_de_outro_recusa   phone autoritativo de A pedido para B -> recusa
    sem_entrega_recusa             OtpService sem canal -> delivery_unavailable
    autoritativo_emite             CONTROLE: phone autoritativo de A para A -> sent
    verify_de_outro_nao_anexa      codigo certo, customer B -> nao confere, PG intacto
    verify_mesmo_cliente_possessed CONTROLE: mesmo cliente -> possessed no cadastro
    rota_viva_recusa_cpf           a ROTA do processo que esta de pe (8010) recusa cpf
    rota_viva_emite                CONTROLE da rota: phone autoritativo -> sent + dev_code

  Mutacoes (cada uma TEM de reprovar o seu caso; os controles seguem verdes):
    --mutar-procedencia  anchor_provenance sempre `authoritative`
    --mutar-subject      verify confere contra o subject GUARDADO, nao o informado
    --mutar-entregavel   `cpf` volta a ser entregavel

  legado     IDN-13: posse gravada em kind NAO-entregavel (o que o OTP ao CPF deixou).
             Fixture plantada direto no cadastro e no indice, como o legado estava.
    legado_quente_resolve_claimed  cpf `possessed` no Redis -> resolve `claimed`
    legado_frio_resolve_claimed    so no cadastro -> `claimed`, e a reidratacao grava `claimed`
    escrita_recusa                 attach_anchor(possessed, cpf) levanta
    migracao_rebaixa               a migracao do boot rebaixa cadastro e chave quente (TTL mantido)
    controle_phone_possessed       CONTROLE: phone `possessed` segue `possessed`, antes e depois
  Mutacoes: --mutar-leitura (regra de leitura desligada) · --mutar-escrita (guarda
  desligada) · --mutar-migracao (cpf entra na lista do que migra como entregavel).

Limpa sempre o que criou (por hash, por id e pelo `system` da importacao).
"""
import asyncio
import json
import os
import sys
import uuid

import asyncpg
import httpx
import redis.asyncio as aioredis

from plughub_channel_gateway.adapters.webhook import WebhookAdapter
from plughub_channel_gateway.config import get_settings
# IDN-14: o mesmo resolvedor de país do gateway; hash à mão leva região None
# porque toda fixture de telefone aqui tem '+', e aí a região não entra.
from plughub_channel_gateway.identity.region import PhoneRegionConfig
from plughub_channel_gateway.identity import index as idx_mod
from plughub_channel_gateway.identity import otp as otp_mod
from plughub_channel_gateway.identity.index import IdentityIndex
from plughub_channel_gateway.identity.normalize import DELIVERABLE_KINDS, hash_anchor
from plughub_channel_gateway.identity.otp import OtpService

MODO = sys.argv[1] if len(sys.argv) > 1 else "exercicio"
MUT_PROV = "--mutar-procedencia" in sys.argv
MUT_SUBJ = "--mutar-subject" in sys.argv
MUT_ENTR = "--mutar-entregavel" in sys.argv
MUT_LEIT = "--mutar-leitura" in sys.argv
MUT_ESCR = "--mutar-escrita" in sys.argv
MUT_MIGR = "--mutar-migracao" in sys.argv
INJETAR = "--injetar" in sys.argv   # censo: acrescenta um snapshot sintetico que desafia cpf
SYSTEM = "__probe_pid10__"
ROTA = "http://localhost:8010/v1/channels/webhook/identity/otp/challenge"
AR = os.getenv("PLUGHUB_AGENT_REGISTRY_URL", "http://agent-registry:3300")


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


async def censo(s, db, r):
    import yaml
    out = {"pools": 0, "pools_sem_snapshot": 0, "desafios": 0, "violacoes": [], "leitura_falhou": None}
    try:
        flows = []
        async with httpx.AsyncClient(timeout=15, headers={"x-tenant-id": s.tenant_id}) as http:
            rp = await http.get(AR + "/v1/pools")
            rp.raise_for_status()
            pools = rp.json()
            pools = pools.get("pools", pools) if isinstance(pools, dict) else pools
            out["pools_listados"] = len(pools)
            for p in pools:
                pid = p.get("pool_id")
                sr = await http.get("%s/v1/pools/%s/slots" % (AR, pid))
                sj = sr.json() if sr.status_code == 200 else {}
                cur = (sj.get("slots", sj) or {}).get("current") or {}
                snap = cur.get("yaml_snapshot")
                if not snap:
                    out["pools_sem_snapshot"] += 1
                    continue
                out["pools"] += 1
                flows.append((pid, cur.get("skill_id"), yaml.safe_load(snap) if isinstance(snap, str) else snap))
            # Pool sem snapshot roda o `skill.flow` (fallback do bridge) — e o `flow` de
            # produção de TODO skill e o que o proximo promote fotografa. Censo so dos
            # snapshots deixaria esses de fora calado.
            rs = await http.get(AR + "/v1/skills")
            rs.raise_for_status()
            skills = rs.json().get("skills", [])
            out["skills_flow"] = len(skills)
            for sk in skills:
                if sk.get("flow"):
                    flows.append(("(skill.flow)", sk.get("skill_id"), sk["flow"]))
        if INJETAR:
            flows.append(("__injetado__", "__injetado__", {"steps": [
                {"id": "desafio_cpf", "tool": "otp_challenge", "input": {"kind": "cpf", "value": "x"}}]}))
        for pid, skill, flow in flows:
            for st in _walk(flow):
                if st.get("tool") != "otp_challenge":
                    continue
                out["desafios"] += 1
                inp = st.get("input") or {}
                kind = inp.get("kind")
                if kind not in DELIVERABLE_KINDS or not inp.get("customer_id"):
                    out["violacoes"].append({"pool": pid, "skill": skill, "step": st.get("id"),
                                             "kind": kind, "customer_id": bool(inp.get("customer_id"))})
    except Exception as e:  # leitor quebrado nunca vira "zero violacoes"
        out["leitura_falhou"] = "%s: %s" % (type(e).__name__, e)
    rows = await db.fetch(
        "SELECT kind, count(*) n FROM identity.customer_secondary_keys WHERE tenant_id=$1 "
        "AND verification_class='possessed' AND kind <> ALL($2::text[]) GROUP BY kind",
        s.tenant_id, list(DELIVERABLE_KINDS))
    out["posse_nao_entregavel"] = {row["kind"]: row["n"] for row in rows}
    quentes = 0
    async for k in r.scan_iter(match="%s:identity:*" % s.tenant_id, count=500):
        partes = k.decode().split(":")
        if len(partes) != 4 or partes[2] in DELIVERABLE_KINDS:
            continue
        got = idx_mod._decode_index(await r.get(k))
        if got and got[1] == "possessed":
            quentes += 1
    out["posse_nao_entregavel_indice"] = quentes
    return out


async def exercicio(s, r, db):
    t = s.tenant_id
    salt = os.getenv("PLUGHUB_IDENTITY_SALT", "plughub_identity_demo_salt")
    idx = IdentityIndex(redis=r, salt=salt, db_pool=db, phone_region=PhoneRegionConfig(get_settings().config_api_url))
    sx = "%06d" % (uuid.uuid4().int % 1000000)
    phone, phone_decl, cpf = "+55110060" + sx[:5], "+55110070" + sx[:5], "100" + sx + "10"
    outro = "cus_probe_pid10_outro_" + sx
    hashes = [("phone", hash_anchor(salt, "phone", phone, None)), ("phone", hash_anchor(salt, "phone", phone_decl, None)),
              ("cpf", hash_anchor(salt, "cpf", cpf, None))]
    cids = {outro}
    out = {"mutar": {"procedencia": MUT_PROV, "subject": MUT_SUBJ, "entregavel": MUT_ENTR}, "casos": {}}
    c = out["casos"]

    def adaptador(dev=True):
        a = WebhookAdapter.__new__(WebhookAdapter)
        a._identity = idx
        a._otp = OtpService(redis=r, salt=salt, dev_return_code=dev, phone_region=PhoneRegionConfig(get_settings().config_api_url))
        return a

    async def chave(h):
        async with db.acquire() as conn:
            return await conn.fetchrow(
                "SELECT customer_id, verification_class, provenance FROM identity.customer_secondary_keys "
                "WHERE tenant_id=$1 AND kind='phone' AND value_hash=$2", t, h)

    try:
        res = await idx.import_customers(t, SYSTEM, [{
            "external_id": "pid10-" + sx,
            "anchors": [{"kind": "phone", "value": phone}, {"kind": "cpf", "value": cpf}],
        }], imported_by="probe_pid10")
        a_cid = res[0].customer_id if res and res[0].outcome == "created" else ""
        out["fixture"] = {"outcome": res[0].outcome if res else None}
        if not a_cid:
            out["sem_fixture"] = True
            return out
        cids.add(a_cid)
        await idx.attach_anchor(t, a_cid, "phone", phone_decl, persist_durable=True, provenance="declared")

        if MUT_PROV:
            async def _sempre(self, tenant_id, customer_id, kind, value):
                return "authoritative"
            IdentityIndex.anchor_provenance = _sempre
        if MUT_SUBJ:
            orig = OtpService.verify

            async def _subject_guardado(self, tenant_id, kind, value, code, *, subject):
                raw = await self._redis.get(self._chal_key(tenant_id, kind, hash_anchor(self._salt, kind, value, None)))
                guardado = json.loads(raw).get("subject") if raw else subject
                return await orig(self, tenant_id, kind, value, code, subject=guardado)
            OtpService.verify = _subject_guardado
        if MUT_ENTR:
            otp_mod.DELIVERABLE_KINDS = ("phone", "email", "cpf")

        a = adaptador()
        out["cpf"] = await a.otp_challenge(t, a_cid, "cpf", cpf)
        c["cpf_autoritativo_recusa"] = out["cpf"] == {"sent": False, "reason": "undeliverable_kind"}

        out["declarado"] = await a.otp_challenge(t, a_cid, "phone", phone_decl)
        c["declarado_recusa"] = out["declarado"] == {"sent": False, "reason": "anchor_not_authoritative"}

        out["de_outro"] = await a.otp_challenge(t, outro, "phone", phone)
        c["autoritativo_de_outro_recusa"] = out["de_outro"] == {"sent": False, "reason": "anchor_not_authoritative"}

        c["sem_entrega_recusa"] = (await adaptador(dev=False).otp_challenge(t, a_cid, "phone", phone)
                                   == {"sent": False, "reason": "delivery_unavailable"})

        ch = await a.otp_challenge(t, a_cid, "phone", phone)
        out["emite"] = {k: v for k, v in ch.items() if k != "dev_code"}
        c["autoritativo_emite"] = ch.get("sent") is True and ch.get("delivery") == "dev_log" and bool(ch.get("dev_code"))
        code = ch.get("dev_code", "")

        v_outro = await a.otp_verify(t, outro, "phone", phone, code)
        k = await chave(hashes[0][1])
        c["verify_de_outro_nao_anexa"] = (v_outro.get("verified") is False and k is not None
                                          and k["customer_id"] == a_cid and k["verification_class"] == "claimed")
        out["verify_outro"] = v_outro

        v_mesmo = await a.otp_verify(t, a_cid, "phone", phone, code)
        k = await chave(hashes[0][1])
        # sob --mutar-subject o verify alheio JA consumiu o desafio; o controle nao
        # tem como passar, e isso e dito em vez de mascarado
        c["verify_mesmo_cliente_possessed"] = (v_mesmo.get("verified") is True and k is not None
                                               and k["customer_id"] == a_cid
                                               and k["verification_class"] == "possessed"
                                               and k["provenance"] == "authoritative")
        out["verify_mesmo"] = v_mesmo

        async with httpx.AsyncClient(timeout=15) as http:
            rc = await http.post(ROTA, json={"tenant_id": t, "customer_id": a_cid, "kind": "cpf", "value": cpf})
            c["rota_viva_recusa_cpf"] = rc.status_code == 200 and rc.json() == {"sent": False, "reason": "undeliverable_kind"}
            rp = await http.post(ROTA, json={"tenant_id": t, "customer_id": a_cid, "kind": "phone", "value": phone})
            j = rp.json() if rp.status_code == 200 else {}
            out["rota_emite"] = {k2: v2 for k2, v2 in j.items() if k2 != "dev_code"}
            c["rota_viva_emite"] = j.get("sent") is True and bool(j.get("dev_code"))
        return out
    finally:
        async with db.acquire() as conn:
            for kind, h in hashes:
                await conn.execute("DELETE FROM identity.customer_secondary_keys WHERE tenant_id=$1 AND kind=$2 "
                                   "AND value_hash=$3", t, kind, h)
            await conn.execute("DELETE FROM identity.customer_external_refs WHERE tenant_id=$1 AND system=$2",
                               t, SYSTEM)
            await conn.execute("DELETE FROM identity.customers WHERE customer_id = ANY($1::text[])", list(cids))
        for kind, h in hashes:
            await r.delete("%s:identity:%s:%s" % (t, kind, h), "%s:otp:chal:%s:%s" % (t, kind, h),
                           "%s:otp:rl:%s:%s" % (t, kind, h))


async def legado(s, r, db):
    t = s.tenant_id
    salt = os.getenv("PLUGHUB_IDENTITY_SALT", "plughub_identity_demo_salt")
    idx = IdentityIndex(redis=r, salt=salt, db_pool=db, phone_region=PhoneRegionConfig(get_settings().config_api_url))
    sx = "%06d" % (uuid.uuid4().int % 1000000)
    cid = "cus_probe_idn13_" + sx
    cpf, phone = "130" + sx + "13", "+55110130" + sx[:5]
    hc, hp = hash_anchor(salt, "cpf", cpf, None), hash_anchor(salt, "phone", phone, None)
    kc, kp = idx._identity_key(t, "cpf", hc), idx._identity_key(t, "phone", hp)
    out = {"mutar": {"leitura": MUT_LEIT, "escrita": MUT_ESCR, "migracao": MUT_MIGR}, "casos": {}}
    c = out["casos"]

    async def plantar():
        async with db.acquire() as conn:
            await conn.execute("INSERT INTO identity.customers (customer_id, tenant_id, status) VALUES ($1,$2,'identified') "
                               "ON CONFLICT (customer_id) DO NOTHING", cid, t)
            for kind, h, conf in (("cpf", hc, 0.9), ("phone", hp, 0.7)):
                await conn.execute(
                    "INSERT INTO identity.customer_secondary_keys (tenant_id, kind, value_hash, customer_id, confidence, "
                    "verification_class, verified_at) VALUES ($1,$2,$3,$4,$5,'possessed',NOW()) "
                    "ON CONFLICT (tenant_id, kind, value_hash) DO UPDATE SET verification_class='possessed', "
                    "verified_at=NOW(), customer_id=EXCLUDED.customer_id", t, kind, h, cid, conf)
        await r.set(kc, idx_mod._encode_index(cid, "possessed"), ex=3600)
        await r.set(kp, idx_mod._encode_index(cid, "possessed"), ex=3600)

    async def linha(kind, h):
        async with db.acquire() as conn:
            return await conn.fetchrow("SELECT verification_class, verified_at FROM identity.customer_secondary_keys "
                                       "WHERE tenant_id=$1 AND kind=$2 AND value_hash=$3", t, kind, h)

    try:
        if MUT_LEIT:
            idx_mod.effective_verification_class = lambda kind, vc: vc
        if MUT_ESCR:
            idx_mod._writer_verification_class = lambda kind, vc: vc
        if MUT_MIGR:
            idx_mod.DELIVERABLE_KINDS = ("phone", "email", "cpf")

        await plantar()
        ref = await idx.resolve_or_provision(t, [{"kind": "cpf", "value": cpf}], provision=False)
        c["legado_quente_resolve_claimed"] = (ref.customer_id == cid and ref.matched_by == "existing"
                                              and ref.verification_class == "claimed")
        refp = await idx.resolve_or_provision(t, [{"kind": "phone", "value": phone}], provision=False)
        controle_antes = refp.customer_id == cid and refp.verification_class == "possessed"

        await r.delete(kc)
        ref = await idx.resolve_or_provision(t, [{"kind": "cpf", "value": cpf}], provision=False)
        re = idx_mod._decode_index(await r.get(kc))
        c["legado_frio_resolve_claimed"] = (ref.customer_id == cid and ref.matched_by == "durable"
                                            and ref.verification_class == "claimed"
                                            and re is not None and re[1] == "claimed")
        out["frio"] = {"matched_by": ref.matched_by, "vc": ref.verification_class, "reidratado": re}

        try:
            await idx.attach_anchor(t, cid, "cpf", cpf, verification_class="possessed")
            c["escrita_recusa"] = False
        except ValueError:
            c["escrita_recusa"] = True

        await plantar()
        n = await idx.migrate_undeliverable_possession()
        lc, lp = await linha("cpf", hc), await linha("phone", hp)
        rc, rp = idx_mod._decode_index(await r.get(kc)), idx_mod._decode_index(await r.get(kp))
        ttl = await r.ttl(kc)
        out["migracao"] = {"linhas": n, "cpf": dict(lc) if lc else None, "indice_cpf": rc, "ttl": ttl}
        c["migracao_rebaixa"] = (n >= 1 and lc is not None and lc["verification_class"] == "claimed"
                                 and lc["verified_at"] is None and rc == (cid, "claimed") and ttl > 0)
        c["controle_phone_possessed"] = (controle_antes and lp is not None and lp["verification_class"] == "possessed"
                                         and rp == (cid, "possessed"))
        return out
    finally:
        async with db.acquire() as conn:
            await conn.execute("DELETE FROM identity.customer_secondary_keys WHERE tenant_id=$1 AND "
                               "((kind='cpf' AND value_hash=$2) OR (kind='phone' AND value_hash=$3))", t, hc, hp)
            await conn.execute("DELETE FROM identity.customers WHERE customer_id=$1", cid)
        await r.delete(kc, kp)


async def main():
    s = get_settings()
    r = aioredis.from_url(s.redis_url)
    db = await asyncpg.create_pool(s.database_url, min_size=1, max_size=2)
    try:
        if MODO == "censo":
            return await censo(s, db, r)
        if MODO == "legado":
            return await legado(s, r, db)
        return await exercicio(s, r, db)
    finally:
        await db.close()
        await r.aclose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), default=str))
