/**
 * worker.ts
 * Kafka consumer for workflow.events topic.
 * Handles workflow.started, workflow.resumed, workflow.timed_out events.
 *
 * Reliability contract:
 *   • Dispatch layer (JSON parse + getInstance + runInstance kickoff) is retried
 *     up to MAX_ATTEMPTS times with exponential backoff before the message is
 *     published to the dead-letter topic (events.dead_letter).
 *   • The async skill-flow execution (runInstance) runs fire-and-forget after
 *     a successful dispatch so that Kafka partitions are never stalled waiting
 *     for long-running flows (receive step can block up to 4 hours).  Execution
 *     failures are handled by the workflow-api timeout scanner.
 */

import { Kafka, Producer, logLevel } from 'kafkajs'
import Redis from 'ioredis'
import type { WorkerSettings } from './config'
import { WorkflowClient } from './workflow-client'
import { EngineRunner } from './engine-runner'

const MAX_ATTEMPTS    = 3
const BACKOFF_BASE_MS = 500   // 500ms → 1 000ms between retries

interface WorkflowEvent {
  event_type: 'workflow.started' | 'workflow.resumed' | 'workflow.timed_out'
  timestamp: string
  tenant_id: string
  instance_id: string
  flow_id: string
  current_step?: string
  decision?: 'approved' | 'rejected' | 'input' | 'timeout'
  /** For collect.responded resumes — the response data from the target */
  response_data?: Record<string, unknown>
  [key: string]: unknown
}

interface DlqPayload {
  event_id:      string
  source_topic:  string
  consumer_group: string
  service:       string
  error:         string
  attempt_count: number
  payload_raw:   string
  failed_at:     string
}

export class SkillFlowWorker {
  private kafka: Kafka
  private settings: WorkerSettings
  private redis: Redis
  private workflowClient: WorkflowClient
  private engineRunner: EngineRunner
  private producer: Producer | null = null
  private running = false

  /**
   * Tracks in-flight runInstance promises.
   *
   * Skill-flows that contain a `receive` step will block on a Redis BLPOP
   * for up to 4 hours waiting for a stream event. If we awaited runInstance
   * inside eachMessage, the Kafka partition would stall for that entire window.
   *
   * Instead, we fire runInstance without awaiting and track the promise here
   * so that graceful shutdown (SIGTERM/SIGINT) can drain all in-flight
   * executions before exiting.
   */
  private readonly _inflight = new Set<Promise<void>>()

  constructor(settings: WorkerSettings) {
    this.settings = settings
    this.kafka = new Kafka({
      clientId: 'skill-flow-worker',
      brokers: settings.kafkaBrokers,
      logLevel: logLevel.INFO,
    })
    this.redis = new Redis(settings.redisUrl)
    this.workflowClient = new WorkflowClient(settings)
    this.engineRunner = new EngineRunner({
      settings,
      redis: this.redis,
      workflowClient: this.workflowClient,
    })
  }

  async start(): Promise<void> {
    if (this.running) return
    this.running = true

    // ── DLQ producer ──────────────────────────────────────────────────────
    this.producer = this.kafka.producer()
    await this.producer.connect()
    console.log('DLQ producer connected')

    const consumer = this.kafka.consumer({
      groupId: this.settings.kafkaGroupId,
      allowAutoTopicCreation: true,
    })

    await consumer.connect()
    console.log(`Connected to Kafka, subscribing to ${this.settings.kafkaTopic}`)

    await consumer.subscribe({
      topic: this.settings.kafkaTopic,
      fromBeginning: false,
    })

    // Handle graceful shutdown — drain all in-flight executions before exiting.
    // Flows with a `receive` step may block for minutes/hours on a Redis BLPOP;
    // the session:closed sentinel pushed by the orchestrator-bridge will unblock
    // them so this drain should resolve quickly once services begin shutting down.
    const gracefulShutdown = async (signal: string) => {
      console.log(`Received ${signal}, shutting down gracefully...`)
      this.running = false
      await consumer.disconnect()

      if (this._inflight.size > 0) {
        console.log(`Waiting for ${this._inflight.size} in-flight execution(s) to finish...`)
        await Promise.allSettled([...this._inflight])
      }

      await this.producer?.disconnect()
      await this.redis.quit()
      process.exit(0)
    }

    process.on('SIGTERM', () => gracefulShutdown('SIGTERM'))
    process.on('SIGINT', () => gracefulShutdown('SIGINT'))

    console.log('Skill Flow Worker started, waiting for events...')

    await consumer.run({
      eachMessage: async ({ topic, partition, message }) => {
        // Do NOT await — see class doc above.
        const rawValue = message.value?.toString() ?? ''
        const p: Promise<void> = this._handleWithRetry(rawValue, topic, partition)
          .finally(() => { this._inflight.delete(p) })

        this._inflight.add(p)
        // Return immediately — Kafka commits the offset and picks up the next message.
      },
    })
  }

  // ── Retry + DLQ wrapper ────────────────────────────────────────────────────

  private async _handleWithRetry(
    rawValue:  string,
    topic:     string,
    partition: number,
  ): Promise<void> {
    let lastError: unknown

    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      try {
        await this.handleMessage(rawValue)
        return  // success
      } catch (err) {
        lastError = err
        if (attempt < MAX_ATTEMPTS) {
          const delayMs = BACKOFF_BASE_MS * Math.pow(2, attempt - 1)
          console.warn(
            `[retry ${attempt}/${MAX_ATTEMPTS}] topic=${topic} partition=${partition} ` +
            `error=${err instanceof Error ? err.message : String(err)} delay=${delayMs}ms`,
          )
          await this._sleep(delayMs)
        }
      }
    }

    // All retries exhausted — publish to DLQ
    const errMsg = lastError instanceof Error ? lastError.message : String(lastError)
    console.error(
      `[dlq] All ${MAX_ATTEMPTS} attempts failed — topic=${topic} partition=${partition} ` +
      `error=${errMsg}`,
    )
    await this._publishDlq(rawValue, topic, partition, errMsg)
  }

  private async _publishDlq(
    rawValue:   string,
    sourceTopic: string,
    partition:  number,
    error:      string,
  ): Promise<void> {
    if (!this.producer) return

    const payload: DlqPayload = {
      event_id:       crypto.randomUUID(),
      source_topic:   sourceTopic,
      consumer_group: this.settings.kafkaGroupId,
      service:        'skill-flow-worker',
      error,
      attempt_count:  MAX_ATTEMPTS,
      payload_raw:    rawValue,
      failed_at:      new Date().toISOString(),
    }

    try {
      await this.producer.send({
        topic:    this.settings.kafkaDlqTopic,
        messages: [{ value: JSON.stringify(payload) }],
      })
    } catch (dlqErr) {
      console.error('[dlq] Failed to publish to DLQ:', dlqErr)
    }
  }

  private _sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms))
  }

  // ── Message handling ───────────────────────────────────────────────────────

  private async handleMessage(rawValue: string): Promise<void> {
    let event: WorkflowEvent
    try {
      event = JSON.parse(rawValue) as WorkflowEvent
    } catch {
      // Malformed JSON — no point retrying.  Log and skip immediately.
      console.error(`Failed to parse message: ${rawValue}`)
      return
    }

    const { event_type, instance_id } = event

    console.log(`Processing ${event_type} for instance ${instance_id}`)

    const instance = await this.workflowClient.getInstance(instance_id)

    switch (event_type) {
      case 'workflow.started':
        await this.engineRunner.runInstance(instance)
        break

      case 'workflow.resumed': {
        const decision = (event.decision ?? 'approved') as
          'approved' | 'rejected' | 'input' | 'timeout'
        const currentStep = instance.current_step ?? ''
        const resumeContext = {
          decision,
          step_id:  currentStep,
          payload:  event.response_data ?? {},
        }
        await this.engineRunner.runInstance(instance, resumeContext)
        break
      }

      case 'workflow.timed_out': {
        const currentStep = instance.current_step ?? ''
        const resumeContext = {
          decision: 'timeout' as const,
          step_id:  currentStep,
          payload:  {},
        }
        await this.engineRunner.runInstance(instance, resumeContext)
        break
      }

      default:
        console.warn(`Unknown event type: ${event_type}`)
    }
  }
}
