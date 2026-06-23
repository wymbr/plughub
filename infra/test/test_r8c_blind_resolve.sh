#!/usr/bin/env bash
# R8c slice 4 smoke — blind-resolve: diff → CalibrationNote(s) + calibration.events + status.
#
# HOST script: feeds a Python program into the evaluation-api container over stdin.
# Sets up a blind review, re-scores with disagreements (every score -5), then resolves:
# asserts one CalibrationNote per disagreeing dimension is persisted, the review goes
# 'recalibrated', and a second resolve is rejected (409). NEVER touches the result.
# Cleans up (notes/blind/review). KB publish is best-effort (knowledge-api may be down).
#
# Usage (from repo root, WSL):
#   bash infra/test/test_r8c_blind_resolve.sh
#
set -euo pipefail
COMPOSE="docker compose -f docker-compose.demo.yml"

$COMPOSE exec -T evaluation-api python - <<'PY'
import asyncio, json, httpx
from plughub_evaluation_api.config import settings
from plughub_evaluation_api import db as _db

BASE = "http://localhost:3400"
SV = "vSmokeResolve"   # unique skill_version to find/cleanup notes precisely


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

    async with pool.acquire() as c:
        await c.execute("DELETE FROM evaluation.curation_result_blinds WHERE evaluation_instance_id=$1", iid)
        await c.execute("DELETE FROM evaluation.curation_reviews WHERE evaluation_instance_id=$1 AND mode='blind'", iid)
        await c.execute("DELETE FROM evaluation.calibration_notes WHERE skill_version=$1", SV)
    review = await _db.create_curation_review(
        pool, tenant_id=tid, evaluation_instance_id=iid,
        trigger="blind_stage:smoke", mode="blind", deadline_at=None, skill_version=SV,
    )
    review_id = review["id"]
    print("review:", review_id)

    ai = await _db.list_criterion_responses(pool, rid, tid)
    human = []
    for r in ai:
        nr = {"criterion_id": r["criterion_id"]}
        if r.get("na"):
            nr["na"] = True
        elif r.get("score") is not None:
            nr["score"] = max(0.0, float(r["score"]) - 5.0)
        elif r.get("boolean_value") is not None:
            nr["boolean_value"] = not bool(r["boolean_value"])
        else:
            for k in ("choice_value", "text_value"):
                if r.get(k) is not None:
                    nr[k] = r[k]
        human.append(nr)

    try:
        async with httpx.AsyncClient(timeout=20.0) as cli:
            # rescore (produces disagreements)
            r2 = await cli.post(
                f"{BASE}/v1/evaluation/curations/{review_id}/blind-rescore",
                headers=head, json={"criterion_responses": human},
            )
            assert r2.status_code == 200, (r2.status_code, r2.text)
            diffs = r2.json()["per_dimension_diffs"]
            nd = sum(1 for d in diffs if d["disagree"])
            print("disagreements in diff:", nd)
            assert nd >= 1, "smoke needs at least one disagreement"

            # resolve → CalibrationNote per disagreeing dimension
            r3 = await cli.post(
                f"{BASE}/v1/evaluation/curations/{review_id}/blind-resolve",
                headers=head, json={"severity": "medium"},
            )
            assert r3.status_code == 200, (r3.status_code, r3.text)
            data = r3.json()
            print("resolve:", json.dumps({k: data[k] for k in ("status", "disagreements")}),
                  "notes:", len(data["calibration_notes"]))
            assert data["status"] == "recalibrated"
            assert data["disagreements"] == nd
            assert len(data["calibration_notes"]) == nd

            # DB: notes persisted + review resolved
            async with pool.acquire() as c:
                ncount = await c.fetchval(
                    "SELECT COUNT(*) FROM evaluation.calibration_notes WHERE skill_version=$1", SV)
                rstatus = await c.fetchval(
                    "SELECT status FROM evaluation.curation_reviews WHERE id=$1", review_id)
            print("calibration_notes in db:", ncount, "review status:", rstatus)
            assert ncount == nd, f"expected {nd} notes, got {ncount}"
            assert rstatus == "recalibrated"

            # idempotent — second resolve rejected
            r4 = await cli.post(
                f"{BASE}/v1/evaluation/curations/{review_id}/blind-resolve",
                headers=head, json={},
            )
            assert r4.status_code == 409, (r4.status_code, r4.text)
            print("R8C_RESOLVE_SMOKE_OK")
    finally:
        async with pool.acquire() as c:
            await c.execute("DELETE FROM evaluation.calibration_notes WHERE skill_version=$1", SV)
            await c.execute("DELETE FROM evaluation.curation_result_blinds WHERE curation_review_id=$1", review_id)
            await c.execute("DELETE FROM evaluation.curation_reviews WHERE id=$1", review_id)
        await pool.close()


asyncio.run(main())
PY
