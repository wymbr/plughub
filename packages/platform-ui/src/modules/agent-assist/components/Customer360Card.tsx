/**
 * Customer360Card (Cliente 360 — C1b)
 * Aggregated 360 rollup for an identified customer: contacts summary, quality
 * (evaluation_finalized, Official) and voice-of-customer (session_signal).
 *
 * Shared by the Console (ClienteTab) and the Analytics customer view (H5). Keyed
 * only by customer_id — self-contained (own fetch via useCustomer360), fail-soft.
 */

import React from "react";
import { useTranslation } from "react-i18next";
import { Star, MessageSquareHeart, Phone } from "lucide-react";
import { useCustomer360, Customer360Survey } from "../hooks/useCustomer360";

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
}

// score 0..1 → "NN%"; qualquer outra escala (ex. NPS 0-10) mostra o número cru.
export function pct(v: number | null | undefined): string {
  return typeof v === "number" ? `${Math.round(v * 100)}%` : "—";
}

// ── Card 360 (C1b) — quality + survey + resumo de contatos, por customer_id ──
export const Customer360Card: React.FC<{ customerId: string }> = ({ customerId }) => {
  const { t } = useTranslation('agentAssist');
  const { data, loading } = useCustomer360(customerId);

  if (loading) {
    return <div className="text-2xs text-muted-light px-1 py-2">{t('cliente.s360.loading')}</div>;
  }
  if (!data) return null;

  const c = data.contacts;
  const q = data.quality;
  const surveys: Customer360Survey[] = data.surveys ?? [];

  return (
    <section className="border border-border rounded-lg p-3 space-y-3 bg-white">
      <div className="text-2xs font-semibold text-muted uppercase tracking-wide">
        {t('cliente.s360.title')}
      </div>

      {/* Contatos */}
      <div className="flex items-start gap-2">
        <Phone className="w-3.5 h-3.5 text-muted-light mt-0.5 shrink-0" aria-hidden="true" />
        <div className="flex-1 min-w-0">
          <div className="text-2xs text-muted uppercase tracking-wide">{t('cliente.s360.contacts')}</div>
          {c && c.total > 0 ? (
            <div className="text-xs text-dark">
              <span className="font-semibold">{c.total}</span> {t('cliente.s360.total')}
              {" · "}{c.resolved} {t('cliente.s360.resolved')}
              {c.open_count > 0 && <> · <span className="text-warning-text">{c.open_count} {t('cliente.s360.open')}</span></>}
              <div className="text-2xs text-muted-light mt-0.5">
                {(c.channels ?? []).join(", ") || "—"} · {t('cliente.s360.last')}: {fmtDate(c.last_contact_at)}
              </div>
            </div>
          ) : <div className="text-xs text-muted-light">{t('cliente.s360.none')}</div>}
        </div>
      </div>

      {/* Qualidade (Oficial) */}
      <div className="flex items-start gap-2">
        <Star className="w-3.5 h-3.5 text-muted-light mt-0.5 shrink-0" aria-hidden="true" />
        <div className="flex-1 min-w-0">
          <div className="text-2xs text-muted uppercase tracking-wide">{t('cliente.s360.quality')}</div>
          {q ? (
            <div className="text-xs text-dark">
              <span className="font-semibold text-primary">{pct(q.avg_score)}</span> {t('cliente.s360.avg')}
              {" · "}{q.count} {t('cliente.s360.evaluations')}
              <div className="text-2xs text-muted-light mt-0.5">
                {t('cliente.s360.latest')}: {pct(q.latest_score)} ({fmtDate(q.latest_at)})
              </div>
            </div>
          ) : <div className="text-xs text-muted-light">{t('cliente.s360.noQuality')}</div>}
        </div>
      </div>

      {/* Voz do cliente (surveys) */}
      <div className="flex items-start gap-2">
        <MessageSquareHeart className="w-3.5 h-3.5 text-muted-light mt-0.5 shrink-0" aria-hidden="true" />
        <div className="flex-1 min-w-0">
          <div className="text-2xs text-muted uppercase tracking-wide">{t('cliente.s360.surveys')}</div>
          {surveys.length > 0 ? (
            <div className="flex flex-col gap-1 mt-0.5">
              {surveys.map(s => (
                <div key={s.metric} className="text-xs text-dark flex items-baseline justify-between gap-2">
                  <span className="uppercase text-2xs font-semibold text-muted">{s.metric}</span>
                  <span>
                    <span className="font-semibold">{s.latest_label ?? s.latest_value ?? "—"}</span>
                    <span className="text-2xs text-muted-light"> · {t('cliente.s360.avg')} {s.avg_value ?? "—"} · {s.count}×</span>
                  </span>
                </div>
              ))}
            </div>
          ) : <div className="text-xs text-muted-light">{t('cliente.s360.noSurveys')}</div>}
        </div>
      </div>
    </section>
  );
};
