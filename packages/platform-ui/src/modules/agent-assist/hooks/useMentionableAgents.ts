/**
 * useMentionableAgents  (Arc 11 Fase B)
 *
 * Fetches the list of AI agent types available for specialist invitation
 * in the current contact's pool, via GET /v1/pools/:poolId/mentionable-agents.
 *
 * Re-fetches when the poolId changes. Empty list when poolId is null.
 */

import { useEffect, useState } from "react";
import { MentionableAgent } from "../types";
import { apiFetch } from '@/api/apiFetch'

const API_BASE = "/v1";

export function useMentionableAgents(poolId: string | null): MentionableAgent[] {
  const [agents, setAgents] = useState<MentionableAgent[]>([]);

  useEffect(() => {
    if (!poolId) {
      setAgents([]);
      return;
    }

    let cancelled = false;

    apiFetch(`${API_BASE}/pools/${encodeURIComponent(poolId)}/mentionable-agents`, {
      headers: { "x-tenant-id": "tenant_demo", "x-user-id": "operator" },
    })
      .then(r => (r.ok ? r.json() : { agents: [] }))
      .then((data: { agents: MentionableAgent[] }) => {
        if (!cancelled) setAgents(data.agents ?? []);
      })
      .catch(() => { if (!cancelled) setAgents([]); });

    return () => { cancelled = true; };
  }, [poolId]);

  return agents;
}
