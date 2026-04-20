/**
 * infra/audit-kafka.ts
 * Async Kafka audit writer for the MCP interception layer.
 * Spec: PlugHub seção 9 — audit policy / MCP interception.
 *
 * Design:
 * - Events are buffered in memory and flushed on a background interval.
 * - The Kafka producer connects lazily on first flush attempt.
 * - All errors are swallowed — audit loss is preferable to blocking a call.
 * - `stop()` performs a final flush and disconnects gracefully.
 * - `unref()` on the timer prevents it from keeping the process alive.
 *
 * Overhead: write() is synchronous O(1) push. Kafka I/O never blocks a caller.
 */

import { Kafka, type Producer } from "kafkajs"
import type { AuditRecord }     from "@plughub/schemas"

// ─────────────────────────────────────────────
// Config
// ─────────────────────────────────────────────

export interface AuditKafkaConfig {
  brokers:             string[]
  topic:               string
  client_id?:          string
  flush_interval_ms?:  number  // default: 500
  max_buffer_size?:    number  // default: 1000 — drops oldest when full
}

// ─────────────────────────────────────────────
// Writer
// ─────────────────────────────────────────────

export class AuditKafkaWriter {
  private producer:   Producer | null = null
  private connecting: boolean         = false
  private readonly buffer:  AuditRecord[] = []
  private timer:      ReturnType<typeof setInterval> | null = null

  private readonly kafka:            Kafka
  private readonly topic:            string
  private readonly maxBufferSize:    number
  private readonly flushIntervalMs:  number

  constructor(cfg: AuditKafkaConfig) {
    this.kafka           = new Kafka({
      clientId: cfg.client_id ?? "plughub-sdk-audit",
      brokers:  cfg.brokers,
      // Suppress noisy kafkajs logs in audit writer — errors are swallowed anyway
      logCreator: () => () => undefined,
    })
    this.topic           = cfg.topic
    this.maxBufferSize   = cfg.max_buffer_size   ?? 1000
    this.flushIntervalMs = cfg.flush_interval_ms ?? 500
  }

  /**
   * Add an audit record to the buffer.
   * Non-blocking — returns immediately.
   * If the buffer is full, the oldest record is discarded to make room.
   */
  write(record: AuditRecord): void {
    if (this.buffer.length >= this.maxBufferSize) {
      this.buffer.shift()   // drop oldest — backpressure strategy
    }
    this.buffer.push(record)
  }

  /**
   * Start the background flush timer.
   * Call once on interceptor/proxy startup.
   */
  start(): void {
    if (this.timer) return
    this.timer = setInterval(() => { void this._flush() }, this.flushIntervalMs)
    if (typeof this.timer.unref === "function") this.timer.unref()
    // Trigger lazy connect immediately (non-blocking)
    void this._ensureConnected()
  }

  /**
   * Flush remaining buffer and disconnect the producer.
   * Call on graceful shutdown.
   */
  async stop(): Promise<void> {
    if (this.timer) {
      clearInterval(this.timer)
      this.timer = null
    }
    await this._flush()
    if (this.producer) {
      await this.producer.disconnect().catch(() => undefined)
      this.producer = null
    }
  }

  // ─── Internal ─────────────────────────────

  private async _ensureConnected(): Promise<void> {
    if (this.producer || this.connecting) return
    this.connecting = true
    try {
      const p = this.kafka.producer({
        allowAutoTopicCreation: false,
        retry: { retries: 3 },
      })
      await p.connect()
      this.producer = p
    } catch {
      // Non-fatal — will retry on next flush cycle
    } finally {
      this.connecting = false
    }
  }

  private async _flush(): Promise<void> {
    if (this.buffer.length === 0) return
    await this._ensureConnected()
    if (!this.producer) return   // still not connected — skip this cycle

    const batch = this.buffer.splice(0, this.buffer.length)
    try {
      await this.producer.send({
        topic:    this.topic,
        messages: batch.map(r => ({
          key:   r.session_id,                  // partition by session for ordering
          value: JSON.stringify(r),
        })),
      })
    } catch {
      // Non-fatal — put records back at the front of the buffer
      // (limited to maxBufferSize to avoid unbounded growth on persistent failures)
      const remaining = this.maxBufferSize - this.buffer.length
      if (remaining > 0) {
        this.buffer.unshift(...batch.slice(0, remaining))
      }
    }
  }
}
