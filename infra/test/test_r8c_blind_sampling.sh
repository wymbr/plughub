#!/usr/bin/env bash
# R8c slice 2 smoke — blind curation sampling (two-strata) + soft SLA expiry.
#
# HOST script: pipes a Python program into the evaluation-api container (the repo
# is NOT bind-mounted, so we feed the program over stdin). Self-contained: picks an
# existing instance, temporarily enables blind config on its campaign, asserts a
# blind curation_review is created (unflagged stratum), idempotency, and that the
# soft-SLA scanner marks it expired WITHOUT touching the evaluation. Cleans up.
#
# Usage (from repo root, WSL):
#   bash infra/test/test_r8c_blind_sampling.sh
#
set -euo pipefail
COMPOSE="docker compose -f docker-compose.demo.yml"

$COMPOSE exec -T evaluation-api python - <<'PY'
import asyncio, json
from plughub_evaluation_api.config import settings
from plughub_evaluation_api import db as _db
from plughub_evaluation_api.sampling_engine import run_blind_curation_sampling


async def main():
    pool = await _db.create_pool(settings.database_url)

    async with pool.acquire() as c:
        inst = await c.fetchrow(
            "SELECT id, tenant_id, campaign_id FROM evaluation.instances "
            "WHERE campaign_id IS NOT NULL LIMIT 1"
        )
    assert inst, "no instances in demo to smoke against"
    iid, tid, cid = inst["id"], inst["tenant_id"], inst["campaign_id"]
    print(f"using instance={iid} campaign={cid} tenant={tid}")

    # Enable blind config on the campaign: unflagged 100% → deterministic sample.
    policy = {
        "blind_stage_enabled": True,
        "blind_stage_sample_pct_flagged": 0.0,
        "blind_stage_sample_pct_unflagged": 1.0,
        "blind_stage_sla_hours": 48,
    }
    async with pool.acquire() as c:
        old = await c.fetchval(
            "SELECT contestation_policy FROM evaluation.campaigns WHERE id=$1 AND tenant_id=$2",
            cid, tid,
        )
        await c.execute(
            "UPDATE evaluation.campaigns SET contestation_policy=$3::jsonb WHERE id=$1 AND tenant_id=$2",
            cid, tid, json.dumps(policy),
        )
        await c.execute(
            "DELETE FROM evaluation.curation_result_blinds WHERE evaluation_instance_id=$1", iid,
        )
        await c.execute(
            "DELETE FROM evaluation.curation_reviews WHERE evaluation_instance_id=$1 AND mode='blind'", iid,
        )

    try:
        # 1) sampling creates exactly one blind review (unflagged stratum)
        await run_blind_curation_sampling(
            pool, instance_id=iid, tenant_id=tid, campaign_id=cid, flagged=False,
        )
        n1 = await _db.count_blind_reviews_for_instance(pool, tid, iid)
        print("after 1st sampling, blind reviews:", n1)
        assert n1 == 1, f"expected 1 blind review, got {n1}"

        # 2) idempotent — second call does not duplicate
        await run_blind_curation_sampling(
            pool, instance_id=iid, tenant_id=tid, campaign_id=cid, flagged=False,
        )
        n2 = await _db.count_blind_reviews_for_instance(pool, tid, iid)
        print("after 2nd sampling, blind reviews:", n2)
        assert n2 == 1, f"idempotency broken: {n2}"

        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT mode, trigger, status, deadline_at, expired_at "
                "FROM evaluation.curation_reviews WHERE evaluation_instance_id=$1 AND mode='blind'", iid,
            )
        print("review:", dict(row))
        assert row["mode"] == "blind"
        assert row["trigger"] == "blind_stage:unflagged"
        assert row["deadline_at"] is not None
        assert row["expired_at"] is None

        # 3) soft-SLA expiry: backdate deadline, run scanner expiry helper
        async with pool.acquire() as c:
            await c.execute(
                "UPDATE evaluation.curation_reviews SET deadline_at=now()-interval '1 hour' "
                "WHERE evaluation_instance_id=$1 AND mode='blind'", iid,
            )
        n_exp = await _db.expire_overdue_blind_reviews(pool)
        print("soft-expired count:", n_exp)
        assert n_exp >= 1, "expiry did not mark the overdue blind review"

        async with pool.acquire() as c:
            row2 = await c.fetchrow(
                "SELECT status, expired_at FROM evaluation.curation_reviews "
                "WHERE evaluation_instance_id=$1 AND mode='blind'", iid,
            )
        print("after expiry:", dict(row2))
        assert row2["expired_at"] is not None, "expired_at not set"
        assert row2["status"] == "pending", "soft expiry must NOT change status"

        # 4) idempotent expiry — already-expired row not re-counted here
        n_exp2 = await _db.expire_overdue_blind_reviews(pool)
        print("2nd expiry pass (this row already expired):", n_exp2)

        print("R8C_BLIND_SMOKE_OK")
    finally:
        async with pool.acquire() as c:
            await c.execute(
                "DELETE FROM evaluation.curation_reviews WHERE evaluation_instance_id=$1 AND mode='blind'", iid,
            )
            await c.execute(
                "UPDATE evaluation.campaigns SET contestation_policy=$3::jsonb WHERE id=$1 AND tenant_id=$2",
                cid, tid, old if old is not None else "{}",
            )
        await pool.close()


asyncio.run(main())
PY
