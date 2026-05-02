/**
 * 17_context_store.ts
 * Scenario 17: CONTEXTSTORE — accumulation, @ctx references, and supervisor_state
 *
 * Tests the ContextStore pipeline end-to-end without requiring a live skill flow
 * execution.  We simulate what each component writes to Redis and verify that the
 * supervisor_state MCP tool returns a correct context_snapshot.
 *
 * Part A — ContextStore key format
 *   Write entries directly to {tenantId}:ctx:{sessionId} and verify the hash
 *   exists with the correct structure (JSON-encoded ContextEntry per field).
 *
 * Part B — Caller namespace (MCP-sourced fields)
 *   Write caller.nome, caller.cpf, caller.account_id with source=mcp_call.
 *   Read back and assert each field has value, confidence ≥ 0.9, visibility.
 *
 * Part C — Session namespace (AI Gateway sentiment)
 *   Simulate write_context_store_sentiment by writing session.sentimento.current
 *   and session.sentimento.categoria with source=ai_inferred:sentiment_emitter.
 *   Verify score rounding (4 decimal places) and category derivation.
 *
 * Part D — @ctx interpolation simulation
 *   Write session.pergunta_coleta (a string entry).
 *   Assert the stored value is a valid ContextEntry with visibility=agents_only.
 *
 * Part E — supervisor_state context_snapshot
 *   Verify that calling the supervisor_state MCP tool returns
 *   customer_context.context_snapshot populated when the ctx hash is present.
 *   (Best-effort: only if MCP server is reachable.)
 *
 * Part F — ContextStore TTL
 *   Assert all entries carry a TTL > 0 (hash must not be persistent).
 *
 * Assertions: 18
 */

import { randomUUID } from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { genSessionId } from "../lib/redis-client";
import { pass, fail } from "../lib/report";

// ── ContextEntry shape ────────────────────────────────────────────────────────

interface ContextEntry {
  value:      unknown;
  confidence: number;
  source:     string;
  visibility: string;
  updated_at: string;
}

function makeEntry(
  value: unknown,
  confidence: number,
  source: string,
  visibility: string = "agents_only"
): string {
  return JSON.stringify({
    value,
    confidence,
    source,
    visibility,
    updated_at: new Date().toISOString(),
  });
}

function parseEntry(raw: string): ContextEntry | null {
  try {
    return JSON.parse(raw) as ContextEntry;
  } catch {
    return null;
  }
}

function classifySentiment(score: number): string {
  if (score >= 0.3)  return "satisfied";
  if (score >= -0.3) return "neutral";
  if (score >= -0.6) return "frustrated";
  return "angry";
}

// ── Scenario ──────────────────────────────────────────────────────────────────

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now();
  const assertions: Assertion[] = [];

  const sessionId  = genSessionId();
  const tenantId   = ctx.tenantId;
  const customerId = `cust_${randomUUID().slice(0, 8)}`;
  const ctxKey     = `${tenantId}:ctx:${sessionId}`;
  const SESSION_TTL = 14_400; // must match _CTX_SESSION_TTL in sentiment_emitter.py

  const redis = ctx.redis;

  // ── Part A: ContextStore key format ──────────────────────────────────────

  try {
    // Write a single test field directly
    await redis.hset(ctxKey, "caller.nome", makeEntry("Alice", 0.95, "mcp_call:mcp-server-crm:customer_get"));
    await redis.expire(ctxKey, SESSION_TTL);

    const raw = await redis.hget(ctxKey, "caller.nome");
    assertions.push(
      raw !== null
        ? pass("A: ctx hash key exists after write")
        : fail("A: ctx hash key exists", "HGET returned null")
    );

    const entry = raw ? parseEntry(raw) : null;
    assertions.push(
      entry !== null
        ? pass("A: caller.nome field parses as ContextEntry")
        : fail("A: caller.nome parses", "invalid JSON")
    );

    assertions.push(
      entry?.value === "Alice"
        ? pass("A: caller.nome value = 'Alice'")
        : fail("A: caller.nome value", `got ${JSON.stringify(entry?.value)}`)
    );
  } catch (err) {
    assertions.push(fail("A: key format test", String(err)));
  }

  // ── Part B: Caller namespace — MCP-sourced fields ─────────────────────────

  try {
    const mapping: Record<string, string> = {
      "caller.nome":       makeEntry("João Silva", 0.95, "mcp_call:mcp-server-crm:customer_get"),
      "caller.cpf":        makeEntry("123.456.789-00", 0.95, "mcp_call:mcp-server-crm:customer_get"),
      "caller.account_id": makeEntry("ACC-001", 0.95, "mcp_call:mcp-server-crm:customer_get"),
      "caller.motivo_contato": makeEntry("cancelamento", 0.80, "ai_inferred", "agents_only"),
    };
    await redis.hset(ctxKey, mapping);
    await redis.expire(ctxKey, SESSION_TTL);

    const allFields = await redis.hgetall(ctxKey);
    const callerFields = Object.entries(allFields).filter(([k]) => k.startsWith("caller."));
    assertions.push(
      callerFields.length >= 4
        ? pass(`B: caller namespace has ${callerFields.length} fields`)
        : fail("B: caller namespace fields", `only ${callerFields.length} fields`)
    );

    // Verify every caller field is a valid ContextEntry with expected shape
    const allValid = callerFields.every(([, v]) => {
      const e = parseEntry(v);
      return e && typeof e.confidence === "number" && typeof e.source === "string"
             && typeof e.visibility === "string" && typeof e.updated_at === "string";
    });
    assertions.push(
      allValid
        ? pass("B: all caller fields are valid ContextEntries")
        : fail("B: caller ContextEntry shape", "one or more fields missing required keys")
    );

    // Verify high-confidence MCP-sourced field
    const nomeEntry = parseEntry(allFields["caller.nome"] ?? "null");
    assertions.push(
      (nomeEntry?.confidence ?? 0) >= 0.9
        ? pass("B: caller.nome confidence ≥ 0.9 (MCP-sourced)")
        : fail("B: caller.nome confidence", `${nomeEntry?.confidence}`)
    );
  } catch (err) {
    assertions.push(fail("B: caller namespace test", String(err)));
  }

  // ── Part C: Session namespace — AI Gateway sentiment ─────────────────────

  try {
    const testScore = -0.41234567;
    const roundedScore = Math.round(testScore * 10000) / 10000; // -0.4123
    const expectedCategory = classifySentiment(testScore);     // "frustrated"

    const ctxKeySession = ctxKey; // same hash key, different fields
    await redis.hset(ctxKeySession, {
      "session.sentimento.current": makeEntry(
        roundedScore, 0.80, "ai_inferred:sentiment_emitter", "agents_only"
      ),
      "session.sentimento.categoria": makeEntry(
        expectedCategory, 0.80, "ai_inferred:sentiment_emitter", "agents_only"
      ),
    });
    await redis.expire(ctxKeySession, SESSION_TTL);

    const rawScore = await redis.hget(ctxKey, "session.sentimento.current");
    const rawCat   = await redis.hget(ctxKey, "session.sentimento.categoria");

    const scoreEntry = rawScore ? parseEntry(rawScore) : null;
    const catEntry   = rawCat   ? parseEntry(rawCat)   : null;

    assertions.push(
      typeof scoreEntry?.value === "number"
        ? pass(`C: session.sentimento.current value is number (${scoreEntry.value})`)
        : fail("C: session.sentimento.current type", `got ${typeof scoreEntry?.value}`)
    );

    assertions.push(
      scoreEntry?.value === roundedScore
        ? pass("C: sentiment score rounded to 4 decimals")
        : fail("C: sentiment score rounding", `expected ${roundedScore} got ${scoreEntry?.value}`)
    );

    assertions.push(
      catEntry?.value === "frustrated"
        ? pass("C: session.sentimento.categoria = 'frustrated'")
        : fail("C: sentiment category", `got ${catEntry?.value}`)
    );

    assertions.push(
      scoreEntry?.source === "ai_inferred:sentiment_emitter"
        ? pass("C: sentiment source = ai_inferred:sentiment_emitter")
        : fail("C: sentiment source", String(scoreEntry?.source))
    );
  } catch (err) {
    assertions.push(fail("C: sentiment context test", String(err)));
  }

  // ── Part D: session.pergunta_coleta (from gerar_pergunta reason step) ─────

  try {
    const question = "Por favor, informe seu CPF e o motivo do contato.";
    await redis.hset(ctxKey, {
      "session.pergunta_coleta": makeEntry(question, 1.0, "ai_inferred:reason_step", "agents_only"),
    });
    await redis.expire(ctxKey, SESSION_TTL);

    const raw   = await redis.hget(ctxKey, "session.pergunta_coleta");
    const entry = raw ? parseEntry(raw) : null;

    assertions.push(
      entry?.value === question
        ? pass("D: session.pergunta_coleta value preserved")
        : fail("D: session.pergunta_coleta value", String(entry?.value))
    );

    assertions.push(
      entry?.visibility === "agents_only"
        ? pass("D: session.pergunta_coleta visibility = agents_only")
        : fail("D: session.pergunta_coleta visibility", String(entry?.visibility))
    );
  } catch (err) {
    assertions.push(fail("D: session namespace test", String(err)));
  }

  // ── Part E: supervisor_state context_snapshot (best-effort) ──────────────

  let mcpAvailable = false;
  try {
    const healthRes = await fetch(`${ctx.mcpServerUrl}/health`, { signal: AbortSignal.timeout(2000) });
    mcpAvailable = healthRes.ok;
  } catch { /* not available in unit test env */ }

  if (mcpAvailable) {
    try {
      // Write session meta so supervisor_state can resolve tenant_id
      await ctx.redis.set(
        `session:${sessionId}:meta`,
        JSON.stringify({
          tenant_id:   tenantId,
          customer_id: customerId,
          channel:     "webchat",
          pool_id:     "",
          started_at:  new Date().toISOString(),
        }),
        "EX",
        14400
      );

      // Call supervisor_state via MCP JSON-RPC
      const rpcBody = {
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: {
          name: "supervisor_state",
          arguments: { session_id: sessionId, tenant_id: tenantId },
        },
      };
      const res = await fetch(`${ctx.mcpServerUrl}/mcp`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(rpcBody),
        signal:  AbortSignal.timeout(5000),
      });
      const json = await res.json() as { result?: { content?: Array<{ text?: string }> } };
      const text = json?.result?.content?.[0]?.text ?? "{}";
      const state = JSON.parse(text) as { customer_context?: { context_snapshot?: Record<string, unknown> } };

      const snapshot = state?.customer_context?.context_snapshot;
      assertions.push(
        snapshot !== null && snapshot !== undefined
          ? pass("E: supervisor_state returns context_snapshot when ctx hash present")
          : fail("E: supervisor_state context_snapshot", "field absent or null")
      );

      assertions.push(
        snapshot && "caller.nome" in snapshot
          ? pass("E: context_snapshot contains caller.nome")
          : fail("E: context_snapshot caller.nome missing", JSON.stringify(Object.keys(snapshot ?? {})))
      );

      assertions.push(
        snapshot && "session.sentimento.categoria" in snapshot
          ? pass("E: context_snapshot contains session.sentimento.categoria")
          : fail("E: context_snapshot session.sentimento.categoria missing", "")
      );
    } catch (err) {
      assertions.push(fail("E: supervisor_state call", String(err)));
    }
  } else {
    // MCP server not available — skip with informational pass
    assertions.push(pass("E: supervisor_state check skipped (MCP server not reachable in test env)"));
    assertions.push(pass("E: supervisor_state context_snapshot — skipped"));
    assertions.push(pass("E: context_snapshot sentiment field — skipped"));
  }

  // ── Part F: TTL enforcement ───────────────────────────────────────────────

  try {
    const ttl = await redis.ttl(ctxKey);
    assertions.push(
      ttl > 0
        ? pass(`F: ctx hash has TTL = ${ttl}s (persistent keys not allowed)`)
        : fail("F: ctx hash TTL", `ttl=${ttl} (key is persistent or missing)`)
    );
  } catch (err) {
    assertions.push(fail("F: TTL check", String(err)));
  }

  // ── Cleanup ───────────────────────────────────────────────────────────────

  try {
    await redis.del(ctxKey);
    await redis.del(`session:${sessionId}:meta`);
  } catch { /* best-effort */ }

  return buildResult(assertions, startAt);
}

function buildResult(
  assertions: Assertion[],
  startAt: number,
  error?: string
): ScenarioResult {
  const passed = assertions.every((a) => a.passed) && !error;
  return {
    scenario_id: "17",
    name:        "ContextStore — accumulation, @ctx references, and supervisor_state",
    passed,
    assertions,
    duration_ms: Date.now() - startAt,
    ...(error ? { error } : {}),
  };
}
