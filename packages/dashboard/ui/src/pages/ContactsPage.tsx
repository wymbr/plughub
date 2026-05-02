/**
 * ContactsPage — Screen 3
 * Shows all contact evaluations for an agent with item-level detail on expand.
 */

import React, { useState } from "react";
import { ContactEvaluation, ContactListResponse, EvalItemDetail } from "../types";
import { useApi } from "../hooks/useApi";
import { ScoreBadge } from "../components/ScoreBadge";
import { LoadingSpinner } from "../components/LoadingSpinner";

interface ContactsPageProps {
  agentId: string;
  poolId: string;
}

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("pt-BR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

const ItemRow: React.FC<{ item: EvalItemDetail }> = ({ item }) => (
  <div className="flex items-start gap-2 py-1 border-b border-gray-50 last:border-0">
    <div className="flex-1 min-w-0">
      <p className="text-xs text-gray-700 font-medium">
        {item.section_id} › {item.subsection_id} › {item.item_id}
      </p>
      {item.justification && (
        <p className="text-[11px] text-gray-500 mt-0.5 leading-snug">
          {item.justification}
        </p>
      )}
    </div>
    <div className="flex items-center gap-1.5 flex-shrink-0">
      <span className="text-[10px] text-gray-400">w:{item.weight}</span>
      <ScoreBadge score={item.value} size="sm" />
    </div>
  </div>
);

const EvalCard: React.FC<{ evaluation: ContactEvaluation }> = ({
  evaluation,
}) => {
  const [expanded, setExpanded] = useState(false);

  // Group items by section
  const sections: Record<string, EvalItemDetail[]> = {};
  for (const item of evaluation.items) {
    if (!sections[item.section_id]) sections[item.section_id] = [];
    sections[item.section_id].push(item);
  }

  return (
    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
      {/* Header row */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left p-4 flex items-center gap-3 hover:bg-gray-50 transition-colors"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-gray-800 truncate">
              {evaluation.contact_id}
            </span>
          </div>
          <div className="flex gap-3 text-[11px] text-gray-400 mt-0.5">
            <span>{formatDateTime(evaluation.evaluated_at)}</span>
            <span>{evaluation.skill_id}</span>
            <span>{evaluation.items.length} itens</span>
          </div>
        </div>
        <ScoreBadge score={evaluation.overall_score} size="md" />
        <span className="text-gray-400 text-xs">{expanded ? "▲" : "▼"}</span>
      </button>

      {/* Detail */}
      {expanded && (
        <div className="px-4 pb-4 border-t border-gray-100">
          {Object.entries(sections).map(([sectionId, items]) => (
            <div key={sectionId} className="mt-3">
              <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide mb-1">
                {sectionId}
              </p>
              {items.map((item) => (
                <ItemRow
                  key={`${item.subsection_id}-${item.item_id}`}
                  item={item}
                />
              ))}
            </div>
          ))}

          {evaluation.items.length === 0 && (
            <p className="text-xs text-gray-400 mt-3">
              Sem itens de avaliação registados.
            </p>
          )}
        </div>
      )}
    </div>
  );
};

export const ContactsPage: React.FC<ContactsPageProps> = ({
  agentId,
  poolId: _poolId,
}) => {
  const [state, refresh] = useApi<ContactListResponse>(
    `/api/agents/${encodeURIComponent(agentId)}/contacts`
  );

  if (state.status === "loading" || state.status === "idle") {
    return <LoadingSpinner />;
  }

  if (state.status === "error") {
    return (
      <div className="p-6">
        <p className="text-red-600 text-sm">
          Erro ao carregar contatos: {state.error}
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

  const { contacts, total } = state.data;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-gray-800">
          Atendimentos — {agentId} ({total})
        </h2>
        <button
          onClick={refresh}
          className="text-xs text-gray-400 hover:text-gray-600"
        >
          ↺ Atualizar
        </button>
      </div>

      <div className="flex flex-col gap-2">
        {contacts.map((ev) => (
          <EvalCard key={ev.evaluation_id} evaluation={ev} />
        ))}

        {contacts.length === 0 && (
          <div className="text-center py-12 text-sm text-gray-400">
            Nenhuma avaliação encontrada para este agente.
          </div>
        )}
      </div>
    </div>
  );
};
