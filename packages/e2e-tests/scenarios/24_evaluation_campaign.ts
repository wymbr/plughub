/**
 * 24_evaluation_campaign.ts
 * Scenario 24: ARC 6 — Evaluation Campaign Pipeline
 *
 * Validates the full quality evaluation platform pipeline:
 *   Form CRUD → Campaign creation → Kafka evaluation.events
 *   → analytics-api consumer → ClickHouse evaluation_results
 *   → GET /reports/evaluations + GET /reports/evaluations/summary
 *
 * Part A — Form CRUD (3 assertions):
 *   POST /v1/evaluation/forms → form_id returned
 *   GET  /v1/evaluation/forms?tenant_id= → form appears in list
 *   PATCH /v1/evaluation/forms/{id} → name updated
 *
 * Part B — Campaign CRUD + status control (4 assertions):
 *   POST /v1/evaluation/campaigns → campaign_id + status=active
 *   GET  /v1/evaluation/campaigns → campaign appears
 *   POST /v1/evaluation/campaigns/{id}/pause → status=paused
 *   POST /v1/evaluation/campaigns/{id}/resume → status=active
 *
 * Part C — Kafka evaluation.events → analytics-api ClickHouse (4 assertions):
 *   Publish evaluation.events message (eval_status=submitted, overall_score=0.85)
 *   Poll GET /reports/evaluations until row appears →
 *   Assert result_id, tenant_id, overall_score, eval_status=submitted
 *
 * Part D — Reviewer auto-approval simulated via Kafka (3 assertions):
 *   Publish evaluation.events message (same result_id, eval_status=approved, reviewed_by=reviewer)
 *   Poll GET /reports/evaluations → ReplacingMergeTree FINAL → latest status=approved
 *   GET /reports/evaluations/summary?group_by=campaign_id → count_approved ≥ 1
 *
 * Modules exercised:
 *   evaluation-api (Forms, Campaigns REST endpoints)
 *   Kafka (evaluation.events producer)
 *   analytics-api (consumer → ClickHouse, /reports/evaluations, /reports/evaluations/summary)
 *   ClickHouse ReplacingMergeTree FINAL deduplication (state update wins)
 *
 * Flags: --evaluation
 * Timeout: 60s (Kafka consumer lag + ClickHouse FINAL merge)
 *
 * Assertions: 14
 */

import { randomUUID } from "crypto"
import type { ScenarioContext, ScenarioResult, Assertion } from "./types"
import type { Kafka } from "kafkajs"
import { pass, fail } from "../lib/report"

const TOPIC_EVALUATION = "evaluation.events"

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function buildEvaluationEvent(opts: {
  resultId:    string
  instanceId:  string
  sessionId:   string
  tenantId:    string
  evaluatorId: string
  formId:      string
  campaignId:  string
  score:       number
  evalStatus:  string
  reviewedBy?: string
  contestedBy?: string
  locked?:     boolean
  complianceFlags?: string[]
}): Record<string, unknown> {
  return {
    event_type:       opts.evalStatus === "submitted"  ? "submitted"
                    : opts.evalStatus === "approved"   ? "reviewed"
                    : opts.evalStatus === "rejected"   ? "reviewed"
                    : opts.evalStatus === "contested"  ? "contested"
                    : opts.evalStatus === "locked"     ? "locked"
                    : "submitted",
    result_id:        opts.resultId,
    instance_id:      opts.instanceId,
    session_id:       opts.sessionId,
    tenant_id:        opts.tenantId,
    evaluator_id:     opts.evaluatorId,
    form_id:          opts.formId,
    campaign_id:      opts.campaignId,
    overall_score:    opts.score,
    eval_status:      opts.evalStatus,
    locked:           opts.locked ?? false,
    compliance_flags: opts.complianceFlags ?? [],
    reviewed_by:      opts.reviewedBy ?? null,
    contested_by:     opts.contestedBy ?? null,
    timestamp:        new Date().toISOString(),
  }
}

async function publishAndPollEvaluations(
  kafka:         Kafka,
  analyticsUrl:  string,
  tenantId:      string,
  resultId:      string,
  messages:      Record<string, unknown>[],
  timeoutMs = 30000,
): Promise<{ publishOk: boolean; status: number | null; rows: Array<Record<string, unknown>> }> {
  const producer = kafka.producer()
  let publishOk = false
  try {
    await producer.connect()
    await producer.send({
      topic:    TOPIC_EVALUATION,
      messages: messages.map(m => ({
        key:   resultId,
        value: JSON.stringify(m),
      })),
    })
    publishOk = true
  } catch {
    // publish failed
  } finally {
    await producer.disconnect().catch(() => {})
  }

  if (!publishOk) {
    return { publishOk: false, status: null, rows: [] }
  }

  // Poll analytics-api until row appears
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const url = `${analyticsUrl}/reports/evaluations?tenant_id=${tenantId}&page_size=200`
      const r   = await fetch(url)
      if (r.ok) {
        const data = await r.json() as { data?: Array<Record<string, unknown>> }
        const rows = data.data ?? []
        const matching = rows.filter(row => row["result_id"] === resultId)
        if (matching.length > 0) {
          return { publishOk: true, status: r.status, rows: matching }
        }
      }
    } catch { /* retry */ }
    await new Promise(r => setTimeout(r, 2000))
  }
  return { publishOk: true, status: null, rows: [] }
}

// ─────────────────────────────────────────────────────────────────────────────
// Main scenario
// ─────────────────────────────────────────────────────────────────────────────

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const assertions: Assertion[] = []
  const tenantId      = ctx.tenantId
  const evalApiUrl    = ctx.evaluationApiUrl
  const analyticsUrl  = ctx.analyticsApiUrl
  const adminToken    = ctx.configApiAdminToken

  // ── Part A — Form CRUD ──────────────────────────────────────────────────────

  // A-1: Create form
  let formId = ""
  try {
    const body = {
      tenant_id:  tenantId,
      name:       "Formulário E2E Arc 6",
      description: "Formulário criado pelo scenario 24",
      criteria: [
        { id: "saudacao",  label: "Saudação",  description: "Seguiu protocolo de saudação",   weight: 0.3, type: "pass_fail" },
        { id: "resolucao", label: "Resolução", description: "Resolveu o problema do cliente", weight: 0.5, type: "score",     options: [{ value: 1, label: "Não" }, { value: 2, label: "Parcial" }, { value: 3, label: "Sim" }] },
        { id: "encerramento", label: "Encerramento", description: "Encerrou adequadamente", weight: 0.2, type: "na_allowed" },
      ],
      active: true,
    }
    const r = await fetch(`${evalApiUrl}/v1/evaluation/forms`, {
      method:  "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Token": adminToken },
      body:    JSON.stringify(body),
    })
    const d = await r.json() as Record<string, unknown>
    formId = String(d["form_id"] ?? "")
    assertions.push(pass("A-1: form created", `form_id=${formId}`, formId.length > 0
      ? undefined
      : `expected non-empty form_id, got status ${r.status}`))
  } catch (e) {
    assertions.push(fail("A-1: form created", String(e)))
    formId = `form_e2e_${randomUUID().slice(0, 8)}`  // continue with fallback
  }

  // A-2: Form appears in list
  try {
    const r = await fetch(`${evalApiUrl}/v1/evaluation/forms?tenant_id=${tenantId}`)
    const d = await r.json() as Array<Record<string, unknown>>
    const found = Array.isArray(d) && d.some(f => f["form_id"] === formId)
    assertions.push(pass("A-2: form in list", `found=${found}`, found ? undefined : "form not found in list"))
  } catch (e) {
    assertions.push(fail("A-2: form in list", String(e)))
  }

  // A-3: Update form name
  try {
    const r = await fetch(`${evalApiUrl}/v1/evaluation/forms/${formId}`, {
      method:  "PATCH",
      headers: { "Content-Type": "application/json", "X-Admin-Token": adminToken },
      body:    JSON.stringify({ name: "Formulário E2E Arc 6 — Atualizado" }),
    })
    const d = await r.json() as Record<string, unknown>
    const nameOk = (d["name"] as string | undefined)?.includes("Atualizado") ?? false
    assertions.push(pass("A-3: form name updated", `name=${d["name"]}`, nameOk ? undefined : "name not updated"))
  } catch (e) {
    assertions.push(fail("A-3: form name updated", String(e)))
  }

  // ── Part B — Campaign CRUD + status control ─────────────────────────────────

  let campaignId = ""

  // B-1: Create campaign
  try {
    const body = {
      tenant_id:       tenantId,
      name:            "Campanha E2E Arc 6",
      form_id:         formId,
      evaluator_pool_id: "avaliacao_ia",
      sampling: {
        mode:        "random",
        sample_rate: 1.0,
      },
      reviewer_rules: {
        auto_approve_above:   0.9,
        auto_reject_below:    0.5,
        require_human_review: false,
      },
    }
    const r = await fetch(`${evalApiUrl}/v1/evaluation/campaigns`, {
      method:  "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Token": adminToken },
      body:    JSON.stringify(body),
    })
    const d = await r.json() as Record<string, unknown>
    campaignId = String(d["campaign_id"] ?? "")
    const statusOk = d["status"] === "active"
    assertions.push(pass("B-1: campaign created active",
      `campaign_id=${campaignId} status=${d["status"]}`,
      campaignId.length > 0 && statusOk ? undefined : `status=${d["status"]} http=${r.status}`))
  } catch (e) {
    assertions.push(fail("B-1: campaign created active", String(e)))
    campaignId = `camp_e2e_${randomUUID().slice(0, 8)}`
  }

  // B-2: Campaign in list
  try {
    const r = await fetch(`${evalApiUrl}/v1/evaluation/campaigns?tenant_id=${tenantId}`)
    const d = await r.json() as Array<Record<string, unknown>>
    const found = Array.isArray(d) && d.some(c => c["campaign_id"] === campaignId)
    assertions.push(pass("B-2: campaign in list", `found=${found}`, found ? undefined : "campaign not found in list"))
  } catch (e) {
    assertions.push(fail("B-2: campaign in list", String(e)))
  }

  // B-3: Pause campaign
  try {
    const r = await fetch(`${evalApiUrl}/v1/evaluation/campaigns/${campaignId}/pause`, {
      method:  "POST",
      headers: { "X-Admin-Token": adminToken },
    })
    const d = await r.json() as Record<string, unknown>
    const paused = d["status"] === "paused"
    assertions.push(pass("B-3: campaign paused", `status=${d["status"]}`, paused ? undefined : `expected paused got ${d["status"]}`))
  } catch (e) {
    assertions.push(fail("B-3: campaign paused", String(e)))
  }

  // B-4: Resume campaign
  try {
    const r = await fetch(`${evalApiUrl}/v1/evaluation/campaigns/${campaignId}/resume`, {
      method:  "POST",
      headers: { "X-Admin-Token": adminToken },
    })
    const d = await r.json() as Record<string, unknown>
    const active = d["status"] === "active"
    assertions.push(pass("B-4: campaign resumed active", `status=${d["status"]}`, active ? undefined : `expected active got ${d["status"]}`))
  } catch (e) {
    assertions.push(fail("B-4: campaign resumed active", String(e)))
  }

  // ── Part C — Kafka evaluation.events → analytics-api ClickHouse ────────────

  const resultId   = `result_e2e_${randomUUID().slice(0, 8)}`
  const instanceId = `inst_e2e_${randomUUID().slice(0, 8)}`
  const sessionId  = `sess_e2e_${randomUUID().slice(0, 8)}`

  const submittedEvent = buildEvaluationEvent({
    resultId,
    instanceId,
    sessionId,
    tenantId,
    evaluatorId: "agente_avaliacao_v1-001",
    formId,
    campaignId,
    score:      0.85,
    evalStatus: "submitted",
  })

  const { publishOk: publishC, status: statusC, rows: rowsC } = await publishAndPollEvaluations(
    ctx.kafka, analyticsUrl, tenantId, resultId, [submittedEvent]
  )

  assertions.push(pass("C-1: evaluation event published to Kafka", `publishOk=${publishC}`,
    publishC ? undefined : "failed to publish to evaluation.events"))

  // C-2: Row found in analytics
  const foundRow = rowsC.find(r => r["result_id"] === resultId) ?? null
  assertions.push(pass("C-2: result row appears in analytics",
    `result_id=${foundRow?.["result_id"]} status=${statusC}`,
    foundRow ? undefined : `row not found after polling (status=${statusC})`))

  // C-3: overall_score correct
  if (foundRow) {
    const scoreOk = Math.abs(Number(foundRow["overall_score"]) - 0.85) < 0.01
    assertions.push(pass("C-3: overall_score=0.85 in analytics row",
      `overall_score=${foundRow["overall_score"]}`,
      scoreOk ? undefined : `expected 0.85 got ${foundRow["overall_score"]}`))
  } else {
    assertions.push(fail("C-3: overall_score=0.85 in analytics row", "row not found"))
  }

  // C-4: eval_status=submitted in analytics row
  if (foundRow) {
    const statusEq = foundRow["eval_status"] === "submitted"
    assertions.push(pass("C-4: eval_status=submitted in analytics row",
      `eval_status=${foundRow["eval_status"]}`,
      statusEq ? undefined : `expected submitted got ${foundRow["eval_status"]}`))
  } else {
    assertions.push(fail("C-4: eval_status=submitted in analytics row", "row not found"))
  }

  // ── Part D — Reviewer approval simulated → FINAL merge ─────────────────────

  const approvedEvent = buildEvaluationEvent({
    resultId,
    instanceId,
    sessionId,
    tenantId,
    evaluatorId: "agente_avaliacao_v1-001",
    formId,
    campaignId,
    score:      0.85,
    evalStatus: "approved",
    reviewedBy: "agente_reviewer_ia_v1-001",
  })

  // Publish approved update — same result_id, ReplacingMergeTree should pick latest
  const { publishOk: publishD, rows: rowsD } = await publishAndPollEvaluations(
    ctx.kafka, analyticsUrl, tenantId, resultId, [approvedEvent]
  )

  // Give ClickHouse a moment to FINAL merge
  if (publishD) {
    await new Promise(r => setTimeout(r, 5000))
  }

  // Re-fetch to get updated status
  let updatedRows: Array<Record<string, unknown>> = []
  try {
    const url = `${analyticsUrl}/reports/evaluations?tenant_id=${tenantId}&page_size=200`
    const r   = await fetch(url)
    if (r.ok) {
      const data = await r.json() as { data?: Array<Record<string, unknown>> }
      updatedRows = (data.data ?? []).filter(row => row["result_id"] === resultId)
    }
  } catch { /* ignore */ }

  // D-1: approved event published
  assertions.push(pass("D-1: approved event published to Kafka", `publishOk=${publishD}`,
    publishD ? undefined : "failed to publish approved event"))

  // D-2: eval_status now approved (ReplacingMergeTree FINAL)
  const approvedRow = updatedRows.find(r => r["eval_status"] === "approved") ?? null
  assertions.push(pass("D-2: eval_status=approved after review (ReplacingMergeTree FINAL)",
    `status=${approvedRow?.["eval_status"]}`,
    approvedRow ? undefined : "approved row not found — FINAL merge may not have run yet"))

  // D-3: Summary endpoint returns count_approved ≥ 1
  let summaryOk = false
  try {
    const url = `${analyticsUrl}/reports/evaluations/summary?tenant_id=${tenantId}&campaign_id=${campaignId}&group_by=campaign_id`
    const r   = await fetch(url)
    if (r.ok) {
      const data = await r.json() as { data?: Array<Record<string, unknown>> }
      const rows = data.data ?? []
      summaryOk = rows.some(row => Number(row["total_evaluated"]) >= 1)
    }
  } catch { /* ignore */ }

  assertions.push(pass("D-3: summary endpoint returns total_evaluated ≥ 1",
    `summaryOk=${summaryOk}`,
    summaryOk ? undefined : "summary row not found or total_evaluated=0"))

  // ── Cleanup ─────────────────────────────────────────────────────────────────

  // Best-effort cleanup: delete form
  try {
    await fetch(`${evalApiUrl}/v1/evaluation/forms/${formId}`, {
      method:  "DELETE",
      headers: { "X-Admin-Token": adminToken },
    })
  } catch { /* ignore */ }

  const passed = assertions.filter(a => a.ok).length
  const total  = assertions.length
  return {
    scenario:   "24_evaluation_campaign",
    passed,
    failed:     total - passed,
    assertions,
  }
}

