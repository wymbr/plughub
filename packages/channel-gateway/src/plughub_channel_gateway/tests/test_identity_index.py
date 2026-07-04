"""
test_identity_index.py — Identity Resolver (Fase A · Slice 1).

Cobre normalize/hash e IdentityIndex (Lookup 1 resolve/provision + Lookup 2
pendências) com um stub Redis assíncrono in-memory (comportamento real, sem I/O).
"""
from __future__ import annotations

import pytest

from plughub_channel_gateway.identity import (
    IdentityIndex,
    PendingEntry,
    hash_anchor,
    normalize_anchor,
)


# ── In-memory async Redis stub ─────────────────────────────────────────────────

class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None):
        self.kv[key] = value

    async def hset(self, key, field=None, value=None, mapping=None):
        h = self.hashes.setdefault(key, {})
        if mapping:
            h.update(mapping)
        if field is not None:
            h[field] = value

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key, field):
        self.hashes.get(key, {}).pop(field, None)

    async def expire(self, key, ttl):
        return True


class FakeConn:
    """Minimal asyncpg-conn stub for identity durability tests."""
    def __init__(self, state: dict) -> None:
        self.state = state

    async def execute(self, sql: str, *args):
        s = " ".join(sql.split())
        if "INSERT INTO identity.customers" in s:
            # promote_to_durable passes status as $3; attach_anchor hardcodes
            # 'identified' in SQL (only 2 bind args) → default when absent.
            cid, tenant = args[0], args[1]
            status = args[2] if len(args) > 2 else "identified"
            self.state["customers"][cid] = {"tenant_id": tenant, "status": status}
        elif "INSERT INTO identity.customer_secondary_keys" in s:
            tenant, kind, vh, cid, conf = args[0], args[1], args[2], args[3], args[4]
            # verification_class is arg[5] on the Fase-1 schema; default claimed.
            vc = args[5] if len(args) > 5 else "claimed"
            prev = self.state["secondary_keys"].get((tenant, kind, vh))
            # mirror the SQL ON CONFLICT: never downgrade possessed → claimed.
            if prev and prev.get("verification_class") == "possessed":
                vc = "possessed"
            self.state["secondary_keys"][(tenant, kind, vh)] = {
                "customer_id": cid, "confidence": conf, "verification_class": vc,
            }
        # DDL / CREATE SCHEMA / ALTER → ignore
        return "OK"

    async def fetchrow(self, sql: str, *args):
        tenant, kind, vh = args[0], args[1], args[2]
        return self.state["secondary_keys"].get((tenant, kind, vh))


class FakePGPool:
    def __init__(self) -> None:
        self.state = {"customers": {}, "secondary_keys": {}}

    def acquire(self):
        state = self.state

        class _Ctx:
            async def __aenter__(self_inner):
                return FakeConn(state)

            async def __aexit__(self_inner, *a):
                return False

        return _Ctx()


SALT = "test_salt"


# ── normalize / hash ───────────────────────────────────────────────────────────

class TestNormalize:
    def test_phone_e164(self):
        assert normalize_anchor("phone", "(11) 99999-0000") == "+11999990000"

    def test_email_lower(self):
        assert normalize_anchor("email", "  Foo@Bar.COM ") == "foo@bar.com"

    def test_cpf_digits(self):
        assert normalize_anchor("cpf", "123.456.789-00") == "12345678900"

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            normalize_anchor("ssn", "x")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            normalize_anchor("email", "   ")

    def test_hash_deterministic_and_salted(self):
        h1 = hash_anchor(SALT, "phone", "11999990000")
        h2 = hash_anchor(SALT, "phone", "(11) 99999-0000")  # same after normalize
        assert h1 == h2
        assert hash_anchor("other_salt", "phone", "11999990000") != h1
        # never the raw value
        assert "11999990000" not in h1


# ── resolve_or_provision ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestResolveOrProvision:
    async def test_provisions_when_absent(self):
        idx = IdentityIndex(FakeRedis(), SALT)
        ref = await idx.resolve_or_provision("t", [{"kind": "phone", "value": "11999990000"}])
        assert ref.matched_by == "provisioned"
        assert ref.customer_id.startswith("cus_")
        assert ref.status == "prospect"

    async def test_second_anchor_resolves_same_customer(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT)
        # provision with phone + email together
        ref1 = await idx.resolve_or_provision(
            "t", [{"kind": "phone", "value": "11999990000"},
                  {"kind": "email", "value": "a@b.com"}],
        )
        # later, resolving by ONLY the email must return the same customer_id
        ref2 = await idx.resolve_or_provision("t", [{"kind": "email", "value": "A@B.com"}], provision=False)
        assert ref2.customer_id == ref1.customer_id
        assert ref2.matched_by == "existing"

    async def test_no_provision_returns_none(self):
        idx = IdentityIndex(FakeRedis(), SALT)
        ref = await idx.resolve_or_provision("t", [{"kind": "phone", "value": "11111111111"}], provision=False)
        assert ref.matched_by == "none"
        assert ref.customer_id == ""

    async def test_tenant_isolation(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT)
        ref_a = await idx.resolve_or_provision("tA", [{"kind": "cpf", "value": "12345678900"}])
        ref_b = await idx.resolve_or_provision("tB", [{"kind": "cpf", "value": "12345678900"}], provision=False)
        # same cpf, different tenant → must NOT resolve
        assert ref_b.customer_id == ""
        assert ref_a.customer_id != ""

    async def test_ambiguous_on_conflicting_equal_confidence(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT)
        # two different customers, each indexed by a distinct email (same confidence)
        c1 = await idx.resolve_or_provision("t", [{"kind": "email", "value": "one@x.com"}])
        c2 = await idx.resolve_or_provision("t", [{"kind": "email", "value": "two@x.com"}])
        assert c1.customer_id != c2.customer_id
        # resolving with BOTH emails → conflict at equal confidence → ambiguous
        ref = await idx.resolve_or_provision(
            "t", [{"kind": "email", "value": "one@x.com"},
                  {"kind": "email", "value": "two@x.com"}], provision=False,
        )
        assert ref.matched_by == "ambiguous"

    async def test_higher_confidence_wins(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT)
        cphone = await idx.resolve_or_provision("t", [{"kind": "phone", "value": "11999990000"}])
        ccpf   = await idx.resolve_or_provision("t", [{"kind": "cpf", "value": "12345678900"}])
        # resolve with both → cpf (0.90) outranks phone (0.70)
        ref = await idx.resolve_or_provision(
            "t", [{"kind": "phone", "value": "11999990000"},
                  {"kind": "cpf", "value": "12345678900"}], provision=False,
        )
        assert ref.customer_id == ccpf.customer_id
        assert ref.matched_by == "existing"

    async def test_index_never_stores_plaintext_pii(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT)
        await idx.resolve_or_provision("t", [{"kind": "phone", "value": "11999990000"}])
        joined = " ".join(r.kv.keys())
        assert "11999990000" not in joined


# ── pending (Lookup 2) ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestPending:
    async def _entry(self, cid, sid="sess_1", token="tok_1"):
        return PendingEntry(session_id=sid, customer_id=cid, resume_token=token, pool="loja_io")

    async def test_write_find_consume(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT)
        cid = "cus_1"
        # resume_token must be alive in {t}:resume_tokens for find to return it
        r.hashes["t:resume_tokens"] = {"tok_1": "sess_1:step:exp"}
        await idx.write_pending("t", cid, await self._entry(cid), ttl_s=3600)
        found = await idx.find_pending("t", cid)
        assert len(found) == 1
        assert found[0].session_id == "sess_1"
        await idx.consume_pending("t", cid, "sess_1")
        assert await idx.find_pending("t", cid) == []

    async def test_stale_pending_pruned(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT)
        cid = "cus_2"
        # no resume_tokens entry → the pending is stale and must be pruned
        await idx.write_pending("t", cid, await self._entry(cid, token="dead"), ttl_s=3600)
        assert await idx.find_pending("t", cid) == []

    async def test_multiple_pendings_sorted_recent_first(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT)
        cid = "cus_3"
        r.hashes["t:resume_tokens"] = {"tA": "x", "tB": "y"}
        e1 = PendingEntry(session_id="s1", customer_id=cid, resume_token="tA", pool="p", suspended_at="2026-06-01T00:00:00Z")
        e2 = PendingEntry(session_id="s2", customer_id=cid, resume_token="tB", pool="p", suspended_at="2026-06-02T00:00:00Z")
        await idx.write_pending("t", cid, e1, ttl_s=3600)
        await idx.write_pending("t", cid, e2, ttl_s=3600)
        found = await idx.find_pending("t", cid)
        assert [e.session_id for e in found] == ["s2", "s1"]


# ── durability (Slice 2 — PG fallback + promotion) ─────────────────────────────

@pytest.mark.asyncio
class TestDurability:
    async def test_pg_fallback_resolves_after_cold_redis(self):
        r = FakeRedis()
        pg = FakePGPool()
        idx = IdentityIndex(r, SALT, db_pool=pg)
        anchors = [{"kind": "phone", "value": "11999990000"},
                   {"kind": "email", "value": "dur@x.com"}]
        ref = await idx.resolve_or_provision("t", anchors)
        assert ref.matched_by == "provisioned"
        # concrete trigger → promote to durable PG
        await idx.promote_to_durable("t", ref.customer_id, anchors)
        assert ref.customer_id in pg.state["customers"]

        # simulate Redis TTL/cold: drop the identity index keys
        r.kv = {k: v for k, v in r.kv.items() if ":identity:" not in k}

        # resolving by ONLY the email, no provision → must fall back to PG
        ref2 = await idx.resolve_or_provision("t", [{"kind": "email", "value": "dur@x.com"}], provision=False)
        assert ref2.customer_id == ref.customer_id
        assert ref2.matched_by == "durable"
        # and the Redis index was re-hydrated
        assert any(":identity:email:" in k for k in r.kv)

    async def test_no_pg_pool_no_fallback(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT, db_pool=None)
        # provision then clear redis → without PG there is nothing to fall back to
        ref = await idx.resolve_or_provision("t", [{"kind": "email", "value": "np@x.com"}])
        r.kv = {k: v for k, v in r.kv.items() if ":identity:" not in k}
        ref2 = await idx.resolve_or_provision("t", [{"kind": "email", "value": "np@x.com"}], provision=False)
        assert ref2.matched_by == "none"
        assert ref2.customer_id == ""

    async def test_promote_noop_without_pool(self):
        idx = IdentityIndex(FakeRedis(), SALT, db_pool=None)
        # must not raise
        await idx.ensure_schema()
        await idx.promote_to_durable("t", "cus_x", [{"kind": "phone", "value": "11999990000"}])

    async def test_attach_anchor_persist_durable_writes_class(self):
        r, pg = FakeRedis(), FakePGPool()
        idx = IdentityIndex(r, SALT, db_pool=pg)
        await idx.attach_anchor("t", "cus_a", "email", "v@x.com",
                                verification_class="possessed", persist_durable=True)
        key = ("t", "email", hash_anchor(SALT, "email", "v@x.com"))
        assert pg.state["secondary_keys"][key]["verification_class"] == "possessed"
        assert "cus_a" in pg.state["customers"]


# ── Fase 1 — identidade progressiva + verification_class ───────────────────────

@pytest.mark.asyncio
class TestProgressiveIdentity:
    async def test_hit_attaches_missing_anchor_as_claimed(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT)
        # only the phone is known
        c = await idx.resolve_or_provision("t", [{"kind": "phone", "value": "11999990000"}])
        # reconnect presenting phone (hit) + email (miss) → progressive attach
        ref = await idx.resolve_or_provision(
            "t", [{"kind": "phone", "value": "11999990000"},
                  {"kind": "email", "value": "new@x.com"}], provision=False,
        )
        assert ref.customer_id == c.customer_id
        assert ref.matched_by == "existing"
        # now the email ALONE resolves the same customer (the gap Thread A left)
        ref2 = await idx.resolve_or_provision(
            "t", [{"kind": "email", "value": "new@x.com"}], provision=False,
        )
        assert ref2.customer_id == c.customer_id
        assert ref2.verification_class == "claimed"

    async def test_ambiguous_does_not_attach(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT)
        c1 = await idx.resolve_or_provision("t", [{"kind": "email", "value": "one@x.com"}])
        c2 = await idx.resolve_or_provision("t", [{"kind": "email", "value": "two@x.com"}])
        # ambiguous resolve with a fresh miss anchor → must NOT bind the miss
        await idx.resolve_or_provision(
            "t", [{"kind": "email", "value": "one@x.com"},
                  {"kind": "email", "value": "two@x.com"},
                  {"kind": "phone", "value": "11955554444"}], provision=False,
        )
        ref = await idx.resolve_or_provision(
            "t", [{"kind": "phone", "value": "11955554444"}], provision=False,
        )
        assert ref.matched_by == "none"


@pytest.mark.asyncio
class TestVerificationClass:
    async def test_provision_is_claimed(self):
        idx = IdentityIndex(FakeRedis(), SALT)
        ref = await idx.resolve_or_provision("t", [{"kind": "phone", "value": "11999990000"}])
        assert ref.verification_class == "claimed"

    async def test_legacy_plain_string_index_reads_as_claimed(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT)
        # simulate a Slice 1/2 index value: plain customer_id string (no JSON)
        key = idx._identity_key("t", "phone", hash_anchor(SALT, "phone", "11999990000"))
        r.kv[key] = "cus_legacy"
        ref = await idx.resolve_or_provision(
            "t", [{"kind": "phone", "value": "11999990000"}], provision=False,
        )
        assert ref.customer_id == "cus_legacy"
        assert ref.verification_class == "claimed"

    async def test_possessed_outranks_claimed_across_kinds(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT)
        # cus_p owns a POSSESSED phone; cus_c owns a CLAIMED cpf (higher kind conf)
        await idx.attach_anchor("t", "cus_p", "phone", "11999990000", verification_class="possessed")
        await idx.resolve_or_provision("t", [{"kind": "cpf", "value": "12345678900"}])  # cus_c claimed
        cpf_ref = await idx.resolve_or_provision("t", [{"kind": "cpf", "value": "12345678900"}], provision=False)
        # resolve with both → possessed phone (0.70+1.0) beats claimed cpf (0.90)
        ref = await idx.resolve_or_provision(
            "t", [{"kind": "phone", "value": "11999990000"},
                  {"kind": "cpf", "value": "12345678900"}], provision=False,
        )
        assert ref.customer_id == "cus_p"
        assert ref.customer_id != cpf_ref.customer_id
        assert ref.verification_class == "possessed"

    async def test_attach_anchor_never_downgrades_possessed(self):
        r = FakeRedis()
        idx = IdentityIndex(r, SALT)
        await idx.attach_anchor("t", "cus_x", "email", "v@x.com", verification_class="possessed")
        # a later claimed attach for the same anchor+customer must not downgrade
        await idx.attach_anchor("t", "cus_x", "email", "v@x.com", verification_class="claimed")
        ref = await idx.resolve_or_provision("t", [{"kind": "email", "value": "v@x.com"}], provision=False)
        assert ref.verification_class == "possessed"
