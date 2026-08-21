/**
 * EstadoTab
 * Shows supervisor_state: AI participants (Arc 11 F1), sentiment gauge +
 * trend chart, intent, flags, SLA.
 */

import React from "react";
import { useTranslation } from "react-i18next";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { ChatMessage, SupervisorState } from "../../types";
import { AiParticipantCard } from "../AiParticipantCard";

interface EstadoTabProps {
  state: SupervisorState | null;
  /** Current session messages — forwarded to AiParticipantCard for the last-5 drawer. */
  sessionMessages?: ChatMessage[];
  /** Sends @{instanceId} terminate_self via WS (from AgentAssistPage.handleSend). */
  onTerminateSegment?: (instanceId: string) => void;
}

function sentimentColor(value: number): string {
  if (value >= 0.3)  return "text-green-text";
  if (value >= -0.3) return "text-warning-text";
  return "text-red-text";
}

function sentimentLabel(value: number, t: (key: string) => string): string {
  if (value >= 0.5)  return t('estado.sentimentLabel.veryPositive');
  if (value >= 0.2)  return t('estado.sentimentLabel.positive');
  if (value >= -0.2) return t('estado.sentimentLabel.neutral');
  if (value >= -0.5) return t('estado.sentimentLabel.negative');
  return t('estado.sentimentLabel.veryNegative');
}

function trendIcon(trend: string): string {
  if (trend === "improving") return "↑";
  if (trend === "declining") return "↓";
  return "→";
}

export const EstadoTab: React.FC<EstadoTabProps> = ({
  state,
  sessionMessages = [],
  onTerminateSegment,
}) => {
  const { t } = useTranslation('agentAssist');

  if (!state) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-muted-light">
        {t('estado.waiting')}
      </div>
    );
  }

  const { sentiment, intent, flags, sla, turn_count, ai_participants } = state;

  // Build chart data from trajectory
  const chartData = sentiment.trajectory.map((v, i) => ({
    turn:  i + 1,
    value: Math.round(v * 100) / 100,
  }));

  const slaPercent = Math.min(sla.percentage, 100);
  const slaBar =
    sla.breach_imminent
      ? "bg-red"
      : slaPercent > 70
      ? "bg-warning"
      : "bg-green";

  return (
    <div className="flex flex-col gap-4 p-3 overflow-y-auto h-full">

      {/* ── Arc 11 F1 — AI Participants ── */}
      {ai_participants && ai_participants.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
            {t('estado.aiAgents')}
          </h3>
          <div className="flex flex-col gap-2">
            {ai_participants.map(p => (
              <AiParticipantCard
                key={p.instance_id}
                participant={p}
                sessionMessages={sessionMessages}
                onTerminateSegment={onTerminateSegment}
              />
            ))}
          </div>
        </section>
      )}

      {/* Sentiment — só existe quando FOI MEDIDO.
          Esta seção renderizava incondicionalmente e, com o `?? 0` do backend,
          anunciava "0% neutral" para toda sessão da plataforma: era a superfície
          sem guarda nenhuma das quatro. `current === null` = não medido, e a
          decisão é não renderizar (nem o título). */}
      {sentiment.current !== null && (
      <section>
        <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
          {t('state.sentiment')}
        </h3>
        <div className="flex items-baseline gap-2">
          <span className={`text-2xl font-bold ${sentimentColor(sentiment.current)}`}>
            {(sentiment.current * 100).toFixed(0)}%
          </span>
          <span className={`text-sm ${sentimentColor(sentiment.current)}`}>
            {sentimentLabel(sentiment.current, t)}
          </span>
          {sentiment.trend !== null && (
            <span className="text-sm text-muted-light ml-auto">
              {trendIcon(sentiment.trend)} {sentiment.trend}
            </span>
          )}
        </div>
        {sentiment.alert && (
          <div className="mt-1 text-xs font-semibold text-red-text bg-red-light border border-red/30 rounded px-2 py-1">
            {t('estado.sentimentAlert')}
          </div>
        )}

        {chartData.length > 1 && (
          <div className="mt-2 h-24">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <XAxis dataKey="turn" tick={{ fontSize: 9 }} hide />
                <YAxis domain={[-1, 1]} tick={{ fontSize: 9 }} width={24} />
                <Tooltip
                  formatter={(v: number) => [`${(v * 100).toFixed(0)}%`, "sentiment"]}
                  labelFormatter={(l) => `Turn ${l}`}
                />
                <ReferenceLine y={0} stroke="#d1d5db" strokeDasharray="3 3" />
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke="#6366f1"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>
      )}

      {/* Intent */}
      <section>
        <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">
          {t('state.intent')}
        </h3>
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-dark">
            {intent.current ?? "—"}
          </span>
          {intent.current && (
            <span className="text-xs text-muted-light">
              {t('estado.confidence', { pct: (intent.confidence * 100).toFixed(0) })}
            </span>
          )}
        </div>
        {intent.history.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {intent.history.slice(-4).map((h, i) => (
              <span
                key={i}
                className="text-2xs bg-surface-alt text-muted px-1.5 py-0.5 rounded"
              >
                {h}
              </span>
            ))}
          </div>
        )}
      </section>

      {/* Flags */}
      {flags.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">
            {t('state.flags')}
          </h3>
          <div className="flex flex-wrap gap-1">
            {flags.map((f) => (
              <span
                key={f}
                className="text-xs bg-warning-light text-warning-text border border-warning/30 px-2 py-0.5 rounded-full font-medium"
              >
                {f}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* SLA */}
      <section>
        <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-1">
          {t('estado.slaTurn', { count: turn_count })}
        </h3>
        <div className="flex items-center gap-2">
          <div className="flex-1 h-2 bg-border rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${slaBar}`}
              style={{ width: `${slaPercent}%` }}
            />
          </div>
          <span className="text-xs text-muted w-10 text-right">
            {slaPercent.toFixed(0)}%
          </span>
        </div>
        {sla.breach_imminent && (
          <p className="text-xs text-red-text font-semibold mt-1 animate-pulse">
            {t('estado.slaRisk')}
          </p>
        )}
      </section>
    </div>
  );
};
