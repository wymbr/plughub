/**
 * DelegarTarefaDrawer — Arc 11 Fase C (F3) + delegation_input
 *
 * Slide-in drawer for delegating a task to an AI specialist agent.
 * Opened from ActionBar "Delegar" button or from the message selection toolbar.
 *
 * Behaviour:
 *   1. Operator selects an agent from the list of mentionable agents.
 *   2. If the agent's skill defines delegation_input, typed fields are rendered
 *      (select, text, number). Otherwise falls back to a free-text textarea.
 *   3. Chooses visibility (agents_only = internal; all = visible to customer).
 *   4. Submits → calls onDelegate(agentTypeId, instruction, visibility).
 *
 * The parent converts the delegation into an @mention command via handleSend.
 * Typed fields are serialized to a human-readable instruction string so the
 * agent receives natural-language context without orchestrator-bridge changes.
 *
 * Serialization example:
 *   objetivo=Sugerir resposta ao cliente; contexto_extra=Cliente mencionou fatura
 *   → "[objetivo: Sugerir resposta ao cliente] [contexto_extra: Cliente mencionou fatura]"
 */

import React, { useEffect, useRef, useState } from "react";
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
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className="w-full text-xs border border-gray-300 rounded-lg px-3 py-2
        focus:outline-none focus:ring-2 focus:ring-orange-400 bg-white text-gray-700"
    >
      <option value="">— Selecionar —</option>
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
      className="w-full text-xs border border-gray-300 rounded-lg px-3 py-2
        focus:outline-none focus:ring-2 focus:ring-orange-400 text-gray-700 placeholder-gray-400"
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
  const [pickedAgent,  setPickedAgent]  = useState<MentionableAgent | null>(null);
  const [freeText,     setFreeText]     = useState(prefilledContext);
  const [fieldValues,  setFieldValues]  = useState<Record<string, string>>({});
  const [visibility,   setVisibility]   = useState<"all" | "agents_only">("agents_only");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Fetch delegation schema whenever the picked agent changes
  const { schema, loading: schemaLoading } = useDelegationSchema(
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
      // All required fields must have a value
      const missingRequired = schema.fields.some(
        f => f.required && !(fieldValues[f.id] ?? "").trim()
      );
      if (missingRequired) return false;
      // At least one field or context must have content
      const hasContent =
        schema.fields.some(f => (fieldValues[f.id] ?? "").trim().length > 0) ||
        freeText.trim().length > 0;
      return hasContent;
    }
    return freeText.trim().length > 0;
  }

  function handleSubmit() {
    if (!pickedAgent || !isValid()) return;

    const instruction = schema
      ? serializeFields(schema, fieldValues, freeText)
      : freeText.trim();

    // Use alias (e.g. "auth") as the @mention target — mcp-server resolves it via mentionable_pools.
    onDelegate(pickedAgent.alias, instruction, visibility);
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
        flex flex-col border-l border-gray-200 animate-slide-in-right">

        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3
          border-b border-gray-200 flex-shrink-0 bg-orange-50">
          <div className="flex items-center gap-2">
            <span className="text-base">📤</span>
            <h3 className="text-sm font-semibold text-gray-800">Delegar Tarefa</h3>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-lg leading-none p-1"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-4">

          {/* Step 1 — pick agent */}
          <div>
            <label className="block text-[10px] font-bold text-gray-500 uppercase tracking-wide mb-1.5">
              Agente especialista
            </label>
            {agents.length === 0 ? (
              <p className="text-xs text-gray-400">
                Nenhum agente disponível para este pool. Configure{" "}
                <code className="text-[10px] bg-gray-100 px-1 rounded">mentionable_pools</code>.
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
                        ? "bg-orange-50 border-orange-300 ring-1 ring-orange-200"
                        : "bg-gray-50 border-gray-200 hover:bg-orange-50 hover:border-orange-200",
                    ].join(" ")}
                  >
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm">🤖</span>
                      <span className="text-xs font-semibold text-gray-800">
                        {formatAgentName(agent.agent_type_id)}
                      </span>
                      {pickedAgent?.agent_type_id === agent.agent_type_id && (
                        <span className="ml-auto text-orange-500 text-xs">✓</span>
                      )}
                    </div>
                    {agent.description && (
                      <p className="text-[10px] text-gray-500 mt-0.5 ml-5 leading-snug">
                        {agent.description}
                      </p>
                    )}
                    <div className="text-[10px] text-gray-400 font-mono mt-0.5 ml-5">
                      <span className="text-blue-500 font-semibold">@{agent.alias}</span>
                      {agent.pool_id && (
                        <span className="ml-1 text-orange-400">· {agent.pool_id}</span>
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
                  <div className="w-3 h-3 border-2 border-orange-400 border-t-transparent rounded-full animate-spin" />
                  <span className="text-xs text-gray-400">Carregando parâmetros…</span>
                </div>
              ) : schema ? (
                /* ── Typed schema fields ── */
                <div className="flex flex-col gap-3">
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <span className="text-[10px] font-bold text-gray-500 uppercase tracking-wide">
                      Parâmetros da delegação
                    </span>
                    <span className="text-[10px] bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded font-medium">
                      {schema.fields.length} campo{schema.fields.length !== 1 ? "s" : ""}
                    </span>
                  </div>

                  {schema.fields.map(field => (
                    <div key={field.id}>
                      <label className="block text-[10px] font-semibold text-gray-600 mb-1">
                        {field.label}
                        {field.required && <span className="ml-1 text-red-400">*</span>}
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
                      <label className="block text-[10px] font-semibold text-gray-600 mb-1">
                        Contexto das mensagens selecionadas
                      </label>
                      <textarea
                        value={freeText}
                        onChange={e => setFreeText(e.target.value)}
                        rows={3}
                        className="w-full text-xs border border-gray-300 rounded-lg px-3 py-2
                          focus:outline-none focus:ring-2 focus:ring-orange-400 resize-none
                          text-gray-700 placeholder-gray-400"
                      />
                      <p className="text-[10px] text-gray-400 mt-0.5">
                        📋 Pré-preenchido a partir das mensagens selecionadas
                      </p>
                    </div>
                  )}
                </div>
              ) : (
                /* ── Free-text fallback ── */
                <div>
                  <label className="block text-[10px] font-bold text-gray-500 uppercase tracking-wide mb-1.5">
                    Instrução
                    <span className="ml-1 text-red-400">*</span>
                  </label>
                  <textarea
                    ref={textareaRef}
                    value={freeText}
                    onChange={e => setFreeText(e.target.value)}
                    placeholder="Descreva o que o agente deve fazer nesta delegação…"
                    rows={5}
                    className="w-full text-xs border border-gray-300 rounded-lg px-3 py-2
                      focus:outline-none focus:ring-2 focus:ring-orange-400 resize-none
                      text-gray-700 placeholder-gray-400"
                    onKeyDown={e => {
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit();
                    }}
                  />
                  {prefilledContext && (
                    <p className="text-[10px] text-gray-400 mt-0.5">
                      📋 Pré-preenchido a partir das mensagens selecionadas
                    </p>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Step 3 — visibility */}
          {pickedAgent && (
            <div>
              <label className="block text-[10px] font-bold text-gray-500 uppercase tracking-wide mb-1.5">
                Visibilidade da delegação
              </label>
              <div className="flex flex-col gap-1.5">
                {(["agents_only", "all"] as const).map(vis => (
                  <label
                    key={vis}
                    className={[
                      "flex items-start gap-2.5 p-2.5 rounded-lg border cursor-pointer transition-colors",
                      visibility === vis
                        ? "bg-orange-50 border-orange-300"
                        : "bg-gray-50 border-gray-200 hover:bg-orange-50/50",
                    ].join(" ")}
                  >
                    <input
                      type="radio"
                      name="delegation-visibility"
                      value={vis}
                      checked={visibility === vis}
                      onChange={() => setVisibility(vis)}
                      className="mt-0.5 accent-orange-500"
                    />
                    <div>
                      <div className="text-xs font-medium text-gray-800">
                        {vis === "agents_only" ? "🔒 Interno (agents_only)" : "🌐 Visível ao cliente (all)"}
                      </div>
                      <div className="text-[10px] text-gray-500 mt-0.5">
                        {vis === "agents_only"
                          ? "Cliente não vê a delegação nem a resposta do agente"
                          : "Agente responde diretamente ao cliente na conversa"}
                      </div>
                    </div>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-gray-200 px-4 py-3 flex-shrink-0 bg-white">
          <button
            onClick={handleSubmit}
            disabled={!isValid()}
            className="w-full py-2 text-sm font-semibold text-white rounded-lg transition-colors
              bg-orange-600 hover:bg-orange-700 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            📤 Delegar
          </button>
          {!schema && (
            <p className="text-[10px] text-gray-400 text-center mt-1.5">⌘↵ para delegar</p>
          )}
        </div>
      </div>
    </>
  );
};
