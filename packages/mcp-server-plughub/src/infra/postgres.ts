/**
 * infra/postgres.ts
 * Factory do cliente PostgreSQL para o mcp-server-plughub.
 * Usado exclusivamente pelas tools de avaliação (transcript_get).
 */

import { Pool } from "pg"

export type PostgresPool = Pool

export function createPostgresPool(): PostgresPool {
  const connectionString =
    process.env["POSTGRES_DSN"] ?? "postgresql://plughub:plughub@localhost:5432/plughub"
  return new Pool({ connectionString })
}

// ─── Cliente no-op para testes ────────────────────────────────────────────────

export interface TranscriptMessage {
  author_type:  string
  content_text: string
  timestamp:    string
  position:     number
}

export interface PostgresClient {
  fetchTranscript(transcriptId: string): Promise<TranscriptMessage[]>
  end(): Promise<void>
}

export function createPostgresClient(pool?: PostgresPool): PostgresClient {
  const pg = pool ?? createPostgresPool()

  return {
    async fetchTranscript(transcriptId) {
      const result = await pg.query<TranscriptMessage>(
        `SELECT author_type, content_text, timestamp::text AS timestamp, position
           FROM transcript_messages
          WHERE transcript_id = $1
          ORDER BY position`,
        [transcriptId]
      )
      return result.rows
    },

    async end() {
      await pg.end()
    },
  }
}

export function createNoOpPostgresClient(): PostgresClient {
  return {
    async fetchTranscript() { return [] },
    async end() {},
  }
}
