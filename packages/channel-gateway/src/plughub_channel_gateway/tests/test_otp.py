"""
test_otp.py — OtpService (Fase 2): posse de canal (step-up componível).

Stub Redis async in-memory com o suficiente para o OTP: get/set(ex,keepttl),
incr, delete, expire. Testa challenge/verify, tentativas, expiração (via delete),
rate-limit e a promoção verify→possessed (integração com IdentityIndex).
"""
from __future__ import annotations

import pytest

from plughub_channel_gateway.identity import IdentityIndex, OtpService, hash_anchor

SALT = "otp_test_salt"


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
        svc = OtpService(FakeRedis(), SALT, dev_return_code=True)
        out = await svc.challenge("t", "phone", "11999990000")
        assert out["sent"] is True
        assert "dev_code" in out and len(out["dev_code"]) == 6

    async def test_challenge_hides_code_when_dev_off(self):
        svc = OtpService(FakeRedis(), SALT, dev_return_code=False)
        out = await svc.challenge("t", "phone", "11999990000")
        assert out["sent"] is True
        assert "dev_code" not in out

    async def test_challenge_invalid_anchor(self):
        svc = OtpService(FakeRedis(), SALT, dev_return_code=True)
        out = await svc.challenge("t", "phone", "   ")   # empty after normalize
        assert out == {"sent": False, "reason": "invalid_anchor"}

    async def test_verify_success(self):
        r = FakeRedis()
        svc = OtpService(r, SALT, dev_return_code=True)
        code = (await svc.challenge("t", "email", "a@b.com"))["dev_code"]
        res = await svc.verify("t", "email", "a@b.com", code)
        assert res == {"verified": True}
        # challenge consumed (one-shot)
        assert await svc.verify("t", "email", "a@b.com", code) == {"verified": False, "reason": "no_challenge"}

    async def test_verify_wrong_code_decrements_attempts(self):
        svc = OtpService(FakeRedis(), SALT, max_attempts=3, dev_return_code=True)
        await svc.challenge("t", "phone", "11999990000")
        r1 = await svc.verify("t", "phone", "11999990000", "000000")
        assert r1["verified"] is False and r1["reason"] == "wrong_code"
        assert r1["attempts_left"] == 2

    async def test_verify_too_many_attempts_burns_challenge(self):
        svc = OtpService(FakeRedis(), SALT, max_attempts=2, dev_return_code=True)
        await svc.challenge("t", "phone", "11999990000")
        await svc.verify("t", "phone", "11999990000", "bad1")  # 1
        await svc.verify("t", "phone", "11999990000", "bad2")  # 2
        r3 = await svc.verify("t", "phone", "11999990000", "bad3")  # > max
        assert r3 == {"verified": False, "reason": "too_many_attempts"}

    async def test_verify_no_challenge(self):
        svc = OtpService(FakeRedis(), SALT, dev_return_code=True)
        assert await svc.verify("t", "phone", "11999990000", "123456") == {"verified": False, "reason": "no_challenge"}

    async def test_rate_limit_blocks_after_max(self):
        svc = OtpService(FakeRedis(), SALT, rl_max=2, dev_return_code=True)
        assert (await svc.challenge("t", "phone", "11999990000"))["sent"] is True
        assert (await svc.challenge("t", "phone", "11999990000"))["sent"] is True
        blocked = await svc.challenge("t", "phone", "11999990000")
        assert blocked == {"sent": False, "reason": "rate_limited"}

    async def test_code_never_stored_plaintext(self):
        r = FakeRedis()
        svc = OtpService(r, SALT, dev_return_code=True)
        code = (await svc.challenge("t", "phone", "11999990000"))["dev_code"]
        assert all(code not in v for v in r.kv.values())


@pytest.mark.asyncio
class TestOtpPromotesToPossessed:
    async def test_verify_then_attach_makes_anchor_possessed(self):
        """Mirrors WebhookAdapter.otp_verify: verify OK → attach_anchor possessed."""
        r = FakeRedis()
        otp = OtpService(r, SALT, dev_return_code=True)
        idx = IdentityIndex(r, SALT)

        # a claimed prospect exists for the phone
        ref0 = await idx.resolve_or_provision("t", [{"kind": "phone", "value": "11999990000"}])
        assert ref0.verification_class == "claimed"

        # OTP proves possession → adapter attaches possessed to the SAME customer
        code = (await otp.challenge("t", "phone", "11999990000"))["dev_code"]
        assert (await otp.verify("t", "phone", "11999990000", code))["verified"] is True
        await idx.attach_anchor("t", ref0.customer_id, "phone", "11999990000",
                                verification_class="possessed", persist_durable=False)

        ref1 = await idx.resolve_or_provision("t", [{"kind": "phone", "value": "11999990000"}], provision=False)
        assert ref1.customer_id == ref0.customer_id
        assert ref1.verification_class == "possessed"
