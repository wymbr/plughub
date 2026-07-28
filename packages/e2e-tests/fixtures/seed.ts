/**
 * seed.ts
 * Populates the agent-registry with base fixtures before each scenario.
 * Seed is idempotent — 409 Conflict responses are ignored.
 */

import { RegistryClient } from "../lib/http-client";

export interface SeedConfig {
  agentRegistryUrl: string;
  tenantId: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// Base fixtures — used by Scenarios 1–4
// ─────────────────────────────────────────────────────────────────────────────

export async function seedBaseFixtures(config: SeedConfig): Promise<void> {
  const registry = new RegistryClient(config.agentRegistryUrl, config.tenantId);

  // Skills
  await registry.createSkill({
    skill_id: "skill_retencao_oferta_v1",
    name: "Retenção com Oferta",
    version: "1.0",
    description: "Skill de retenção de clientes com oferta personalizada",
    classification: {
      type: "vertical",
      vertical: "telecom",
      domain: "retencao",
    },
    instruction: {
      prompt_id: "prompt_retencao_oferta_v1",
    },
    tools: [],
  });

  await registry.createSkill({
    skill_id: "skill_analise_credito_v1",
    name: "Análise de Crédito",
    version: "1.0",
    description: "Skill de análise e concessão de crédito",
    classification: {
      type: "vertical",
      vertical: "finserv",
      domain: "credito",
    },
    instruction: {
      prompt_id: "prompt_analise_credito_v1",
    },
    tools: [],
  });

  // Skill de AVALIAÇÃO — necessário aos cenários 09 (session replayer) e 11
  // (comparison mode), que fazem agent_login como avaliador para exercitar
  // `evaluation_context_get`. O gate dessa tool exige `agent_role: evaluator`
  // declarado no registry (auto-declaração no login não autoriza), e o
  // `agent_login` resolve a identidade via GET /v1/skills/{id} — sem esta
  // fixture o login devolve `agent_type_not_found` e os dois cenários abortam.
  await registry.createSkill({
    skill_id: "skill_avaliacao_v1",
    name: "Avaliação de Qualidade",
    version: "1.0",
    description: "Skill de avaliação de qualidade pós-sessão",
    classification: {
      type: "horizontal",
      domain: "quality",
    },
    agent_role: "evaluator",
    instruction: {
      prompt_id: "prompt_avaliacao_v1",
    },
    tools: [],
  });

  // Pools
  await registry.createPool({
    pool_id: "retencao_humano",
    description: "Pool de agentes humanos de retenção",
    channel_types: ["webchat", "whatsapp"],
    sla_target_ms: 300000,
    max_concurrent_sessions: 2,
  });

  await registry.createPool({
    pool_id: "especialista_onboarding",
    description: "Pool de especialistas de onboarding",
    channel_types: ["webchat"],
    sla_target_ms: 600000,
    max_concurrent_sessions: 1,
  });

  // Agent Types
  await registry.createAgentType({
    agent_type_id: "agente_retencao_v1",
    framework: "anthropic_sdk",
    execution_model: "stateless",
    role: "executor",
    max_concurrent_sessions: 2,
    skills: [{ skill_id: "skill_retencao_oferta_v1" }],
    pools: ["retencao_humano"],
    permissions: ["mcp-server-plughub:agent_heartbeat"],
  });

  await registry.createAgentType({
    agent_type_id: "agente_credito_v3",
    framework: "anthropic_sdk",
    execution_model: "stateless",
    role: "executor",
    max_concurrent_sessions: 1,
    skills: [{ skill_id: "skill_analise_credito_v1" }],
    pools: ["especialista_onboarding"],
    permissions: ["mcp-server-plughub:agent_heartbeat"],
  });

  console.log(`[seed] Base fixtures seeded for tenant ${config.tenantId}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Performance fixtures — used by Scenario 5 only
// ─────────────────────────────────────────────────────────────────────────────

export async function seedPerfFixtures(config: SeedConfig): Promise<void> {
  const registry = new RegistryClient(config.agentRegistryUrl, config.tenantId);

  // Seed the base skill first (reused by all perf agent types)
  await registry.createSkill({
    skill_id: "skill_perf_test_v1",
    name: "Performance Test Skill",
    version: "1.0",
    description: "Skill used by performance test agents",
    classification: { type: "horizontal" },
    instruction: { prompt_id: "prompt_perf_test_v1" },
    tools: [],
  });

  // 5 pools
  for (let i = 0; i < 5; i++) {
    await registry.createPool({
      pool_id: `pool_perf_${i}`,
      description: `Performance test pool ${i}`,
      channel_types: ["webchat"],
      sla_target_ms: 30000,
      max_concurrent_sessions: 10,
    });
  }

  // 50 agent types, spread across 5 pools (10 per pool)
  const createPromises: Promise<unknown>[] = [];
  for (let i = 0; i < 50; i++) {
    const poolIndex = i % 5;
    createPromises.push(
      registry.createAgentType({
        agent_type_id: `agent_perf_${i}_v1`,
        framework: "anthropic_sdk",
        execution_model: "stateless",
        role: "executor",
        max_concurrent_sessions: 5,
        skills: [{ skill_id: "skill_perf_test_v1" }],
        pools: [`pool_perf_${poolIndex}`],
        permissions: [],
      })
    );
  }
  await Promise.all(createPromises);

  console.log(`[seed] Performance fixtures seeded for tenant ${config.tenantId}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Cleanup — soft cleanup only (pools cannot be deleted, only deactivated)
// ─────────────────────────────────────────────────────────────────────────────

export async function cleanupFixtures(_config: SeedConfig): Promise<void> {
  // Agent Registry data is persistent across test runs.
  // Actual test isolation is achieved via Redis flush (flushTestData) before each scenario.
  // The seed functions are idempotent (ignore 409), so re-running seed on existing data is safe.
  console.log(`[seed] Cleanup: relying on Redis flush for test isolation (registry data is persistent).`);
}
