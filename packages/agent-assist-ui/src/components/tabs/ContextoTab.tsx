/**
 * ContextoTab
 * Shows:
 *   1. Contact Context card — structured fields enriched by agente_contexto_ia_v1
 *      before human handoff (nome, CPF, motivo_contato, etc.)
 *   2. Historical insights — long-term memory from previous contacts
 *   3. Conversation insights — session-scoped insights
 */

import React from "react";
import { ContactContextData, ContactContextField, CustomerContext, InsightItem } from "../../types";

interface ContextoTabProps {
  context: CustomerContext | null;
}

// ── helpers ──────────────────────────────────────────────────────────────────

function formatLastSeen(iso?: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("pt-BR");
  } catch {
    return iso;
  }
}

function confidenceColor(c: number): string {
  if (c >= 0.9) return "text-green-700 bg-green-100";
  if (c >= 0.7) return "text-blue-700 bg-blue-100";
  if (c >= 0.4) return "text-yellow-700 bg-yellow-100";
  return "text-gray-500 bg-gray-100";
}

function confidenceLabel(c: number): string {
  if (c >= 0.9) return "confirmado";
  if (c >= 0.7) return "alta certeza";
  if (c >= 0.4) return "incerto";
  return "desconhecido";
}

function sourceLabel(source: string): string {
  const labels: Record<string, string> = {
    customer_input:   "cliente",
    mcp_call:         "CRM",
    ai_inferred:      "inferido",
    insight_historico: "histórico",
    insight_conversa: "conversa",
    pipeline_state:   "sessão",
  };
  return labels[source] ?? source;
}

// ── ContactContextCard ────────────────────────────────────────────────────────

interface FieldRowProps {
  label: string;
  field: ContactContextField | undefined;
}

const FieldRow: React.FC<FieldRowProps> = ({ label, field }) => {
  if (!field) return null;
  return (
    <div className="flex items-start gap-2 py-1.5 border-b border-gray-100 last:border-0">
      <span className="text-xs text-gray-500 w-28 shrink-0 pt-0.5">{label}</span>
      <div className="flex-1 min-w-0">
        <span className="text-sm font-medium text-gray-900 break-all">{field.value}</span>
        <div className="flex gap-1.5 mt-0.5">
          <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${confidenceColor(field.confidence)}`}>
            {confidenceLabel(field.confidence)}
          </span>
          <span className="text-[10px] text-gray-400 py-0.5">
            via {sourceLabel(field.source)}
          </span>
        </div>
      </div>
    </div>
  );
};

const ContactContextCard: React.FC<{ cc: ContactContextData }> = ({ cc }) => {
  const scorePercent = cc.completeness_score !== undefined
    ? Math.round(cc.completeness_score * 100)
    : null;

  const hasData = cc.nome || cc.cpf || cc.account_id || cc.telefone || cc.email ||
                  cc.motivo_contato || cc.intencao_primaria || cc.sentimento_atual ||
                  cc.resumo_conversa;

  if (!hasData) return null;

  return (
    <section className="bg-emerald-50 border border-emerald-200 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-emerald-100 border-b border-emerald-200">
        <span className="text-xs font-semibold text-emerald-800 uppercase tracking-wide">
          Contexto do Cliente
        </span>
        {scorePercent !== null && (
          <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
            scorePercent >= 80
              ? "bg-emerald-600 text-white"
              : scorePercent >= 50
              ? "bg-yellow-500 text-white"
              : "bg-gray-300 text-gray-700"
          }`}>
            {scorePercent}% completo
          </span>
        )}
      </div>

      {/* Fields */}
      <div className="px-3 py-1 divide-y divide-gray-100">
        <FieldRow label="Nome"          field={cc.nome} />
        <FieldRow label="CPF"           field={cc.cpf} />
        <FieldRow label="Conta"         field={cc.account_id} />
        <FieldRow label="Telefone"      field={cc.telefone} />
        <FieldRow label="E-mail"        field={cc.email} />
        <FieldRow label="Motivo"        field={cc.motivo_contato} />
        <FieldRow label="Intenção"      field={cc.intencao_primaria} />
        <FieldRow label="Sentimento"    field={cc.sentimento_atual} />
        <FieldRow label="Resumo"        field={cc.resumo_conversa} />
      </div>
    </section>
  );
};

// ── InsightCard ───────────────────────────────────────────────────────────────

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

// ── ContextoTab ───────────────────────────────────────────────────────────────

export const ContextoTab: React.FC<ContextoTabProps> = ({ context }) => {
  if (!context) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-gray-400">
        Aguardando dados…
      </div>
    );
  }

  const { historical_insights, conversation_insights, contact_context } = context;

  return (
    <div className="flex flex-col gap-4 p-3 overflow-y-auto h-full">

      {/* ── Contact Context (from agente_contexto_ia_v1) ── */}
      {contact_context && <ContactContextCard cc={contact_context} />}

      {/* ── Historical Insights ── */}
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

      {/* ── Conversation Insights ── */}
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
