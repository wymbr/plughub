"""
test_identity_operator_register.py — IDN-08 (2026-09-13)

O cadastro feito pelo operador no Console passa a ser DURÁVEL e com procedência
`operator`. As decisões de `IdentityIndex.register_by_operator` são testadas aqui com
o cadastro substituído; a escrita no Postgres, o portão da rota (credencial, ABAC no
pool da sessão, tenant do JWT) e a procedência gravada são do
`infra/test/probe_identity_operator.sh`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from plughub_channel_gateway.identity.index import IdentityIndex, _encode_index
from plughub_channel_gateway.identity.normalize import hash_anchor

SALT = "s"
T = "t"
PHONE, EMAIL = "+5511999990001", "a@b.com"


class _Redis:
    def __init__(self):
        self.kv: dict[str, str] = {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None, keepttl=None):
        self.kv[k] = v


def _idx(donos=None, attrs=None, region="BR"):
    # Os quatro métodos substituídos existem no produto — o mock não os cria (VOZ-03).
    for m in ("_pg_key_owner", "attach_anchor", "get_customer", "update_attributes"):
        assert hasattr(IdentityIndex, m), m
    r = _Redis()
    idx = IdentityIndex(redis=r, salt=SALT, db_pool=MagicMock(), phone_region=region)
    donos = donos or {}
    idx._pg_key_owner = AsyncMock(side_effect=lambda t, k, vh: donos.get((k, vh)))
    idx.attach_anchor = AsyncMock(return_value=True)
    idx.get_customer = AsyncMock(return_value={"attributes": attrs or {}} if attrs is not None else None)
    idx.update_attributes = AsyncMock(return_value=True)
    return idx, r


def _h(kind, value):
    return hash_anchor(SALT, kind, value, "BR")


class TestRecusas:
    async def test_kind_invalido_recusa_nomeando_e_nao_escreve(self):
        # O caso real: a aba mandava `kind: "telefone"`.
        idx, _r = _idx()
        res = await idx.register_by_operator(T, [{"kind": "telefone", "value": PHONE}], name="Ana")
        assert (res.outcome, res.reason, res.invalid) == ("refused", "invalid_anchors", ["telefone"])
        idx.attach_anchor.assert_not_awaited()
        idx.update_attributes.assert_not_awaited()

    async def test_telefone_nacional_sem_pais_do_tenant_recusa(self):
        idx, _r = _idx(region=None)
        res = await idx.register_by_operator(T, [{"kind": "phone", "value": "11 99999-0001"}])
        assert res.reason == "invalid_anchors"

    async def test_sem_ancora_recusa(self):
        idx, _r = _idx()
        res = await idx.register_by_operator(T, [], name="Ana")
        assert (res.outcome, res.reason) == ("refused", "no_anchors")
        idx.update_attributes.assert_not_awaited()

    async def test_ancora_de_outro_cliente_no_cadastro_recusa_sem_mover(self):
        # email frio de cus_outro (0.80) vence o phone quente de cus_a (0.70); o phone é
        # de OUTRO cliente que não o vencedor, e isso recusa o cadastro inteiro — anexá-lo
        # moveria a âncora de cus_a para cus_outro.
        idx, r = _idx(donos={("email", _h("email", EMAIL)): ("cus_outro", "claimed", 0.8)})
        r.kv[idx._identity_key(T, "phone", _h("phone", PHONE))] = _encode_index("cus_a", "claimed")
        res = await idx.register_by_operator(
            T, [{"kind": "phone", "value": PHONE}, {"kind": "email", "value": EMAIL}])
        assert res.outcome == "refused" and res.reason == "anchor_owned_by_other_customer"
        assert res.conflicts == ["phone"]
        idx.attach_anchor.assert_not_awaited()

    async def test_ambiguo_recusa(self):
        idx, r = _idx()
        r.kv[idx._identity_key(T, "phone", _h("phone", PHONE))] = _encode_index("cus_a", "claimed")
        r.kv[idx._identity_key(T, "phone", _h("phone", "+5511999990002"))] = _encode_index("cus_b", "claimed")
        res = await idx.register_by_operator(
            T, [{"kind": "phone", "value": PHONE}, {"kind": "phone", "value": "+5511999990002"}])
        assert (res.outcome, res.reason) == ("refused", "ambiguous")
        idx.attach_anchor.assert_not_awaited()


class TestCadastro:
    async def test_novo_cria_duravel_com_procedencia_operator_e_nome(self):
        idx, _r = _idx(attrs=None)
        res = await idx.register_by_operator(T, [{"kind": "phone", "value": "(11) 99999-0001"}],
                                             name=" Ana ", operator="op@x")
        assert res.outcome == "created" and res.customer_id.startswith("cus_") and res.anchors == 1
        args, kwargs = idx.attach_anchor.await_args
        assert args[:4] == (T, res.customer_id, "phone", "(11) 99999-0001")
        assert kwargs == {"verification_class": "claimed", "persist_durable": True, "provenance": "operator"}
        idx.update_attributes.assert_awaited_once_with(T, res.customer_id, {"nome": "Ana"})

    async def test_existente_nao_duplica_e_anexa_ao_mesmo_cliente(self):
        # Controle POSITIVO da recusa por conflito: âncora do PRÓPRIO cliente passa.
        idx, r = _idx(donos={("phone", _h("phone", PHONE)): ("cus_a", "claimed", 0.7)}, attrs={})
        r.kv[idx._identity_key(T, "phone", _h("phone", PHONE))] = _encode_index("cus_a", "claimed")
        res = await idx.register_by_operator(
            T, [{"kind": "phone", "value": PHONE}, {"kind": "email", "value": EMAIL}], name="Ana")
        assert (res.outcome, res.customer_id, res.anchors) == ("existing", "cus_a", 2)
        assert [c.args[2] for c in idx.attach_anchor.await_args_list] == ["phone", "email"]
        idx.update_attributes.assert_awaited_once_with(T, "cus_a", {"nome": "Ana"})

    async def test_existente_com_nome_nao_tem_o_nome_sobrescrito(self):
        idx, r = _idx(attrs={"nome": "Beatriz"})
        r.kv[idx._identity_key(T, "phone", _h("phone", PHONE))] = _encode_index("cus_a", "claimed")
        res = await idx.register_by_operator(T, [{"kind": "phone", "value": PHONE}], name="Ana")
        assert res.outcome == "existing"
        idx.update_attributes.assert_not_awaited()
