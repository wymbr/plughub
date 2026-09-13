"""
test_otp_gate.py — PID-10 (2026-09-13): o portão de PROCEDÊNCIA do OTP.

ADR adr-identity-door-evidence D8: OTP só é emitido contra âncora entregável **de
procedência autoritativa** — para o cliente que pede. OTP contra âncora declarada é
tautológico: prova que o interlocutor tem o número que ele próprio informou.

A proposição aqui é a do ADAPTADOR (`WebhookAdapter.otp_challenge`/`otp_verify`):
a ordem das recusas, a leitura da procedência por (âncora, CLIENTE), e a amarração
do verify ao cliente do desafio. O cadastro é substituído; contra Postgres e Redis
reais quem responde é `infra/test/probe_otp_gate.sh`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from plughub_channel_gateway.adapters.webhook import WebhookAdapter
from plughub_channel_gateway.identity import IdentityIndex, OtpService
from plughub_channel_gateway.tests.test_otp import FakeRedis

SALT = "otp_gate_salt"
PHONE = "+5511999990001"


def _adapter(procedencias: dict[tuple[str, str, str], str | None], *, dev: bool = True):
    # Um mock não verifica que o alvo existe; ele o CRIA (VOZ-03). Antes de
    # substituir, confere que os dois métodos são do produto.
    assert hasattr(IdentityIndex, "anchor_provenance")
    assert hasattr(IdentityIndex, "attach_anchor")
    r = FakeRedis()
    a = WebhookAdapter.__new__(WebhookAdapter)
    a._otp = OtpService(r, SALT, dev_return_code=dev, phone_region="BR")
    ident = AsyncMock()
    ident.anchor_provenance = AsyncMock(
        side_effect=lambda t, cid, kind, value: procedencias.get((cid, kind, value)))
    ident.attach_anchor = AsyncMock(return_value=True)
    a._identity = ident
    return a, r


@pytest.mark.asyncio
class TestPortaoDeProcedencia:
    async def test_autoritativa_do_proprio_cliente_emite(self):
        # Controle POSITIVO: sem ele, todas as recusas abaixo passariam por um
        # adaptador que nunca emite nada.
        a, r = _adapter({("cus_a", "phone", PHONE): "authoritative"})
        out = await a.otp_challenge("t", "cus_a", "phone", PHONE)
        assert out["sent"] is True and out["delivery"] == "dev_log"
        assert len(r.kv) == 1

    @pytest.mark.parametrize("prov", ["declared", "channel_origin", "operator", None])
    async def test_procedencia_nao_autoritativa_recusa(self, prov):
        a, r = _adapter({("cus_a", "phone", PHONE): prov})
        out = await a.otp_challenge("t", "cus_a", "phone", PHONE)
        assert out == {"sent": False, "reason": "anchor_not_authoritative"}
        assert r.kv == {} and r.counters == {}

    async def test_autoritativa_de_OUTRO_cliente_recusa(self):
        # `anchor_provenance` responde por (âncora, cliente): a do cus_a não empresta
        # a confiança ao cus_b.
        a, r = _adapter({("cus_a", "phone", PHONE): "authoritative"})
        out = await a.otp_challenge("t", "cus_b", "phone", PHONE)
        assert out == {"sent": False, "reason": "anchor_not_authoritative"}
        a._identity.anchor_provenance.assert_awaited_once_with("t", "cus_b", "phone", PHONE)

    async def test_nao_entregavel_recusa_antes_de_ler_o_cadastro(self):
        # CPF autoritativo continua sem canal: a resposta é sobre o MECANISMO.
        a, _r = _adapter({("cus_a", "cpf", "52998224725"): "authoritative"})
        out = await a.otp_challenge("t", "cus_a", "cpf", "52998224725")
        assert out == {"sent": False, "reason": "undeliverable_kind"}
        a._identity.anchor_provenance.assert_not_awaited()

    async def test_sem_entrega_recusa_antes_de_ler_o_cadastro(self):
        a, _r = _adapter({("cus_a", "phone", PHONE): "authoritative"}, dev=False)
        out = await a.otp_challenge("t", "cus_a", "phone", PHONE)
        assert out == {"sent": False, "reason": "delivery_unavailable"}
        a._identity.anchor_provenance.assert_not_awaited()

    async def test_sem_cliente_recusa(self):
        a, _r = _adapter({})
        assert await a.otp_challenge("t", "", "phone", PHONE) == {"sent": False, "reason": "customer_required"}


@pytest.mark.asyncio
class TestVerifyAmarradoAoCliente:
    async def test_codigo_do_cus_a_nao_vira_posse_do_cus_b(self):
        a, _r = _adapter({("cus_a", "phone", PHONE): "authoritative"})
        code = (await a.otp_challenge("t", "cus_a", "phone", PHONE))["dev_code"]
        res = await a.otp_verify("t", "cus_b", "phone", PHONE, code)
        assert res["verified"] is False
        a._identity.attach_anchor.assert_not_awaited()

    async def test_mesmo_cliente_vira_possessed(self):
        # Controle POSITIVO do de cima.
        a, _r = _adapter({("cus_a", "phone", PHONE): "authoritative"})
        code = (await a.otp_challenge("t", "cus_a", "phone", PHONE))["dev_code"]
        res = await a.otp_verify("t", "cus_a", "phone", PHONE, code)
        # PID-02: `provenance` viaja para a evidência (`core.journey.identity.otp.source`).
        assert res == {"verified": True, "verification_class": "possessed", "provenance": "authoritative"}
        a._identity.attach_anchor.assert_awaited_once()
        assert a._identity.attach_anchor.await_args.args[:4] == ("t", "cus_a", "phone", PHONE)
