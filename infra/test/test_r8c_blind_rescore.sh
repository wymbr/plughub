#!/usr/bin/env bash
# R8c slice 3 smoke — blind-context + blind-rescore endpoints (reveal + diff).
#
# HOST script: feeds a Python program into the evaluation-api container over stdin.
# Sets up a blind review against a real result (with criterion_responses), then drives
# the HTTP endpoints via httpx on localhost:3400: GET blind-context (no AI scores),
# POST blind-rescore (human responses derived from the AI's with one score nudged to
# force a disagreement) → asserts reveal + per-dimension diff + idempotency (409).
# Cleans up. NEVER touches the immutable result.
#
# Usage (from repo root, WSL):
#   bash infra/test/test_r8c_blind_rescore.sh
#
set -euo pipefail
COMPOSE="docker compose -f docker-compose.demo.yml"

$COMPOSE exec -T evaluation-api python - <<'PY'
import asyncio, json, httpx
from plughub_evaluation_api.config import settings
from plughub_evaluation_api import db as _db

BASE = "http://localhost:3400"


async def main():
    pool = await _db.create_pool(settings.database_url)

    async with pool.acquire() as c:
        row = await c.fetchrow(
            """
            SELECT r.instance_id, r.id AS result_id, r.tenant_id, r.campaign_id
              FROM evaluation.results r
             WHERE EXISTS (SELECT 1 FROM evaluation.criterion_responses cr WHERE cr.result_id = r.id)
             LIMIT 1
            """
        )
    assert row, "no result with criterion_responses to smoke against"
    iid, rid, tid, cid = row["instance_id"], row["result_id"], row["tenant_id"], row["campaign_id"]
    head = {"X-Tenant-ID": tid, "X-User-ID": "curator_smoke"}
    print(f"instance={iid} result={rid} tenant={tid}")

    # clean slate + create a blind review for this instance
    async with pool.acquire() as c:
        await c.execute("DELETE FROM evaluation.curation_result_blinds WHERE evaluation_instance_id=$1", iid)
        await c.execute("DELETE FROM evaluation.curation_reviews WHERE evaluation_instance_id=$1 AND mode='blind'", iid)
    review = await _db.create_curation_review(
        pool, tenant_id=tid, evaluation_instance_id=iid,
        trigger="blind_stage:smoke", mode="blind", deadline_at=None, skill_version="vSmoke",
    )
    review_id = review["id"]
    print("review:", review_id)

    # human responses = AI's, with EVERY scored criterion dropped by 5 (clamped >=0) and
    # every boolean flipped → guarantees a dimension-level disagreement crosses the threshold
    # (a single-criterion nudge is diluted by intra-dimension averaging).
    ai_resps = await _db.list_criterion_responses(pool, rid, tid)
    human, nudged = [], False
    for r in ai_resps:
        nr = {"criterion_id": r["criterion_id"]}
        if r.get("na"):
            nr["na"] = True
        elif r.get("score") is not None:
            nr["score"] = max(0.0, float(r["score"]) - 5.0)
            nudged = True
        elif r.get("boolean_value") is not None:
            nr["boolean_value"] = not bool(r["boolean_value"])
            nudged = True
        else:
            for k in ("choice_value", "text_value"):
                if r.get(k) is not None:
                    nr[k] = r[k]
        human.append(nr)
    print(f"built {len(human)} human responses, nudged={nudged}")

    try:
        async with httpx.AsyncClient(timeout=20.0) as cli:
            # 1) blind-context (pre) — form present, NOT yet rescored
            r1 = await cli.get(f"{BASE}/v1/evaluation/curations/{review_id}/blind-context", headers=head)
            assert r1.status_code == 200, (r1.status_code, r1.text)
            ctx = r1.json()
            assert ctx.get("form"), "blind-context missing form"
            assert ctx.get("already_rescored") is False, "should not be rescored yet"
            assert "ai_overall_score" not in json.dumps(ctx) or ctx.get("blind_result") is None
            print("context ok — form dims:", len(ctx["form"].get("dimensions", [])))

            # 2) blind-rescore → reveal + diff
            r2 = await cli.post(
                f"{BASE}/v1/evaluation/curations/{review_id}/blind-rescore",
                headers=head, json={"criterion_responses": human},
            )
            assert r2.status_code == 200, (r2.status_code, r2.text)
            data = r2.json()
            diffs = data["per_dimension_diffs"]
            print("ai_overall:", data["ai_overall_score"], "blind_overall:", data["blind_overall_score"])
            print("severity_min:", data["severity_min"])
            print("diffs:", json.dumps(diffs))
            assert isinstance(diffs, list) and diffs, "no per-dimension diffs"
            if nudged:
                assert any(d["disagree"] for d in diffs), "expected at least one disagreement"

            # 3) idempotent — second rescore is rejected
            r3 = await cli.post(
                f"{BASE}/v1/evaluation/curations/{review_id}/blind-rescore",
                headers=head, json={"criterion_responses": human},
            )
            assert r3.status_code == 409, (r3.status_code, r3.text)

            # 4) blind-context (post) — already_rescored True + reveal attached
            r4 = await cli.get(f"{BASE}/v1/evaluation/curations/{review_id}/blind-context", headers=head)
            ctx2 = r4.json()
            assert ctx2.get("already_rescored") is True
            assert ctx2.get("blind_result"), "blind_result not attached after rescore"
            print("R8C_RESCORE_SMOKE_OK")
    finally:
        async with pool.acquire() as c:
            await c.execute("DELETE FROM evaluation.curation_result_blinds WHERE curation_review_id=$1", review_id)
            await c.execute("DELETE FROM evaluation.curation_reviews WHERE id=$1", review_id)
        await pool.close()


asyncio.run(main())
PY
