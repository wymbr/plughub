/**
 * worker.ts
 * Kafka consumer for workflow.events topic.
 * Handles workflow.started, workflow.resumed, workflow.timed_out events.
 */

import { Kafka, logLevel } from 'kafkajs'
import Redis from 'ioredis'
import type { WorkerSettings } from './config'
import { WorkflowClient } from './workflow-client'
import { EngineRunner } from './engine-runner'

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

export class SkillFlowWorker {
  private kafka: Kafka
  private settings: WorkerSettings
  private redis: Redis
  private workflowClient: WorkflowClient
  private engineRunner: EngineRunner
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

      await this.redis.quit()
      process.exit(0)
    }

    process.on('SIGTERM', () => gracefulShutdown('SIGTERM'))
    process.on('SIGINT', () => gracefulShutdown('SIGINT'))

    console.log('Skill Flow Worker started, waiting for events...')

    await consumer.run({
      eachMessage: async ({ topic, partition, message }) => {
        // Do NOT await handleMessage — skill-flows with a `receive` step block
        // on a Redis BLPOP and would stall the Kafka consumer partition for the
        // entire wait window (up to 4 hours) if awaited here.
        // Instead, fire-and-forget and track the promise for graceful drain.
        const p: Promise<void> = this.handleMessage(message.value?.toString() ?? '')
          .catch(err => {
            const error = err instanceof Error ? err.message : String(err)
            console.error(
              `Error processing message from ${topic} partition ${partition}: ${error}`,
            )
          })
          .finally(() => { this._inflight.delete(p) })

        this._inflight.add(p)
        // Return immediately — Kafka commits the offset and picks up the next message.
        // In-flight executions continue in the background.
      },
    })
  }

  private async handleMessage(rawValue: string): Promise<void> {
    let event: WorkflowEvent
    try {
      event = JSON.parse(rawValue) as WorkflowEvent
    } catch {
      console.error(`Failed to parse message: ${rawValue}`)
      return
    }

    const { event_type, tenant_id, instance_id } = event

    console.log(`Processing ${event_type} for instance ${instance_id}`)

    try {
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
            // For collect responses, forward the response_data as payload so
            // the collect step can write it to pipeline_state[output_as].
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
    } catch (err) {
      const error = err instanceof Error ? err.message : String(err)
      console.error(`Failed to process event for instance ${instance_id}: ${error}`)
    }
  }
}
