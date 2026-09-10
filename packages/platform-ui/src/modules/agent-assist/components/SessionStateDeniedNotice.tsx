import { useTranslation } from "react-i18next";
import { ShieldAlert, CircleSlash } from "lucide-react";
import type { RecusaDeEstado } from "../hooks/useSupervisorState";

/**
 * SessionStateDeniedNotice — a tela DIZ que o vazio e recusa, em vez de so ficar vazia.
 *
 * ── Por que existe (AUT-48, 2026-09-09) ─────────────────────────────────────
 *
 * A AUT-47 pos eixo de POOL no `/api/supervisor_state/{id}` — ate entao qualquer
 * portador de `agent_assist.atender` lia o estado de QUALQUER sessao, e o corpo levava
 * junto o `resume_token` da tarefa (medido: `supervisor@`, fora do pool, recebia 200
 * COM a credencial). Fechado o portao, a recusa passou a acontecer — e o
 * `useSupervisorState` a engolia (`if (res.ok)` sem `else`), entao o Console ficava com
 * o painel vazio, para sempre, sem dizer por que.
 *
 * É a mesma decisao do `EmptyScopeNotice` (AUT-10): *"nao vejo nada" e o sintoma que
 * chega ao suporte*, e o servidor ja loga o motivo — esta e a metade da frente.
 *
 * ⚠️ **As quatro respostas do servidor NAO tem a mesma cara**, e e isso que o
 * componente existe para preservar:
 *
 *   403 pool_not_accessible ........... e recusa, e o caminho e pedir escopo
 *   403 session_scope_undeterminable .. e recusa, mas ninguem tem o que conceder
 *   403 tenant_mismatch ............... e recusa, e nao ha caminho nenhum
 *   404 session_expired_or_unknown .... **nao e recusa**: e ausencia
 *
 * Colapsar o 404 nos 403 mandaria o operador pedir permissao para ver uma sessao que
 * nao existe mais — a mesma familia de "duas ausencias com a mesma cara" que o portao
 * do servidor separou.
 *
 * ⚠️ AFORDANCIA, nunca fronteira: quem decide e o servidor. Isto so nomeia o que ele
 * ja decidiu.
 */
export function SessionStateDeniedNotice({ recusa }: { recusa: RecusaDeEstado }) {
  const { t } = useTranslation("agentAssist");
  const ausencia = recusa.status === 404;
  const chave =
    recusa.reason === "pool_not_accessible"          ? "poolNotAccessible" :
    recusa.reason === "session_scope_undeterminable" ? "scopeUndeterminable" :
    recusa.reason === "tenant_mismatch"              ? "tenantMismatch" :
    recusa.reason === "session_expired_or_unknown"   ? "expired" :
    "unknown";

  const Icone = ausencia ? CircleSlash : ShieldAlert;
  const cor = ausencia
    ? "border-border bg-surface"
    : "border-warning/40 bg-warning-light/40";
  const corTitulo = ausencia ? "text-muted" : "text-warning-text";

  return (
    <div className={`mx-3 mt-2 rounded-lg border px-3 py-2 ${cor}`} role="status">
      <p className={`flex items-center gap-1.5 text-xs font-medium ${corTitulo}`}>
        <Icone className="w-4 h-4 shrink-0" aria-hidden="true" />
        {t(`stateDenied.${chave}.title`)}
      </p>
      <p className="text-muted mt-0.5 text-xs leading-snug">
        {t(`stateDenied.${chave}.body`)}
      </p>
      {/* O status cru fica visivel de proposito: e o que o operador repassa ao
          suporte, e o que distingue esta tela de uma que simplesmente falhou. */}
      <p className="text-muted-light mt-0.5 text-[11px]">
        {t("stateDenied.code", { status: recusa.status, reason: recusa.reason || "-" })}
      </p>
    </div>
  );
}
