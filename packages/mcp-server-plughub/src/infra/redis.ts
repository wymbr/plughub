/**
 * infra/redis.ts
 * Factory do cliente Redis e helpers de chaves para o mcp-server-plughub.
 * Todas as chaves são prefixadas com {tenant_id}: conforme spec 14 (multi-tenant).
 */

import Redis from "ioredis"

export type RedisClient = Redis

export function createRedisClient(): RedisClient {
  const url = process.env["REDIS_URL"] ?? "redis://localhost:6379"
  return new Redis(url, { lazyConnect: true })
}

// ─── Key helpers (tenant-prefixed) ────────────────────────────────────────────

export const keys = {
  /** Estado e metadata da instância do agente */
  agentInstance: (tenantId: string, instanceId: string) =>
    `${tenantId}:agent:instance:${instanceId}`,

  /** session_token → instance_id (TTL = expiração do JWT) */
  agentToken: (tenantId: string, sessionToken: string) =>
    `${tenantId}:agent:token:${sessionToken}`,

  // REMOVIDO 2026-08-05 — `poolAvailable` (`{t}:pool:{p}:available`).
  //
  // Era um SET com UM escritor (o `sadd` do `agent_ready`), quatro `srem` e
  // NENHUM leitor fora de teste em todo o repo — zero ocorrências de
  // `:available` no routing-engine (`.py`). O pertencimento que a produção lê é
  // `poolInstances` (abaixo) + `pool_roster:{p}`.
  //
  // Medido antes de remover (`infra/test/probe_pool_registration.sh`, tenant_demo):
  //   · 37 chaves `:pool:*:instances` vivas   ·  0 chaves `:pool:*:available`
  //   · 2471 SADD em `:instances` numa janela de 150 s, TODOS de um único
  //     cliente — 172.20.0.35 = orchestrator-bridge (routing-engine e mcp-server
  //     mudos na janela). Quem inscreve instância de IA em pool é o
  //     `instance_bootstrap`, a partir do slot de deploy.
  //   · 0 SADD e 0 comandos de QUALQUER tipo sobre `:available`.
  // O laço que alimentava o `sadd` era vazio por construção: ele itera o campo
  // `pools` do hash da instância, escrito pelo `agent_login` a partir do cliente
  // HTTP que devolve `pools: []` sem ramo algum (`registry-client.ts` §71),
  // porque config por-agente migrou para o slot de deploy do pool.

  /** SET de conversation_ids ativos para uma instância */
  agentConversations: (tenantId: string, instanceId: string) =>
    `${tenantId}:agent:instance:${instanceId}:conversations`,

  /** Insight de sessão: chave → JSON da SessionItem */
  insight: (tenantId: string, conversationId: string, itemId: string) =>
    `${tenantId}:insight:${conversationId}:${itemId}`,

  /** Snapshot operacional de pool — escrito pelo Routing Engine após cada roteamento */
  poolQueueSnapshot: (tenantId: string, poolId: string) =>
    `${tenantId}:pool:${poolId}:snapshot`,

  /** ZSET de sessões na fila de um pool (score = queued_at_ms) */
  poolQueue: (tenantId: string, poolId: string) =>
    `${tenantId}:pool:${poolId}:queue`,

  /** JSON do contato enfileirado (escrito pelo Routing Engine) */
  queueContact: (tenantId: string, sessionId: string) =>
    `${tenantId}:queue_contact:${sessionId}`,

  /** P3 — epoch ms do PRIMEIRO enqueue (NX+TTL, escrito pelo Routing Engine);
   * preserva a espera real do contato através de re-enfileiramentos. */
  firstQueued: (tenantId: string, sessionId: string) =>
    `${tenantId}:queue:first_queued:${sessionId}`,

  /** I5 — ledger do item de trabalho parqueado por um `delegate` (escrito pelo
   * channel-gateway no despacho): { pool_id, queue_session_id, resume_token,
   * step_id, assigned_to, deadline }. É onde o gatilho de supervisor acha o
   * resume_token a partir do session_id. */
  workTask: (tenantId: string, sessionId: string) =>
    `${tenantId}:work_task:${sessionId}`,

  /** Frente 1 (pull): lease do claim — {instance_id, claimed_at}. TTL curto
   *  (`routing.claim_lease_s`, default 180 s), MUITO menor que o prazo do item. */
  claimLease: (tenantId: string, poolId: string, sessionId: string) =>
    `${tenantId}:pool:${poolId}:claim:${sessionId}`,

  /** Fase A / D6 — registro DURÁVEL de posse do item de trabalho:
   *  {instance_id, claimant_user_id, claimed_at}, TTL casado ao prazo do item.
   *  Escrito pelo Routing Engine no claim, apagado em release/expire/re-parque.
   *  Ler só a `claimLease` faz um item reivindicado há mais de 180 s parecer
   *  sem dono — é a leitura que a Fase A corrigiu. */
  claimRecord: (tenantId: string, poolId: string, sessionId: string) =>
    `${tenantId}:pool:${poolId}:claim_record:${sessionId}`,

  /** SET de instance_ids disponíveis (prontos) num pool.
   *  ATENÇÃO: é PERTENCIMENTO, não capacidade. `SCARD` disto conta instância lotada
   *  como disponível e ignora a vaga que o recurso gastou em pool irmão — foi o
   *  fallback errado de `pool_status_get` até a F5b. Capacidade vem do snapshot do
   *  pool (por pool) ou de `tenantCapacity` (agregada, deduplicada). */
  poolInstances: (tenantId: string, poolId: string) =>
    `${tenantId}:pool:${poolId}:instances`,

  /** Rollup de capacidade do tenant por TIPO DE LICENÇA — escrito pelo Routing Engine
   *  (F4). Única fonte que agrega sobre instâncias DISTINTAS: somar `available` das
   *  linhas de pool conta o mesmo recurso uma vez por pool. */
  tenantCapacity: (tenantId: string) => `${tenantId}:capacity:snapshot`,
}

// ─── Estado canônico da instância ──────────────────────────────────────────────

export type AgentInstanceState =
  | "logged_in"
  | "ready"
  | "busy"
  | "paused"
  | "draining"
  | "logged_out"
