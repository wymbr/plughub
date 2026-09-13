"""
test_identity_resolve_temperature.py — IDN-11 (2026-09-13)

*"Estas âncoras identificam quem?"* tem UMA resposta, com o índice Redis quente,
frio ou parcialmente quente. Até aqui eram duas regras de empate — `ambiguous` no
caminho quente, "primeiro estritamente maior" no frio — e, com o Redis parcial, o
dono das âncoras frias nem entrava na decisão.

A proposição contra Postgres e Redis reais é do `probe_identity_index_owner.sh`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from plughub_channel_gateway.identity.index import IdentityIndex, _encode_index
from plughub_channel_gateway.identity.normalize import hash_anchor, kind_confidence

SALT = "s"
T = "t"
PHONE_A, PHONE_B = "+5511999990001", "+5511999990002"
CPF_B = "12345678909"


class _Redis:
    def __init__(self):
        self.kv: dict[str, str] = {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None):
        self.kv[k] = v


def _pg(donos):
    async def fetchrow(sql, tenant, kind, vh):
        d = donos.get((kind, vh))
        if not d:
            return None
        return {"customer_id": d[0], "verification_class": d[1],
                "confidence": kind_confidence(kind), "provenance": None}
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=fetchrow)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _h(kind, value):
    return hash_anchor(SALT, kind, value)


def _anc(*pares):
    return [{"kind": k, "value": v} for k, v in pares]


class TestMesmaRespostaQualquerTemperatura:
    async def test_empate_entre_clientes_e_ambiguo_quente_e_frio(self):
        donos = {("phone", _h("phone", PHONE_A)): ("cus_a", "claimed"),
                 ("phone", _h("phone", PHONE_B)): ("cus_b", "claimed")}
        anc = _anc(("phone", PHONE_A), ("phone", PHONE_B))

        # FRIO: só o cadastro sabe. Antes: vencia a ORDEM das âncoras.
        frio = IdentityIndex(redis=_Redis(), salt=SALT, db_pool=_pg(donos))
        ref_frio = await frio.resolve_or_provision(T, anc, provision=False)

        # QUENTE: o índice sabe tudo.
        quente_redis = _Redis()
        quente = IdentityIndex(redis=quente_redis, salt=SALT, db_pool=_pg(donos))
        quente_redis.kv[quente._identity_key(T, "phone", _h("phone", PHONE_A))] = _encode_index("cus_a", "claimed")
        quente_redis.kv[quente._identity_key(T, "phone", _h("phone", PHONE_B))] = _encode_index("cus_b", "claimed")
        ref_quente = await quente.resolve_or_provision(T, anc, provision=False)

        assert ref_frio.matched_by == "ambiguous"
        assert ref_quente.matched_by == "ambiguous"
        assert ref_frio.customer_id == ref_quente.customer_id == ""   # IDN-12

    async def test_ambiguo_frio_nao_escreve_nada_no_indice(self):
        donos = {("phone", _h("phone", PHONE_A)): ("cus_a", "claimed"),
                 ("phone", _h("phone", PHONE_B)): ("cus_b", "claimed")}
        redis = _Redis()
        idx = IdentityIndex(redis=redis, salt=SALT, db_pool=_pg(donos))
        await idx.resolve_or_provision(T, _anc(("phone", PHONE_A), ("phone", PHONE_B), ("email", "x@y.com")),
                                       provision=False)
        assert redis.kv == {}, "sob ambiguidade nada e reidratado nem anexado"

    async def test_redis_parcial_considera_o_dono_da_ancora_fria(self):
        # phone de A quente no índice (claimed, 0.70); CPF de B frio, no cadastro (0.90).
        # Frio de tudo, vence B. Antes, com o phone quente, o CPF nem entrava: vencia A.
        donos = {("phone", _h("phone", PHONE_A)): ("cus_a", "claimed"),
                 ("cpf", _h("cpf", CPF_B)): ("cus_b", "claimed")}
        anc = _anc(("phone", PHONE_A), ("cpf", CPF_B))

        frio = IdentityIndex(redis=_Redis(), salt=SALT, db_pool=_pg(donos))
        ref_frio = await frio.resolve_or_provision(T, anc, provision=False)

        parcial_redis = _Redis()
        parcial = IdentityIndex(redis=parcial_redis, salt=SALT, db_pool=_pg(donos))
        parcial_redis.kv[parcial._identity_key(T, "phone", _h("phone", PHONE_A))] = _encode_index("cus_a", "claimed")
        ref_parcial = await parcial.resolve_or_provision(T, anc, provision=False)

        assert ref_frio.customer_id == ref_parcial.customer_id == "cus_b"
        assert ref_parcial.matched_by == "durable"
        # e o phone de A, quente e de outro cliente, NÃO é reapontado para B
        assert parcial_redis.kv[parcial._identity_key(T, "phone", _h("phone", PHONE_A))] == _encode_index("cus_a", "claimed")

    async def test_vencedor_unico_segue_igual_nos_dois(self):
        # Controle POSITIVO: sem empate, a resposta não mudou.
        # (Era um CPF `possessed` — o legado que a IDN-13 passou a ler como `claimed`.)
        donos = {("phone", _h("phone", PHONE_B)): ("cus_b", "possessed")}
        frio = IdentityIndex(redis=_Redis(), salt=SALT, db_pool=_pg(donos))
        ref = await frio.resolve_or_provision(T, _anc(("phone", PHONE_B)), provision=False)
        assert (ref.customer_id, ref.matched_by, ref.verification_class) == ("cus_b", "durable", "possessed")
