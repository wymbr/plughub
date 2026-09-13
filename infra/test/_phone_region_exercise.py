# -*- coding: utf-8 -*-
"""Exercicio da IDN-14 DENTRO do container do gateway.

Lido por stdin: `docker exec -i [-e CFG_ADMIN_TOKEN=…] <gw> python - <modo> [--mutar-*]`.

  config       o pais que o resolvedor de producao le para o tenant.

  exercicio    o codigo da IMAGEM contra Postgres e Redis reais. Fixture: um cliente com
               o celular gravado em E.164 (`+55 11 9…`).
    nacional_casa_quente     `(11) 9…` resolve o MESMO cliente (indice quente)
    nacional_casa_frio       idem, so com o cadastro
    ddi_sem_mais_casa        `55 11 9…` resolve o mesmo cliente
    internacional_casa       CONTROLE: a forma gravada resolve (a fixture existe)
    sem_regiao_recusa        indice sem pais: `(11) 9…` nao resolve e nao cria nada
    cli_nao_vira_ancora      `contact_identifier` `cli_<digitos>` nao grava ancora phone
    cli_controle             CONTROLE: `contact_identifier` telefone grava a ancora
    rota_viva_nacional       a rota do PROCESSO de pe resolve `(11) 9…` para o cliente
  Mutacoes: --mutar-normalizacao (volta `"+" + digitos`) · --mutar-letras (aceita letra).

  invalidacao  a troca do pais no config-api vale SEM boot: override do tenant para `PT`
               -> a rota viva deixa de achar o cliente pelo numero nacional; o override
               sai -> volta a achar. O override e removido num `finally`.
    troca_de_pais_vale_sem_boot · volta_ao_default

Limpa sempre o que criou.
"""
import asyncio
import hashlib
import json
import os
import re
import sys
import uuid

import asyncpg
import httpx
import redis.asyncio as aioredis

from plughub_channel_gateway.adapters.webhook import WebhookAdapter
from plughub_channel_gateway.config import get_settings
from plughub_channel_gateway.identity import normalize as norm_mod
from plughub_channel_gateway.identity.index import IdentityIndex
from plughub_channel_gateway.identity.region import PhoneRegionConfig

MODO = sys.argv[1] if len(sys.argv) > 1 else "exercicio"
MUT_NORM = "--mutar-normalizacao" in sys.argv
MUT_LETRAS = "--mutar-letras" in sys.argv
GW_URL = os.environ.get("PROBE_GW_URL", "http://localhost:8010")   # vermelho-primeiro roda de fora
ROTA = GW_URL + "/v1/channels/webhook/identity/resolve"


async def _setup():
    s = get_settings()
    r = aioredis.from_url(s.redis_url)
    db = await asyncpg.create_pool(s.database_url, min_size=1, max_size=2)
    salt = os.getenv("PLUGHUB_IDENTITY_SALT", "plughub_identity_demo_salt")
    return s, r, db, salt


def _fixture():
    sx = "%08d" % (uuid.uuid4().int % 100000000)
    return {
        "cid": "cus_probe_idn14_" + sx,
        "intl": "+55 11 9%s" % sx,
        "nacional": "(11) 9%s-%s" % (sx[:4], sx[4:]),
        "ddi": "55 11 9%s" % sx,
        "e164": "+55119" + sx,
    }


async def _limpa(r, db, t, cids, hashes):
    async with db.acquire() as conn:
        await conn.execute("DELETE FROM identity.customer_secondary_keys WHERE tenant_id=$1 AND kind='phone' "
                           "AND value_hash = ANY($2::text[])", t, list(hashes))
        await conn.execute("DELETE FROM identity.customers WHERE customer_id = ANY($1::text[])", list(cids))
    for h in hashes:
        await r.delete("%s:identity:phone:%s" % (t, h))


async def exercicio(s, r, db, salt):
    t = s.tenant_id
    regiao = PhoneRegionConfig(s.config_api_url)
    out = {"mutar": {"normalizacao": MUT_NORM, "letras": MUT_LETRAS}, "regiao": await regiao(t), "casos": {}}
    c = out["casos"]
    f = _fixture()
    idx = IdentityIndex(redis=r, salt=salt, db_pool=db, phone_region=regiao)
    h = await idx.anchor_hash(t, "phone", f["intl"])
    k = idx._identity_key(t, "phone", h)
    digitos_cli = "7%010d" % (uuid.uuid4().int % 10000000000)
    h_cli_antigo = hashlib.sha256((salt + "+" + digitos_cli).encode()).hexdigest()
    tel_ctx = "+55 21 9" + digitos_cli[-8:]
    h_ctx = await idx.anchor_hash(t, "phone", tel_ctx)
    cids = {f["cid"]}
    h_extra: set = set()
    try:
        await idx.attach_anchor(t, f["cid"], "phone", f["intl"], persist_durable=True, provenance="declared")

        if MUT_NORM:
            norm_mod._e164_phone = lambda v, region: "+" + re.sub(r"\D", "", v)
        if MUT_LETRAS:
            norm_mod._PHONE_CHARS = re.compile(r".*")

        async def quem(valor, indice=idx):
            ref = await indice.resolve_or_provision(t, [{"kind": "phone", "value": valor}], provision=False)
            return ref.customer_id, ref.matched_by

        c["internacional_casa"] = (await quem(f["intl"]))[0] == f["cid"]
        c["nacional_casa_quente"] = await quem(f["nacional"]) == (f["cid"], "existing")
        c["ddi_sem_mais_casa"] = (await quem(f["ddi"]))[0] == f["cid"]
        await r.delete(k)
        c["nacional_casa_frio"] = await quem(f["nacional"]) == (f["cid"], "durable")

        sem = IdentityIndex(redis=r, salt=salt, db_pool=db, phone_region=None)
        ref = await sem.resolve_or_provision(t, [{"kind": "phone", "value": f["nacional"]}], provision=False)
        c["sem_regiao_recusa"] = (ref.customer_id, ref.matched_by) == ("", "none")
        out["sem_regiao"] = {"matched_by": ref.matched_by, "status": ref.status}

        # ⚠️ Medido pelos KINDS indexados no prospect, nunca por um hash esperado: com
        # letra aceita, o parser descarta o `cli_` e grava o número num hash que o
        # probe não previu — a primeira versão olhava só o hash antigo e não reprovava.
        async def kinds_indexados(ref):
            raw = await r.get("%s:customer:prospect:%s" % (t, ref.customer_id))
            return json.loads(raw).get("kinds") if raw else None

        anc = WebhookAdapter._anchors_from_context({"contact_identifier": "cli_" + digitos_cli})
        ref = await idx.resolve_or_provision(t, anc, provision=True)
        if ref.customer_id:
            cids.add(ref.customer_id)
        out["cli_kinds"] = await kinds_indexados(ref)
        try:   # sob mutação o `cli_` vira âncora: o hash dela entra na limpeza
            h_extra.add(await idx.anchor_hash(t, "phone", "cli_" + digitos_cli))
        except ValueError:
            pass
        c["cli_nao_vira_ancora"] = (out["cli_kinds"] == [] and
                                    await r.get("%s:identity:phone:%s" % (t, h_cli_antigo)) is None)
        anc = WebhookAdapter._anchors_from_context({"contact_identifier": tel_ctx})
        ref = await idx.resolve_or_provision(t, anc, provision=True)
        if ref.customer_id:
            cids.add(ref.customer_id)
        c["cli_controle"] = (await kinds_indexados(ref) == ["phone"]
                             and await r.get(idx._identity_key(t, "phone", h_ctx)) is not None)

        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(ROTA, json={"tenant_id": t, "provision": False,
                                               "anchors": [{"kind": "phone", "value": f["nacional"]}]})
            c["rota_viva_nacional"] = resp.status_code == 200 and resp.json().get("customer_id") == f["cid"]
        return out
    finally:
        # prospects efemeros (Redis) das chamadas com provision=True
        for cid in cids:
            await r.delete("%s:customer:prospect:%s" % (t, cid))
        await _limpa(r, db, t, cids, {h, h_cli_antigo, h_ctx} | h_extra)


async def invalidacao(s, r, db, salt):
    t = s.tenant_id
    token = os.environ.get("CFG_ADMIN_TOKEN", "")
    cfg = "http://config-api:3600/config/identity/default_phone_region"
    out = {"casos": {}}
    c = out["casos"]
    f = _fixture()
    idx = IdentityIndex(redis=r, salt=salt, db_pool=db, phone_region=PhoneRegionConfig(s.config_api_url))
    h = await idx.anchor_hash(t, "phone", f["intl"])
    await idx.attach_anchor(t, f["cid"], "phone", f["intl"], persist_durable=True, provenance="declared")
    async with httpx.AsyncClient(timeout=15) as http:
        async def acha():
            resp = await http.post(ROTA, json={"tenant_id": t, "provision": False,
                                               "anchors": [{"kind": "phone", "value": f["nacional"]}]})
            return resp.status_code == 200 and resp.json().get("customer_id") == f["cid"]

        async def espera(cond, s_max=20):
            for _ in range(s_max * 2):
                if await acha() == cond:
                    return True
                await asyncio.sleep(0.5)
            return False

        try:
            out["antes"] = await acha()
            put = await http.put(cfg, headers={"X-Admin-Token": token},
                                 json={"tenant_id": t, "value": "PT", "description": "probe IDN-14 (temporario)"})
            out["put"] = put.status_code
            c["troca_de_pais_vale_sem_boot"] = out["antes"] and put.status_code in (200, 201) and await espera(False)
        finally:
            dele = await http.delete(cfg, headers={"X-Admin-Token": token}, params={"tenant_id": t})
            out["delete"] = dele.status_code
        c["volta_ao_default"] = dele.status_code in (200, 204) and await espera(True)
    await _limpa(r, db, t, {f["cid"]}, {h})
    return out


async def main():
    s, r, db, salt = await _setup()
    try:
        if MODO == "config":
            return {"tenant": s.tenant_id, "regiao": await PhoneRegionConfig(s.config_api_url)(s.tenant_id)}
        if MODO == "invalidacao":
            return await invalidacao(s, r, db, salt)
        return await exercicio(s, r, db, salt)
    finally:
        await db.close()
        await r.aclose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), default=str))
