/**
 * AcoesTab — console-acoes-tab spec (Arc 11 refactor)
 *
 * Two vertical sections:
 *
 *   Seção A — Ativos na Sessão
 *     Human agent card + AI participant cards.
 *
 *   Seção B — Agentes disponíveis (AcaoItemRow × N)
 *     Each row unifies invite + delegate into a single "Acionar" button
 *     with an inline YAML-driven form.
 *
 * Key behaviours of AcaoItemRow:
 *   • Expands an inline form when the operator clicks "Acionar".
 *   • If delegation_input exists → renders typed YAML fields.
 *   • If no schema and no pending → renders empty form (button immediately enabled).
 *   • delegation_visibility from YAML → locks and hides the visibility radio.
 *   • Ctrl+Enter confirms the open form.
 *   • State machine: available → expanded → pending → active → done (re-invoke).
 *
 * Note: Processos/Journey mode removed in Arc 19 Fase F.
 */

import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { User, Bot } from "lucide-react";
import {
  AiParticipantInfo,
  ChatMessage,
  DelegationField,
  DelegationSchema,
  MentionableAgent,
  SupervisorState,
} from "../../types";
import { AiParticipantCard }   from "../AiParticipantCard";
import { useDelegationSchema } from "../../hooks/useDelegationSchema";

// ── Helpers ────────────────────────────────────────────────────────────────────

function formatAgentName(id: string): string {
  return id.replace(/_v\d+$/, "").replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

/**
 * Serialize typed field values into a human-readable instruction string.
 * Empty fields are omitted.
 *   { motivo: "desconto" } → "[Motivo: Desconto]"
 */
function serializeFields(schema: DelegationSchema, values: Record<string, string>): string {
  return schema.fields
    .filter(f => (values[f.id] ?? "").trim())
    .map(f => {
      const raw = values[f.id].trim();
      const display =
        f.type === "select"
          ? (f.options?.find(o => o.value === raw)?.label ?? raw)
          : raw;
      return `[${f.label}: ${display}]`;
    })
    .join(" ");
}

// ── Agent invite state machine ─────────────────────────────────────────────────

type InviteState = "available" | "expanded" | "pending" | "active" | "done";

function resolveInviteState(
  agent:        MentionableAgent,
  participants: AiParticipantInfo[],
  pending:      Set<string>,
  expanded:     boolean,
): InviteState {
  const p = participants.find(a => a.agent_type_id === agent.agent_type_id);
  if (p) return p.ai_state.step_status === "done" ? "done" : "active";
  if (pending.has(agent.alias)) return "pending";
  if (expanded) return "expanded";
  return "available";
}

// ── FieldSelect / FieldText ───────────────────────────────────────────────────

function FieldSelect({ field, value, onChange }: {
  field: DelegationField; value: string; onChange: (v: string) => void;
}) {
  const { t } = useTranslation('agentAssist');
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className="w-full text-xs border border-border-strong rounded px-2 py-1.5
        focus:outline-none focus:ring-1 focus:ring-primary/40 bg-white text-dark"
    >
      <option value="">{t('delegar.select')}</option>
      {(field.options ?? []).map(opt => (
        <option key={opt.value} value={opt.value}>{opt.label}</option>
      ))}
    </select>
  );
}

function FieldText({ field, value, onChange }: {
  field: DelegationField; value: string; onChange: (v: string) => void;
}) {
  return (
    <input
      type={field.type === "number" ? "number" : "text"}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={field.placeholder ?? ""}
      className="w-full text-xs border border-border-strong rounded px-2 py-1.5
        focus:outline-none focus:ring-1 focus:ring-primary/40 text-dark placeholder-muted-light"
    />
  );
}

// ── AcaoItemRow ────────────────────────────────────────────────────────────────

interface AcaoItemRowProps {
  agent:       MentionableAgent;
  participants: AiParticipantInfo[];
  pendingAliases: Set<string>;
  disabled:    boolean;
  onAcionar:   (alias: string, instruction: string, visibility: "all" | "agents_only") => void;
}

const AcaoItemRow: React.FC<AcaoItemRowProps> = ({
  agent,
  participants,
  pendingAliases,
  disabled,
  onAcionar,
}) => {
  const { t } = useTranslation('agentAssist');
  const [expanded,    setExpanded]    = useState(false);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});

  const { schema, delegationVisibility, loading: schemaLoading } = useDelegationSchema(
    expanded ? agent.agent_type_id : null // only fetch when expanded
  );

  // Reset field values when schema loads
  useEffect(() => {
    if (schema) {
      const init: Record<string, string> = {};
      schema.fields.forEach(f => { init[f.id] = ""; });
      setFieldValues(init);
    }
  }, [schema]);

  const inviteState = resolveInviteState(agent, participants, pendingAliases, expanded);

  // ── Dot and button appearance ──

  const dotCls: Record<InviteState, string> = {
    available: "bg-border",
    expanded:  "bg-border animate-pulse",
    pending:   "bg-warning animate-pulse",
    active:    "bg-green",
    done:      "bg-ai",
  };

  const canAct = !disabled && inviteState !== "pending" && inviteState !== "active";

  // ── Validation ──

  function isFormValid(): boolean {
    if (schemaLoading) return false;
    if (schema) {
      return !schema.fields.some(f => f.required && !(fieldValues[f.id] ?? "").trim());
    }
    return true; // no schema → always valid
  }

  function handleConfirm() {
    if (!isFormValid()) return;
    const instruction = schema ? serializeFields(schema, fieldValues) : "";
    onAcionar(agent.alias, instruction, delegationVisibility ?? "agents_only");
    setExpanded(false);
    setFieldValues({});
  }

  function handleToggle() {
    if (!canAct) return;
    setExpanded(v => !v);
  }

  // Button label
  const btnLabel = (() => {
    if (inviteState === "active")   return t('acoes.active');
    if (inviteState === "pending")  return t('acoes.waiting');
    if (inviteState === "expanded") return t('acoes.cancel');
    return t('acoes.trigger'); // available | done
  })();

  return (
    <div className="rounded-lg border border-border bg-white overflow-hidden">
      {/* Main row */}
      <div className="flex items-center gap-2 p-2.5">
        <Bot className="w-4 h-4 text-ai flex-shrink-0" aria-hidden="true" />

        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold text-dark truncate">
            {formatAgentName(agent.agent_type_id)}
          </div>
          {agent.description && (
            <div className="text-2xs text-muted leading-snug mt-0.5 truncate">
              {agent.description}
            </div>
          )}
          <div className="text-2xs text-ai font-mono">@{agent.alias}</div>
        </div>

        {/* State dot */}
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${dotCls[inviteState]}`} />

        {/* Acionar button */}
        <button
          onClick={handleToggle}
          disabled={!canAct}
          className={[
            "px-2 py-0.5 rounded border text-2xs font-medium flex-shrink-0 transition-colors",
            inviteState === "active"
              ? "text-green-text bg-green-light border-green/30 cursor-default"
              : inviteState === "pending"
              ? "text-muted-light bg-surface-muted border-border cursor-not-allowed"
              : inviteState === "expanded"
              ? "text-muted bg-surface-muted border-border hover:bg-surface-alt"
              : "text-ai-text bg-ai-light border-ai/30 hover:bg-ai/10",
            disabled ? "opacity-40 cursor-not-allowed" : "",
          ].join(" ")}
        >
          {btnLabel}
        </button>
      </div>

      {/* Inline form — visible when expanded */}
      {expanded && inviteState === "expanded" && (
        <div
          className="px-2.5 pb-2.5 border-t border-border bg-surface-muted"
          onKeyDown={e => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleConfirm();
            if (e.key === "Escape") { setExpanded(false); setFieldValues({}); }
          }}
        >
          {schemaLoading ? (
            <div className="flex items-center gap-2 py-2">
              <div className="w-3 h-3 border-2 border-primary/40 border-t-transparent rounded-full animate-spin" />
              <span className="text-2xs text-muted-light">{t('delegar.loadingParams')}</span>
            </div>
          ) : schema && schema.fields.length > 0 ? (
            /* ── Typed YAML fields ── */
            <div className="flex flex-col gap-2 mt-2">
              {schema.fields.map(field => (
                <div key={field.id}>
                  <label className="block text-2xs font-semibold text-muted mb-0.5">
                    {field.label}
                    {field.required && <span className="ml-1 text-red">*</span>}
                  </label>
                  {field.type === "select" ? (
                    <FieldSelect
                      field={field}
                      value={fieldValues[field.id] ?? ""}
                      onChange={v => setFieldValues(prev => ({ ...prev, [field.id]: v }))}
                    />
                  ) : (
                    <FieldText
                      field={field}
                      value={fieldValues[field.id] ?? ""}
                      onChange={v => setFieldValues(prev => ({ ...prev, [field.id]: v }))}
                    />
                  )}
                </div>
              ))}
            </div>
          ) : null /* no schema → no fields, button immediately enabled */ }

          {/* Confirm / Cancel */}
          <div className="flex gap-1.5 mt-2">
            <button
              onClick={handleConfirm}
              disabled={!isFormValid()}
              className="flex-1 py-1 text-2xs font-semibold text-white
                bg-ai hover:bg-ai-text rounded transition-colors
                disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {t('acoes.trigger')}
            </button>
            <button
              onClick={() => { setExpanded(false); setFieldValues({}); }}
              className="px-2 py-1 text-2xs text-muted border border-border
                rounded hover:bg-surface-alt transition-colors"
            >
              {t('acoes.cancel')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

// ── HumanAgentCard (unchanged from AgentesTab) ────────────────────────────────

interface HumanAgentCardProps {
  agentName:                string;
  sessionClosed:            boolean;
  substitutionMode:         boolean;
  onToggleSubstitutionMode: () => void;
}

const HumanAgentCard: React.FC<HumanAgentCardProps> = ({
  agentName,
  sessionClosed,
  substitutionMode,
  onToggleSubstitutionMode,
}) => {
  const { t } = useTranslation('agentAssist');
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (!menuOpen) return;
    function handler(e: MouseEvent) {
      const target = e.target as HTMLElement;
      if (!target.closest("[data-human-card-menu]")) setMenuOpen(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  return (
    <div className="rounded-lg border border-primary/30 bg-primary-light p-2.5 relative">
      <div className="flex items-center gap-1.5">
        <User className="w-4 h-4 text-primary flex-shrink-0" aria-hidden="true" />
        <span className="text-xs font-semibold text-dark truncate flex-1">{agentName}</span>
        <span className="text-2xs font-medium px-1.5 py-0.5 rounded-full bg-primary-light text-primary flex-shrink-0">
          primary
        </span>

        <div data-human-card-menu className="relative flex-shrink-0">
          <button
            onClick={() => setMenuOpen(v => !v)}
            className="text-muted-light hover:text-muted w-6 h-6 flex items-center justify-center
              rounded hover:bg-white/60 text-sm leading-none"
            title={t('agentes.options')}
          >
            ···
          </button>
          {menuOpen && (
            <div className="absolute right-0 top-full mt-1 z-10 bg-white border border-border
              rounded-lg shadow-lg min-w-[150px] py-1 overflow-hidden">
              <button
                onClick={() => { onToggleSubstitutionMode(); setMenuOpen(false); }}
                disabled={sessionClosed}
                className={[
                  "w-full text-left px-3 py-1.5 text-xs transition-colors",
                  "text-warning-text hover:bg-warning-light",
                  "disabled:opacity-40 disabled:cursor-not-allowed",
                ].join(" ")}
              >
                {substitutionMode ? t('agentes.substitution.stop') : t('agentes.substitution.start')}
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
        <span className={[
          "text-2xs font-semibold border rounded px-1.5 py-0.5",
          sessionClosed
            ? "bg-surface-muted text-muted border-border"
            : "bg-green-light text-green-text border-green/30",
        ].join(" ")}>
          {sessionClosed ? t('agentes.status.closed') : t('agentes.status.attending')}
        </span>
        {substitutionMode && (
          <span className="text-2xs bg-warning-light text-warning-text border border-warning/30 rounded px-1.5 py-0.5">
            {t('agentes.substituting')}
          </span>
        )}
      </div>
    </div>
  );
};

// ── Props ──────────────────────────────────────────────────────────────────────

export interface AcoesTabProps {
  agentName:                string;
  // Seção A
  supervisorState:          SupervisorState | null;
  sessionMessages:          ChatMessage[];
  onTerminateSegment?:      (instanceId: string) => void;
  substitutionMode:         boolean;
  onToggleSubstitutionMode: () => void;
  // Seção B — Agentes
  mentionableAgents:        MentionableAgent[];
  onAddSpecialist:          (alias: string, instruction: string, visibility: "all" | "agents_only") => void;
  // Session state
  sessionClosed:            boolean;
  hasContact:               boolean;
}

// ── Main component ─────────────────────────────────────────────────────────────

export const AcoesTab: React.FC<AcoesTabProps> = ({
  agentName,
  supervisorState,
  sessionMessages,
  onTerminateSegment,
  substitutionMode,
  onToggleSubstitutionMode,
  mentionableAgents,
  onAddSpecialist,
  sessionClosed,
  hasContact,
}) => {
  const { t } = useTranslation('agentAssist');
  const [pendingAliases, setPendingAliases] = useState<Set<string>>(new Set());

  const aiParticipants = supervisorState?.ai_participants ?? [];

  // Clear pending state when agents join
  useEffect(() => {
    if (pendingAliases.size === 0) return;
    const joinedTypes = new Set(aiParticipants.map(p => p.agent_type_id));
    const stillPending = new Set(
      [...pendingAliases].filter(alias => {
        const agent = mentionableAgents.find(a => a.alias === alias);
        return agent ? !joinedTypes.has(agent.agent_type_id) : false;
      })
    );
    if (stillPending.size !== pendingAliases.size) setPendingAliases(stillPending);
  }, [aiParticipants, mentionableAgents, pendingAliases]);

  function handleAcionar(alias: string, instruction: string, visibility: "all" | "agents_only") {
    onAddSpecialist(alias, instruction, visibility);
    setPendingAliases(prev => new Set([...prev, alias]));
  }

  return (
    <div className="flex flex-col gap-4 p-3 overflow-y-auto h-full">

      {/* ── Seção A: Ativos na Sessão ── */}
      <section>
        <h3 className="text-2xs font-bold text-muted-light uppercase tracking-widest mb-2">
          {t('agentes.activeInSession')}
        </h3>
        <div className="flex flex-col gap-2">
          <HumanAgentCard
            agentName={agentName}
            sessionClosed={sessionClosed}
            substitutionMode={substitutionMode}
            onToggleSubstitutionMode={onToggleSubstitutionMode}
          />
          {aiParticipants.map(p => (
            <AiParticipantCard
              key={p.instance_id}
              participant={p}
              sessionMessages={sessionMessages}
              onTerminateSegment={onTerminateSegment}
            />
          ))}
          {aiParticipants.length === 0 && (
            <p className="text-xs text-muted-light text-center py-1 italic">
              {t('agentes.noAiAgents')}
            </p>
          )}
        </div>
      </section>

      {/* ── Seção B: Agentes disponíveis ── */}
      <section>
        {mentionableAgents.length === 0 ? (
          <p className="text-xs text-muted-light text-center py-2 italic">
            {t('agentes.noAgentsAvailable')}
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {mentionableAgents.map(agent => (
              <AcaoItemRow
                key={agent.alias}
                agent={agent}
                participants={aiParticipants}
                pendingAliases={pendingAliases}
                disabled={!hasContact || sessionClosed}
                onAcionar={handleAcionar}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
};
