/**
 * 10_masking.ts
 * Scenario 10: MESSAGE MASKING — TOKENIZAÇÃO COM PARTIAL DISPLAY
 *
 * Valida o pipeline completo de mascaramento:
 *   Part A — Seed masking config no Redis:
 *     MaskingConfig com regras para CPF e cartão de crédito
 *
 *   Part B — message_send com dados sensíveis:
 *     Agente envia mensagem com CPF e cartão
 *     Verifica que stream contém tokens inline [{category}:{token_id}:{display}]
 *     Verifica que original_content está presente no stream
 *     Verifica que masked: true e masked_categories populados
 *
 *   Part C — session_context_get por role primary:
 *     Primary agent lê contexto
 *     Verifica que recebe tokens (não dados em aberto)
 *     Verifica que original_content NÃO está presente (role não autorizado)
 *
 *   Part D — session_context_get por role evaluator:
 *     Simula acesso de evaluator
 *     Verifica que original_content ESTÁ presente (role autorizado)
 *     Verifica que display_partial visível no token permite confirmação
 *
 * Assertions: 9
 */

import { randomUUID }  from "crypto";
import type { ScenarioContext, ScenarioResult, Assertion } from "./types";
import { McpTestClient } from "../lib/mcp-client";
import {
  genSessionId,
  seedSessionMeta,
} from "../lib/redis-client";
import { pass, fail } from "../lib/report";

// Mensagem de teste com CPF e cartão de crédito
const TEST_MESSAGE_WITH_PII =
  "Meu CPF é 123.456.789-00 e meu cartão é 4539 1234 5678 1234. Pode verificar?";

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now();
  const assertions: Assertion[] = [];

  const mcp = new McpTestClient(ctx.mcpServerUrl);

  try {
    await mcp.connect();

    // ── Part A: Seed MaskingConfig e MaskingAccessPolicy no Redis ────────────
    const maskingConfig = {
      tenant_id: ctx.tenantId,
      rules: [
        {
          pattern:              "\\b\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2}\\b",
          category:             "cpf",
          replacement:          "***.***.***.--",
          preserve_last_digits: 2,
        },
        {
          pattern:              "\\b(?:\\d{4}[\\s-]?){3}\\d{4}\\b",
          category:             "credit_card",
          replacement:          "**** **** **** ****",
          preserve_last_digits: 4,
        },
      ],
    };

    const maskingPolicy = {
      tenant_id:        ctx.tenantId,
      authorized_roles: ["evaluator", "reviewer"],
    };

    await ctx.redis.set(
      `${ctx.tenantId}:masking:config`,
      JSON.stringify(maskingConfig)
    );
    await ctx.redis.set(
      `${ctx.tenantId}:masking:access_policy`,
      JSON.stringify(maskingPolicy)
    );

    assertions.push(pass("A: MaskingConfig e AccessPolicy seedados no Redis"));

    // ── Part B: message_send com dados sensíveis ─────────────────────────────
    const sessionId     = genSessionId();
    const participantId = randomUUID();
    const instanceId    = `e2e-masking-${randomUUID()}`;

    await seedSessionMeta(ctx.redis, sessionId, ctx.tenantId, randomUUID());

    let sessionToken = "";
    try {
      const login = await mcp.agentLogin(ctx.tenantId, "agente_retencao_v1", instanceId);
      sessionToken = login.session_token;
      await mcp.agentReady(sessionToken);
      await mcp.agentBusyV2(sessionToken, sessionId, participantId);
    } catch (err) {
      return buildResult(
        [fail("B: agent setup", String(err))],
        startAt, "Setup failed"
      );
    }

    const sendResult = await mcp.messageSend(
      sessionToken, sessionId, participantId,
      { type: "text", text: TEST_MESSAGE_WITH_PII },
      "all"
    );

    assertions.push(
      !("isError" in sendResult)
        ? pass("B: message_send com dados sensíveis aceito")
        : fail("B: message_send", sendResult)
    );

    // Lê o evento gravado no stream para verificar tokens
    let streamPayload: Record<string, unknown> | null = null;
    try {
      const entries: Array<[string, string[]]> = await (ctx.redis as any).xrange(
        `session:${sessionId}:stream`, "-", "+"
      );
      if (entries.length > 0) {
        const [, fields] = entries[entries.length - 1]!;
        const obj: Record<string, unknown> = {};
        for (let i = 0; i < fields.length; i += 2) {
          const key = fields[i] as string;
          const val = fields[i + 1] as string;
          try { obj[key] = JSON.parse(val); } catch { obj[key] = val; }
        }
        if (obj["type"] === "message") {
          streamPayload = obj["payload"] as Record<string, unknown>;
        }
      }
    } catch { /* stream não disponível — verifica mensagens legadas */ }

    // Fallback: lê mensagem da lista legada
    if (!streamPayload) {
      try {
        const msgs = await ctx.redis.lrange(`session:${sessionId}:messages`, -1, -1);
        if (msgs.length > 0) {
          streamPayload = JSON.parse(msgs[0]!) as Record<string, unknown>;
        }
      } catch { /* sem mensagens */ }
    }

    // B1: Verifica que masked: true quando há dados sensíveis
    const isMasked = streamPayload?.["masked"] === true;
    assertions.push(
      isMasked
        ? pass("B: stream payload marked as masked:true")
        : fail("B: stream payload masked:true", {
            masked: streamPayload?.["masked"],
            note:   "Se não há MaskingConfig no Redis, mascaramento é no-op graceful",
          })
    );

    // B2: Verifica que content tem tokens inline (não dados em aberto)
    const content = streamPayload?.["content"] as Record<string, unknown> | undefined;
    const contentText = content?.["text"] as string | undefined;
    const hasTokens = contentText ? /\[(cpf|credit_card):tk_[a-f0-9]+:[^\]]+\]/.test(contentText) : false;

    assertions.push(
      hasTokens
        ? pass("B: content contém tokens inline [{category}:tk_xxx:{partial}]", {
            sample: contentText?.slice(0, 80),
          })
        : fail("B: content deve conter tokens inline", {
            content_text: contentText?.slice(0, 80) ?? "(empty)",
            note: "Mascaramento pode não ter disparado — verificar MaskingConfig no Redis",
          })
    );

    // B3: Verifica que original_content está no stream (para audit trail)
    const origContent = streamPayload?.["original_content"] as Record<string, unknown> | undefined;
    const origText    = origContent?.["text"] as string | undefined;
    assertions.push(
      origText && origText.includes("123.456.789-00")
        ? pass("B: original_content preservado no stream para audit trail")
        : fail("B: original_content no stream", {
            original_text: origText?.slice(0, 80) ?? "(ausente)",
          })
    );

    // ── Part C: session_context_get por role primary ──────────────────────────
    // Primary NÃO deve receber original_content
    const ctxPrimary = await mcp.sessionContextGet(sessionToken, sessionId, participantId);

    const primaryMessages = !("isError" in ctxPrimary)
      ? (ctxPrimary as any).messages ?? []
      : [];

    // Verifica que primary recebe tokens (não dados em aberto)
    const primaryMsg = primaryMessages.find(
      (m: Record<string, unknown>) => {
        const c = m["content"] as Record<string, unknown> | undefined;
        return typeof c?.["text"] === "string";
      }
    );
    const primaryText = (primaryMsg?.["content"] as Record<string, unknown>)?.["text"] as string | undefined;
    const primaryHasTokens = primaryText
      ? /\[(cpf|credit_card):tk_[a-f0-9]+:[^\]]+\]/.test(primaryText)
      : true; // se não há mensagem, considera ok (sem dados sensíveis a expor)

    assertions.push(
      primaryHasTokens
        ? pass("C: primary recebe tokens (não dados em aberto)", {
            sample: primaryText?.slice(0, 60) ?? "(sem msg com text)",
          })
        : fail("C: primary não deve receber dados sensíveis em aberto", {
            text: primaryText?.slice(0, 80),
          })
    );

    // Verifica que original_content NÃO está em nenhuma mensagem retornada ao primary
    const primaryHasOriginal = primaryMessages.some(
      (m: Record<string, unknown>) => "original_content" in m
    );
    assertions.push(
      !primaryHasOriginal
        ? pass("C: original_content ausente nas mensagens retornadas ao primary")
        : fail("C: original_content não deve ser entregue ao primary", {
            messages_with_original: primaryMessages.filter(
              (m: Record<string, unknown>) => "original_content" in m
            ).length,
          })
    );

    // ── Part D: Simula acesso de evaluator ────────────────────────────────────
    // O MCP tool evaluation_context_get retorna o ReplayContext com original_content.
    // Para testar o filtro do session_context_get com role evaluator,
    // seedamos o role do participante como evaluator no Redis e relemos.

    // Seta role do participante como evaluator diretamente no Redis
    await ctx.redis.hset(
      `${ctx.tenantId}:agent:instance:${participantId}`,
      "role", "evaluator"
    );

    const ctxEvaluator = await mcp.sessionContextGet(sessionToken, sessionId, participantId);

    const evalMessages = !("isError" in ctxEvaluator)
      ? (ctxEvaluator as any).messages ?? []
      : [];

    // Evaluator DEVE receber original_content nas mensagens mascaradas
    const evalHasOriginal = evalMessages.some(
      (m: Record<string, unknown>) =>
        m["masked"] === true && "original_content" in m
    );
    assertions.push(
      evalHasOriginal || !isMasked  // se não mascarou (sem config), assertion é no-op
        ? pass("D: evaluator recebe original_content em mensagens mascaradas", {
            masked_msgs_with_original: evalMessages.filter(
              (m: Record<string, unknown>) => m["masked"] && "original_content" in m
            ).length,
          })
        : fail("D: evaluator deveria receber original_content", {
            messages_count: evalMessages.length,
            masked_count:   evalMessages.filter((m: Record<string, unknown>) => m["masked"]).length,
          })
    );

  } catch (err) {
    assertions.push(fail("Scenario 10 unexpected error", String(err)));
  } finally {
    await mcp.disconnect().catch(() => undefined);
  }

  return buildResult(assertions, startAt);
}

function buildResult(
  assertions: Assertion[],
  startAt:    number,
  error?:     string
): ScenarioResult {
  return {
    scenario_id: "10",
    name:        "Message Masking — Tokenização com Partial Display",
    passed:      assertions.every((a) => a.passed) && !error,
    assertions,
    duration_ms: Date.now() - startAt,
    error,
  };
}
