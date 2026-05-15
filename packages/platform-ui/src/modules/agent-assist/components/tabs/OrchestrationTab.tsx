/**
 * OrchestrationTab  (Arc 11 Fase D — F4)
 *
 * Supervisor view of the active Skill-Flow pipeline in a session.
 * Shows:
 *   - AI participants with step state (re-uses AiParticipantCard)
 *   - Pipeline transition timeline (step history)
 *   - Supervisor actions: inject context into ContextStore, force-complete pipeline
 *
 * Permissions: requires role "supervisor" — AgentAssistPage gates this tab.
 * API calls go directly to mcp-server-plughub REST endpoints:
 *   POST /api/inject-context/:sessionId
 *   POST /api/force-complete/:sessionId
 */

import React, { useState } from "react";
import { AiParticipantInfo, ChatMessage, PipelineTransition, SupervisorState } from "../../types";
import { AiParticipantCard } from "../AiParticipantCard";

// ── Helpers ────────────────────────────────────────────────────────────────────

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch { return iso; }
}

function formatStepLabel(stepId: string | null): string {
  if (!stepId) return "—";
  return stepId.replace(/_/g, " ");
}

const STEP_TYPE_ICON: Record<string, string> = {
  reason:   "🧠",
  invoke:   "🔧",
  task:     "📋",
  notify:   "📢",
  menu:     "📝",
  receive:  "📨",
  suspend:  "⏸",
  collect:  "📞",
  choice:   "🔀",
  escalate: "🚨",
  resolve:  "🔍",
  complete: "✅",
};

// ── Inject context form ────────────────────────────────────────────────────────

interface InjectFormProps {
  sessionId: string;
  mcpBase:   string;
  onDone:    () => void;
}

const InjectContextForm: React.FC<InjectFormProps> = ({ sessionId, mcpBase, onDone }) => {
  const [key,   setKey]   = useState("");
  const [value, setValue] = useState("");
  const [conf,  setConf]  = useState("0.9");
  const [busy,  setBusy]  = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok,    setOk]    = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!key.trim() || !value.trim()) return;
    setBusy(true); setError(null);
    try {
      const res = await fetch(`${mcpBase}/api/inject-context/${sessionId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: key.trim(), value: value.trim(), confidence: Number(conf) || 0.9 }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setOk(true);
      setTimeout(() => { setOk(false); onDone(); }, 1200);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  if (ok) {
    return (
      <div className="flex items-center gap-2 text-green-700 text-xs py-2">
        <span>✓</span>
        <span>Contexto injetado com sucesso.</span>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-2 mt-2">
      <div className="flex gap-2">
        <input
          value={key}
          onChange={e => setKey(e.target.value)}
          placeholder="chave (ex: caller.nome)"
          className="flex-1 text-xs border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400 font-mono"
          disabled={busy}
        />
        <input
          value={conf}
          onChange={e => setConf(e.target.value)}
          placeholder="conf"
          className="w-14 text-xs border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400 text-center"
          disabled={busy}
          type="number"
          min="0"
          max="1"
          step="0.1"
        />
      </div>
      <textarea
        value={value}
        onChange={e => setValue(e.target.value)}
        placeholder="valor"
        rows={2}
        className="text-xs border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400 resize-none"
        disabled={busy}
      />
      {error && <p className="text-[11px] text-red-600">{error}</p>}
      <button
        type="submit"
        disabled={busy || !key.trim() || !value.trim()}
        className="self-end text-xs px-3 py-1.5 bg-indigo-600 text-white rounded hover:bg-indigo-700
                   disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        {busy ? "Injetando…" : "Injetar"}
      </button>
    </form>
  );
};

// ── Force complete confirm ─────────────────────────────────────────────────────

interface ForceCompleteProps {
  sessionId: string;
  mcpBase:   string;
  onDone:    () => void;
}

const ForceCompleteConfirm: React.FC<ForceCompleteProps> = ({ sessionId, mcpBase, onDone }) => {
  const [busy,     setBusy]     = useState(false);
  const [error,    setError]    = useState<string | null>(null);
  const [ok,       setOk]       = useState(false);
  const [confirm,  setConfirm]  = useState(false);

  if (!confirm) {
    return (
      <button
        onClick={() => setConfirm(true)}
        className="w-full py-2 px-3 text-xs font-medium text-red-600 border border-red-300
                   rounded-lg hover:bg-red-50 transition-colors"
      >
        ⚡ Force-complete pipeline
      </button>
    );
  }

  const handleConfirm = async () => {
    setBusy(true); setError(null);
    try {
      const res = await fetch(`${mcpBase}/api/force-complete/${sessionId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "supervisor_force_complete", outcome: "resolved" }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setOk(true);
      setTimeout(() => { setOk(false); onDone(); }, 1500);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  if (ok) {
    return (
      <div className="flex items-center gap-2 text-green-700 text-xs py-2">
        <span>✓</span>
        <span>Pipeline encerrado com sucesso.</span>
      </div>
    );
  }

  return (
    <div className="border border-red-200 rounded-lg p-3 bg-red-50 flex flex-col gap-2">
      <p className="text-xs text-red-800 font-medium">
        Isso forçará o encerramento do Skill-Flow. Confirma?
      </p>
      {error && <p className="text-[11px] text-red-600">{error}</p>}
      <div className="flex gap-2">
        <button
          onClick={() => setConfirm(false)}
          className="flex-1 py-1.5 text-xs text-gray-600 border border-gray-300 rounded hover:bg-gray-50 transition-colors"
          disabled={busy}
        >
          Cancelar
        </button>
        <button
          onClick={handleConfirm}
          className="flex-1 py-1.5 text-xs text-white bg-red-600 rounded hover:bg-red-700 transition-colors disabled:opacity-50"
          disabled={busy}
        >
          {busy ? "Encerrando…" : "Confirmar"}
        </button>
      </div>
    </div>
  );
};

// ── OrchestrationTab ───────────────────────────────────────────────────────────

interface OrchestrationTabProps {
  supervisorState:     SupervisorState | null;
  sessionMessages?:    ChatMessage[];
  onTerminateSegment?: (instanceId: string) => void;
  /** Base URL for mcp-server-plughub REST (e.g. "http://localhost:3100") */
  mcpBase?:            string;
  /** Called after a successful inject-context or force-complete to trigger a state refresh */
  onRefresh?:          () => void;
}

export const OrchestrationTab: React.FC<OrchestrationTabProps> = ({
  supervisorState,
  sessionMessages = [],
  onTerminateSegment,
  mcpBase = "",
  onRefresh,
}) => {
  const [showInjectForm, setShowInjectForm] = useState(false);

  if (!supervisorState) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-gray-400 p-4">
        Aguardando dados da sessão…
      </div>
    );
  }

  const participants:  AiParticipantInfo[]   = supervisorState.ai_participants ?? [];
  const transitions:   PipelineTransition[]  = supervisorState.pipeline_transitions ?? [];
  const sessionId = supervisorState.session_id;

  const handleActionDone = () => {
    setShowInjectForm(false);
    onRefresh?.();
  };

  return (
    <div className="flex flex-col gap-4 p-3 overflow-y-auto h-full">

      {/* ── AI Agents ── */}
      <section>
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Agentes AI activos
        </h3>
        {participants.length === 0 ? (
          <p className="text-xs text-gray-400">Nenhum agente AI ativo nesta sessão.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {participants.map(p => (
              <AiParticipantCard
                key={p.instance_id}
                participant={p}
                sessionMessages={sessionMessages}
                onTerminateSegment={onTerminateSegment}
              />
            ))}
          </div>
        )}
      </section>

      {/* ── Pipeline transitions ── */}
      <section>
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Histórico de steps ({transitions.length})
        </h3>
        {transitions.length === 0 ? (
          <p className="text-xs text-gray-400">Nenhuma transição registrada.</p>
        ) : (
          <div className="flex flex-col gap-1.5 max-h-52 overflow-y-auto">
            {[...transitions].reverse().map((t, i) => {
              // Infer step type from to_step name for icon
              const lower = (t.to_step ?? "").toLowerCase();
              let typeKey = "invoke";
              for (const k of Object.keys(STEP_TYPE_ICON)) {
                if (lower.startsWith(k) || lower.includes(`_${k}_`) || lower.endsWith(`_${k}`)) {
                  typeKey = k; break;
                }
              }
              return (
                <div
                  key={i}
                  className="flex items-start gap-2 text-[11px] bg-gray-50 border border-gray-100 rounded px-2 py-1.5"
                >
                  <span className="flex-shrink-0 mt-0.5">{STEP_TYPE_ICON[typeKey] ?? "▸"}</span>
                  <div className="flex-1 min-w-0">
                    {t.from_step && (
                      <span className="text-gray-400 mr-1">{formatStepLabel(t.from_step)} →</span>
                    )}
                    <span className="font-medium text-gray-700">{formatStepLabel(t.to_step)}</span>
                    {t.reason && (
                      <span className="ml-1 text-gray-400">({t.reason})</span>
                    )}
                  </div>
                  <span className="text-gray-400 flex-shrink-0">{formatTime(t.timestamp)}</span>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* ── Supervisor interventions ── */}
      <section className="border-t border-gray-100 pt-4">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
          Intervenções de supervisor
        </h3>

        {/* Inject context */}
        <div className="mb-4">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs font-medium text-gray-700">💉 Injetar contexto</span>
            <button
              onClick={() => setShowInjectForm(v => !v)}
              className="text-[11px] text-indigo-600 hover:underline"
            >
              {showInjectForm ? "Fechar" : "Abrir"}
            </button>
          </div>
          <p className="text-[11px] text-gray-400">
            Escreve uma chave no ContextStore desta sessão (confiança configurável).
          </p>
          {showInjectForm && (
            <InjectContextForm sessionId={sessionId} mcpBase={mcpBase} onDone={handleActionDone} />
          )}
        </div>

        {/* Force complete */}
        <div>
          <span className="text-xs font-medium text-gray-700 block mb-1">⚡ Forçar encerramento</span>
          <p className="text-[11px] text-gray-400 mb-2">
            Marca o pipeline como "completed" no Redis. Use apenas em emergências.
          </p>
          <ForceCompleteConfirm sessionId={sessionId} mcpBase={mcpBase} onDone={handleActionDone} />
        </div>
      </section>
    </div>
  );
};
