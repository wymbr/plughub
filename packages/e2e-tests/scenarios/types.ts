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
  redis: Redis;
  kafka: Kafka;
  tenantId: string;
  jwtSecret: string;
}

export type ScenarioFn = (ctx: ScenarioContext) => Promise<ScenarioResult>;
