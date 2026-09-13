# -*- coding: utf-8 -*-
"""Exercicio da procedencia de identidade DENTRO do container do gateway (PID-12).

Lido por stdin: `docker exec -i -e T_ADM=... -e T_OPE=... <gw> python - [--mutar-sql]`.
Roda o codigo da IMAGEM contra Postgres e Redis reais.

CASOS
  rota_401          sem Bearer                      -> 401
  rota_403          operador (sem o campo)          -> 403
  import_cria       admin importa e1 (phone+cpf)    -> created, 2 chaves `authoritative`, `claimed`
  resolve_casa      /identity/resolve com o phone   -> devolve o cliente importado
  reimport_idem     importa e1 de novo              -> updated, MESMO customer_id, 1 cliente
  conflito_recusa   e2 traz o phone de e1           -> refused, e o phone segue de e1
  reatribui_zera    escritor comum anexa o phone a OUTRO cliente -> procedencia deixa de ser
                    `authoritative` (senao quem nao tem credencial herdaria a confianca)
  escritor_recusa   escritor comum pedindo `authoritative` -> ValueError

`--mutar-sql` troca o upsert por um que mantem a procedencia na reatribuicao: o
caso `reatribui_zera` TEM de reprovar, senao ele mede a fixture e nao a regra.

Imprime uma linha JSON. Limpa sempre o que criou (por hash e por id, nunca por tenant).
"""
import asyncio
import json
import os
import sys
import uuid

import asyncpg
import httpx
import redis.asyncio as aioredis

from plughub_channel_gateway.config import get_settings
from plughub_channel_gateway.identity import index as idx_mod
from plughub_channel_gateway.identity.index import IdentityIndex
from plughub_channel_gateway.identity.normalize import hash_anchor

MUTAR = "--mutar-sql" in sys.argv
BASE = "http://localhost:8010/v1/channels/webhook/identity"
SYSTEM = "__probe_pid12__"


async def main():
    s = get_settings()
    tenant = s.tenant_id
    salt = os.getenv("PLUGHUB_IDENTITY_SALT", "plughub_identity_demo_salt")
    sufixo = "%06d" % (uuid.uuid4().int % 1000000)
    phone, cpf = "+55110000" + sufixo[:5], "000" + sufixo + "00"
    db = await asyncpg.create_pool(s.database_url, min_size=1, max_size=2)
    rds = aioredis.from_url(s.redis_url)
    idx = IdentityIndex(redis=rds, salt=salt, db_pool=db)
    hp, hc = hash_anchor(salt, "phone", phone), hash_anchor(salt, "cpf", cpf)
    outro = "cus_probe_pid12_" + sufixo
    out = {"mutar_sql": MUTAR, "casos": {}}
    c = out["casos"]
    cids = set([outro])
    adm = {"authorization": "Bearer " + os.environ.get("T_ADM", "")}
    ope = {"authorization": "Bearer " + os.environ.get("T_OPE", "")}
    corpo = lambda rows: {"system": SYSTEM, "customers": rows}
    e1 = {"external_id": "e1-" + sufixo, "anchors": [{"kind": "phone", "value": phone},
                                                     {"kind": "cpf", "value": cpf}],
          "attributes": {"nome": "Probe PID-12"}}

    async def chave(h, kind):
        async with db.acquire() as conn:
            return await conn.fetchrow(
                "SELECT customer_id, provenance, verification_class FROM identity.customer_secondary_keys "
                "WHERE tenant_id = $1 AND kind = $2 AND value_hash = $3", tenant, kind, h)

    try:
        async with httpx.AsyncClient(timeout=15) as http:
            c["rota_401"] = (await http.post(BASE + "/import", json=corpo([e1]))).status_code == 401
            c["rota_403"] = (await http.post(BASE + "/import", json=corpo([e1]), headers=ope)).status_code == 403

            r = await http.post(BASE + "/import", json=corpo([e1]), headers=adm)
            out["import_http"] = r.status_code
            j = r.json() if r.status_code == 200 else {}
            linha = (j.get("results") or [{}])[0]
            cid = linha.get("customer_id", "")
            if cid:
                cids.add(cid)
            kp, kc = await chave(hp, "phone"), await chave(hc, "cpf")
            c["import_cria"] = bool(
                linha.get("outcome") == "created" and kp and kc
                and kp["customer_id"] == cid and kc["customer_id"] == cid
                and kp["provenance"] == "authoritative" and kc["provenance"] == "authoritative"
                and kp["verification_class"] == "claimed")

            r = await http.post(BASE + "/resolve", json={
                "tenant_id": tenant, "anchors": [{"kind": "phone", "value": phone}], "provision": False})
            c["resolve_casa"] = r.status_code == 200 and r.json().get("customer_id") == cid and bool(cid)

            r = await http.post(BASE + "/import", json=corpo([e1]), headers=adm)
            l2 = ((r.json() if r.status_code == 200 else {}).get("results") or [{}])[0]
            async with db.acquire() as conn:
                n_refs = await conn.fetchval(
                    "SELECT count(*) FROM identity.customer_external_refs WHERE tenant_id=$1 AND system=$2",
                    tenant, SYSTEM)
            c["reimport_idem"] = l2.get("outcome") == "updated" and l2.get("customer_id") == cid and n_refs == 1

            e2 = {"external_id": "e2-" + sufixo, "anchors": [{"kind": "phone", "value": phone}]}
            r = await http.post(BASE + "/import", json=corpo([e2]), headers=adm)
            l3 = ((r.json() if r.status_code == 200 else {}).get("results") or [{}])[0]
            if l3.get("customer_id"):
                cids.add(l3["customer_id"])
            kp = await chave(hp, "phone")
            c["conflito_recusa"] = l3.get("outcome") == "refused" and kp and kp["customer_id"] == cid

        if MUTAR:
            idx_mod._SQL_UPSERT_KEY = idx_mod._SQL_UPSERT_KEY.replace(
                "WHEN identity.customer_secondary_keys.customer_id <> EXCLUDED.customer_id\n"
                "                              THEN EXCLUDED.provenance\n", "")
            out["mutacao_aplicada"] = "customer_id <> EXCLUDED" not in idx_mod._SQL_UPSERT_KEY
        await idx.attach_anchor(tenant, outro, "phone", phone, persist_durable=True, provenance="declared")
        kp = await chave(hp, "phone")
        c["reatribui_zera"] = bool(kp and kp["customer_id"] == outro and kp["provenance"] == "declared")

        try:
            await idx.attach_anchor(tenant, outro, "cpf", cpf, persist_durable=True, provenance="authoritative")
            c["escritor_recusa"] = False
        except ValueError:
            c["escritor_recusa"] = True
        return out
    finally:
        async with db.acquire() as conn:
            await conn.execute(
                "DELETE FROM identity.customer_secondary_keys WHERE tenant_id=$1 AND "
                "((kind='phone' AND value_hash=$2) OR (kind='cpf' AND value_hash=$3))", tenant, hp, hc)
            await conn.execute("DELETE FROM identity.customer_external_refs WHERE tenant_id=$1 AND system=$2",
                               tenant, SYSTEM)
            await conn.execute("DELETE FROM identity.customers WHERE customer_id = ANY($1::text[])", list(cids))
        await rds.delete("%s:identity:phone:%s" % (tenant, hp), "%s:identity:cpf:%s" % (tenant, hc))
        await rds.aclose()
        await db.close()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), default=str))
