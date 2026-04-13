/**
 * PoolsPage — Screen 1
 * Shows all pools with aggregate metrics. Click a pool to drill into agents.
 */

import React from "react";
import { PoolListResponse, Screen } from "../types";
import { useApi } from "../hooks/useApi";
import { ScoreBadge } from "../components/ScoreBadge";
import { LoadingSpinner } from "../components/LoadingSpinner";

interface PoolsPageProps {
  onNavigate: (screen: Screen) => void;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("pt-BR");
  } catch {
    return iso;
  }
}

export const PoolsPage: React.FC<PoolsPageProps> = ({ onNavigate }) => {
  const [state, refresh] = useApi<PoolListResponse>("/api/pools");

  if (state.status === "loading" || state.status === "idle") {
    return <LoadingSpinner />;
  }

  if (state.status === "error") {
    return (
      <div className="p-6">
        <p className="text-red-600 text-sm">Erro ao carregar pools: {state.error}</p>
        <button
          onClick={refresh}
          className="mt-2 text-sm text-indigo-600 underline"
        >
          Tentar novamente
        </button>
      </div>
    );
  }

  const { pools } = state.data;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-gray-800">
          Pools ({pools.length})
        </h2>
        <button
          onClick={refresh}
          className="text-xs text-gray-400 hover:text-gray-600"
        >
          ↺ Atualizar
        </button>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {pools.map((pool) => (
          <button
            key={pool.pool_id}
            onClick={() =>
              onNavigate({ type: "agents", poolId: pool.pool_id })
            }
            className="text-left bg-white rounded-2xl border border-gray-200 p-4 hover:border-indigo-400 hover:shadow-md transition-all"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="font-semibold text-gray-800 truncate">
                  {pool.pool_id}
                </p>
                <p className="text-xs text-gray-400 mt-0.5">
                  {pool.agent_count} agentes · {pool.evaluation_count} avaliações
                </p>
              </div>
              <ScoreBadge score={pool.avg_score} size="lg" />
            </div>

            {/* IQR bar */}
            <div className="mt-3">
              <div className="relative h-1.5 bg-gray-100 rounded-full w-full">
                <div
                  className="absolute h-full bg-indigo-300 rounded-full"
                  style={{
                    left: `${(pool.p25_score / 10) * 100}%`,
                    width: `${((pool.p75_score - pool.p25_score) / 10) * 100}%`,
                  }}
                />
                <div
                  className="absolute w-1 h-3 -translate-y-[3px] bg-indigo-600 rounded-full"
                  style={{ left: `${(pool.avg_score / 10) * 100}%` }}
                />
              </div>
              <div className="flex justify-between text-[10px] text-gray-400 mt-1">
                <span>P25: {pool.p25_score.toFixed(1)}</span>
                <span>P75: {pool.p75_score.toFixed(1)}</span>
              </div>
            </div>

            <p className="text-[10px] text-gray-400 mt-2">
              Última avaliação: {formatDate(pool.last_evaluated_at)}
            </p>
          </button>
        ))}

        {pools.length === 0 && (
          <div className="col-span-full text-center py-12 text-sm text-gray-400">
            Nenhum dado de avaliação disponível ainda.
          </div>
        )}
      </div>
    </div>
  );
};
