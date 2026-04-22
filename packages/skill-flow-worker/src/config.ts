/**
 * config.ts
 * Environment configuration for the Skill Flow Worker.
 */

export interface WorkerSettings {
  kafkaBrokers: string[]
  kafkaTopic: string
  kafkaGroupId: string
  workflowApiUrl: string
  mcpServerUrl: string
  aiGatewayUrl: string
  redisUrl: string
  pollIntervalMs: number
}

export function loadSettings(): WorkerSettings {
  const kafkaBrokersStr = process.env.KAFKA_BROKERS ?? 'localhost:9092'
  const kafkaBrokers = kafkaBrokersStr.split(',').map(s => s.trim())

  return {
    kafkaBrokers,
    kafkaTopic: process.env.KAFKA_TOPIC ?? 'workflow.events',
    kafkaGroupId: process.env.KAFKA_GROUP_ID ?? 'skill-flow-worker',
    workflowApiUrl: process.env.WORKFLOW_API_URL ?? 'http://localhost:3800',
    mcpServerUrl: process.env.MCP_SERVER_URL ?? 'http://localhost:3100',
    aiGatewayUrl: process.env.AI_GATEWAY_URL ?? 'http://localhost:3200',
    redisUrl: process.env.REDIS_URL ?? 'redis://localhost:6379',
    pollIntervalMs: parseInt(process.env.POLL_INTERVAL_MS ?? '100', 10),
  }
}
