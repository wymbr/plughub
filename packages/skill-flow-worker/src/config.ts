/**
 * config.ts
 * Environment configuration for the Skill Flow Worker.
 */

export interface WorkerSettings {
  kafkaBrokers:   string[]
  kafkaTopic:     string
  kafkaGroupId:   string
  workflowApiUrl: string
  calendarApiUrl: string
  mcpServerUrl:   string
  aiGatewayUrl:   string
  redisUrl:       string
  pollIntervalMs: number
  /**
   * Session token forwarded in every MCP call.
   * The mcp-server-plughub requires Authorization: Bearer <token>.
   * In the worker context a long-lived service token can be used.
   */
  mcpSessionToken: string
  /**
   * Tenant ID used when no tenant can be derived from a Kafka event.
   * The engine-runner overrides this with the instance's tenant_id at run time.
   */
  defaultTenantId: string
}

export function loadSettings(): WorkerSettings {
  const kafkaBrokersStr = process.env.KAFKA_BROKERS ?? 'localhost:9092'
  const kafkaBrokers = kafkaBrokersStr.split(',').map(s => s.trim())

  return {
    kafkaBrokers,
    kafkaTopic:      process.env.KAFKA_TOPIC        ?? 'workflow.events',
    kafkaGroupId:    process.env.KAFKA_GROUP_ID     ?? 'skill-flow-worker',
    workflowApiUrl:  process.env.WORKFLOW_API_URL   ?? 'http://localhost:3800',
    calendarApiUrl:  process.env.CALENDAR_API_URL   ?? 'http://localhost:3700',
    mcpServerUrl:    process.env.MCP_SERVER_URL     ?? 'http://localhost:3100',
    aiGatewayUrl:    process.env.AI_GATEWAY_URL     ?? 'http://localhost:3200',
    redisUrl:        process.env.REDIS_URL           ?? 'redis://localhost:6379',
    pollIntervalMs:  parseInt(process.env.POLL_INTERVAL_MS ?? '100', 10),
    mcpSessionToken: process.env.MCP_SESSION_TOKEN  ?? '',
    defaultTenantId: process.env.DEFAULT_TENANT_ID  ?? 'tenant_default',
  }
}
