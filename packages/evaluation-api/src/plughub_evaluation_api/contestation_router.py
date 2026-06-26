"""
contestation_router.py
Arc 13 Fase A — endpoints for contestation threads, curation reviews,
calibration notes, and curation sampling rules.

Endpoints:
  ContestationThreads:
    GET  /v1/evaluation/instances/{id}/threads        list threads for instance
    POST /v1/evaluation/instances/{id}/contest        human_agent files contestation
    POST /v1/evaluation/instances/{id}/review         reviewer submits decision
    POST /v1/evaluation/instances/{id}/pre-review     pre-publication AI reviewer submits

  CurationReviews (curator queue):
    GET  /v1/evaluation/curations                     list curation queue
    POST /v1/evaluation/curations/{id}/resolve        curator resolves a review

  CalibrationNotes:
    GET  /v1/evaluation/calibration-notes             list calibration notes
    POST /v1/evaluation/calibration-notes/{id}/publish  mark published to KB

  CurationSamplingRules:
    GET  /v1/evaluation/campaigns/{id}/sampling-rules   list rules for campaign
    POST /v1/evaluation/campaigns/{id}/sampling-rules   create rule
    PUT  /v1/evaluation/campaigns/{id}/sampling-rules/{rid}  update rule
    DELETE /v1/evaluation/campaigns/{id}/sampling-rules/{rid}  delete rule
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .config import settings
from . import db as _db
from . import kafka_emitter as _kafka
from . import scoring as _scoring
from . import sampling as _sampling

logger = logging.getLogger("plughub.evaluation.contestation")

contestation_router = APIRouter()


# ─── Pydantic request models ──────────────────────────────────────────────────

class EvidenceEntryBody(BaseModel):
    stream_entry_id: str
    excerpt: str
    relevance_note: str


class ContestBody(BaseModel):
    """T5/5c — contestação por critério em LOTE (vários critérios num round).

    Forma canônica (lote): ``dimension_ids`` + ``reasons`` (criterion_id → texto) +
    ``evidence`` opcional (criterion_id → entradas). ``round`` opcional faz anti-replay.
    Forma legada (single, compat): ``dimension_id`` + ``text`` + ``evidence_entries``.
    """
    # batch (canônico)
    dimension_ids: list[str] | None = None
    reasons: dict[str, str] | None = None
    evidence: dict[str, list[EvidenceEntryBody]] | None = None
    round: int | None = None  # anti-replay opcional
    # single (legado/compat)
    dimension_id: str | None = None
    text: str | None = None
    evidence_entries: list[EvidenceEntryBody] | None = None

    def normalized_items(self) -> list[dict]:
        """→ [{criterion_id, text, evidence_entries}] a partir de qualquer das formas."""
        items: list[dict] = []
        if self.dimension_ids:
            reasons = self.reasons or {}
            evidence = self.evidence or {}
            for cid in self.dimension_ids:
                txt = (reasons.get(cid) or "").strip()
                if not txt:
                    raise HTTPException(status_code=400, detail=f"missing reason for criterion {cid}")
                items.append({
                    "criterion_id": cid,
                    "text": txt,
                    "evidence_entries": [e.model_dump() for e in (evidence.get(cid) or [])],
                })
        elif self.dimension_id:
            txt = (self.text or "").strip()
            if not txt:
                raise HTTPException(status_code=400, detail="text required")
            items.append({
                "criterion_id": self.dimension_id,
                "text": txt,
                "evidence_entries": [e.model_dump() for e in (self.evidence_entries or [])],
            })
        if not items:
            raise HTTPException(status_code=400, detail="no criteria to contest")
        return items


class ReviewDecisionItem(BaseModel):
    """Decisão do revisor para um critério contestado."""
    dimension_id: str
    decision: str  # "upheld" | "revised"
    justification: str | None = None  # forma da UI/MCP
    text: str | None = None           # forma legada
    score_override: float | None = None
    evidence_entries: list[EvidenceEntryBody] = []

    def reason_text(self) -> str:
        return (self.justification or self.text or "").strip()


class ReviewBody(BaseModel):
    """T5/5c — revisão em LOTE: decisão para o conjunto de critérios contestados do round.

    Forma canônica: ``dimension_decisions`` (lista de :class:`ReviewDecisionItem`).
    Forma legada (single, compat): ``dimension_id`` + ``decision`` + ``text``.
    """
    dimension_decisions: list[ReviewDecisionItem] | None = None
    reviewer_id: str | None = None
    # single (legado/compat)
    dimension_id: str | None = None
    decision: str | None = None
    text: str | None = None
    score_override: float | None = None
    evidence_entries: list[EvidenceEntryBody] | None = None

    def normalized_decisions(self) -> list[ReviewDecisionItem]:
        if self.dimension_decisions:
            return self.dimension_decisions
        if self.dimension_id and self.decision:
            return [ReviewDecisionItem(
                dimension_id=self.dimension_id,
                decision=self.decision,
                text=self.text,
                score_override=self.score_override,
                evidence_entries=self.evidence_entries or [],
            )]
        raise HTTPException(status_code=400, detail="dimension_decisions required")


class PreReviewBody(BaseModel):
    """Filed by pre_reviewer_ai before result publication."""
    dimension_id: str
    action: str  # "approve" | "adjust"
    text: str = Field(..., min_length=1)
    score_override: float | None = None
    evidence_entries: list[EvidenceEntryBody] = []
    calibration_signal: dict | None = None  # CalibrationSignal | null


class CurationResolveBody(BaseModel):
    status: str  # "approved" | "recalibrated" | "bias_flagged"
    curator_notes: str | None = None
    # Fields for creating a CalibrationNote (required when status != "approved")
    calibration_note_text: str | None = None
    dimension_id: str | None = None
    criterion_id: str | None = None   # T14 (c) — critério implicado (opcional; RAG por critério)
    evaluator_id: str | None = None
    skill_version: str | None = None
    severity: str = "low"  # "low" | "medium" | "high"


class SamplingRuleBody(BaseModel):
    rule_type: str
    params: dict = {}
    enabled: bool = True
    priority: int = 10


class SamplingRuleUpdateBody(BaseModel):
    rule_type: str | None = None
    params: dict | None = None
    enabled: bool | None = None
    priority: int | None = None


# ─── Auth helpers (reuse from router.py pattern) ──────────────────────────────

def _get_tenant(request: Request) -> str:
    """Tenant via header `X-Tenant-ID`; fallback ao claim `tenant_id` do Bearer JWT.

    T10-D — os hooks Arc 13 da UI (threads/contest/review) mandam só o `Authorization: Bearer`,
    não o `X-Tenant-ID`. Sem este fallback, `GET /instances/{id}/threads` e os submits davam 400 →
    a UI caía no caminho Arc 6 legado (que mexe em `eval_status` mas não em `result_state`). O JWT
    do auth-api carrega `tenant_id`, então derivamos dele quando o header falta."""
    tenant_id = request.headers.get("X-Tenant-ID") or request.headers.get("x-tenant-id")
    if tenant_id:
        return tenant_id
    from .router import _decode_jwt_optional  # local import: evita ciclo
    payload = _decode_jwt_optional(request)
    if payload and payload.get("tenant_id"):
        return str(payload["tenant_id"])
    raise HTTPException(status_code=400, detail="X-Tenant-ID header or Bearer token required")


def _get_user(request: Request) -> str:
    """Extract user identity from X-User-ID or JWT subject."""
    user_id = request.headers.get("X-User-ID") or request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="X-User-ID header required")
    return user_id


async def _curation_pool_id(db_pool, review_id: str, tenant_id: str) -> str | None:
    """ABAC scope (curar) — resolve o pool da curation review via sua campanha.
    None se não resolver (campanha cross-pool / não achada) → o check aceita any-scope."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT c.pool_id
            FROM evaluation.curation_reviews cr
            JOIN evaluation.instances i ON i.id = cr.evaluation_instance_id
            JOIN evaluation.campaigns  c ON c.id = i.campaign_id
            WHERE cr.id = $1 AND cr.tenant_id = $2
            """,
            review_id, tenant_id,
        )
    return (row["pool_id"] if row and row["pool_id"] else None)


def _require_curar(request: Request, *, write: bool) -> dict:
    """Gate ABAC de curadoria: exige Bearer JWT + grant `curar` no module_config.
    write=True exige read_write; leitura exige read_only. Devolve o jwt_payload.
    O escopo por pool é checado pelo caller (que resolve o pool da review)."""
    from .router import _decode_jwt  # local import: evita ciclo
    return _decode_jwt(request)


# 5a — campo ABAC por round (cada um concede um round; perfil combina o que precisar).
def _contest_field(round_n: int) -> str:
    return {1: "contestar", 2: "contestar_replica", 3: "contestar_treplica"}.get(round_n, "contestar_treplica")


def _review_field(round_n: int) -> str:
    return {1: "revisar", 2: "revisar_replica", 3: "revisar_treplica"}.get(round_n, "revisar_treplica")


# ─── ContestationThread endpoints ────────────────────────────────────────────

@contestation_router.get("/v1/evaluation/instances/{instance_id}/threads")
async def list_threads(
    instance_id: str,
    request: Request,
    dimension_id: str | None = None,
) -> dict:
    """
    List all ContestationThread entries for an evaluation instance.
    Returns threads grouped by dimension, ordered by round ASC.
    """
    tenant_id = _get_tenant(request)
    # T10-D2 — leitura AGRUPADA por dimensão (entries/current_state/scores) que a UI espera.
    grouped = await _db.get_instance_threads_grouped(
        request.app.state.db_pool, instance_id, tenant_id,
    )
    threads = grouped["threads"]
    if dimension_id:
        threads = [t for t in threads if t["dimension_id"] == dimension_id]
    return {
        "instance_id":   grouped["instance_id"],
        "result_id":     grouped["result_id"],
        "current_round": grouped["current_round"],
        "threads":       threads,
        "count":         len(threads),
    }


@contestation_router.post("/v1/evaluation/instances/{instance_id}/contest")
async def file_contestation(
    instance_id: str,
    body: ContestBody,
    request: Request,
) -> dict:
    """
    T5/5c — o avaliado contesta um CONJUNTO de critérios da sua avaliação num único round.
    Cria uma ContestationThread (author_type=human_agent) por critério e move o resultado
    contestation_open → under_review uma única vez (o round inteiro segue para revisão).
    """
    tenant_id = _get_tenant(request)
    # 5a — JWT obrigatório (mata o header-only do G-PROBE); identidade vem do 'sub'.
    from .router import _decode_jwt, _check_abac_permission  # local import: evita ciclo
    jwt_payload = _decode_jwt(request)
    user_id = jwt_payload["sub"]

    # Normaliza lote/single ANTES de tocar o estado (valida shape do corpo).
    items = body.normalized_items()

    # Verify instance exists and is in contestation_open state
    instance = await _db.get_instance(request.app.state.db_pool, instance_id, tenant_id)
    if not instance:
        raise HTTPException(status_code=404, detail="instance not found")

    result = await _db.get_result_by_instance(request.app.state.db_pool, instance_id, tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="result not found")

    if result.get("contestation_state") not in ("contestation_open", None):
        raise HTTPException(
            status_code=409,
            detail=f"contestation not allowed in state: {result.get('contestation_state')}",
        )

    # T5 — round = ciclo atual (contestação=1, réplica=2, tréplica=3). O contest NÃO
    # incrementa o round; só move open→under_review dentro do mesmo ciclo.
    current_round = result.get("round") or 1

    # Anti-replay opcional: se o cliente declarou o round, tem de bater com o corrente.
    if body.round is not None and body.round != current_round:
        raise HTTPException(
            status_code=409,
            detail=f"round mismatch: body={body.round} current={current_round}",
        )

    # 5a — POSSE: só o avaliado contesta a própria avaliação.
    evaluated = result.get("evaluated_user_id")
    if evaluated and evaluated != user_id:
        raise HTTPException(status_code=403, detail="only the evaluated agent can contest this result")
    # 5a — ABAC: campo de contestação do round corrente, com scope no pool da campanha.
    _camp = await _db.get_campaign(request.app.state.db_pool, result.get("campaign_id", ""), tenant_id)
    _pool_id = (_camp or {}).get("pool_id")
    if not _check_abac_permission(jwt_payload, _contest_field(current_round), _pool_id):
        raise HTTPException(status_code=403, detail=f"missing permission: {_contest_field(current_round)}")

    # Uma thread human_agent por critério contestado (mesmo round).
    threads = []
    for it in items:
        threads.append(await _db.create_contestation_thread(
            request.app.state.db_pool,
            tenant_id=tenant_id,
            evaluation_instance_id=instance_id,
            dimension_id=it["criterion_id"],
            round=current_round,
            author_type="human_agent",
            author_id=user_id,
            text=it["text"],
            evidence_entries=it["evidence_entries"],
        ))

    # Advance state machine UMA vez (round inteiro → under_review).
    await _db.set_contestation_state(
        request.app.state.db_pool,
        result["id"],
        "under_review",
        action_required="review",
        current_round=current_round,
    )

    # T4 — deadline de revisão ao entrar em under_review.
    from .router import apply_state_deadline  # local import: evita ciclo em import-time
    campaign = await _db.get_campaign(
        request.app.state.db_pool, result.get("campaign_id", ""), tenant_id
    )
    await apply_state_deadline(request.app.state.db_pool, campaign, result["id"], "review")

    contested = [it["criterion_id"] for it in items]
    logger.info("contestation filed: instance=%s criteria=%s round=%s by=%s",
                instance_id, contested, current_round, user_id)
    return {
        "submitted": True,
        "contested_dimensions": contested,
        "contestation_state": "under_review",
        "current_round": current_round,
        "threads": threads,
    }


@contestation_router.post("/v1/evaluation/instances/{instance_id}/review")
async def submit_review(
    instance_id: str,
    body: ReviewBody,
    request: Request,
) -> dict:
    """
    T5/5c — Revisão HUMANA em LOTE de uma contestação. JWT + ABAC do round + guarda
    revisor≠avaliado. Exige decisão para o conjunto EXATO de critérios contestados no
    round corrente (gate "tratar todas" §15.3 → 409 pending_contestations se faltar).
    Cria uma ContestationThread por decisão; aplica a transição do round uma única vez
    (reabre para o próximo round ou finaliza no último).
    """
    tenant_id = _get_tenant(request)
    # 5a — JWT obrigatório; revisor é sempre humano (a revisão IA é o ai-review/pre-review).
    from .router import _decode_jwt, _check_abac_permission  # local import: evita ciclo
    jwt_payload = _decode_jwt(request)
    author_id = jwt_payload["sub"]
    author_type = "human_reviewer"

    # T5/5c — normaliza lote/single e valida cada decisão.
    decisions = body.normalized_decisions()
    for d in decisions:
        if d.decision not in ("upheld", "revised"):
            raise HTTPException(status_code=400, detail=f"decision must be upheld or revised ({d.dimension_id})")
        if d.decision == "revised" and d.score_override is None:
            raise HTTPException(status_code=400, detail=f"score_override required when decision=revised ({d.dimension_id})")
        if not d.reason_text():
            raise HTTPException(status_code=400, detail=f"justification required ({d.dimension_id})")

    result = await _db.get_result_by_instance(request.app.state.db_pool, instance_id, tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="result not found")

    if result.get("contestation_state") != "under_review":
        raise HTTPException(status_code=409, detail="result not under_review")

    # 5a — guarda revisor≠avaliado: ninguém revisa a própria avaliação.
    if result.get("evaluated_user_id") and result["evaluated_user_id"] == author_id:
        raise HTTPException(status_code=403, detail="reviewer cannot be the evaluated agent")
    # 5a — ABAC: campo de revisão do round corrente, scope no pool da campanha.
    _round = result.get("round") or 1
    _camp = await _db.get_campaign(request.app.state.db_pool, result.get("campaign_id", ""), tenant_id)
    if not _check_abac_permission(jwt_payload, _review_field(_round), (_camp or {}).get("pool_id")):
        raise HTTPException(status_code=403, detail=f"missing permission: {_review_field(_round)}")

    # T5 — round = ciclo (1=contestação, 2=réplica, 3=tréplica). A revisão NÃO incrementa
    # o round; ela decide e então reabre (round+1) ou finaliza no último.
    current_round = result.get("round") or 1

    # T5/5c — gate "tratar todas" (§15.3): a revisão tem de cobrir o conjunto EXATO de
    # critérios contestados no round corrente. Faltando algum → 409 pending_contestations.
    contested = set(await _db.list_contested_criteria_for_round(
        request.app.state.db_pool, instance_id, tenant_id, current_round,
    ))
    decided = {d.dimension_id for d in decisions}
    missing = contested - decided
    if missing:
        raise HTTPException(
            status_code=409,
            detail={"error": "pending_contestations", "missing": sorted(missing),
                    "contested": sorted(contested), "round": current_round},
        )
    extra = decided - contested
    if extra:
        raise HTTPException(
            status_code=400,
            detail={"error": "decision_for_uncontested_criterion", "extra": sorted(extra),
                    "contested": sorted(contested), "round": current_round},
        )

    # Uma thread human_reviewer por critério decidido (mesmo round).
    threads = []
    for d in decisions:
        threads.append(await _db.create_contestation_thread(
            request.app.state.db_pool,
            tenant_id=tenant_id,
            evaluation_instance_id=instance_id,
            dimension_id=d.dimension_id,
            round=current_round,
            author_type=author_type,
            author_id=author_id,
            text=d.reason_text(),
            decision=d.decision,
            score_override=d.score_override,
            evidence_entries=[e.model_dump() for e in d.evidence_entries],
        ))

    upheld  = [d.dimension_id for d in decisions if d.decision == "upheld"]
    revised = [d.dimension_id for d in decisions if d.decision == "revised"]
    any_revised = len(revised) > 0

    max_rounds = 3  # default
    campaign = None
    try:
        campaign = await _db.get_campaign(
            request.app.state.db_pool, result.get("campaign_id", ""), tenant_id
        )
        if campaign:
            max_rounds = int((campaign.get("contestation_policy") or {}).get("max_rounds", 3))
    except Exception:
        pass  # non-fatal — default 3

    from .router import finalize_evaluation, apply_state_deadline  # local import: evita ciclo

    finalized = False
    if current_round < max_rounds:
        # T5 — decisão do round (qualquer upheld/revised) REABRE para a apelação seguinte
        # enquanto há round restante; o avaliado decide re-contestar (avança) ou aceitar
        # (o prazo finaliza em uncontested). Só o último round finaliza pela revisão.
        next_state = "contestation_open"
        await _db.set_contestation_state(
            request.app.state.db_pool, result["id"], next_state,
            action_required=None, current_round=current_round + 1,
        )
        _camp2 = campaign or await _db.get_campaign(
            request.app.state.db_pool, result.get("campaign_id", ""), tenant_id
        )
        await apply_state_deadline(request.app.state.db_pool, _camp2, result["id"], "contest")
        new_round = current_round + 1
    else:
        # Último round → finaliza pelo emissor único (T3). reason: revised se houve qualquer
        # override no round; senão upheld. (Consolidação por pesos do form é T7; aqui a
        # nota corrente.)
        reason   = "revised" if any_revised else "upheld"
        next_state = "closed_revised" if any_revised else "closed_upheld"
        await finalize_evaluation(
            request.app.state.db_pool,
            request.app.state.kafka_producer,
            result_id=result["id"],
            tenant_id=tenant_id,
            instance_id=instance_id,
            session_id=result.get("session_id", "") or "",
            campaign_id=result.get("campaign_id", "") or "",
            contestation_state=next_state,
            finalize_reason=reason,
            final_score=float(result.get("overall_score") or result.get("final_score") or 0),
            evaluated_agent_type=result.get("evaluated_agent_type"),
            process_duration_ms=0,
        )
        finalized = True
        new_round = current_round

    logger.info(
        "review submitted: instance=%s upheld=%s revised=%s round=%s max=%s next_state=%s finalized=%s by=%s",
        instance_id, upheld, revised, current_round, max_rounds, next_state, finalized, author_id,
    )
    return {
        "submitted": True,
        "contestation_state": next_state,
        "current_round": new_round,
        "finalized": finalized,
        "dimensions_upheld": upheld,
        "dimensions_revised": revised,
        "threads": threads,
    }


@contestation_router.post("/v1/evaluation/instances/{instance_id}/pre-review")
async def submit_pre_review(
    instance_id: str,
    body: PreReviewBody,
    request: Request,
) -> dict:
    """
    Pre-publication AI reviewer submits quality gate review.
    author_type=pre_reviewer_ai, round=1 (same round as evaluator).
    Optionally includes calibration_signal → triggers CurationReview creation.
    """
    from .router import _require_service  # local import: evita ciclo
    _require_service(request)  # G-PROBE fase 2 — pre_reviewer_ai (sistema)
    tenant_id = _get_tenant(request)
    agent_id = _get_user(request)

    instance = await _db.get_instance(request.app.state.db_pool, instance_id, tenant_id)
    if not instance:
        raise HTTPException(status_code=404, detail="instance not found")

    result = await _db.get_result_by_instance(request.app.state.db_pool, instance_id, tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="result not found")

    if result.get("contestation_state") not in ("pre_review_pending", None):
        raise HTTPException(status_code=409, detail="pre-review already completed or not applicable")

    thread = await _db.create_contestation_thread(
        request.app.state.db_pool,
        tenant_id=tenant_id,
        evaluation_instance_id=instance_id,
        dimension_id=body.dimension_id,
        round=1,  # stored as round=1, author_type distinguishes from evaluator
        author_type="pre_reviewer_ai",
        author_id=agent_id,
        text=body.text,
        score_override=body.score_override if body.action == "adjust" else None,
        evidence_entries=[e.model_dump() for e in body.evidence_entries],
        calibration_signal=body.calibration_signal,
    )

    # If calibration_signal present → create CurationReview (async, non-blocking)
    curation_review = None
    if body.calibration_signal:
        curation_review = await _db.create_curation_review(
            request.app.state.db_pool,
            tenant_id=tenant_id,
            evaluation_instance_id=instance_id,
            trigger="reviewer_signal",
        )
        logger.info("curation review triggered from calibration_signal: instance=%s", instance_id)

    # Advance to contestation_open (pre-review complete → publish result to evaluated agent)
    await _db.set_contestation_state(
        request.app.state.db_pool,
        result["id"],
        "contestation_open",
        action_required=None,
    )
    # Mark pre_review_complete on result
    async with request.app.state.db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE evaluation.results SET pre_review_complete=TRUE, updated_at=now() WHERE id=$1",
            result["id"],
        )

    logger.info("pre-review submitted: instance=%s dimension=%s action=%s by=%s",
                instance_id, body.dimension_id, body.action, agent_id)
    return {
        "thread": thread,
        "contestation_state": "contestation_open",
        "curation_review_created": curation_review is not None,
    }


# ─── T12 — ai-review (resolve o gate de resultados sinalizados) ────────────────

class AiReviewBody(BaseModel):
    adjusted_overall:   float | None = None   # ajuste opcional da nota geral (0–10)
    notes:              str = ""
    calibration_signal: dict | None = None    # opcional → fila de curadoria (laço mole)


@contestation_router.post("/v1/evaluation/instances/{instance_id}/ai-review")
async def submit_ai_review(instance_id: str, body: AiReviewBody, request: Request) -> dict:
    """T12 — resolve o gate `ai_review` de um resultado sinalizado no ingest (score fora
    de faixa ∨ sem nota). O revisor IA (sistema) opcionalmente ajusta a nota e PUBLICA:
    avaliado IA → finalize(auto_ai); avaliado humano → contestation_open (abre janela).
    Timeout técnico do gate (stall → publica sem ajuste) é follow-up."""
    from .router import _require_service  # local import: evita ciclo
    tenant_id = _get_tenant(request)
    _require_service(request)  # G-PROBE fase 2 — revisor IA = sistema
    pool = request.app.state.db_pool
    producer = request.app.state.kafka_producer

    result = await _db.get_result_by_instance(pool, instance_id, tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="result not found")
    if result.get("result_state") != "ai_review" and result.get("contestation_state") != "pre_review_pending":
        raise HTTPException(status_code=409, detail=f"result not in ai_review: {result.get('result_state')}")

    final = (body.adjusted_overall if body.adjusted_overall is not None
             else float(result.get("overall_score") or result.get("final_score") or 0))
    if body.adjusted_overall is not None:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE evaluation.results SET overall_score=$1, normalized_score=$2, updated_at=now() WHERE id=$3",
                final, round(final / 10.0, 3), result["id"],
            )

    if body.calibration_signal:
        try:
            await _db.create_curation_review(
                pool, tenant_id=tenant_id, evaluation_instance_id=instance_id, trigger="reviewer_signal",
            )
        except Exception as exc:
            logger.warning("ai-review: curation review failed (non-fatal): %s", exc)

    from .router import finalize_evaluation, apply_state_deadline  # local import: evita ciclo
    eat = result.get("evaluated_agent_type")
    if eat == "ai_agent":
        await finalize_evaluation(
            pool, producer,
            result_id=result["id"], tenant_id=tenant_id, instance_id=instance_id,
            session_id=result.get("session_id", "") or "",
            campaign_id=result.get("campaign_id", "") or "",
            contestation_state="auto_finalized", finalize_reason="auto_ai",
            final_score=final, evaluated_agent_type="ai_agent",
            run_curation=True, normalized_score=round(final / 10.0, 3),
        )
        logger.info("ai-review: instance=%s (ai_agent) → finalized auto_ai score=%s", instance_id, final)
        return {"instance_id": instance_id, "result_state": "finalized", "published_to": "auto_ai"}

    # avaliado humano: publica abrindo a janela de contestação
    await _db.set_contestation_state(pool, result["id"], "contestation_open", action_required=None)
    _camp = await _db.get_campaign(pool, result.get("campaign_id", "") or "", tenant_id)
    await apply_state_deadline(pool, _camp, result["id"], "contest")
    logger.info("ai-review: instance=%s (human) → contestation_open score=%s", instance_id, final)
    return {"instance_id": instance_id, "result_state": "open", "published_to": "human_contestation"}


# ─── CurationReview (curator queue) ──────────────────────────────────────────

@contestation_router.get("/v1/evaluation/curations")
async def list_curations(
    request: Request,
    campaign_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List curation reviews — the curator queue."""
    from .router import _check_abac_permission  # local import: evita ciclo
    tenant_id = _get_tenant(request)
    jwt_payload = _require_curar(request, write=False)
    if not _check_abac_permission(jwt_payload, "curar", None, min_access="read_only"):
        raise HTTPException(status_code=403, detail="caller lacks 'curar' permission")
    reviews = await _db.list_curation_reviews(
        request.app.state.db_pool,
        tenant_id,
        campaign_id=campaign_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"reviews": reviews, "count": len(reviews)}


@contestation_router.post("/v1/evaluation/curations/{review_id}/resolve")
async def resolve_curation(
    review_id: str,
    body: CurationResolveBody,
    request: Request,
) -> dict:
    """
    Curator resolves a curation review.
    - approved      → mark as approved, no CalibrationNote created
    - recalibrated  → create CalibrationNote, publish to KB (async)
    - bias_flagged  → create CalibrationNote with severity=high, publish to KB
    """
    from .router import _check_abac_permission  # local import: evita ciclo
    tenant_id = _get_tenant(request)
    jwt_payload = _require_curar(request, write=True)
    curator_id = jwt_payload.get("sub", "")
    # ABAC curar (read_write), escopo por pool da review.
    _pool_id = await _curation_pool_id(request.app.state.db_pool, review_id, tenant_id)
    if not _check_abac_permission(jwt_payload, "curar", _pool_id, min_access="read_write"):
        raise HTTPException(status_code=403, detail="caller lacks 'curar' permission for this pool")

    if body.status not in ("approved", "recalibrated", "bias_flagged"):
        raise HTTPException(status_code=400, detail="status must be approved, recalibrated, or bias_flagged")

    # Always fetch campaign_id and evaluation_instance_id (needed for Kafka event regardless of status)
    async with request.app.state.db_pool.acquire() as conn:
        _cr_row = await conn.fetchrow(
            """
            SELECT i.campaign_id, cr.evaluation_instance_id
            FROM evaluation.curation_reviews cr
            JOIN evaluation.instances i ON i.id = cr.evaluation_instance_id
            WHERE cr.id=$1 AND cr.tenant_id=$2
            """,
            review_id, tenant_id,
        )
    if not _cr_row:
        raise HTTPException(status_code=404, detail="curation review not found")
    _campaign_id = _cr_row["campaign_id"] or ""
    _evaluation_instance_id = _cr_row["evaluation_instance_id"] or ""

    calibration_note = None
    if body.status in ("recalibrated", "bias_flagged"):
        if not all([body.calibration_note_text, body.dimension_id, body.evaluator_id, body.skill_version]):
            raise HTTPException(
                status_code=400,
                detail="calibration_note_text, dimension_id, evaluator_id, skill_version required for recalibrated/bias_flagged",
            )

        calibration_note = await _db.create_calibration_note(
            request.app.state.db_pool,
            tenant_id=tenant_id,
            campaign_id=_campaign_id,  # 5d — bug: usava 'row' inexistente → NameError
            dimension_id=body.dimension_id,
            criterion_id=body.criterion_id,  # T14 (c)
            evaluator_id=body.evaluator_id,
            skill_version=body.skill_version,
            text=body.calibration_note_text,
            severity="high" if body.status == "bias_flagged" else body.severity,
        )

    review = await _db.resolve_curation_review(
        request.app.state.db_pool,
        review_id,
        tenant_id,
        status=body.status,
        curator_id=curator_id,
        curator_notes=body.curator_notes,
        calibration_note_id=calibration_note["id"] if calibration_note else None,
    )
    if not review:
        raise HTTPException(status_code=404, detail="curation review not found")

    # Publish CalibrationNote to mcp-server-knowledge knowledge namespace
    kb_published = False
    if calibration_note:
        note_severity = "high" if body.status == "bias_flagged" else body.severity
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                kb_resp = await client.post(
                    f"{settings.knowledge_api_url}/v1/knowledge/snippets",
                    json={
                        "tenant_id":  tenant_id,
                        "namespace":  f"evaluation:calibration:{calibration_note.get('campaign_id', '')}",
                        "content":    calibration_note.get("text", ""),
                        "source_ref": f"calibration_note:{calibration_note['id']}",
                        "metadata": {
                            "dimension_id":  calibration_note.get("dimension_id", ""),
                            "criterion_id":  calibration_note.get("criterion_id") or "",  # T14 (c)
                            "evaluator_id":  calibration_note.get("evaluator_id", ""),
                            "skill_version": calibration_note.get("skill_version", ""),
                            "severity":      note_severity,
                            "note_id":       calibration_note["id"],
                        },
                    },
                )
            if kb_resp.status_code in (200, 201):
                kb_published = True
                await _db.mark_calibration_note_published(
                    request.app.state.db_pool,
                    calibration_note["id"],
                    tenant_id,
                )
                calibration_note["published_to_kb"] = True
                logger.info(
                    "calibration note published to KB: note=%s campaign=%s",
                    calibration_note["id"], calibration_note.get("campaign_id"),
                )
            else:
                logger.warning(
                    "KB publish failed (HTTP %s): note=%s",
                    kb_resp.status_code, calibration_note["id"],
                )
        except Exception as exc:
            logger.error("KB publish error (non-blocking): note=%s err=%s",
                         calibration_note["id"], exc)

    # Emit calibration.events Kafka event — always, regardless of status (approved/recalibrated/bias_flagged)
    try:
        await _kafka.emit_calibration_reviewed(
            request.app.state.kafka_producer,
            review_id=review_id,
            campaign_id=_campaign_id,
            evaluation_instance_id=_evaluation_instance_id,
            tenant_id=tenant_id,
            evaluator_id=body.evaluator_id or "",
            skill_version=body.skill_version or "",
            decision=body.status,
            calibration_note_id=calibration_note["id"] if calibration_note else None,
        )
    except Exception as exc:
        logger.error("failed to emit calibration_reviewed event: %s", exc)

    # Emit calibration_note_published to evaluation.events (only if KB succeeded)
    if calibration_note and kb_published:
        try:
            await _kafka.emit_calibration_note_published(
                request.app.state.kafka_producer,
                note_id=calibration_note["id"],
                campaign_id=calibration_note.get("campaign_id", ""),
                evaluator_id=body.evaluator_id,
                severity=note_severity,
                tenant_id=tenant_id,
            )
        except Exception as exc:
            logger.error("failed to emit calibration_note_published event: %s", exc)

    logger.info("curation resolved: review=%s status=%s kb_published=%s by=%s",
                review_id, body.status, kb_published, curator_id)
    return {
        "review": review,
        "calibration_note": calibration_note,
        "kb_published": kb_published,
    }


# ─── R8c — Curadoria cega (re-pontuação sem ver a IA) ─────────────────────────

class BlindRescoreBody(BaseModel):
    """Re-pontuação CEGA do curador: respostas por critério (mesmo shape do avaliador)."""
    criterion_responses: list[dict] = Field(default_factory=list)


async def _load_blind_review_ctx(pool, review_id: str, tenant_id: str) -> dict:
    """Resolve review(blind) → instance → result → form (versão pinada). Erros HTTP
    explícitos. NÃO inclui as notas da IA (o caller decide o que expor)."""
    review = await _db.get_curation_review(pool, review_id, tenant_id)
    if not review:
        raise HTTPException(404, detail="curation review not found")
    if review.get("mode") != "blind":
        raise HTTPException(400, detail="curation review is not a blind-stage review")
    instance_id = review.get("evaluation_instance_id") or ""
    instance = await _db.get_instance(pool, instance_id, tenant_id)
    if not instance:
        raise HTTPException(409, detail="evaluation instance not found")
    result = await _db.get_result_by_instance(pool, instance_id, tenant_id)
    if not result:
        raise HTTPException(409, detail="evaluation result not found for instance")
    form = await _db.get_form_version(
        pool, result.get("form_id") or "", tenant_id,
        int(instance.get("form_version") or 1),
    )
    if not form:
        raise HTTPException(409, detail="pinned form version not found")
    return {"review": review, "instance": instance, "result": result, "form": form}


@contestation_router.get("/v1/evaluation/curations/{review_id}/blind-context")
async def get_blind_context(review_id: str, request: Request) -> dict:
    """Contexto da tarefa CEGA: o form a re-pontuar + ponteiros p/ o transcript —
    **sem** as notas da IA (o curador pontua às cegas). Se já houver re-pontuação,
    devolve o reveal armazenado (re-abrir mostra o diff)."""
    from .router import _check_abac_permission  # local import: evita ciclo
    tenant_id = _get_tenant(request)
    jwt_payload = _require_curar(request, write=False)
    pool = request.app.state.db_pool
    _pool_id = await _curation_pool_id(pool, review_id, tenant_id)
    if not _check_abac_permission(jwt_payload, "curar", _pool_id, min_access="read_only"):
        raise HTTPException(status_code=403, detail="caller lacks 'curar' permission for this pool")
    ctx = await _load_blind_review_ctx(pool, review_id, tenant_id)
    blind = await _db.get_blind_result(pool, review_id, tenant_id)
    return {
        "review":       ctx["review"],
        "form":         ctx["form"],                       # dimensions[].criteria[] p/ pontuar
        "result_id":    ctx["result"].get("id"),           # só p/ buscar transcript (sem notas)
        "session_id":   ctx["result"].get("session_id"),
        "instance_id":  ctx["review"].get("evaluation_instance_id"),
        "already_rescored": blind is not None,
        "blind_result": blind,                             # reveal+diff se já re-pontuado, senão null
    }


@contestation_router.post("/v1/evaluation/curations/{review_id}/blind-rescore")
async def blind_rescore(review_id: str, body: BlindRescoreBody, request: Request) -> dict:
    """Curador submete a re-pontuação CEGA → sistema revela a nota da IA + diff por
    dimensão. A nota cega é um **artefato de calibração** (NÃO altera `final_score` nem
    re-emite `evaluation_finalized`). Reusa `scoring.aggregate_scores` (form = fonte única)
    p/ humano E IA — diff apples-to-apples. Idempotente (`uq_blind_per_review` → 409)."""
    from .router import _check_abac_permission  # local import: evita ciclo
    tenant_id  = _get_tenant(request)
    jwt_payload = _require_curar(request, write=True)
    curator_id = jwt_payload.get("sub", "")
    pool = request.app.state.db_pool
    _pool_id = await _curation_pool_id(pool, review_id, tenant_id)
    if not _check_abac_permission(jwt_payload, "curar", _pool_id, min_access="read_write"):
        raise HTTPException(status_code=403, detail="caller lacks 'curar' permission for this pool")

    ctx = await _load_blind_review_ctx(pool, review_id, tenant_id)
    review, instance, result, form = ctx["review"], ctx["instance"], ctx["result"], ctx["form"]

    existing = await _db.get_blind_result(pool, review_id, tenant_id)
    if existing:
        raise HTTPException(409, detail="blind review already re-scored")

    # Valida as respostas cegas contra o form pinado.
    violations = _scoring.validate_criterion_responses(form, body.criterion_responses)
    if violations:
        raise HTTPException(422, detail={"error": "invalid_criterion_responses", "violations": violations})

    # Agregação humana (cega) e snapshot da IA — MESMA função, MESMO form.
    blind_overall, blind_by_dim = _scoring.aggregate_scores(form, body.criterion_responses)
    ai_responses = await _db.list_criterion_responses(pool, result.get("id") or "", tenant_id)
    ai_overall, ai_by_dim = _scoring.aggregate_scores(form, ai_responses)

    # Limiar de desacordo da campanha (default 0.20).
    campaign = await _db.get_campaign(pool, review.get("campaign_id") or result.get("campaign_id") or "", tenant_id)
    severity_min = _sampling.blind_stage_config(campaign or {})["severity_min"]
    diffs = _scoring.compute_dimension_diffs(ai_by_dim, blind_by_dim, severity_min)

    blind = await _db.create_blind_result(
        pool,
        tenant_id=tenant_id,
        curation_review_id=review_id,
        evaluation_instance_id=review.get("evaluation_instance_id") or "",
        campaign_id=review.get("campaign_id") or result.get("campaign_id") or "",
        curator_id=curator_id,
        blind_criterion_responses=body.criterion_responses,
        blind_overall_score=blind_overall,
        blind_by_dimension=blind_by_dim,
        ai_overall_score=ai_overall,
        ai_by_dimension=ai_by_dim,
        per_dimension_diffs=diffs,
    )
    logger.info("blind re-score stored: review=%s by=%s disagreements=%s",
                review_id, curator_id, sum(1 for d in diffs if d.get("disagree")))

    # Reveal: o curador vê a nota da IA + o diff só DEPOIS de pontuar.
    return {
        "blind_result":        blind,
        "severity_min":        severity_min,
        "ai_overall_score":    ai_overall,
        "blind_overall_score": blind_overall,
        "per_dimension_diffs": diffs,
    }


class BlindResolveBody(BaseModel):
    """Resolução da curadoria cega após o reveal. `flag_bias` eleva a `bias_flagged`
    (severidade alta); `severity` aplica-se às notas geradas (default medium)."""
    curator_notes: str | None = None
    severity: str = "medium"
    flag_bias: bool = False
    evaluator_id: str | None = None   # default: evaluator_agent_id do resultado


async def _publish_calibration_note_kb(producer, *, tenant_id: str, note: dict, severity: str) -> bool:
    """Publica UMA CalibrationNote no namespace de conhecimento (RAG) + emite
    `calibration_note_published`. Best-effort (não derruba a resolução). Retorna se publicou."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.knowledge_api_url}/v1/knowledge/snippets",
                json={
                    "tenant_id":  tenant_id,
                    "namespace":  f"evaluation:calibration:{note.get('campaign_id', '')}",
                    "content":    note.get("text", ""),
                    "source_ref": f"calibration_note:{note['id']}",
                    "metadata": {
                        "dimension_id":  note.get("dimension_id", ""),
                        "evaluator_id":  note.get("evaluator_id", ""),
                        "skill_version": note.get("skill_version", ""),
                        "severity":      severity,
                        "note_id":       note["id"],
                        "origin":        "blind_curation",  # R8c — distingue do laço Arc 13
                    },
                },
            )
        return resp.status_code in (200, 201)
    except Exception as exc:  # noqa: BLE001
        logger.error("KB publish error (non-blocking): note=%s err=%s", note.get("id"), exc)
        return False


@contestation_router.post("/v1/evaluation/curations/{review_id}/blind-resolve")
async def blind_resolve(review_id: str, body: BlindResolveBody, request: Request) -> dict:
    """Resolve a curadoria cega: para cada dimensão em DESACORDO (`disagree`), gera uma
    `CalibrationNote` (→ KB/RAG + `calibration.events`) e resolve a review. A nota cega é
    **artefato de calibração** — corrige avaliações FUTURAS, não esta (não toca `final_score`).
    Sem desacordo → `approved`, sem nota. `flag_bias` → `bias_flagged` (severidade alta)."""
    from .router import _check_abac_permission  # local import: evita ciclo
    tenant_id  = _get_tenant(request)
    jwt_payload = _require_curar(request, write=True)
    curator_id = jwt_payload.get("sub", "")
    pool     = request.app.state.db_pool
    producer = request.app.state.kafka_producer
    _pool_id = await _curation_pool_id(pool, review_id, tenant_id)
    if not _check_abac_permission(jwt_payload, "curar", _pool_id, min_access="read_write"):
        raise HTTPException(status_code=403, detail="caller lacks 'curar' permission for this pool")

    review = await _db.get_curation_review(pool, review_id, tenant_id)
    if not review:
        raise HTTPException(404, detail="curation review not found")
    if review.get("mode") != "blind":
        raise HTTPException(400, detail="curation review is not a blind-stage review")
    if review.get("status") != "pending":
        raise HTTPException(409, detail=f"review already resolved ({review.get('status')})")

    blind = await _db.get_blind_result(pool, review_id, tenant_id)
    if not blind:
        raise HTTPException(409, detail="blind review not re-scored yet")

    instance_id = review.get("evaluation_instance_id") or ""
    result      = await _db.get_result_by_instance(pool, instance_id, tenant_id)
    campaign_id = review.get("campaign_id") or (result or {}).get("campaign_id") or ""
    evaluator_id  = body.evaluator_id or (result or {}).get("evaluator_agent_id") or ""
    skill_version = review.get("skill_version") or ""

    disagreements = [d for d in (blind.get("per_dimension_diffs") or []) if d.get("disagree")]
    status = _scoring.blind_resolution_status(len(disagreements), body.flag_bias)
    note_severity = "high" if body.flag_bias else body.severity

    # Uma CalibrationNote por dimensão em desacordo (a nota humana é a referência).
    notes: list[dict] = []
    for d in disagreements:
        dim = d.get("dimension_id", "")
        text = (
            f"Curadoria cega — divergência em '{dim}': IA={d.get('ai_score')} vs "
            f"humano={d.get('human_score')} (diff={d.get('diff')}, limiar excedido). "
            f"A nota humana (contra a realidade, não a KB) é a referência."
        )
        if body.curator_notes:
            text += f" Nota do curador: {body.curator_notes}"
        note = await _db.create_calibration_note(
            pool,
            tenant_id=tenant_id, campaign_id=campaign_id, dimension_id=dim,
            evaluator_id=evaluator_id, skill_version=skill_version,
            text=text, severity=note_severity,
        )
        kb_ok = await _publish_calibration_note_kb(
            producer, tenant_id=tenant_id, note=note, severity=note_severity,
        )
        if kb_ok:
            await _db.mark_calibration_note_published(pool, note["id"], tenant_id)
            note["published_to_kb"] = True
            try:
                await _kafka.emit_calibration_note_published(
                    producer, note_id=note["id"], campaign_id=campaign_id,
                    evaluator_id=evaluator_id, severity=note_severity, tenant_id=tenant_id,
                )
            except Exception as exc:
                logger.error("emit calibration_note_published failed: %s", exc)
        notes.append(note)

    resolved = await _db.resolve_curation_review(
        pool, review_id, tenant_id,
        status=status, curator_id=curator_id, curator_notes=body.curator_notes,
        calibration_note_id=notes[0]["id"] if notes else None,
    )

    # calibration.events — sempre (alimenta a divergência R8b por skill_version).
    try:
        await _kafka.emit_calibration_reviewed(
            producer, review_id=review_id, campaign_id=campaign_id,
            evaluation_instance_id=instance_id, tenant_id=tenant_id,
            evaluator_id=evaluator_id, skill_version=skill_version,
            decision=status, calibration_note_id=notes[0]["id"] if notes else None,
        )
    except Exception as exc:
        logger.error("emit calibration_reviewed failed: %s", exc)

    logger.info("blind curation resolved: review=%s status=%s disagreements=%s notes=%s by=%s",
                review_id, status, len(disagreements), len(notes), curator_id)
    return {
        "review":            resolved,
        "status":            status,
        "disagreements":     len(disagreements),
        "calibration_notes": notes,
    }


# ─── CalibrationNotes ─────────────────────────────────────────────────────────

@contestation_router.get("/v1/evaluation/calibration-notes")
async def list_calibration_notes(
    request: Request,
    campaign_id: str | None = None,
    evaluator_id: str | None = None,
    published_to_kb: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List calibration notes — the evaluator calibration history."""
    from .router import _require_any_evaluation  # local import: evita ciclo
    _require_any_evaluation(request)  # G-PROBE fase 2 — read compartilhado de lista
    tenant_id = _get_tenant(request)
    notes = await _db.list_calibration_notes(
        request.app.state.db_pool,
        tenant_id,
        campaign_id=campaign_id,
        evaluator_id=evaluator_id,
        published_to_kb=published_to_kb,
        limit=limit,
        offset=offset,
    )
    return {"notes": notes, "count": len(notes)}


@contestation_router.post("/v1/evaluation/calibration-notes/{note_id}/publish")
async def publish_calibration_note(
    note_id: str,
    request: Request,
) -> dict:
    """
    Mark a CalibrationNote as published to the knowledge namespace.
    Called after successful ingest into mcp-server-knowledge.
    Emits calibration_note_published Kafka event.
    """
    from .router import _require_service  # local import: evita ciclo
    _require_service(request)  # G-PROBE fase 2 — sistema (após ingest no KB)
    tenant_id = _get_tenant(request)
    note = await _db.mark_calibration_note_published(
        request.app.state.db_pool,
        note_id,
        tenant_id,
    )
    if not note:
        raise HTTPException(status_code=404, detail="calibration note not found")

    try:
        await _kafka.emit_calibration_note_published(
            request.app.state.kafka_producer,
            note_id=note_id,
            campaign_id=note.get("campaign_id", ""),
            evaluator_id=note.get("evaluator_id", ""),
            severity=note.get("severity", "low"),
            tenant_id=tenant_id,
        )
    except Exception as exc:
        logger.error("failed to emit calibration_note_published event: %s", exc)

    return {"note": note}


# ─── CurationSamplingRules CRUD ───────────────────────────────────────────────

@contestation_router.get("/v1/evaluation/campaigns/{campaign_id}/sampling-rules")
async def list_sampling_rules(
    campaign_id: str,
    request: Request,
) -> dict:
    """List curation sampling rules for a campaign."""
    from .router import _require_any_evaluation  # local import: evita ciclo
    _require_any_evaluation(request)  # G-PROBE fase 2 — read compartilhado de lista
    tenant_id = _get_tenant(request)
    rules = await _db.list_sampling_rules(
        request.app.state.db_pool,
        tenant_id,
        campaign_id,
    )
    return {"rules": rules, "count": len(rules)}


@contestation_router.post("/v1/evaluation/campaigns/{campaign_id}/sampling-rules")
async def create_sampling_rule(
    campaign_id: str,
    body: SamplingRuleBody,
    request: Request,
) -> dict:
    """Create a curation sampling rule for a campaign."""
    from .router import _require_service_or_eval_write  # local import: evita ciclo
    _require_service_or_eval_write(request)  # G-PROBE fase 2 — UI (Bearer+ABAC) ou serviço
    tenant_id = _get_tenant(request)
    rule = await _db.create_sampling_rule(
        request.app.state.db_pool,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        rule_type=body.rule_type,
        params=body.params,
        enabled=body.enabled,
        priority=body.priority,
    )
    return {"rule": rule}


@contestation_router.put("/v1/evaluation/campaigns/{campaign_id}/sampling-rules/{rule_id}")
async def update_sampling_rule(
    campaign_id: str,
    rule_id: str,
    body: SamplingRuleUpdateBody,
    request: Request,
) -> dict:
    """Update a curation sampling rule."""
    from .router import _require_service_or_eval_write  # local import: evita ciclo
    _require_service_or_eval_write(request)  # G-PROBE fase 2 — UI (Bearer+ABAC) ou serviço
    tenant_id = _get_tenant(request)
    updates = body.model_dump(exclude_none=True)
    rule = await _db.update_sampling_rule(
        request.app.state.db_pool,
        rule_id,
        tenant_id,
        **updates,
    )
    if not rule:
        raise HTTPException(status_code=404, detail="sampling rule not found")
    return {"rule": rule}


@contestation_router.delete("/v1/evaluation/campaigns/{campaign_id}/sampling-rules/{rule_id}")
async def delete_sampling_rule(
    campaign_id: str,
    rule_id: str,
    request: Request,
) -> dict:
    """Delete a curation sampling rule."""
    from .router import _require_service_or_eval_write  # local import: evita ciclo
    _require_service_or_eval_write(request)  # G-PROBE fase 2 — UI (Bearer+ABAC) ou serviço
    tenant_id = _get_tenant(request)
    deleted = await _db.delete_sampling_rule(
        request.app.state.db_pool,
        rule_id,
        tenant_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="sampling rule not found")
    return {"deleted": True}
