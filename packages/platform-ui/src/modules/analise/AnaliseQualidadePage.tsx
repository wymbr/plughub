/**
 * AnaliseQualidadePage — /analise/qualidade
 *
 * Evaluation quality analytics: scores, contestation rates, trends.
 * Backend: evaluation-api + analytics-api (available via /reports/evaluations/summary).
 */
import React from 'react'

export default function AnaliseQualidadePage() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-3 bg-gray-50">
      <span className="text-4xl">✓</span>
      <p className="text-sm font-medium text-gray-500">Análise de Qualidade</p>
      <p className="text-xs text-center max-w-xs text-gray-400">
        Scores de avaliação, taxa de contestação e tendências por campanha.
        Consumirá <code className="bg-gray-100 px-1 rounded">GET /reports/evaluations/summary</code>.
      </p>
    </div>
  )
}
