/**
 * useMentionableProcesses  (console-acoes-tab Phase C)
 *
 * Fetches the list of invokable process skills available for the current
 * contact's pool, via GET /v1/pools/:poolId/mentionable-processes.
 *
 * Each entry includes delegation_params (inline schema — no separate fetch
 * needed) and delegation_visibility.
 *
 * Re-fetches when poolId changes. Returns [] when poolId is null.
 */

import { useEffect, useState } from "react";
import { MentionableProcess } from "../types";

const API_BASE = "/v1";

export function useMentionableProcesses(poolId: string | null): MentionableProcess[] {
  const [processes, setProcesses] = useState<MentionableProcess[]>([]);

  useEffect(() => {
    if (!poolId) {
      setProcesses([]);
      return;
    }

    let cancelled = false;

    fetch(`${API_BASE}/pools/${encodeURIComponent(poolId)}/mentionable-processes`, {
      headers: { "x-tenant-id": "tenant_demo", "x-user-id": "operator" },
    })
      .then(r => (r.ok ? r.json() : { processes: [] }))
      .then((data: { processes: MentionableProcess[] }) => {
        if (!cancelled) setProcesses(data.processes ?? []);
      })
      .catch(() => { if (!cancelled) setProcesses([]); });

    return () => { cancelled = true; };
  }, [poolId]);

  return processes;
}
