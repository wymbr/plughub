/**
 * tools.ts
 * MCP tool registration for mcp-server-knowledge.
 *
 * Tools:
 *   knowledge_search  — vector + full-text hybrid search
 *   knowledge_upsert  — insert or update a snippet with optional embedding
 *   knowledge_delete  — delete one snippet by ID
 *
 * Embedding generation:
 *   When EMBEDDING_PROVIDER=openai (and OPENAI_API_KEY is set), the server
 *   calls /v1/embeddings with text-embedding-3-small to compute embeddings
 *   before upsert and to embed the query before search.
 *   When no embedding provider is configured, the server stores content-only
 *   and falls back to PostgreSQL full-text search on queries.
 *
 * Audit:
 *   All tools log tenant_id + snippet_id + operation for traceability.
 *   No sensitive user data is stored — content should be pre-anonymised by
 *   the caller (evaluation agent removes PII before knowledge_upsert).
 */

import { z }             from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import {
  upsertSnippet,
  deleteSnippet,
  searchSnippets,
  type DbClient,
} from "./db.js"

// ─── Config ───────────────────────────────────────────────────────────────────

export interface KnowledgeConfig {
  /** Embedding provider. "openai" = call /v1/embeddings. Empty = text-only mode. */
  embeddingProvider: "" | "openai"
  /** OpenAI API key (required when embeddingProvider=openai) */
  openaiApiKey:      string
  /** Model to use for embeddings (default: text-embedding-3-small) */
  embeddingModel:    string
  /** Output dimension for the embedding model (default: 1536) */
  embeddingDim:      number
}

export type KnowledgeDeps = {
  db:     DbClient
  config: KnowledgeConfig
}

// ─── Embedding helper ─────────────────────────────────────────────────────────

async function embedText(
  text: string,
  config: KnowledgeConfig
): Promise<number[] | null> {
  if (config.embeddingProvider !== "openai" || !config.openaiApiKey) {
    return null
  }

  try {
    const res = await fetch("https://api.openai.com/v1/embeddings", {
      method:  "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${config.openaiApiKey}`,
      },
      body: JSON.stringify({
        input:      text,
        model:      config.embeddingModel,
        dimensions: config.embeddingDim,
      }),
    })

    if (!res.ok) {
      console.warn(`[mcp-server-knowledge] embedText HTTP ${res.status}`)
      return null
    }

    const body = (await res.json()) as {
      data: Array<{ embedding: number[] }>
    }
    return body.data[0]?.embedding ?? null
  } catch (err) {
    console.warn("[mcp-server-knowledge] embedText failed:", err)
    return null
  }
}

// ─── Input schemas ────────────────────────────────────────────────────────────

const KnowledgeSearchInputSchema = z.object({
  /** Tenant identifier for row-level isolation */
  tenant_id:   z.string().min(1),
  /** Namespace within the tenant (e.g. "evaluation_policies", "greeting_scripts") */
  namespace:   z.string().optional(),
  /** Free-text query — embedded if embedding provider is configured; full-text otherwise */
  query:       z.string().min(1),
  /** Maximum number of results (1–50, default 5) */
  top_k:       z.number().int().min(1).max(50).default(5),
  /**
   * Minimum similarity score (0–1 cosine, or ts_rank for full-text).
   * Default 0.0 means no filtering (return top_k regardless of score).
   */
  min_score:   z.number().min(0).max(1).default(0.0),
})

const KnowledgeUpsertInputSchema = z.object({
  /** Tenant identifier */
  tenant_id:  z.string().min(1),
  /** Namespace for the snippet (default: "default") */
  namespace:  z.string().default("default"),
  /**
   * Snippet content — the text that will be stored and indexed.
   * Callers are responsible for removing PII before calling this tool.
   */
  content:    z.string().min(1).max(8000),
  /**
   * Pre-computed embedding vector. When omitted and the server has an
   * embedding provider configured, the server computes it automatically.
   * When both are absent, the snippet is stored in text-only mode.
   */
  embedding:  z.array(z.number()).optional(),
  /**
   * External reference (e.g. doc URL, evaluation_id, policy_id).
   * Used as the natural key for upsert — two upserts with the same
   * (tenant_id, source_ref) update the same row instead of inserting new.
   */
  source_ref: z.string().optional(),
  /** Arbitrary metadata bag. Useful for filtering/attribution. */
  metadata:   z.record(z.unknown()).optional(),
})

const KnowledgeDeleteInputSchema = z.object({
  /** Tenant identifier */
  tenant_id:  z.string().min(1),
  /** UUID of the snippet to delete */
  snippet_id: z.string().uuid(),
})

// ─── Helpers ──────────────────────────────────────────────────────────────────

type ToolResult = { isError?: true; content: Array<{ type: "text"; text: string }> }

function ok(data: unknown): ToolResult {
  return { content: [{ type: "text", text: JSON.stringify(data) }] }
}

function toolError(code: string, message: string): ToolResult {
  return {
    isError: true,
    content: [{ type: "text", text: JSON.stringify({ error: code, message }) }],
  }
}

function handleError(e: unknown): ToolResult {
  if (e instanceof z.ZodError) {
    return toolError(
      "validation_error",
      e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; ")
    )
  }
  return toolError("internal_error", e instanceof Error ? e.message : String(e))
}

// ─── Tool registration ────────────────────────────────────────────────────────

export function registerKnowledgeTools(server: McpServer, deps: KnowledgeDeps): void {
  const { db, config } = deps

  // ── knowledge_search ───────────────────────────────────────────────────────
  server.tool(
    "knowledge_search",
    "Searches the knowledge base for snippets relevant to a query. " +
    "Uses vector cosine similarity when the server has an embedding provider; " +
    "falls back to PostgreSQL full-text search otherwise. " +
    "Returns up to top_k snippets with their similarity scores. " +
    "Typical use: evaluator agent calls knowledge_search before scoring to " +
    "retrieve relevant policies, SLAs, or best-practice references.",
    KnowledgeSearchInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const parsed = KnowledgeSearchInputSchema.parse(input)
        const { tenant_id, namespace, query, top_k, min_score } = parsed

        // Try to embed the query; fall back to text search if embedding unavailable
        const embedding = await embedText(query, config)

        const snippets = await searchSnippets(db, {
          tenant_id,
          namespace,
          embedding,
          query_text: embedding ? undefined : query,
          top_k,
          min_score,
        })

        return ok({
          query,
          tenant_id,
          namespace,
          search_mode:    embedding ? "vector" : "fulltext",
          result_count:   snippets.length,
          snippets:       snippets.map(s => ({
            snippet_id:  s.snippet_id,
            content:     s.content,
            score:       Math.round((s.score ?? 0) * 10000) / 10000,
            source_ref:  s.source_ref,
            namespace:   s.namespace,
            metadata:    s.metadata,
          })),
        })
      } catch (e) {
        return handleError(e)
      }
    }
  )

  // ── knowledge_upsert ───────────────────────────────────────────────────────
  server.tool(
    "knowledge_upsert",
    "Inserts or updates a knowledge snippet. " +
    "When source_ref is provided, upserts by (tenant_id, source_ref). " +
    "Embedding is computed automatically if the server has a configured provider " +
    "and no pre-computed embedding is supplied. " +
    "Callers must ensure PII is removed from content before calling this tool.",
    KnowledgeUpsertInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const parsed = KnowledgeUpsertInputSchema.parse(input)
        const { tenant_id, namespace, content, source_ref, metadata } = parsed
        let { embedding } = parsed

        // Auto-compute embedding when not supplied and provider is available
        if (!embedding) {
          const computed = await embedText(content, config)
          if (computed) embedding = computed
        }

        const result = await upsertSnippet(db, {
          tenant_id,
          namespace,
          content,
          embedding: embedding ?? null,
          source_ref,
          metadata,
        })

        console.log(
          `[mcp-server-knowledge] upsert tenant=${tenant_id} ns=${namespace} ` +
          `snippet_id=${result.snippet_id} created=${result.created}`
        )

        return ok({
          snippet_id: result.snippet_id,
          created:    result.created,
          tenant_id,
          namespace,
          has_embedding: embedding != null,
        })
      } catch (e) {
        return handleError(e)
      }
    }
  )

  // ── knowledge_delete ───────────────────────────────────────────────────────
  server.tool(
    "knowledge_delete",
    "Deletes a single knowledge snippet by its UUID. " +
    "Returns deleted: true if the snippet was found and removed. " +
    "Returns deleted: false (no error) if the snippet did not exist — idempotent.",
    KnowledgeDeleteInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { tenant_id, snippet_id } = KnowledgeDeleteInputSchema.parse(input)

        const deleted = await deleteSnippet(db, { tenant_id, snippet_id })

        console.log(
          `[mcp-server-knowledge] delete tenant=${tenant_id} ` +
          `snippet_id=${snippet_id} deleted=${deleted}`
        )

        return ok({ deleted, snippet_id, tenant_id })
      } catch (e) {
        return handleError(e)
      }
    }
  )
}
