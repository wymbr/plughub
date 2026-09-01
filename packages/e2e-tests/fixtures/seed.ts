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
  // (comparison mode), que fazem agent_login como avaliador. O `agent_login`
  // resolve a identidade via GET /v1/skills/{id}; sem esta fixture ele devolve
  // `agent_type_not_found` e os dois cenários abortam.
  //
  // ⚠️ Não declara mais `agent_role`: o eixo saiu em 2026-09-01 (CAP-03) e o PUT
  // o recusa com 422. O que o skill precisa continuar sendo é o que o token
  // carrega — é dele que sai a procedência do resultado (CAP-02).
  await registry.createSkill({
    skill_id: "skill_avaliacao_v1",
    name: "Avaliação de Qualidade",
    version: "1.0",
    description: "Skill de avaliação de qualidade pós-sessão",
    classification: {
      type: "horizontal",
      domain: "quality",
    },
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

  // NÃO existe bloco de AgentType aqui — e a ausência é a fixture.
  //
  // A entidade AgentType foi aposentada (Fase 3d/C; ver `registry_syncer.py` §293).
  // `/v1/agent-types` não é uma rota gateada nem protegida: ela NÃO EXISTE no
  // agent-registry (`app.ts` §43-49 monta pools, skills, instances, channels,
  // channel-endpoints e operational). Os dois `createAgentType` que moravam aqui
  // tomavam `404 Cannot POST` e matavam o seed — e o seed roda antes de TODO
  // cenário que precise do registry (`runner.ts` §363), então a suíte inteira
  // parava neste ponto, não só o cenário que o log estivesse mostrando.
  //
  // No modelo deploy-driven a identidade de um agente É o `skill_id` deployado:
  // `agent_login` resolve por `GET /v1/skills/{id}` (`mcp-server/infra/registry-client.ts`
  // §41-47). Por isso as três skills acima são a fixture de identidade completa —
  // os cenários logam como `skill_retencao_oferta_v1` (executor) ou
  // `skill_avaliacao_v1` (evaluator), e não há mais nada a semear.
  //
  // Nota sobre o que NÃO se perdeu na remoção: o bloco morto declarava
  // `pools: ["retencao_humano"]` e `max_concurrent_sessions: 2`, mas nenhum dos
  // dois chegava a lugar algum — o cliente HTTP de produção devolve `pools: []` e
  // `max_concurrent_sessions: 1` FIXOS (registry-client.ts §69-72), porque config
  // por-agente passou a viver no slot de deploy do pool. Quem depender de o
  // routing ALOCAR uma instância logada por aqui está quebrado por essa razão,
  // que é outra e anterior a esta.

  console.log(`[seed] Base fixtures seeded for tenant ${config.tenantId}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Performance fixtures — used by Scenario 5 only
//
// ⚠️ DUAS AFIRMAÇÕES FALSAS NESTE BLOCO, deixadas visíveis de propósito (2026-08-05):
//
//   1. "used by Scenario 5" — NINGUÉM chama esta função. `runner.ts` §90 importa
//      apenas `seedBaseFixtures`. O cenário 05 monta `agent_perf_{i}_v1` a partir
//      de fixtures que nunca foram semeadas em execução alguma.
//   2. `createAgentType` abaixo aponta para `/v1/agent-types`, rota que não existe
//      (entidade aposentada) — se alguém passasse a chamar esta função, ela morreria
//      no primeiro `await`, igual ao seed base morria.
//
// Não foi removida junto com o bloco base porque remover código morto e migrar o
// cenário 05 são a mesma decisão, e ela não cabia na fatia que destravou a suíte.
// Registrada em TODO.md § "Fixtures do e2e ainda falam AgentType".
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
