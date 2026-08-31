/**
 * PauseReasonModal
 * Shown when an agent clicks "Pause" — requires selecting a reason before pausing.
 * Reasons are loaded from Config API (namespace: agent_activity, key: pause_reasons);
 * the tenant-configured labels are shown as-is. When the Config API has none, a
 * built-in fallback list is used whose labels follow the UI language (i18n).
 * Reasons with `requires_note: true` show an additional free-text field.
 *
 * Pause reasons are agent-level (a pause removes the agent from ALL pools), so the
 * list is global — there is no per-pool association.
 */

import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Pause } from "lucide-react";
import { apiFetch } from '@/api/apiFetch'

interface PauseReason {
  id:            string;
  label:         string;
  requires_note: boolean;
  max_minutes?:  number;
}

interface Props {
  onConfirm: (reasonId: string, reasonLabel: string, note?: string, maxMinutes?: number) => void;
  onCancel:  () => void;
}

export const PauseReasonModal: React.FC<Props> = ({ onConfirm, onCancel }) => {
  const { t } = useTranslation("agentAssist");

  // Built-in fallback list — labels translated so they follow the UI language.
  // Used only when the Config API returns no pause_reasons.
  const defaultReasons: PauseReason[] = [
    { id: "intervalo",   label: t("pause.reasons.intervalo"),   requires_note: false, max_minutes: 15 },
    { id: "almoco",      label: t("pause.reasons.almoco"),      requires_note: false, max_minutes: 60 },
    { id: "treinamento", label: t("pause.reasons.treinamento"), requires_note: false, max_minutes: 120 },
    { id: "reuniao",     label: t("pause.reasons.reuniao"),     requires_note: true,  max_minutes: 60 },
    { id: "outro",       label: t("pause.reasons.outro"),       requires_note: true  },
  ];

  const [reasons,  setReasons]  = useState<PauseReason[]>(defaultReasons);
  const [selected, setSelected] = useState<string | null>(null);
  const [note,     setNote]     = useState("");
  const [loading,  setLoading]  = useState(true);

  // Load pause reasons from Config API; fall back to (translated) defaults on any error
  useEffect(() => {
    let cancelled = false;
    apiFetch("/config/agent_activity/pause_reasons")
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (cancelled) return;
        // Config API returns { value: [...], ... }
        const list: unknown = data?.value ?? data;
        if (Array.isArray(list) && list.length > 0) {
          setReasons(list as PauseReason[]);
        }
      })
      .catch(() => { /* keep defaults */ })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const activeReason = reasons.find(r => r.id === selected);
  const needsNote    = activeReason?.requires_note ?? false;
  const canConfirm   = selected !== null && (!needsNote || note.trim().length >= 3);

  const handleConfirm = () => {
    if (!selected || !activeReason) return;
    onConfirm(selected, activeReason.label, needsNote ? note.trim() : undefined, activeReason.max_minutes);
  };

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onCancel]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: "rgba(0,0,0,0.4)" }}
      onMouseDown={e => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-sm mx-4 overflow-hidden">
        {/* Header */}
        <div className="px-5 py-4 border-b border-border">
          <h2 className="text-base font-semibold text-dark">{t("pause.title")}</h2>
          <p className="text-xs text-muted mt-0.5">{t("pause.subtitle")}</p>
        </div>

        {/* Body */}
        <div className="px-5 py-4">
          {loading ? (
            <div className="flex items-center justify-center py-6">
              <span className="text-sm text-muted-light animate-pulse">{t("pause.loading")}</span>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {reasons.map(r => (
                <label
                  key={r.id}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer
                    border transition-colors select-none ${
                    selected === r.id
                      ? "border-warning bg-warning-light"
                      : "border-border hover:border-border-strong hover:bg-surface-muted"
                  }`}
                >
                  <input
                    type="radio"
                    name="pause_reason"
                    value={r.id}
                    checked={selected === r.id}
                    onChange={() => { setSelected(r.id); setNote(""); }}
                    className="accent-warning w-3.5 h-3.5 flex-shrink-0"
                  />
                  <span className="text-sm text-dark font-medium">{r.label}</span>
                  {r.requires_note && (
                    <span className="ml-auto text-2xs text-muted-light font-normal">{t("pause.noteRequired")}</span>
                  )}
                </label>
              ))}
            </div>
          )}

          {/* Note field — shown when selected reason requires it */}
          {needsNote && (
            <div className="mt-3">
              <label className="block text-xs font-medium text-muted mb-1">
                {t("pause.noteLabel")} <span className="text-red">*</span>
              </label>
              <textarea
                autoFocus
                value={note}
                onChange={e => setNote(e.target.value)}
                placeholder={t("pause.notePlaceholder")}
                rows={3}
                className="w-full text-sm border border-border-strong rounded-lg px-3 py-2
                  focus:outline-none focus:ring-2 focus:ring-warning/40 focus:border-warning
                  resize-none placeholder-muted-light"
              />
              {note.trim().length > 0 && note.trim().length < 3 && (
                <p className="text-xs text-red mt-1">{t("pause.noteMin")}</p>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 bg-surface-muted border-t border-border flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-4 py-1.5 rounded-lg text-sm font-medium text-muted
              border border-border hover:bg-surface-alt transition-colors"
          >
            {t("pause.cancel")}
          </button>
          <button
            onClick={handleConfirm}
            disabled={!canConfirm}
            className="px-4 py-1.5 rounded-lg text-sm font-semibold text-white
              bg-warning hover:bg-warning-text disabled:opacity-40 disabled:cursor-not-allowed
              transition-colors"
          >
            <Pause className="w-3.5 h-3.5 inline mr-1" aria-hidden="true" />{t("pause.confirmPause")}
          </button>
        </div>
      </div>
    </div>
  );
};
