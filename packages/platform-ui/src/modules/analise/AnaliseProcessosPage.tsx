/**
 * AnaliseProcessosPage — /analise/processos
 *
 * Workflow analytics: completion rates, step durations, failure analysis.
 * Backend: analytics-api ClickHouse queries (pending implementation).
 */
import React from 'react'

export default function AnaliseProcessosPage() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-3 bg-gray-50">
      <span className="text-4xl">⚙️</span>
      <p className="text-sm font-medium text-gray-500">Análise de Processos</p>
      <p className="text-xs text-center max-w-xs text-gray-400">
        Métricas de workflows: taxa de conclusão, duração por step, análise de falhas.
        Disponível após implementação do endpoint analytics-api.
      </p>
    </div>
  )
}
