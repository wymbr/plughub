/**
 * useDelegationSchema  (delegation_input feature)
 *
 * Fetches the delegation_input schema for a given agent type via
 * GET /v1/agent-types/:agentTypeId/delegation-schema.
 *
 * Returns { schema, loading }:
 *   schema   — DelegationSchema when the agent has one defined, null otherwise.
 *   loading  — true while the request is in flight.
 *
 * The drawer falls back to a free-text textarea when schema is null.
 * Re-fetches whenever agentTypeId changes.
 */

import { useEffect, useState } from "react";
import { DelegationSchema } from "../types";

const API_BASE = "/v1";

export function useDelegationSchema(agentTypeId: string | null): {
  schema:  DelegationSchema | null;
  loading: boolean;
} {
  const [schema,  setSchema]  = useState<DelegationSchema | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!agentTypeId) {
      setSchema(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);

    fetch(`${API_BASE}/agent-types/${encodeURIComponent(agentTypeId)}/delegation-schema`, {
      headers: { "x-tenant-id": "tenant_demo", "x-user-id": "operator" },
    })
      .then(r => {
        if (!r.ok) return null;
        return r.json() as Promise<{ delegation_input: DelegationSchema | null }>;
      })
      .then(data => {
        if (!cancelled) {
          setSchema(data?.delegation_input ?? null);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSchema(null);
          setLoading(false);
        }
      });

    return () => { cancelled = true; };
  }, [agentTypeId]);

  return { schema, loading };
}
