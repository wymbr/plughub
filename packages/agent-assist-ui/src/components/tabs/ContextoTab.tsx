/**
 * ContextoTab
 * Shows customer_context: historical insights (long-term memory) and
 * conversation insights (session-scoped, expires on close).
 */

import React from "react";
import { CustomerContext, InsightItem } from "../../types";

interface ContextoTabProps {
  context: CustomerContext | null;
}

function formatLastSeen(iso?: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("pt-BR");
  } catch {
    return iso;
  }
}

const InsightCard: React.FC<{ item: InsightItem; historical: boolean }> = ({
  item,
  historical,
}) => (
  <div
    className={`rounded-lg p-2.5 text-sm leading-snug ${
      historical
        ? "bg-blue-50 border border-blue-200 text-blue-900"
        : "bg-purple-50 border border-purple-200 text-purple-900"
    }`}
  >
    <p>{item.content}</p>
    <div className="flex gap-3 mt-1 text-[10px] text-gray-500">
      {item.confidence !== undefined && (
        <span>
          Confiança: {(item.confidence * 100).toFixed(0)}%
        </span>
      )}
      {item.last_seen && (
        <span>Visto: {formatLastSeen(item.last_seen)}</span>
      )}
      {item.turn !== undefined && (
        <span>Turn: {item.turn}</span>
      )}
    </div>
  </div>
);

export const ContextoTab: React.FC<ContextoTabProps> = ({ context }) => {
  if (!context) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-gray-400">
        Aguardando dados…
      </div>
    );
  }

  const { historical_insights, conversation_insights } = context;

  return (
    <div className="flex flex-col gap-4 p-3 overflow-y-auto h-full">
      {/* Historical */}
      <section>
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Memória histórica ({historical_insights.length})
        </h3>
        {historical_insights.length === 0 ? (
          <p className="text-xs text-gray-400">
            Sem histórico para este contato.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {historical_insights.map((item, i) => (
              <InsightCard key={i} item={item} historical={true} />
            ))}
          </div>
        )}
      </section>

      {/* Conversation */}
      <section>
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Insights desta conversa ({conversation_insights.length})
        </h3>
        {conversation_insights.length === 0 ? (
          <p className="text-xs text-gray-400">
            Nenhum insight registrado nesta sessão.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {conversation_insights.map((item, i) => (
              <InsightCard key={i} item={item} historical={false} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
};
