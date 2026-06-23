#!/usr/bin/env bash
# R8c — seed ONE persistent blind curation review for visual QA in CuradoriaPage.
#
# HOST script: creates a pending mode='blind' curation review against a real instance
# that has a result + criterion_responses (so blind-context returns a form). Does NOT
# clean up — open the Curadoria page, score it blind, reveal the diff, resolve.
# To remove it afterwards, run:  bash infra/test/seed_r8c_blind_review.sh clean
#
set -euo pipefail
COMPOSE="docker compose -f docker-compose.demo.yml"
MODE="${1:-seed}"

$COMPOSE exec -T evaluation-api env SEED_MODE="$MODE" python - <<'PY'
import asyncio, os
from plughub_evaluation_api.config import settings
from plughub_evaluation_api import db as _db

SV = "vBlindQA"
MODE = os.environ.get("SEED_MODE", "seed")


async def main():
    pool = await _db.create_pool(settings.database_url)
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """
            SELECT r.instance_id, r.tenant_id, r.campaign_id
              FROM evaluation.results r
             WHERE EXISTS (SELECT 1 FROM evaluation.criterion_responses cr WHERE cr.result_id = r.id)
             LIMIT 1
            """
        )
    assert row, "no result with criterion_responses to seed against"
    iid, tid, cid = row["instance_id"], row["tenant_id"], row["campaign_id"]

    # always clear prior QA rows first (idempotent)
    async with pool.acquire() as c:
        await c.execute("DELETE FROM evaluation.curation_result_blinds WHERE evaluation_instance_id=$1", iid)
        await c.execute("DELETE FROM evaluation.curation_reviews WHERE evaluation_instance_id=$1 AND mode='blind' AND skill_version=$2", iid, SV)
        await c.execute("DELETE FROM evaluation.calibration_notes WHERE skill_version=$1", SV)

    if MODE == "clean":
        print("R8C_BLIND_QA_CLEANED")
        await pool.close()
        return

    review = await _db.create_curation_review(
        pool, tenant_id=tid, evaluation_instance_id=iid,
        trigger="blind_stage:qa", mode="blind", deadline_at=None, skill_version=SV,
    )
    print(f"seeded blind review: {review['id']}")
    print(f"tenant={tid} campaign={cid} instance={iid}")
    print("Open Curadoria (Avaliação → Curadoria), filter status=pending, find the 'Blind' card.")
    print("R8C_BLIND_QA_SEEDED")
    await pool.close()


asyncio.run(main())
PY
