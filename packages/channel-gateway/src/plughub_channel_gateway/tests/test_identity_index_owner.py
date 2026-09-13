"""
test_identity_index_owner.py — IDN-10 (2026-09-13)

Os dois escritores do índice Redis — a identidade progressiva do caminho quente e a
reidratação do caminho `durable` — não apontam uma âncora para um cliente que o
CADASTRO atribui a outro. Antes, os dois apontavam: a promessa *"âncoras que apontam
a OUTRO cliente NÃO são tocadas"* só conferia o Redis.

A proposição contra Postgres e Redis reais é do `probe_identity_index_owner.sh`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from plughub_channel_gateway.identity.index import IdentityIndex, _decode_index, _encode_index
from plughub_channel_gateway.identity.normalize import hash_anchor

SALT = "s"
T = "t"


class _Redis:
    def __init__(self):
        self.kv: dict[str, str] = {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None):
        self.kv[k] = v


def _pg(donos: dict[tuple[str, str], tuple[str, str]]):
    """Pool cujo `fetchrow` responde pelo mapa (kind, value_hash) → (customer_id, vc).

    Serve às duas consultas que o resolve faz: dono da chave e melhor linha durável.
    """
    async def fetchrow(sql, tenant, kind, vh):
        d = donos.get((kind, vh))
        if not d:
            return None
        return {"customer_id": d[0], "verification_class": d[1], "confidence": 0.9, "provenance": None}

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


PHONE, EMAIL, CPF = "+5511999990000", "p@x.com", "12345678909"
PHONE2 = "+5511999990009"


class TestIdentidadeProgressiva:
    async def test_ancora_fria_com_outro_dono_no_cadastro_nao_e_anexada(self, caplog):
        redis = _Redis()
        idx = IdentityIndex(redis=redis, salt=SALT, db_pool=_pg({("phone", _h("phone", PHONE)): ("cus_dono", "claimed")}))
        redis.kv[idx._identity_key(T, "email", _h("email", EMAIL))] = _encode_index("cus_prospect", "claimed")

        ref = await idx.resolve_or_provision(T, [{"kind": "email", "value": EMAIL},
                                                  {"kind": "phone", "value": PHONE}])

        assert ref.customer_id == "cus_prospect"
        assert idx._identity_key(T, "phone", _h("phone", PHONE)) not in redis.kv
        assert "IDN-10" in caplog.text

    async def test_ancora_sem_dono_segue_anexada_e_com_o_mesmo_dono_preserva_a_classe(self):
        # Controle POSITIVO: a identidade progressiva continua funcionando.
        # (A âncora `possessed` era um CPF até a IDN-13 — a fixture era o próprio legado
        # do OTP ao CPF, que hoje é lido como `claimed`. Posse só em kind entregável.)
        redis = _Redis()
        idx = IdentityIndex(redis=redis, salt=SALT,
                            db_pool=_pg({("phone", _h("phone", PHONE2)): ("cus_prospect", "possessed")}))
        redis.kv[idx._identity_key(T, "email", _h("email", EMAIL))] = _encode_index("cus_prospect", "claimed")

        await idx.resolve_or_provision(T, [{"kind": "email", "value": EMAIL},
                                           {"kind": "phone", "value": PHONE},
                                           {"kind": "phone", "value": PHONE2}])

        assert _decode_index(redis.kv[idx._identity_key(T, "phone", _h("phone", PHONE))]) == ("cus_prospect", "claimed")
        assert _decode_index(redis.kv[idx._identity_key(T, "phone", _h("phone", PHONE2))]) == ("cus_prospect", "possessed")


class TestReidratacao:
    async def test_nao_aponta_para_o_vencedor_a_ancora_de_outro_dono(self, caplog):
        redis = _Redis()
        idx = IdentityIndex(redis=redis, salt=SALT, db_pool=_pg({
            ("cpf", _h("cpf", CPF)):       ("cus_vencedor", "claimed"),
            ("email", _h("email", EMAIL)): ("cus_outro", "claimed"),
        }))

        ref = await idx.resolve_or_provision(T, [{"kind": "cpf", "value": CPF},
                                                  {"kind": "email", "value": EMAIL},
                                                  {"kind": "phone", "value": PHONE}], provision=False)

        assert ref.matched_by == "durable"
        vencedor = ref.customer_id
        dono_do_email = "cus_outro" if vencedor == "cus_vencedor" else "cus_vencedor"
        chave_do_perdedor = ("email", EMAIL) if vencedor == "cus_vencedor" else ("cpf", CPF)
        assert idx._identity_key(T, chave_do_perdedor[0], _h(*chave_do_perdedor)) not in redis.kv, (
            "a ancora de %s foi apontada para %s" % (dono_do_email, vencedor))
        # a âncora sem linha no cadastro segue anexada ao vencedor (identidade progressiva)
        assert _decode_index(redis.kv[idx._identity_key(T, "phone", _h("phone", PHONE))])[0] == vencedor
        assert "IDN-10" in caplog.text
