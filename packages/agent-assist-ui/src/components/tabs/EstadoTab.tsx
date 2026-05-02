/**
 * EstadoTab
 * Shows supervisor_state: sentiment gauge + trend chart, intent, flags, SLA.
 */

import React from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { SupervisorState } from "../../types";

interface EstadoTabProps {
  state: SupervisorState | null;
}

function sentimentColor(value: number): string {
  if (value >= 0.3) return "text-green-600";
  if (value >= -0.3) return "text-yellow-600";
  return "text-red-600";
}

function sentimentLabel(value: number): string {
  if (value >= 0.5) return "Muito positivo";
  if (value >= 0.2) return "Positivo";
  if (value >= -0.2) return "Neutro";
  if (value >= -0.5) return "Negativo";
  return "Muito negativo";
}

function trendIcon(trend: string): string {
  if (trend === "improving") return "↑";
  if (trend === "declining") return "↓";
  return "→";
}

export const EstadoTab: React.FC<EstadoTabProps> = ({ state }) => {
  if (!state) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-gray-400">
        Aguardando dados…
      </div>
    );
  }

  const { sentiment, intent, flags, sla, turn_count } = state;

  // Build chart data from trajectory
  const chartData = sentiment.trajectory.map((v, i) => ({
    turn: i + 1,
    value: Math.round(v * 100) / 100,
  }));

  const slaPercent = Math.min(sla.percentage, 100);
  const slaBar =
    sla.breach_imminent
      ? "bg-red-500"
      : slaPercent > 70
      ? "bg-yellow-400"
      : "bg-green-500";

  return (
    <div className="flex flex-col gap-4 p-3 overflow-y-auto h-full">
      {/* Sentiment */}
      <section>
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Sentimento
        </h3>
        <div className="flex items-baseline gap-2">
          <span
            className={`text-2xl font-bold ${sentimentColor(sentiment.current)}`}
          >
            {(sentiment.current * 100).toFixed(0)}%
          </span>
          <span className={`text-sm ${sentimentColor(sentiment.current)}`}>
            {sentimentLabel(sentiment.current)}
          </span>
          <span className="text-sm text-gray-400 ml-auto">
            {trendIcon(sentiment.trend)} {sentiment.trend}
          </span>
        </div>
        {sentiment.alert && (
          <div className="mt-1 text-xs font-semibold text-red-600 bg-red-50 border border-red-200 rounded px-2 py-1">
            ⚠ Alerta de sentimento crítico
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

      {/* Intent */}
      <section>
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
          Intenção
        </h3>
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-800">
            {intent.current ?? "—"}
          </span>
          {intent.current && (
            <span className="text-xs text-gray-400">
              {(intent.confidence * 100).toFixed(0)}% confiança
            </span>
          )}
        </div>
        {intent.history.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {intent.history.slice(-4).map((h, i) => (
              <span
                key={i}
                className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded"
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
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
            Flags
          </h3>
          <div className="flex flex-wrap gap-1">
            {flags.map((f) => (
              <span
                key={f}
                className="text-[11px] bg-amber-100 text-amber-800 border border-amber-300 px-2 py-0.5 rounded-full font-medium"
              >
                {f}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* SLA */}
      <section>
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
          SLA · Turn {turn_count}
        </h3>
        <div className="flex items-center gap-2">
          <div className="flex-1 h-2 bg-gray-200 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${slaBar}`}
              style={{ width: `${slaPercent}%` }}
            />
          </div>
          <span className="text-xs text-gray-600 w-10 text-right">
            {slaPercent.toFixed(0)}%
          </span>
        </div>
        {sla.breach_imminent && (
          <p className="text-xs text-red-600 font-semibold mt-1 animate-pulse">
            SLA em risco iminente
          </p>
        )}
      </section>
    </div>
  );
};
