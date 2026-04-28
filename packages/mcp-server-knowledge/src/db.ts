/**
 * db.ts
 * PostgreSQL + pgvector schema and CRUD for the knowledge base.
 *
 * Schema: knowledge.*
 *   knowledge.snippets   — main table with embedding vector(1536)
 *   knowledge.namespaces — virtual view via GROUP BY (no separate table)
 *
 * pgvector operators used:
 *   <=>  cosine distance    (default, normalized vectors)
 *   <->  L2 distance        (alternative for non-normalized)
 *   <#>  negative dot-product (fastest with normalized unit vectors)
 *
 * Full-text fallback:
 *   When USE_EMBEDDING=false or embedding is null, uses PostgreSQL
 *   full-text search (ts_rank + to_tsvector) instead of vector cosine.
 *   This allows the service to be tested and operated without an
 *   embedding provider.
 *
 * Index strategy:
 *   IVFFlat with lists=100 (suitable for up to ~1M rows per tenant/ns).
 *   HNSW available via pgvector 0.5+ — switch by changing CREATE INDEX.
 */

import { Pool, type PoolClient } from "pg"

// ─── Types ────────────────────────────────────────────────────────────────────

export interface KnowledgeSnippet {
  snippet_id:  string
  tenant_id:   string
  namespace:   string
  content:     string
  embedding:   number[] | null
  source_ref:  string | null
  metadata:    Record<string, unknown>
  created_at:  string
  updated_at:  string
  /** Similarity score (0–1), present on search results only */
  score?:      number
}

export interface NamespaceStat {
  tenant_id:     string
  namespace:     string
  snippet_count: number
  has_embeddings: boolean
}

export interface UpsertResult {
  snippet_id: string
  created:    boolean   // true = INSERT, false = UPDATE
}

// ─── DDL ─────────────────────────────────────────────────────────────────────

export const DDL_SCHEMA = `
CREATE SCHEMA IF NOT EXISTS knowledge;
`

export const DDL_EXTENSION = `
CREATE EXTENSION IF NOT EXISTS vector;
`

export const DDL_SNIPPETS = `
CREATE TABLE IF NOT EXISTS knowledge.snippets (
  snippet_id   UUID        NOT NULL DEFAULT gen_random_uuid(),
  tenant_id    TEXT        NOT NULL,
  namespace    TEXT        NOT NULL DEFAULT 'default',
  content      TEXT        NOT NULL,
  embedding    vector(1536),
  source_ref   TEXT,
  metadata     JSONB       NOT NULL DEFAULT '{}',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT snippets_pkey PRIMARY KEY (snippet_id)
);
`

export const DDL_INDEXES = `
CREATE INDEX IF NOT EXISTS idx_snippets_tenant_ns
  ON knowledge.snippets (tenant_id, namespace);

CREATE INDEX IF NOT EXISTS idx_snippets_ts
  ON knowledge.snippets
  USING GIN (to_tsvector('portuguese', content));
`

/**
 * IVFFlat index for cosine similarity.
 * Created separately because it requires data to exist (lists=100 needs ~100k rows
 * to be effective; safe to skip in fresh environments and create later).
 * Wrapped in DO block to be idempotent.
 */
export const DDL_VECTOR_INDEX = `
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'knowledge'
      AND tablename  = 'snippets'
      AND indexname  = 'idx_snippets_embedding'
  ) THEN
    CREATE INDEX idx_snippets_embedding
      ON knowledge.snippets
      USING ivfflat (embedding vector_cosine_ops)
      WITH (lists = 100);
  END IF;
END
$$;
`

// ─── DB interface (for easy mocking in tests) ─────────────────────────────────

export interface DbClient {
  query<R = Record<string, unknown>>(
    sql: string,
    values?: unknown[]
  ): Promise<{ rows: R[] }>
}

// ─── Schema bootstrap ─────────────────────────────────────────────────────────

/**
 * Ensures schema + table + indexes exist.
 * Safe to call on every startup — all statements are idempotent.
 * Vector index is attempted but failures are logged as warnings (not fatal),
 * because pgvector may not be installed in all environments.
 */
export async function ensureSchema(client: DbClient): Promise<void> {
  await client.query(DDL_SCHEMA)
  await client.query(DDL_SNIPPETS)
  await client.query(DDL_INDEXES)

  // pgvector extension + vector index — optional, non-fatal
  try {
    await client.query(DDL_EXTENSION)
    await client.query(DDL_VECTOR_INDEX)
  } catch (err) {
    console.warn("[mcp-server-knowledge] pgvector not available — vector search disabled:", err)
  }
}

// ─── CRUD ─────────────────────────────────────────────────────────────────────

/**
 * Upsert a snippet. Matches by (tenant_id, source_ref) when source_ref is
 * provided; otherwise always inserts.
 */
export async function upsertSnippet(
  client: DbClient,
  params: {
    tenant_id:  string
    namespace:  string
    content:    string
    embedding?: number[] | null
    source_ref?: string | null
    metadata?:  Record<string, unknown>
  }
): Promise<UpsertResult> {
  const { tenant_id, namespace, content, embedding, source_ref, metadata } = params
  const embStr = embedding ? `[${embedding.join(",")}]` : null

  if (source_ref) {
    // Try UPDATE first
    const upd = await client.query<{ snippet_id: string }>(
      `UPDATE knowledge.snippets
         SET content    = $1,
             embedding  = $2::vector,
             metadata   = $3,
             updated_at = now()
       WHERE tenant_id = $4
         AND source_ref = $5
       RETURNING snippet_id`,
      [content, embStr, JSON.stringify(metadata ?? {}), tenant_id, source_ref]
    )
    if (upd.rows.length > 0) {
      return { snippet_id: upd.rows[0]!.snippet_id, created: false }
    }
  }

  // INSERT
  const ins = await client.query<{ snippet_id: string }>(
    `INSERT INTO knowledge.snippets
       (tenant_id, namespace, content, embedding, source_ref, metadata)
     VALUES ($1, $2, $3, $4::vector, $5, $6)
     RETURNING snippet_id`,
    [tenant_id, namespace, content, embStr, source_ref ?? null, JSON.stringify(metadata ?? {})]
  )
  return { snippet_id: ins.rows[0]!.snippet_id, created: true }
}

/**
 * Delete a single snippet by ID. Returns true if deleted.
 */
export async function deleteSnippet(
  client: DbClient,
  params: { tenant_id: string; snippet_id: string }
): Promise<boolean> {
  const res = await client.query<{ snippet_id: string }>(
    `DELETE FROM knowledge.snippets
      WHERE tenant_id  = $1
        AND snippet_id = $2
      RETURNING snippet_id`,
    [params.tenant_id, params.snippet_id]
  )
  return res.rows.length > 0
}

/**
 * Delete all snippets for a namespace (admin bulk-delete).
 * Returns the number of deleted rows.
 */
export async function deleteNamespace(
  client: DbClient,
  params: { tenant_id: string; namespace: string }
): Promise<number> {
  const res = await client.query<{ snippet_id: string }>(
    `DELETE FROM knowledge.snippets
      WHERE tenant_id = $1
        AND namespace  = $2
      RETURNING snippet_id`,
    [params.tenant_id, params.namespace]
  )
  return res.rows.length
}

/**
 * Vector similarity search using cosine distance (pgvector <=>).
 * Falls back to PostgreSQL full-text search when no embedding is supplied.
 */
export async function searchSnippets(
  client: DbClient,
  params: {
    tenant_id:   string
    namespace?:  string
    embedding?:  number[] | null  // null → full-text fallback
    query_text?: string            // used for full-text fallback
    top_k:       number
    min_score:   number           // 0–1 range
  }
): Promise<KnowledgeSnippet[]> {
  const { tenant_id, namespace, embedding, query_text, top_k, min_score } = params

  const nsCond = namespace ? "AND namespace = $3" : ""

  if (embedding && embedding.length > 0) {
    // ── Vector cosine search ────────────────────────────────────────────────
    // cosine distance: 0 = identical, 2 = opposite. Convert to similarity: 1 − d.
    const embStr = `[${embedding.join(",")}]`
    const args: unknown[] = [tenant_id, embStr, top_k]
    if (namespace) args.splice(2, 0, namespace)

    const nsIdx = namespace ? 3 : ""
    const sql = namespace
      ? `SELECT snippet_id, tenant_id, namespace, content, source_ref,
                metadata, created_at, updated_at,
                1 - (embedding <=> $2::vector) AS score
           FROM knowledge.snippets
          WHERE tenant_id = $1 AND namespace = $${nsIdx}
            AND embedding IS NOT NULL
          ORDER BY embedding <=> $2::vector
          LIMIT $${namespace ? 4 : 3}`
      : `SELECT snippet_id, tenant_id, namespace, content, source_ref,
                metadata, created_at, updated_at,
                1 - (embedding <=> $2::vector) AS score
           FROM knowledge.snippets
          WHERE tenant_id = $1
            AND embedding IS NOT NULL
          ORDER BY embedding <=> $2::vector
          LIMIT $3`

    const res = await client.query<KnowledgeSnippet & { score: string }>(
      sql,
      namespace ? [tenant_id, embStr, namespace, top_k] : [tenant_id, embStr, top_k]
    )

    return res.rows
      .map(r => ({ ...r, score: parseFloat(r.score as unknown as string) }))
      .filter(r => r.score >= min_score)
  }

  if (query_text) {
    // ── Full-text fallback (ts_rank) ────────────────────────────────────────
    const args: unknown[] = [tenant_id, query_text, top_k]
    const namespaceFilter = namespace ? `AND namespace = $4` : ""
    if (namespace) args.push(namespace)

    const sql = `
      SELECT snippet_id, tenant_id, namespace, content, source_ref,
             metadata, created_at, updated_at,
             ts_rank(to_tsvector('portuguese', content),
                     plainto_tsquery('portuguese', $2)) AS score
        FROM knowledge.snippets
       WHERE tenant_id = $1
         ${namespaceFilter}
         AND to_tsvector('portuguese', content)
             @@ plainto_tsquery('portuguese', $2)
       ORDER BY score DESC
       LIMIT $3`

    const res = await client.query<KnowledgeSnippet & { score: string }>(sql, args)

    return res.rows
      .map(r => ({ ...r, score: parseFloat(r.score as unknown as string) }))
      .filter(r => r.score > 0 && r.score >= min_score)
  }

  return []
}

/**
 * List namespace statistics for a tenant.
 */
export async function listNamespaces(
  client: DbClient,
  tenant_id: string
): Promise<NamespaceStat[]> {
  const res = await client.query<{
    tenant_id: string
    namespace: string
    snippet_count: string
    has_embeddings: boolean
  }>(
    `SELECT tenant_id, namespace,
            COUNT(*)                                          AS snippet_count,
            BOOL_OR(embedding IS NOT NULL)                    AS has_embeddings
       FROM knowledge.snippets
      WHERE tenant_id = $1
      GROUP BY tenant_id, namespace
      ORDER BY namespace`,
    [tenant_id]
  )
  return res.rows.map(r => ({
    tenant_id:      r.tenant_id,
    namespace:      r.namespace,
    snippet_count:  parseInt(r.snippet_count, 10),
    has_embeddings: r.has_embeddings,
  }))
}

// ─── Pool factory ─────────────────────────────────────────────────────────────

export function createPool(connectionString: string): Pool {
  return new Pool({
    connectionString,
    max:             10,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 5_000,
  })
}
