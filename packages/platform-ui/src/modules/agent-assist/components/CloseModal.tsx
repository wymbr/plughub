/**
 * CloseModal
 * Confirmation dialog before sending agent_done / closing a session.
 * Collects issue_status and outcome before confirming.
 */

import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { ClosePayload } from "../types";

interface CloseModalProps {
  onConfirm: (payload: ClosePayload) => void;
  onCancel: () => void;
  defaultIssueStatus?: string;
  defaultOutcome?: ClosePayload["outcome"];
}

export const CloseModal: React.FC<CloseModalProps> = ({
  onConfirm,
  onCancel,
  defaultIssueStatus = "",
  defaultOutcome = "resolved",
}) => {
  const { t } = useTranslation('agentAssist');
  const [issueStatus, setIssueStatus] = useState(defaultIssueStatus);
  const [outcome, setOutcome] = useState<ClosePayload["outcome"]>(defaultOutcome);
  const [handoffReason, setHandoffReason] = useState("");

  const OUTCOMES: Array<{ value: ClosePayload["outcome"]; label: string }> = [
    { value: "resolved",  label: t('close.outcomeResolved') },
    { value: "escalated", label: t('close.outcomeEscalated') },
    { value: "abandoned", label: t('close.outcomeAbandoned') },
  ];

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
        <h2 className="text-lg font-semibold text-dark">{t('close.title')}</h2>

        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-muted">
            {t('close.issueStatus')} <span className="text-red">*</span>
          </label>
          <input
            type="text"
            value={issueStatus}
            onChange={(e) => setIssueStatus(e.target.value)}
            placeholder={t('close.issueStatusPlaceholder')}
            className="border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-muted">{t('close.outcomeLabel')}</label>
          <div className="flex gap-2">
            {OUTCOMES.map((o) => (
              <button
                key={o.value}
                onClick={() => setOutcome(o.value)}
                className={`flex-1 py-2 rounded-lg text-sm font-medium border transition-colors ${
                  outcome === o.value
                    ? "bg-primary text-white border-primary"
                    : "bg-white text-muted border-border hover:border-primary"
                }`}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>

        {outcome !== "resolved" && (
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted">{t('close.handoff')}</label>
            <textarea
              value={handoffReason}
              onChange={(e) => setHandoffReason(e.target.value)}
              rows={2}
              placeholder={t('close.handoffPlaceholder')}
              className="border border-border-strong rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg text-sm text-muted hover:bg-surface-alt transition-colors"
          >
            {t('close.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-red text-white hover:bg-red-text disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {t('close.confirm')}
          </button>
        </div>
      </div>
    </div>
  );
};
