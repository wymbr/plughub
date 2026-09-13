"""
test_identity_possession_deliverable.py — IDN-13 (2026-09-13)

`possessed` só existe em âncora ENTREGÁVEL (ADR adr-identity-door-evidence D8). Até a
PID-10 o OTP desafiava CPF e gravava a posse nele, e o portão de retomada
(`pending_workflow_get`) lê essa classe: quem digitasse o CPF recebia as pendências.
A PID-10 fechou o produtor; aqui fecham as três pontas do que ficou gravado:

  - LEITURA: a classe guardada (Redis quente ou cadastro) passa por
    `effective_verification_class` em todo ponto que a lê;
  - ESCRITA: `attach_anchor`/`promote_to_durable` recusam gravar posse em kind
    não-entregável;
  - DADO: `migrate_undeliverable_possession` rebaixa o legado no cadastro e na chave
    quente do mesmo cliente.

Contra Postgres e Redis reais: `infra/test/probe_otp_gate.sh` (ramos A e E).
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from plughub_channel_gateway.identity import effective_verification_class
from plughub_channel_gateway.identity.index import IdentityIndex, _decode_index, _encode_index
from plughub_channel_gateway.identity.normalize import hash_anchor

SALT = "s"
T = "t"
CPF, PHONE = "52998224725", "+5511999990001"


class _Redis:
    def __init__(self):
        self.kv: dict[str, str] = {}
        self.keepttl: list[str] = []

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None, keepttl=None):
        self.kv[k] = v
        if keepttl:
            self.keepttl.append(k)


def _pool(conn):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _pg(donos):
    async def fetchrow(sql, tenant, kind, vh):
        d = donos.get((kind, vh))
        if not d:
            return None
        return {"customer_id": d[0], "verification_class": d[1], "confidence": 0.9, "provenance": None}
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=fetchrow)
    return _pool(conn)


def _h(kind, value):
    return hash_anchor(SALT, kind, value)


class TestRegra:
    @pytest.mark.parametrize("kind,vc,esperado", [
        ("cpf", "possessed", "claimed"), ("princ", "possessed", "claimed"), ("dev", "possessed", "claimed"),
        ("phone", "possessed", "possessed"), ("email", "possessed", "possessed"),
        ("cpf", "claimed", "claimed"), ("phone", "claimed", "claimed"),
    ])
    def test_possessed_so_em_kind_entregavel(self, kind, vc, esperado):
        assert effective_verification_class(kind, vc) == esperado


class TestLeitura:
    async def test_cpf_possessed_quente_no_redis_resolve_claimed(self):
        r = _Redis()
        idx = IdentityIndex(redis=r, salt=SALT)
        r.kv[idx._identity_key(T, "cpf", _h("cpf", CPF))] = _encode_index("cus_a", "possessed")
        ref = await idx.resolve_or_provision(T, [{"kind": "cpf", "value": CPF}], provision=False)
        assert (ref.customer_id, ref.verification_class) == ("cus_a", "claimed")

    async def test_cpf_possessed_frio_no_cadastro_resolve_claimed_e_nao_reidrata_posse(self):
        r = _Redis()
        idx = IdentityIndex(redis=r, salt=SALT, db_pool=_pg({("cpf", _h("cpf", CPF)): ("cus_a", "possessed")}))
        ref = await idx.resolve_or_provision(T, [{"kind": "cpf", "value": CPF}], provision=False)
        assert (ref.customer_id, ref.matched_by, ref.verification_class) == ("cus_a", "durable", "claimed")
        assert _decode_index(r.kv[idx._identity_key(T, "cpf", _h("cpf", CPF))]) == ("cus_a", "claimed")

    async def test_controle_phone_possessed_segue_possessed(self):
        # Sem este, os dois de cima passariam por um resolve que rebaixa tudo.
        r = _Redis()
        idx = IdentityIndex(redis=r, salt=SALT)
        r.kv[idx._identity_key(T, "phone", _h("phone", PHONE))] = _encode_index("cus_a", "possessed")
        ref = await idx.resolve_or_provision(T, [{"kind": "phone", "value": PHONE}], provision=False)
        assert ref.verification_class == "possessed"

    async def test_cpf_legado_nao_ganha_mais_o_bonus_de_posse_no_desempate(self):
        # Antes: cpf possessed (0.90 + 1.0) vencia um phone possessed de outro cliente
        # (0.70 + 1.0). A posse que não existe não pode decidir QUEM é o cliente.
        r = _Redis()
        idx = IdentityIndex(redis=r, salt=SALT)
        r.kv[idx._identity_key(T, "cpf", _h("cpf", CPF))] = _encode_index("cus_cpf", "possessed")
        r.kv[idx._identity_key(T, "phone", _h("phone", PHONE))] = _encode_index("cus_phone", "possessed")
        ref = await idx.resolve_or_provision(
            T, [{"kind": "cpf", "value": CPF}, {"kind": "phone", "value": PHONE}], provision=False)
        assert ref.customer_id == "cus_phone"


class TestEscrita:
    async def test_attach_possessed_em_cpf_recusa(self):
        idx = IdentityIndex(redis=_Redis(), salt=SALT)
        with pytest.raises(ValueError, match="IDN-13"):
            await idx.attach_anchor(T, "cus_a", "cpf", CPF, verification_class="possessed")

    async def test_promote_possessed_em_cpf_recusa(self):
        idx = IdentityIndex(redis=_Redis(), salt=SALT, db_pool=_pool(AsyncMock()))
        with pytest.raises(ValueError, match="IDN-13"):
            await idx.promote_to_durable(T, "cus_a", [{"kind": "cpf", "value": CPF}], verification_class="possessed")

    async def test_attach_claimed_sobre_cpf_legado_nao_preserva_a_posse(self):
        r = _Redis()
        idx = IdentityIndex(redis=r, salt=SALT)
        k = idx._identity_key(T, "cpf", _h("cpf", CPF))
        r.kv[k] = _encode_index("cus_a", "possessed")
        await idx.attach_anchor(T, "cus_a", "cpf", CPF, verification_class="claimed")
        assert _decode_index(r.kv[k]) == ("cus_a", "claimed")

    async def test_controle_attach_claimed_sobre_phone_possessed_preserva(self):
        r = _Redis()
        idx = IdentityIndex(redis=r, salt=SALT)
        k = idx._identity_key(T, "phone", _h("phone", PHONE))
        r.kv[k] = _encode_index("cus_a", "possessed")
        await idx.attach_anchor(T, "cus_a", "phone", PHONE, verification_class="claimed")
        assert _decode_index(r.kv[k]) == ("cus_a", "possessed")


class TestMigracao:
    async def test_rebaixa_e_reescreve_so_a_chave_quente_do_mesmo_cliente(self, caplog):
        r = _Redis()
        conn = AsyncMock()
        vh_a, vh_b = _h("cpf", CPF), _h("cpf", "11144477735")
        conn.fetch = AsyncMock(return_value=[
            {"tenant_id": T, "kind": "cpf", "value_hash": vh_a, "customer_id": "cus_a"},
            {"tenant_id": T, "kind": "cpf", "value_hash": vh_b, "customer_id": "cus_b"},
        ])
        idx = IdentityIndex(redis=r, salt=SALT, db_pool=_pool(conn))
        ka, kb = idx._identity_key(T, "cpf", vh_a), idx._identity_key(T, "cpf", vh_b)
        r.kv[ka] = _encode_index("cus_a", "possessed")
        r.kv[kb] = _encode_index("cus_outro", "possessed")   # o índice aponta para OUTRO cliente

        with caplog.at_level(logging.WARNING):
            n = await idx.migrate_undeliverable_possession()

        assert n == 2
        assert _decode_index(r.kv[ka]) == ("cus_a", "claimed") and r.keepttl == [ka]
        assert _decode_index(r.kv[kb]) == ("cus_outro", "possessed"), "chave de outro cliente não é da migração"
        sql, kinds = conn.fetch.await_args.args
        assert "UPDATE identity.customer_secondary_keys" in sql and "verified_at = NULL" in sql
        assert sorted(kinds) == ["email", "phone"]
        assert "IDN-13" in caplog.text

    async def test_sem_legado_nao_loga_nem_toca_o_indice(self, caplog):
        r = _Redis()
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        idx = IdentityIndex(redis=r, salt=SALT, db_pool=_pool(conn))
        with caplog.at_level(logging.WARNING):
            assert await idx.migrate_undeliverable_possession() == 0
        assert r.kv == {} and "IDN-13" not in caplog.text
