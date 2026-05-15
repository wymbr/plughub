/**
 * PauseReasonModal
 * Shown when an agent clicks "Pausar" — requires selecting a reason before pausing.
 * Reasons are loaded from Config API (namespace: agent_activity, key: pause_reasons).
 * Reasons with `requires_note: true` show an additional free-text field.
 */

import React, { useEffect, useState } from "react";

interface PauseReason {
  id:            string;
  label:         string;
  requires_note: boolean;
}

interface Props {
  onConfirm: (reasonId: string, reasonLabel: string, note?: string) => void;
  onCancel:  () => void;
}

const DEFAULT_REASONS: PauseReason[] = [
  { id: "intervalo",    label: "Intervalo",   requires_note: false },
  { id: "almoco",       label: "Almoço",      requires_note: false },
  { id: "treinamento",  label: "Treinamento", requires_note: false },
  { id: "reuniao",      label: "Reunião",     requires_note: true  },
  { id: "outro",        label: "Outro",       requires_note: true  },
];

export const PauseReasonModal: React.FC<Props> = ({ onConfirm, onCancel }) => {
  const [reasons,  setReasons]  = useState<PauseReason[]>(DEFAULT_REASONS);
  const [selected, setSelected] = useState<string | null>(null);
  const [note,     setNote]     = useState("");
  const [loading,  setLoading]  = useState(true);

  // Load pause reasons from Config API; fall back to defaults on any error
  useEffect(() => {
    let cancelled = false;
    fetch("/config/agent_activity/pause_reasons")
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

  const activeReason   = reasons.find(r => r.id === selected);
  const needsNote      = activeReason?.requires_note ?? false;
  const canConfirm     = selected !== null && (!needsNote || note.trim().length >= 3);

  const handleConfirm = () => {
    if (!selected || !activeReason) return;
    onConfirm(selected, activeReason.label, needsNote ? note.trim() : undefined);
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
        <div className="px-5 py-4 border-b border-gray-100">
          <h2 className="text-base font-semibold text-gray-800">Motivo da pausa</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Selecione o motivo antes de pausar o recebimento de novos contatos.
          </p>
        </div>

        {/* Body */}
        <div className="px-5 py-4">
          {loading ? (
            <div className="flex items-center justify-center py-6">
              <span className="text-sm text-gray-400 animate-pulse">Carregando motivos…</span>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {reasons.map(r => (
                <label
                  key={r.id}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer
                    border transition-colors select-none ${
                    selected === r.id
                      ? "border-amber-400 bg-amber-50"
                      : "border-gray-200 hover:border-gray-300 hover:bg-gray-50"
                  }`}
                >
                  <input
                    type="radio"
                    name="pause_reason"
                    value={r.id}
                    checked={selected === r.id}
                    onChange={() => { setSelected(r.id); setNote(""); }}
                    className="accent-amber-500 w-3.5 h-3.5 flex-shrink-0"
                  />
                  <span className="text-sm text-gray-700 font-medium">{r.label}</span>
                  {r.requires_note && (
                    <span className="ml-auto text-[10px] text-gray-400 font-normal">nota obrigatória</span>
                  )}
                </label>
              ))}
            </div>
          )}

          {/* Note field — shown when selected reason requires it */}
          {needsNote && (
            <div className="mt-3">
              <label className="block text-xs font-medium text-gray-600 mb-1">
                Observação <span className="text-red-500">*</span>
              </label>
              <textarea
                autoFocus
                value={note}
                onChange={e => setNote(e.target.value)}
                placeholder="Descreva brevemente o motivo…"
                rows={3}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2
                  focus:outline-none focus:ring-2 focus:ring-amber-400 focus:border-amber-400
                  resize-none placeholder-gray-400"
              />
              {note.trim().length > 0 && note.trim().length < 3 && (
                <p className="text-xs text-red-500 mt-1">Mínimo de 3 caracteres.</p>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 bg-gray-50 border-t border-gray-100 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-4 py-1.5 rounded-lg text-sm font-medium text-gray-600
              border border-gray-200 hover:bg-gray-100 transition-colors"
          >
            Cancelar
          </button>
          <button
            onClick={handleConfirm}
            disabled={!canConfirm}
            className="px-4 py-1.5 rounded-lg text-sm font-semibold text-white
              bg-amber-500 hover:bg-amber-600 disabled:opacity-40 disabled:cursor-not-allowed
              transition-colors"
          >
            ⏸ Pausar
          </button>
        </div>
      </div>
    </div>
  );
};
