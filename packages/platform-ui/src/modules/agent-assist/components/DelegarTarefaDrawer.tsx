/**
 * DelegarTarefaDrawer — Arc 11 Fase C (F3) + delegation_input + console-acoes-tab
 *
 * Slide-in drawer for delegating a task to an AI specialist agent.
 * Opened from the message selection toolbar (context menu).
 *
 * Behaviour:
 *   1. Operator selects an agent from the list of mentionable agents.
 *   2. If the agent's skill defines delegation_input, typed fields are rendered
 *      (select, text, number). No typed fields AND no prefilledContext → button
 *      enabled immediately with no text input shown.
 *   3. Visibility is owned by the agent's YAML (delegation_visibility) — no UI choice.
 *      Default: agents_only. Resolved silently and passed to onDelegate.
 *   4. Submits → calls onDelegate(alias, instruction, visibility).
 *
 * Typed fields are serialized to a human-readable instruction string:
 *   → "[objetivo: Sugerir resposta ao cliente] [contexto_extra: fatura em aberto]"
 */

import React, { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Upload, Bot, X, Check, FileText } from "lucide-react";
import { DelegationField, DelegationSchema, MentionableAgent } from "../types";
import { useDelegationSchema } from "../hooks/useDelegationSchema";

export interface DelegarTarefaDrawerProps {
  open:            boolean;
  agents:          MentionableAgent[];
  /** Pre-filled context from selected messages (optional) */
  prefilledContext?: string;
  onDelegate: (alias: string, instruction: string, visibility: "all" | "agents_only") => void;
  onClose:    () => void;
}

function formatAgentName(id: string): string {
  return id
    .replace(/_v\d+$/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, c => c.toUpperCase());
}

/**
 * Serialize typed field values into a readable instruction string.
 * e.g. { objetivo: "Sugerir resposta", contexto_extra: "fatura em aberto" }
 *   → "[objetivo: Sugerir resposta] [contexto_extra: fatura em aberto]"
 * Empty fields are omitted.
 */
function serializeFields(
  schema:  DelegationSchema,
  values:  Record<string, string>,
  context: string,
): string {
  const parts: string[] = [];

  for (const field of schema.fields) {
    const raw = (values[field.id] ?? "").trim();
    if (!raw) continue;
    // For select fields use the option label (not the value) for readability
    let display = raw;
    if (field.type === "select" && field.options) {
      const opt = field.options.find(o => o.value === raw);
      if (opt) display = opt.label;
    }
    parts.push(`[${field.label}: ${display}]`);
  }

  if (context.trim()) parts.push(context.trim());
  return parts.join(" ");
}

// ── Typed field renderers ─────────────────────────────────────────────────────

function FieldSelect({ field, value, onChange }: {
  field:    DelegationField;
  value:    string;
  onChange: (v: string) => void;
}) {
  const { t } = useTranslation('agentAssist');
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className="w-full text-xs border border-border-strong rounded-lg px-3 py-2
        focus:outline-none focus:ring-2 focus:ring-primary/40 bg-white text-dark"
    >
      <option value="">{t('delegar.select')}</option>
      {(field.options ?? []).map(opt => (
        <option key={opt.value} value={opt.value}>{opt.label}</option>
      ))}
    </select>
  );
}

function FieldText({ field, value, onChange }: {
  field:    DelegationField;
  value:    string;
  onChange: (v: string) => void;
}) {
  return (
    <input
      type={field.type === "number" ? "number" : "text"}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={field.placeholder ?? ""}
      className="w-full text-xs border border-border-strong rounded-lg px-3 py-2
        focus:outline-none focus:ring-2 focus:ring-primary/40 text-dark placeholder-muted-light"
    />
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export const DelegarTarefaDrawer: React.FC<DelegarTarefaDrawerProps> = ({
  open,
  agents,
  prefilledContext = "",
  onDelegate,
  onClose,
}) => {
  const { t } = useTranslation('agentAssist');
  const [pickedAgent,  setPickedAgent]  = useState<MentionableAgent | null>(null);
  const [freeText,     setFreeText]     = useState(prefilledContext);
  const [fieldValues,  setFieldValues]  = useState<Record<string, string>>({});
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Fetch delegation schema + delegation_visibility whenever the picked agent changes
  const { schema, delegationVisibility, loading: schemaLoading } = useDelegationSchema(
    pickedAgent?.agent_type_id ?? null
  );

  // Reset on open / context change
  useEffect(() => {
    if (open) {
      setFreeText(prefilledContext);
      setPickedAgent(null);
      setFieldValues({});
    }
  }, [open, prefilledContext]);

  // Reset field values when schema changes (new agent picked)
  useEffect(() => {
    if (schema) {
      const initial: Record<string, string> = {};
      schema.fields.forEach(f => { initial[f.id] = ""; });
      setFieldValues(initial);
    }
  }, [schema]);

  // Focus textarea (free-text mode) when agent is picked
  useEffect(() => {
    if (pickedAgent && !schema) {
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  }, [pickedAgent, schema]);

  // Keyboard: Escape closes
  useEffect(() => {
    if (!open) return;
    function handler(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  // ── Validation ──────────────────────────────────────────────────────────────

  function isValid(): boolean {
    if (!pickedAgent) return false;
    if (schemaLoading) return false;
    if (schema) {
      // Only required fields must be filled — no free-text requirement
      return !schema.fields.some(f => f.required && !(fieldValues[f.id] ?? "").trim());
    }
    // No schema: if no prefilledContext the button is immediately enabled (no input shown)
    if (!prefilledContext) return true;
    // With prefilledContext, the textarea is shown — require it not to be empty
    return freeText.trim().length > 0;
  }

  function handleSubmit() {
    if (!pickedAgent || !isValid()) return;

    const instruction = schema
      ? serializeFields(schema, fieldValues, freeText)
      : freeText.trim();

    // Use alias (e.g. "auth") as the @mention target — mcp-server resolves it via mentionable_pools.
    // Visibility is owned by the agent's YAML (delegation_visibility); default: agents_only.
    onDelegate(pickedAgent.alias, instruction, delegationVisibility ?? "agents_only");
    setPickedAgent(null);
    setFreeText(prefilledContext);
    setFieldValues({});
  }

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/20"
        onClick={onClose}
      />

      {/* Drawer panel */}
      <div className="fixed right-0 top-0 bottom-0 z-50 w-80 bg-white shadow-xl
        flex flex-col border-l border-border animate-slide-in-right">

        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3
          border-b border-border flex-shrink-0 bg-primary-light">
          <div className="flex items-center gap-2">
            <Upload className="w-4 h-4 text-primary" aria-hidden="true" />
            <h3 className="text-sm font-semibold text-dark">{t('delegar.title')}</h3>
          </div>
          <button
            onClick={onClose}
            className="text-muted-light hover:text-muted leading-none p-1"
            aria-label={t('delegar.close')}
          >
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-4">

          {/* Step 1 — pick agent */}
          <div>
            <label className="block text-2xs font-bold text-muted uppercase tracking-wide mb-1.5">
              {t('delegar.specialistAgent')}
            </label>
            {agents.length === 0 ? (
              <p className="text-xs text-muted-light">
                {t('delegar.noAgents')}{" "}
                <code className="text-2xs bg-surface-alt px-1 rounded">mentionable_pools</code>.
              </p>
            ) : (
              <div className="flex flex-col gap-1">
                {agents.map(agent => (
                  <button
                    key={agent.agent_type_id}
                    onClick={() => setPickedAgent(agent)}
                    className={[
                      "w-full text-left px-3 py-2.5 rounded-lg border transition-colors",
                      pickedAgent?.agent_type_id === agent.agent_type_id
                        ? "bg-primary-light border-primary/30 ring-1 ring-primary/20"
                        : "bg-surface-muted border-border hover:bg-primary-light hover:border-primary/30",
                    ].join(" ")}
                  >
                    <div className="flex items-center gap-1.5">
                      <Bot className="w-4 h-4 text-ai flex-shrink-0" aria-hidden="true" />
                      <span className="text-xs font-semibold text-dark">
                        {formatAgentName(agent.agent_type_id)}
                      </span>
                      {pickedAgent?.agent_type_id === agent.agent_type_id && (
                        <Check className="ml-auto w-3.5 h-3.5 text-primary" aria-hidden="true" />
                      )}
                    </div>
                    {agent.description && (
                      <p className="text-2xs text-muted mt-0.5 ml-5 leading-snug">
                        {agent.description}
                      </p>
                    )}
                    <div className="text-2xs text-muted-light font-mono mt-0.5 ml-5">
                      <span className="text-secondary font-semibold">@{agent.alias}</span>
                      {agent.pool_id && (
                        <span className="ml-1 text-muted-light">· {agent.pool_id}</span>
                      )}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Step 2 — typed fields OR free-text instruction */}
          {pickedAgent && (
            <div>
              {schemaLoading ? (
                <div className="flex items-center gap-2 py-2">
                  <div className="w-3 h-3 border-2 border-primary/40 border-t-transparent rounded-full animate-spin" />
                  <span className="text-xs text-muted-light">{t('delegar.loadingParams')}</span>
                </div>
              ) : schema ? (
                /* ── Typed schema fields ── */
                <div className="flex flex-col gap-3">
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className="text-2xs font-bold text-muted uppercase tracking-wide">
                      {t('delegar.params')}
                    </span>
                    <span className="text-2xs bg-primary-light text-primary px-1.5 py-0.5 rounded font-medium">
                      {t(schema.fields.length === 1 ? 'delegar.fields_one' : 'delegar.fields_other', { count: schema.fields.length })}
                    </span>
                  </div>

                  {schema.fields.map(field => (
                    <div key={field.id}>
                      <label className="block text-2xs font-semibold text-muted mb-1">
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

                  {/* Optional extra context when schema exists */}
                  {prefilledContext && (
                    <div>
                      <label className="block text-2xs font-semibold text-muted mb-1">
                        {t('delegar.prefilledContext')}
                      </label>
                      <textarea
                        value={freeText}
                        onChange={e => setFreeText(e.target.value)}
                        rows={3}
                        className="w-full text-xs border border-border-strong rounded-lg px-3 py-2
                          focus:outline-none focus:ring-2 focus:ring-primary/40 resize-none
                          text-dark placeholder-muted-light"
                      />
                      <p className="flex items-center gap-1 text-2xs text-muted-light mt-0.5">
                        <FileText className="w-3 h-3" aria-hidden="true" />
                        {t('delegar.prefilledFrom')}
                      </p>
                    </div>
                  )}
                </div>
              ) : prefilledContext ? (
                /* ── Free-text fallback — only shown when prefilledContext exists ── */
                <div>
                  <label className="block text-2xs font-bold text-muted uppercase tracking-wide mb-1.5">
                    {t('delegar.instruction')}
                  </label>
                  <textarea
                    ref={textareaRef}
                    value={freeText}
                    onChange={e => setFreeText(e.target.value)}
                    placeholder={t('delegar.instructionPlaceholder')}
                    rows={5}
                    className="w-full text-xs border border-border-strong rounded-lg px-3 py-2
                      focus:outline-none focus:ring-2 focus:ring-primary/40 resize-none
                      text-dark placeholder-muted-light"
                    onKeyDown={e => {
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit();
                    }}
                  />
                  <p className="flex items-center gap-1 text-2xs text-muted-light mt-0.5">
                    <FileText className="w-3 h-3" aria-hidden="true" />
                    {t('delegar.prefilledFrom')}
                  </p>
                </div>
              ) : null /* no schema + no prefilledContext → no input, button enabled immediately */
              }
            </div>
          )}

        </div>

        {/* Footer */}
        <div className="border-t border-border px-4 py-3 flex-shrink-0 bg-white">
          <button
            onClick={handleSubmit}
            disabled={!isValid()}
            className="w-full py-2 text-sm font-semibold text-white rounded-lg transition-colors
              bg-primary hover:bg-primary-dark disabled:opacity-40 disabled:cursor-not-allowed
              inline-flex items-center justify-center gap-2"
          >
            <Upload className="w-4 h-4" aria-hidden="true" />
            {t('delegar.delegate')}
          </button>
          {!schema && !!prefilledContext && (
            <p className="text-2xs text-muted text-center mt-1.5">{t('delegar.ctrlEnter')}</p>
          )}
        </div>
      </div>
    </>
  );
};
