"""
test_otp.py — OtpService (Fase 2): posse de canal (step-up componível).

Stub Redis async in-memory com o suficiente para o OTP: get/set(ex,keepttl),
incr, delete, expire. Testa challenge/verify, tentativas, expiração (via delete),
rate-limit e a promoção verify→possessed (integração com IdentityIndex).

PID-10 (2026-09-13): o mecanismo recusa sobre si mesmo — âncora não-entregável,
ausência de canal de entrega, desafio sem `subject`, e verify com `subject` alheio.
O portão de PROCEDÊNCIA (que exige o cadastro) é do adaptador: `test_otp_gate.py`.
"""
from __future__ import annotations

import json

import pytest

from plughub_channel_gateway.identity import DELIVERABLE_KINDS, IdentityIndex, OtpService

SALT = "otp_test_salt"
CUS = "cus_a"


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.counters: dict[str, int] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None, keepttl=None):
        self.kv[key] = value

    async def delete(self, key):
        self.kv.pop(key, None)

    async def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key, ttl):
        return True


@pytest.mark.asyncio
class TestOtpService:
    async def test_challenge_returns_dev_code_when_enabled(self):
        svc = OtpService(FakeRedis(), SALT, dev_return_code=True, phone_region="BR")
        out = await svc.challenge("t", "phone", "11999990000", subject=CUS)
        assert out["sent"] is True
        assert out["delivery"] == "dev_log"
        assert "dev_code" in out and len(out["dev_code"]) == 6

    async def test_challenge_invalid_anchor(self):
        svc = OtpService(FakeRedis(), SALT, dev_return_code=True, phone_region="BR")
        out = await svc.challenge("t", "phone", "   ", subject=CUS)   # empty after normalize
        assert out == {"sent": False, "reason": "invalid_anchor"}

    async def test_verify_success(self):
        r = FakeRedis()
        svc = OtpService(r, SALT, dev_return_code=True, phone_region="BR")
        code = (await svc.challenge("t", "email", "a@b.com", subject=CUS))["dev_code"]
        res = await svc.verify("t", "email", "a@b.com", code, subject=CUS)
        assert res == {"verified": True}
        # challenge consumed (one-shot)
        assert await svc.verify("t", "email", "a@b.com", code, subject=CUS) == {"verified": False, "reason": "no_challenge"}

    async def test_verify_wrong_code_decrements_attempts(self):
        svc = OtpService(FakeRedis(), SALT, max_attempts=3, dev_return_code=True, phone_region="BR")
        await svc.challenge("t", "phone", "11999990000", subject=CUS)
        r1 = await svc.verify("t", "phone", "11999990000", "000000", subject=CUS)
        assert r1["verified"] is False and r1["reason"] == "wrong_code"
        assert r1["attempts_left"] == 2

    async def test_verify_too_many_attempts_burns_challenge(self):
        svc = OtpService(FakeRedis(), SALT, max_attempts=2, dev_return_code=True, phone_region="BR")
        await svc.challenge("t", "phone", "11999990000", subject=CUS)
        await svc.verify("t", "phone", "11999990000", "bad1", subject=CUS)  # 1
        await svc.verify("t", "phone", "11999990000", "bad2", subject=CUS)  # 2
        r3 = await svc.verify("t", "phone", "11999990000", "bad3", subject=CUS)  # > max
        assert r3 == {"verified": False, "reason": "too_many_attempts"}

    async def test_verify_no_challenge(self):
        svc = OtpService(FakeRedis(), SALT, dev_return_code=True, phone_region="BR")
        assert await svc.verify("t", "phone", "11999990000", "123456", subject=CUS) == {"verified": False, "reason": "no_challenge"}

    async def test_rate_limit_blocks_after_max(self):
        svc = OtpService(FakeRedis(), SALT, rl_max=2, dev_return_code=True, phone_region="BR")
        assert (await svc.challenge("t", "phone", "11999990000", subject=CUS))["sent"] is True
        assert (await svc.challenge("t", "phone", "11999990000", subject=CUS))["sent"] is True
        blocked = await svc.challenge("t", "phone", "11999990000", subject=CUS)
        assert blocked == {"sent": False, "reason": "rate_limited"}

    async def test_code_never_stored_plaintext(self):
        r = FakeRedis()
        svc = OtpService(r, SALT, dev_return_code=True, phone_region="BR")
        code = (await svc.challenge("t", "phone", "11999990000", subject=CUS))["dev_code"]
        assert all(code not in v for v in r.kv.values())


@pytest.mark.asyncio
class TestOtpRecusaSobreSiMesmo:
    """PID-10 — ADR D8: nunca `sent: true` sem entrega; só âncora entregável."""

    @pytest.mark.parametrize("kind,value", [("cpf", "52998224725"), ("princ", "sub-1"), ("dev", "d-1")])
    async def test_ancora_nao_entregavel_recusa_sem_efeito(self, kind, value):
        r = FakeRedis()
        svc = OtpService(r, SALT, dev_return_code=True, phone_region="BR")
        out = await svc.challenge("t", kind, value, subject=CUS)
        assert out == {"sent": False, "reason": "undeliverable_kind"}
        # nem desafio guardado, nem rate-limit consumido
        assert r.kv == {} and r.counters == {}

    async def test_entregaveis_sao_exatamente_phone_e_email(self):
        # Trava do CONJUNTO: acrescentar `cpf` aqui reabriria o desafio tautológico.
        assert set(DELIVERABLE_KINDS) == {"phone", "email"}

    async def test_sem_canal_de_entrega_nao_diz_sent_true(self):
        # Antes: `sent: true` com a entrega marcada TODO(prod).
        r = FakeRedis()
        svc = OtpService(r, SALT, dev_return_code=False, phone_region="BR")
        out = await svc.challenge("t", "phone", "11999990000", subject=CUS)
        assert out == {"sent": False, "reason": "delivery_unavailable"}
        assert r.kv == {} and r.counters == {}

    async def test_desafio_sem_subject_recusa(self):
        svc = OtpService(FakeRedis(), SALT, dev_return_code=True, phone_region="BR")
        assert await svc.challenge("t", "phone", "11999990000", subject="") == {"sent": False, "reason": "subject_required"}

    async def test_verify_de_outro_subject_nao_confere_nem_com_o_codigo_certo(self):
        r = FakeRedis()
        svc = OtpService(r, SALT, max_attempts=3, dev_return_code=True, phone_region="BR")
        code = (await svc.challenge("t", "phone", "11999990000", subject=CUS))["dev_code"]
        alheio = await svc.verify("t", "phone", "11999990000", code, subject="cus_b")
        assert alheio == {"verified": False, "reason": "wrong_code", "attempts_left": 2}
        # controle POSITIVO: o desafio segue vivo para o subject certo
        assert await svc.verify("t", "phone", "11999990000", code, subject=CUS) == {"verified": True}

    async def test_subject_fica_guardado_no_desafio(self):
        r = FakeRedis()
        svc = OtpService(r, SALT, dev_return_code=True, phone_region="BR")
        await svc.challenge("t", "email", "a@b.com", subject=CUS)
        (raw,) = r.kv.values()
        assert json.loads(raw)["subject"] == CUS


@pytest.mark.asyncio
class TestOtpPromotesToPossessed:
    async def test_verify_then_attach_makes_anchor_possessed(self):
        """Mirrors WebhookAdapter.otp_verify: verify OK → attach_anchor possessed."""
        r = FakeRedis()
        otp = OtpService(r, SALT, dev_return_code=True, phone_region="BR")
        idx = IdentityIndex(r, SALT, phone_region="BR")

        # a claimed prospect exists for the phone
        ref0 = await idx.resolve_or_provision("t", [{"kind": "phone", "value": "11999990000"}])
        assert ref0.verification_class == "claimed"

        # OTP proves possession → adapter attaches possessed to the SAME customer
        code = (await otp.challenge("t", "phone", "11999990000", subject=ref0.customer_id))["dev_code"]
        assert (await otp.verify("t", "phone", "11999990000", code, subject=ref0.customer_id))["verified"] is True
        await idx.attach_anchor("t", ref0.customer_id, "phone", "11999990000",
                                verification_class="possessed", persist_durable=False)

        ref1 = await idx.resolve_or_provision("t", [{"kind": "phone", "value": "11999990000"}], provision=False)
        assert ref1.customer_id == ref0.customer_id
        assert ref1.verification_class == "possessed"
