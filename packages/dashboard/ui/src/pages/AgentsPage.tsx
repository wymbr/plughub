/**
 * AgentsPage — Screen 2
 * Shows agent profiles for a pool: score, trend sparkline, section breakdown.
 * Click an agent to drill into their contact evaluations.
 */

import React from "react";
import {
  LineChart,
  Line,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { AgentListResponse, AgentProfile, Screen } from "../types";
import { useApi } from "../hooks/useApi";
import { ScoreBadge } from "../components/ScoreBadge";
import { LoadingSpinner } from "../components/LoadingSpinner";

interface AgentsPageProps {
  poolId: string;
  onNavigate: (screen: Screen) => void;
}

const AgentCard: React.FC<{
  agent: AgentProfile;
  poolId: string;
  onNavigate: (screen: Screen) => void;
}> = ({ agent, poolId, onNavigate }) => {
  const trendData = agent.trend.map((t) => ({
    date: t.date,
    score: t.avg_score,
  }));

  return (
    <button
      onClick={() =>
        onNavigate({ type: "contacts", agentId: agent.agent_id, poolId })
      }
      className="text-left bg-white rounded-2xl border border-gray-200 p-4 hover:border-indigo-400 hover:shadow-md transition-all flex flex-col gap-3"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold text-gray-800 text-sm truncate">
            {agent.agent_id}
          </p>
          <p className="text-xs text-gray-400 mt-0.5">
            {agent.agent_type} · {agent.evaluation_count} avaliações
          </p>
        </div>
        <ScoreBadge score={agent.avg_score} size="lg" />
      </div>

      {/* Sparkline */}
      {trendData.length > 1 && (
        <div className="h-12">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trendData}>
              <Tooltip
                formatter={(v: number) => [v.toFixed(1), "score"]}
                labelFormatter={(l) => l}
              />
              <Line
                type="monotone"
                dataKey="score"
                stroke="#6366f1"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Section scores */}
      {agent.section_scores.length > 0 && (
        <div className="flex flex-col gap-1">
          {agent.section_scores.map((sec) => (
            <div key={sec.section_id} className="flex items-center gap-2">
              <span className="text-[11px] text-gray-500 flex-1 truncate">
                {sec.section_id}
              </span>
              <span className="text-[10px] text-gray-400">
                {sec.score_type === "base_score" ? "base" : "ctx"}
              </span>
              <div className="w-16 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full bg-indigo-500"
                  style={{ width: `${(sec.avg_score / 10) * 100}%` }}
                />
              </div>
              <span className="text-[11px] font-medium text-gray-700 w-6 text-right">
                {sec.avg_score.toFixed(1)}
              </span>
            </div>
          ))}
        </div>
      )}
    </button>
  );
};

export const AgentsPage: React.FC<AgentsPageProps> = ({
  poolId,
  onNavigate,
}) => {
  const [state, refresh] = useApi<AgentListResponse>(
    `/api/pools/${encodeURIComponent(poolId)}/agents`
  );

  if (state.status === "loading" || state.status === "idle") {
    return <LoadingSpinner />;
  }

  if (state.status === "error") {
    return (
      <div className="p-6">
        <p className="text-red-600 text-sm">
          Erro ao carregar agentes: {state.error}
        </p>
        <button
          onClick={refresh}
          className="mt-2 text-sm text-indigo-600 underline"
        >
          Tentar novamente
        </button>
      </div>
    );
  }

  const { agents, total } = state.data;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-gray-800">
          Agentes — {poolId} ({total})
        </h2>
        <button
          onClick={refresh}
          className="text-xs text-gray-400 hover:text-gray-600"
        >
          ↺ Atualizar
        </button>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {agents.map((agent) => (
          <AgentCard
            key={agent.agent_id}
            agent={agent}
            poolId={poolId}
            onNavigate={onNavigate}
          />
        ))}

        {agents.length === 0 && (
          <div className="col-span-full text-center py-12 text-sm text-gray-400">
            Nenhum agente encontrado neste pool.
          </div>
        )}
      </div>
    </div>
  );
};
