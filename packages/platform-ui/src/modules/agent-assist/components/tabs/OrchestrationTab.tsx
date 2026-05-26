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
import { useTranslation } from "react-i18next";
import { Pause, Search, Check } from "lucide-react";
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
  collect:  "📞",
  choice:   "🔀",
  escalate: "🚨",
  complete: "✅",
};

// Step types whose icon is a lucide component (overrides STEP_TYPE_ICON)
const STEP_TYPE_LUCIDE: Record<string, React.FC<{ className?: string }>> = {
  suspend: Pause,
  resolve: Search,
};

// ── Inject context form ────────────────────────────────────────────────────────

interface InjectFormProps {
  sessionId: string;
  mcpBase:   string;
  onDone:    () => void;
}

const InjectContextForm: React.FC<InjectFormProps> = ({ sessionId, mcpBase, onDone }) => {
  const { t } = useTranslation('agentAssist');
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
      <div className="flex items-center gap-2 text-green-text text-xs py-2">
        <Check className="w-3.5 h-3.5" aria-hidden="true" />
        <span>{t('orchestration.contextInjected')}</span>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-2 mt-2">
      <div className="flex gap-2">
        <input
          value={key}
          onChange={e => setKey(e.target.value)}
          placeholder={t('orchestration.keyPlaceholder')}
          className="flex-1 text-xs border border-border-strong rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/40 font-mono"
          disabled={busy}
        />
        <input
          value={conf}
          onChange={e => setConf(e.target.value)}
          placeholder="conf"
          className="w-14 text-xs border border-border-strong rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/40 text-center"
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
        placeholder={t('orchestration.valuePlaceholder')}
        rows={2}
        className="text-xs border border-border-strong rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/40 resize-none"
        disabled={busy}
      />
      {error && <p className="text-xs text-red-text">{error}</p>}
      <button
        type="submit"
        disabled={busy || !key.trim() || !value.trim()}
        className="self-end text-xs px-3 py-1.5 bg-primary text-white rounded hover:bg-primary-dark
                   disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        {busy ? t('orchestration.injecting') : t('orchestration.inject')}
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
  const { t } = useTranslation('agentAssist');
  const [busy,     setBusy]     = useState(false);
  const [error,    setError]    = useState<string | null>(null);
  const [ok,       setOk]       = useState(false);
  const [confirm,  setConfirm]  = useState(false);

  if (!confirm) {
    return (
      <button
        onClick={() => setConfirm(true)}
        className="w-full py-2 px-3 text-xs font-medium text-red border border-red/30
                   rounded-lg hover:bg-red-light transition-colors"
      >
        {t('orchestration.forcePipelineBtn')}
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
      <div className="flex items-center gap-2 text-green-text text-xs py-2">
        <Check className="w-3.5 h-3.5" aria-hidden="true" />
        <span>{t('orchestration.pipelineClosed')}</span>
      </div>
    );
  }

  return (
    <div className="border border-red/30 rounded-lg p-3 bg-red-light flex flex-col gap-2">
      <p className="text-xs text-red-text font-medium">
        {t('orchestration.forceCompleteConfirm')}
      </p>
      {error && <p className="text-xs text-red-text">{error}</p>}
      <div className="flex gap-2">
        <button
          onClick={() => setConfirm(false)}
          className="flex-1 py-1.5 text-xs text-muted border border-border-strong rounded hover:bg-surface-muted transition-colors"
          disabled={busy}
        >
          {t('orchestration.cancel')}
        </button>
        <button
          onClick={handleConfirm}
          className="flex-1 py-1.5 text-xs text-white bg-red rounded hover:bg-red-text transition-colors disabled:opacity-50"
          disabled={busy}
        >
          {busy ? t('orchestration.closing') : t('orchestration.confirm')}
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
  const { t } = useTranslation('agentAssist');
  const [showInjectForm, setShowInjectForm] = useState(false);

  if (!supervisorState) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-muted-light p-4">
        {t('orchestration.waiting')}
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
        <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
          {t('orchestration.aiAgents')}
        </h3>
        {participants.length === 0 ? (
          <p className="text-xs text-muted-light">{t('orchestration.noAiAgents')}</p>
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
        <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
          {t('orchestration.stepHistory', { count: transitions.length })}
        </h3>
        {transitions.length === 0 ? (
          <p className="text-xs text-muted-light">{t('orchestration.noTransitions')}</p>
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
                  className="flex items-start gap-2 text-xs bg-surface-muted border border-border rounded px-2 py-1.5"
                >
                  <span className="flex-shrink-0 mt-0.5">
                    {(() => {
                      const LucideIcon = STEP_TYPE_LUCIDE[typeKey];
                      return LucideIcon
                        ? <LucideIcon className="w-3 h-3" aria-hidden="true" />
                        : (STEP_TYPE_ICON[typeKey] ?? "▸");
                    })()}
                  </span>
                  <div className="flex-1 min-w-0">
                    {t.from_step && (
                      <span className="text-muted-light mr-1">{formatStepLabel(t.from_step)} →</span>
                    )}
                    <span className="font-medium text-dark">{formatStepLabel(t.to_step)}</span>
                    {t.reason && (
                      <span className="ml-1 text-muted-light">({t.reason})</span>
                    )}
                  </div>
                  <span className="text-muted-light flex-shrink-0">{formatTime(t.timestamp)}</span>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* ── Supervisor interventions ── */}
      <section className="border-t border-border pt-4">
        <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-3">
          {t('orchestration.supervisorInterventions')}
        </h3>

        {/* Inject context */}
        <div className="mb-4">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs font-medium text-dark">{t('orchestration.injectContext')}</span>
            <button
              onClick={() => setShowInjectForm(v => !v)}
              className="text-xs text-primary hover:underline"
            >
              {showInjectForm ? t('orchestration.closeForm') : t('orchestration.openForm')}
            </button>
          </div>
          <p className="text-xs text-muted-light">
            {t('orchestration.injectContextDesc')}
          </p>
          {showInjectForm && (
            <InjectContextForm sessionId={sessionId} mcpBase={mcpBase} onDone={handleActionDone} />
          )}
        </div>

        {/* Force complete */}
        <div>
          <span className="text-xs font-medium text-dark block mb-1">{t('orchestration.forceComplete')}</span>
          <p className="text-xs text-muted-light mb-2">
            {t('orchestration.forceCompleteDesc')}
          </p>
          <ForceCompleteConfirm sessionId={sessionId} mcpBase={mcpBase} onDone={handleActionDone} />
        </div>
      </section>
    </div>
  );
};
