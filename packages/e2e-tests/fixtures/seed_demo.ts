/**
 * seed_demo.ts
 * Seed para demo do fluxo completo de conversa com cliente via LLM.
 *
 * Registra:
 *   - Pool suporte_humano (destino de escalação)
 *   - Skill skill_demo_chat_v1 (orchestrator — skill flow com loop de conversa)
 *   - AgentType orquestrador_demo_v1 (usa a skill acima)
 *
 * Idempotente: 409 Conflict ignorado.
 *
 * Uso:
 *   ts-node packages/e2e-tests/fixtures/seed_demo.ts
 *   ou importar em qualquer runner de cenário via:
 *     import { seedDemoFixtures } from "./seed_demo"
 */

import { RegistryClient } from "../lib/http-client"

export interface SeedDemoConfig {
  agentRegistryUrl: string
  tenantId: string
}

export async function seedDemoFixtures(config: SeedDemoConfig): Promise<void> {
  const registry = new RegistryClient(config.agentRegistryUrl, config.tenantId)

  // ── 1a. Pool IA do demo — entry point do orquestrador ───────────────────────
  //       framework: "plughub-native" → Bridge ativa via Skill Flow Service
  //       Canal webchat deve ter PLUGHUB_ENTRY_POINT_POOL_ID=demo_ia
  await registry.createPool({
    pool_id: "demo_ia",
    description: "Pool IA — entry point do fluxo de demo com LLM",
    channel_types: ["chat", "whatsapp"],
    sla_target_ms: 480_000,
    max_concurrent_sessions: 10,
  })

  // ── 1b. Pool humano — destino das escalações do orquestrador ────────────────
  //       framework: "human" → Bridge publica conversation.assigned para Agent Assist UI
  await registry.createPool({
    pool_id: "suporte_humano",
    description: "Pool de agentes humanos para suporte geral",
    channel_types: ["chat", "whatsapp"],
    sla_target_ms: 300_000,
    max_concurrent_sessions: 5,
  })

  // ── 2. Skill de orquestração — conversa com cliente via LLM ─────────────────
  //
  // Fluxo (seta = on_success salvo indicado):
  //
  //   saudacao (notify)
  //     → coleta_msg (menu/text, timeout 120s)
  //       on_success → analisar
  //       on_timeout / on_failure → encerrar_timeout
  //       on_disconnect → encerrar_disconnect
  //     → analisar (reason → {reply, intent, sentiment_score, deve_escalar, contexto})
  //       on_success → verificar_escalacao
  //       on_failure → responder_erro (notify retry)
  //     → verificar_escalacao (choice)
  //       ai.deve_escalar == true → avisar_escalacao → executar_escalacao (escalate)
  //       default → responder (notify "{{ai.reply}}")
  //     → proxima_pergunta (menu/button, timeout 60s)
  //       on_success → verificar_continuar
  //       timeout → encerrar_ok
  //     → verificar_continuar (choice)
  //       continuar == "sim" → coleta_msg   (loop)
  //       default → encerrar_ok
  //
  // O step `reason` → AI Gateway → popula session:{id}:ai com:
  //   sentiment_score (-1..1), intent, flags
  // O supervisor polling lê session:{id}:ai e exibe em tempo real no painel.

  await registry.createSkill({
    skill_id:    "skill_demo_chat_v1",
    name:        "Demo Chat IA",
    version:     "1.0",
    description: "Skill de demonstração — conversa com cliente via LLM com análise de sentimento em tempo real",
    classification: {
      type:     "orchestrator",
      vertical: "demo",
      domain:   "atendimento",
    },
    instruction: {
      prompt_id: "prompt_demo_assistente_v1",
      language:  "pt-BR",
    },
    tools:             [],
    knowledge_domains: ["atendimento", "demo"],
    flow: {
      entry: "saudacao",
      steps: [
        // ── Saudação inicial ────────────────────────────────────────────────
        {
          id:         "saudacao",
          type:       "notify",
          message:    "Olá! Sou o assistente virtual PlugHub. Como posso ajudar você hoje?",
          channel:    "session",
          on_success: "coleta_msg",
          on_failure: "encerrar_erro",
        },

        // ── Coletar mensagem do cliente ──────────────────────────────────────
        // Armazena reply em pipeline_state.msg (usado como input do reason step)
        {
          id:           "coleta_msg",
          type:         "menu",
          prompt:       "Por favor, descreva o que você precisa:",
          interaction:  "text",
          output_as:    "msg",
          timeout_s:    120,
          on_success:   "analisar",
          on_failure:   "encerrar_timeout",
          on_timeout:   "encerrar_timeout",
          on_disconnect: "encerrar_disconnect",
        },

        // ── Analisar via LLM ─────────────────────────────────────────────────
        // Gera reply, detecta intent/sentiment, decide escalação.
        // O AI Gateway actualiza session:{id}:ai após cada chamada, alimentando
        // o painel de sentimento do agente humano em tempo real.
        //
        // Output schema:
        //   reply           — resposta ao cliente
        //   intent          — intenção detectada (lida pelo supervisor)
        //   sentiment_score — sentimento -1..1 (lido pelo supervisor)
        //   deve_escalar    — true → escalar para suporte_humano
        //   contexto        — resumo acumulado para próxima iteração
        {
          id:        "analisar",
          type:      "reason",
          prompt_id: "prompt_demo_assistente_v1",
          input: {
            mensagem_cliente:  "$.pipeline_state.msg",
            contexto_anterior: "$.pipeline_state.ai.contexto",
          },
          output_schema: {
            reply: {
              type:        "string",
              required:    true,
            },
            intent: {
              type:        "string",
              required:    true,
            },
            sentiment_score: {
              type:        "number",
              minimum:     -1,
              maximum:     1,
              required:    true,
            },
            deve_escalar: {
              type:        "boolean",
              required:    true,
            },
            contexto: {
              type:        "string",
              required:    false,
            },
          },
          output_as:   "ai",
          on_success:  "verificar_escalacao",
          on_failure:  "responder_erro",
        },

        // ── Decidir: escalar ou responder ────────────────────────────────────
        {
          id:   "verificar_escalacao",
          type: "choice",
          conditions: [
            {
              field:    "$.pipeline_state.ai.deve_escalar",
              operator: "eq",
              value:    true,
              next:     "avisar_escalacao",
            },
          ],
          default: "responder",
        },

        // ── Enviar resposta da IA ao cliente ─────────────────────────────────
        // {{$.pipeline_state.ai.reply}} interpolado por executeNotify
        {
          id:         "responder",
          type:       "notify",
          message:    "{{$.pipeline_state.ai.reply}}",
          channel:    "session",
          on_success: "proxima_pergunta",
          on_failure: "encerrar_erro",
        },

        // ── Perguntar se há mais dúvidas (botões) ────────────────────────────
        {
          id:          "proxima_pergunta",
          type:        "menu",
          prompt:      "Posso te ajudar com mais alguma coisa?",
          interaction: "button",
          options: [
            { id: "sim", label: "Sim, tenho outra dúvida" },
            { id: "nao", label: "Não, obrigado!" },
          ],
          output_as:    "continuar",
          timeout_s:    60,
          on_success:   "verificar_continuar",
          on_failure:   "encerrar_timeout",
          on_timeout:   "encerrar_timeout",
          on_disconnect: "encerrar_disconnect",
        },

        // ── Avaliar resposta: loop ou encerrar ───────────────────────────────
        {
          id:   "verificar_continuar",
          type: "choice",
          conditions: [
            {
              field:    "$.pipeline_state.continuar",
              operator: "eq",
              value:    "sim",
              next:     "coleta_msg",
            },
          ],
          default: "encerrar_ok",
        },

        // ── Escalar para humano ──────────────────────────────────────────────
        {
          id:         "avisar_escalacao",
          type:       "notify",
          message:    "Entendo a sua necessidade. Vou te conectar com um especialista. Um momento!",
          channel:    "session",
          on_success: "executar_escalacao",
          on_failure: "encerrar_erro",
        },
        {
          id:           "executar_escalacao",
          type:         "escalate",
          target:       { pool: "suporte_humano" },
          context:      "pipeline_state",
          error_reason: "escalacao_solicitada_pelo_cliente",
        },

        // ── Retry após falha no reason step ─────────────────────────────────
        {
          id:         "responder_erro",
          type:       "notify",
          message:    "Desculpe, tive uma dificuldade ao processar sua mensagem. Pode tentar novamente?",
          channel:    "session",
          on_success: "coleta_msg",
          on_failure: "encerrar_erro",
        },

        // ── Terminais ────────────────────────────────────────────────────────
        { id: "encerrar_ok",         type: "complete", outcome: "resolved" },
        { id: "encerrar_timeout",    type: "complete", outcome: "resolved" },
        { id: "encerrar_disconnect", type: "complete", outcome: "resolved" },
        { id: "encerrar_erro",       type: "complete", outcome: "resolved" },
      ],
    },
  })

  // ── 3a. AgentType orquestrador_demo_v1 — IA (entry point: demo_ia) ──────────
  await registry.createAgentType({
    agent_type_id:           "orquestrador_demo_v1",
    framework:               "plughub-native",
    execution_model:         "stateless",
    role:                    "orchestrator",
    max_concurrent_sessions: 10,
    skills: [{ skill_id: "skill_demo_chat_v1" }],
    pools:  ["demo_ia"],
    permissions: [
      "mcp-server-plughub:agent_heartbeat",
      "mcp-server-plughub:notification_send",
      "mcp-server-plughub:conversation_escalate",
    ],
  })

  // ── 3b. AgentType agente_suporte_humano_v1 — humano (pool: suporte_humano) ──
  await registry.createAgentType({
    agent_type_id:           "agente_suporte_humano_v1",
    framework:               "human",
    execution_model:         "stateful",
    role:                    "executor",
    max_concurrent_sessions: 3,
    skills: [],
    pools:  ["suporte_humano"],
    permissions: [],
  })

  console.log(`[seed_demo] Demo fixtures seeded for tenant ${config.tenantId}`)
}

// ── CLI runner ────────────────────────────────────────────────────────────────
if (require.main === module) {
  const config: SeedDemoConfig = {
    agentRegistryUrl: process.env["AGENT_REGISTRY_URL"] ?? "http://localhost:3001",
    tenantId:         process.env["TENANT_ID"]          ?? "tenant_demo",
  }
  seedDemoFixtures(config)
    .then(() => process.exit(0))
    .catch((err) => { console.error(err); process.exit(1) })
}
