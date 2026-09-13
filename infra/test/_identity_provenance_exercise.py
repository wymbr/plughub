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
  resolve_expoe     a RESPOSTA HTTP do resolve traz `provenance: authoritative`
  le_via_redis      resolve (indice Redis quente) devolve a procedencia do cadastro   (IDN-07)
  le_via_duravel    resolve com o Redis frio (caminho `durable`) tambem a devolve      (IDN-07)
  divergencia_nao_empresta  Redis aponta a ancora para OUTRO cliente -> procedencia None:
                    a confianca de um cliente nao e emprestada a outro na leitura
  anchor_provenance_confere a pergunta da PID-10 responde por (ancora, CLIENTE)
  posse_antes       testemunha: o phone foi provado (`possessed`) para o cliente importado
  reatribui_posse   a mesma reatribuicao ZERA a prova de posse no PG (`claimed`, sem
                    `verified_at`) e o indice Redis concorda (IDN-09) — senao o OTP de um
                    cliente valeria para outro

`--mutar-sql` troca o upsert por um que mantem a procedencia na reatribuicao: o
caso `reatribui_zera` TEM de reprovar, senao ele mede a fixture e nao a regra.
`--mutar-posse` devolve a regra antiga do `possessed` (sobrevive a troca de
cliente): o caso `reatribui_posse` TEM de reprovar.
`--mutar-leitura` tira da leitura a conferencia do cliente: o caso
`divergencia_nao_empresta` TEM de reprovar.

Imprime uma linha JSON. Limpa sempre o que criou (por hash e por id, nunca por tenant).
"""
import asyncio
import json
import os
import re
import sys
import uuid

import asyncpg
import httpx
import redis.asyncio as aioredis

from plughub_channel_gateway.config import get_settings
# IDN-14: o mesmo resolvedor de país do gateway; hash à mão leva região None
# porque toda fixture de telefone aqui tem '+', e aí a região não entra.
from plughub_channel_gateway.identity.region import PhoneRegionConfig
from plughub_channel_gateway.identity import index as idx_mod
from plughub_channel_gateway.identity.index import IdentityIndex
from plughub_channel_gateway.identity.normalize import hash_anchor

MUTAR = "--mutar-sql" in sys.argv
MUTAR_POSSE = "--mutar-posse" in sys.argv
MUTAR_LEITURA = "--mutar-leitura" in sys.argv
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
    idx = IdentityIndex(redis=rds, salt=salt, db_pool=db, phone_region=PhoneRegionConfig(get_settings().config_api_url))
    hp, hc = hash_anchor(salt, "phone", phone, None), hash_anchor(salt, "cpf", cpf, None)
    outro = "cus_probe_pid12_" + sufixo
    out = {"mutar_sql": MUTAR, "mutar_posse": MUTAR_POSSE, "casos": {}}
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
                "tenant_id": tenant, "anchors": [{"kind": "phone", "value": phone}], "provision": False},
                headers={"x-service-token": s.channel_gateway_service_token})   # IDN-06
            c["resolve_casa"] = r.status_code == 200 and r.json().get("customer_id") == cid and bool(cid)
            c["resolve_expoe"] = r.status_code == 200 and r.json().get("provenance") == "authoritative"

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

        # ── IDN-07: a LEITURA da procedencia ─────────────────────────────────
        if MUTAR_LEITURA:
            async def _sem_conferir(self, tenant_id, kind, value_hash, customer_id):
                async with self._db.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT provenance FROM identity.customer_secondary_keys "
                        "WHERE tenant_id=$1 AND kind=$2 AND value_hash=$3", tenant_id, kind, value_hash)
                return row["provenance"] if row else None
            IdentityIndex._pg_provenance = _sem_conferir
            out["mutacao_leitura_aplicada"] = True

        ref = await idx.resolve_or_provision(tenant, [{"kind": "phone", "value": phone}], provision=False)
        c["le_via_redis"] = ref.matched_by == "existing" and ref.customer_id == cid and ref.provenance == "authoritative"

        await rds.delete("%s:identity:cpf:%s" % (tenant, hc))
        ref = await idx.resolve_or_provision(tenant, [{"kind": "cpf", "value": cpf}], provision=False)
        c["le_via_duravel"] = ref.matched_by == "durable" and ref.customer_id == cid and ref.provenance == "authoritative"

        # Redis aponta o phone para um prospect enquanto o cadastro diz que ele e do
        # importado (a forma da IDN-10). A confianca do importado NAO pode ser
        # emprestada ao prospect.
        prospect = "cus_probe_prospect_" + sufixo
        await rds.set("%s:identity:phone:%s" % (tenant, hp), idx_mod._encode_index(prospect, "claimed"))
        ref = await idx.resolve_or_provision(tenant, [{"kind": "phone", "value": phone}], provision=False)
        c["divergencia_nao_empresta"] = ref.customer_id == prospect and ref.provenance is None
        c["anchor_provenance_confere"] = (
            await idx.anchor_provenance(tenant, cid, "phone", phone) == "authoritative"
            and await idx.anchor_provenance(tenant, prospect, "phone", phone) is None)
        await rds.set("%s:identity:phone:%s" % (tenant, hp), idx_mod._encode_index(cid, "claimed"))

        # prova de posse para o cliente importado, pelo caminho do OTP (attach possessed)
        await idx.attach_anchor(tenant, cid, "phone", phone, verification_class="possessed",
                                persist_durable=True, provenance="declared")
        kp = await chave(hp, "phone")
        c["posse_antes"] = bool(kp and kp["customer_id"] == cid and kp["verification_class"] == "possessed")

        if MUTAR_POSSE:
            # regex e nao string exata: o que se muta e a REGRA, e a indentacao do SQL
            # nao pode decidir se a mutacao casou.
            novo, n1 = re.subn(
                r"WHEN identity\.customer_secondary_keys\.customer_id = EXCLUDED\.customer_id\s+"
                r"AND (identity\.customer_secondary_keys\.verification_class = 'possessed')",
                r"WHEN \1", idx_mod._SQL_UPSERT_KEY, count=1)
            novo, n2 = re.subn(
                r"verified_at = CASE\s+WHEN identity\.customer_secondary_keys\.customer_id = EXCLUDED\.customer_id\s+"
                r"THEN (COALESCE\(identity\.customer_secondary_keys\.verified_at, EXCLUDED\.verified_at\))\s+"
                r"ELSE EXCLUDED\.verified_at END",
                r"verified_at = \1", novo, count=1)
            out["mutacao_posse_aplicada"] = (n1, n2) == (1, 1)
            idx_mod._SQL_UPSERT_KEY = novo
        if MUTAR:
            idx_mod._SQL_UPSERT_KEY = idx_mod._SQL_UPSERT_KEY.replace(
                "WHEN identity.customer_secondary_keys.customer_id <> EXCLUDED.customer_id\n"
                "                              THEN EXCLUDED.provenance\n", "")
            out["mutacao_aplicada"] = "customer_id <> EXCLUDED" not in idx_mod._SQL_UPSERT_KEY
        await idx.attach_anchor(tenant, outro, "phone", phone, persist_durable=True, provenance="declared")
        kp = await chave(hp, "phone")
        c["reatribui_zera"] = bool(kp and kp["customer_id"] == outro and kp["provenance"] == "declared")
        async with db.acquire() as conn:
            vat = await conn.fetchval(
                "SELECT verified_at FROM identity.customer_secondary_keys "
                "WHERE tenant_id=$1 AND kind='phone' AND value_hash=$2", tenant, hp)
        redis_idx = idx_mod._decode_index(await rds.get("%s:identity:phone:%s" % (tenant, hp)))
        c["reatribui_posse"] = bool(
            kp and kp["customer_id"] == outro and kp["verification_class"] == "claimed" and vat is None
            and redis_idx == (outro, "claimed"))

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
