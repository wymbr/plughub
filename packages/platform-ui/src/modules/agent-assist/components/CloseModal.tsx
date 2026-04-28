/**
 * CloseModal
 * Confirmation dialog before sending agent_done / closing a session.
 * Collects issue_status and outcome before confirming.
 */

import React, { useState } from "react";
import { ClosePayload } from "../types";

interface CloseModalProps {
  onConfirm: (payload: ClosePayload) => void;
  onCancel: () => void;
  defaultIssueStatus?: string;
  defaultOutcome?: ClosePayload["outcome"];
}

const OUTCOMES: Array<{ value: ClosePayload["outcome"]; label: string }> = [
  { value: "resolved", label: "Resolvido" },
  { value: "escalated", label: "Escalado" },
  { value: "abandoned", label: "Abandonado" },
];

export const CloseModal: React.FC<CloseModalProps> = ({
  onConfirm,
  onCancel,
  defaultIssueStatus = "",
  defaultOutcome = "resolved",
}) => {
  const [issueStatus, setIssueStatus] = useState(defaultIssueStatus);
  const [outcome, setOutcome] = useState<ClosePayload["outcome"]>(defaultOutcome);
  const [handoffReason, setHandoffReason] = useState("");

  const canSubmit = issueStatus.trim().length > 0;

  const handleSubmit = () => {
    if (!canSubmit) return;
    const payload: ClosePayload = {
      issue_status: issueStatus.trim(),
      outcome,
      ...(outcome !== "resolved" && handoffReason.trim()
        ? { handoff_reason: handoffReason.trim() }
        : {}),
    };
    onConfirm(payload);
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md max-h-[90vh] overflow-y-auto p-6 flex flex-col gap-4">
        <h2 className="text-lg font-semibold text-gray-800">Encerrar atendimento</h2>

        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-gray-600">
            Status do problema <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            value={issueStatus}
            onChange={(e) => setIssueStatus(e.target.value)}
            placeholder="Ex: Portabilidade solicitada com sucesso"
            className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-gray-600">Desfecho</label>
          <div className="flex gap-2">
            {OUTCOMES.map((o) => (
              <button
                key={o.value}
                onClick={() => setOutcome(o.value)}
                className={`flex-1 py-2 rounded-lg text-sm font-medium border transition-colors ${
                  outcome === o.value
                    ? "bg-indigo-600 text-white border-indigo-600"
                    : "bg-white text-gray-600 border-gray-300 hover:border-indigo-400"
                }`}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>

        {outcome !== "resolved" && (
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-gray-600">Motivo do handoff</label>
            <textarea
              value={handoffReason}
              onChange={(e) => setHandoffReason(e.target.value)}
              rows={2}
              placeholder="Descreva o motivo (opcional)"
              className="border border-gray-300 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100 transition-colors"
          >
            Cancelar
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Confirmar encerramento
          </button>
        </div>
      </div>
    </div>
  );
};
