/**
 * types.ts
 * Shared types for E2E scenario functions.
 */

import type { Redis } from "ioredis";
import type { Kafka } from "kafkajs";
import type { Assertion, ScenarioResult } from "../lib/report";

export type { Assertion, ScenarioResult };

export interface ScenarioContext {
  mcpServerUrl: string;
  agentRegistryUrl: string;
  skillFlowUrl: string;
  rulesEngineUrl: string;
  aiGatewayUrl: string;
  /** WebSocket base URL for the Channel Gateway (e.g. ws://localhost:8010) */
  channelGatewayWsUrl: string;
  /** HTTP base URL for the Channel Gateway (e.g. http://localhost:8010) */
  channelGatewayHttpUrl: string;
  redis: Redis;
  kafka: Kafka;
  tenantId: string;
  jwtSecret: string;
  /** HS256 secret used by Channel Gateway to validate webchat JWTs */
  webchatJwtSecret: string;
}

export type ScenarioFn = (ctx: ScenarioContext) => Promise<ScenarioResult>;
