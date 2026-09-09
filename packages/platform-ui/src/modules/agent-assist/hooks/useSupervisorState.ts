/**
 * useSupervisorState
 * Polls mcp-server-plughub supervisor_state tool via the REST API proxy.
 * Fires once when sessionId is available, then re-fires on every new WS event
 * (so state is always fresh after each customer turn).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { SupervisorState, WsServerEvent } from "../types";

/**
 * Recusa do servidor ao servir o estado da sessao (AUT-47). `reason` e o do backend —
 * `pool_not_accessible` · `session_scope_undeterminable` · `tenant_mismatch` —, e 404
 * com `session_expired_or_unknown` NAO e recusa: e ausencia, e as duas nao podem
 * chegar a tela com a mesma cara.
 */
export interface RecusaDeEstado {
  status: number;
  reason: string;
}
import { getAccessToken } from "../../../auth/token-store";

const API_BASE = "/api";

export function useSupervisorState(
  sessionId: string | null,
  lastEvent: WsServerEvent | null
): { state: SupervisorState | null; refresh: () => void; recusa: RecusaDeEstado | null } {
  const [state, setState] = useState<SupervisorState | null>(null);
  const [recusa, setRecusa] = useState<RecusaDeEstado | null>(null);
  const fetchingRef = useRef(false);

  const fetchState = useCallback(async () => {
    if (!sessionId || fetchingRef.current) return;
    fetchingRef.current = true;
    try {
      // The mcp-server /api/supervisor_state route requires a JWT (requireJwtRole).
      // Without the Bearer token it returns 401 → customer_context (and thus the
      // resolved caller.customer_id / context_snapshot) never reaches the console,
      // which surfaced as "customer not identified" even with the tag in ContextStore.
      const token = getAccessToken();
      const res = await fetch(`${API_BASE}/supervisor_state/${sessionId}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.ok) {
        const data = (await res.json()) as SupervisorState;
        setState(data);
        setRecusa(null);
      } else {
        // ⚠️ AUT-47 (2026-09-09): aqui NAO havia `else`. Um 401/403 nao virava estado
        // nem mensagem, e o Console ficava com o painel vazio para sempre — a tela
        // dizia "nao ha contexto" quando o servidor tinha dito "voce nao pode". Com o
        // portao de POOL deste endpoint isso passou a acontecer mais vezes, e uma
        // recusa muda e pior que a recusa: manda o operador procurar dado onde falta
        // permissao. O `reason` vem do servidor (`pool_not_accessible`,
        // `session_scope_undeterminable`, `tenant_mismatch`) e viaja no retorno para
        // quem for renderizar o aviso.
        let motivo = "";
        try {
          motivo = String(((await res.json()) as { reason?: unknown })?.reason ?? "");
        } catch { /* corpo vazio/nao-JSON: o status ja diz o bastante */ }
        setRecusa({ status: res.status, reason: motivo });
        console.warn(
          `[supervisor_state] ${res.status}${motivo ? ` ${motivo}` : ""} — o estado ` +
          `desta sessao nao foi servido. Nao e "sem contexto": e recusa do servidor.`,
        );
      }
    } catch {
      // ignore transient errors — stale state is acceptable
    } finally {
      fetchingRef.current = false;
    }
  }, [sessionId]);

  // Initial fetch + short retries when sessionId arrives.
  // R0 form-fill (wrap-up/aprovação): um contato reivindicado é uma workflow
  // SUSPENSA, sem mensagens de cliente — logo NÃO há evento de WS
  // (message.text/menu.render) que dispare o re-fetch abaixo. Se o primeiro fetch
  // corre com o claim (context_snapshot ainda não legível / transitório), o Console
  // fica no chat "para sempre" (sem re-fetch), em vez de renderizar o DialogForm.
  // Alguns re-fetches curtos garantem que o snapshot (dialog_form_id + resume_token)
  // seja pego logo após o claim. Barato e idempotente (fetchingRef evita concorrência).
  useEffect(() => {
    if (!sessionId) return;
    fetchState();
    const t1 = setTimeout(fetchState, 700);
    const t2 = setTimeout(fetchState, 1800);
    const t3 = setTimeout(fetchState, 3500);
    return () => { clearTimeout(t1); clearTimeout(t2); clearTimeout(t3); };
  }, [fetchState, sessionId]);

  // Re-fetch on events that signal new content or updated AI analysis.
  useEffect(() => {
    if (!lastEvent) return;
    if (
      lastEvent.type === "message.text" ||
      lastEvent.type === "menu.render" ||
      lastEvent.type === "supervisor_state.updated"
    ) {
      fetchState();
    }
  }, [lastEvent, fetchState]);

  return { state, refresh: fetchState, recusa };
}
