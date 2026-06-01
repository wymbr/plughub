/**
 * useDelegationSchema  (delegation_input feature)
 *
 * Fetches the delegation_input schema and delegation_visibility for a given
 * agent type via GET /v1/agent-types/:agentTypeId/delegation-schema.
 *
 * Returns { schema, delegationVisibility, loading }:
 *   schema              — DelegationSchema when the agent declares params, null otherwise.
 *   delegationVisibility — "all" | "agents_only" | null
 *                          null  → show visibility radio (default agents_only)
 *                          value → locked; radio hidden in UI
 *   loading             — true while the request is in flight.
 *
 * The endpoint always returns 200 so delegation_visibility is available even
 * for agents without typed parameters.
 * Re-fetches whenever agentTypeId changes.
 */

import { useEffect, useState } from "react";
import { DelegationSchema } from "../types";

const API_BASE = "/v1";

export function useDelegationSchema(agentTypeId: string | null): {
  schema:               DelegationSchema | null;
  delegationVisibility: "all" | "agents_only" | null;
  loading:              boolean;
} {
  const [schema,               setSchema]               = useState<DelegationSchema | null>(null);
  const [delegationVisibility, setDelegationVisibility] = useState<"all" | "agents_only" | null>(null);
  const [loading,              setLoading]              = useState(false);

  useEffect(() => {
    if (!agentTypeId) {
      setSchema(null);
      setDelegationVisibility(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);

    // AgentType retired: delegation_input lives on the skill, and (deploy-driven)
    // agentTypeId carries the skill_id — fetch the skill's delegation-schema.
    fetch(`${API_BASE}/skills/${encodeURIComponent(agentTypeId)}/delegation-schema`, {
      headers: { "x-tenant-id": "tenant_demo", "x-user-id": "operator" },
    })
      .then(r => {
        if (!r.ok) return null;
        return r.json() as Promise<{
          delegation_input:     DelegationSchema | null;
          delegation_visibility: "all" | "agents_only" | null;
        }>;
      })
      .then(data => {
        if (!cancelled) {
          setSchema(data?.delegation_input ?? null);
          setDelegationVisibility(data?.delegation_visibility ?? null);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSchema(null);
          setDelegationVisibility(null);
          setLoading(false);
        }
      });

    return () => { cancelled = true; };
  }, [agentTypeId]);

  return { schema, delegationVisibility, loading };
}
