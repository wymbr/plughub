/**
 * AgentReportsPage — Arc 8
 * Route: /config/agent-reports  (roles: supervisor, admin)
 *
 * Two tabs:
 *   1. Disponibilidade — pivot table agent × date showing total pause time
 *   2. Pausas          — detailed pause interval rows with reason, duration, pool, CSV export
 *
 * Data source: GET /reports/agent-availability (analytics-api port 3500,
 * proxied via Vite as /reports)
 */

import React, { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/ui/PageHeader";

// ── Types ─────────────────────────────────────────────────────────────────────

interface ReasonBreakdown {
  reason_id:    string;
  reason_label: string;
  count:        number;
  total_ms:     number;
}

interface AvailabilityRow {
  agent_type_id:    string;
  pool_id:          string;
  period_date:      string;  // "YYYY-MM-DD"
  total_pauses:     number;
  total_pause_ms:   number;
  reason_breakdown: ReasonBreakdown[];
}

interface AvailabilityMeta {
  page:      number;
  page_size: number;
  total:     number;
  from_dt:   string;
  to_dt:     string;
}

interface AvailabilityResponse {
  data: AvailabilityRow[];
  meta: AvailabilityMeta;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDuration(ms: number): string {
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
  const m = Math.floor(ms / 60_000);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  return `${m}m`;
}

function isoToday(): string {
  return new Date().toISOString().slice(0, 10);
}

function iso7DaysAgo(): string {
  const d = new Date();
  d.setDate(d.getDate() - 6);
  return d.toISOString().slice(0, 10);
}

function shortAgent(id: string): string {
  return id.replace(/_v\d+$/, "").replace(/_/g, " ");
}

function shortPool(id: string): string {
  return id.replace(/_/g, " ").replace(/\s*(humano|ia|v\d+)$/i, "").trim() || id;
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useAvailability(params: {
  fromDt:    string;
  toDt:      string;
  poolId:    string;
  agentId:   string;
  page:      number;
  pageSize:  number;
  tenantId:  string;
}) {
  const [data,    setData]    = useState<AvailabilityRow[]>([]);
  const [meta,    setMeta]    = useState<AvailabilityMeta | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  const { fromDt, toDt, poolId, agentId, page, pageSize, tenantId } = params;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const qs = new URLSearchParams({
      tenant_id: tenantId,
      from_dt:   fromDt,
      to_dt:     toDt,
      page:      String(page),
      page_size: String(pageSize),
    });
    if (poolId)  qs.set("pool_id",        poolId);
    if (agentId) qs.set("agent_type_id",  agentId);

    fetch(`/reports/agent-availability?${qs}`)
      .then(r => r.ok ? (r.json() as Promise<AvailabilityResponse>) : Promise.reject(r.status))
      .then(resp => {
        if (cancelled) return;
        setData(resp.data ?? []);
        setMeta(resp.meta ?? null);
      })
      .catch(e => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [fromDt, toDt, poolId, agentId, page, pageSize, tenantId]);

  return { data, meta, loading, error };
}

// ── Pivot: Disponibilidade tab ────────────────────────────────────────────────

interface PivotProps { rows: AvailabilityRow[] }

const DisponibilidadeTab: React.FC<PivotProps> = ({ rows }) => {
  const { t } = useTranslation("agentReports");
  if (rows.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-light text-sm">
        {t("availability.noData")}
      </div>
    );
  }

  // Collect all unique dates across rows
  const dateSet = new Set(rows.map(r => r.period_date));
  const dates   = [...dateSet].sort();

  // Group by agent+pool
  type Key = string;
  const groups = new Map<Key, { agent: string; pool: string; byDate: Map<string, AvailabilityRow> }>();
  for (const row of rows) {
    const key = `${row.agent_type_id}|${row.pool_id}`;
    if (!groups.has(key)) {
      groups.set(key, { agent: row.agent_type_id, pool: row.pool_id, byDate: new Map() });
    }
    groups.get(key)!.byDate.set(row.period_date, row);
  }

  return (
    <div className="flex-1 overflow-auto">
      <table className="min-w-full text-xs border-collapse">
        <thead className="sticky top-0 bg-white z-10">
          <tr className="border-b border-border">
            <th className="text-left px-3 py-2 font-semibold text-muted whitespace-nowrap min-w-40">Agent</th>
            <th className="text-left px-3 py-2 font-semibold text-muted whitespace-nowrap min-w-[120px]">Pool</th>
            {dates.map(d => (
              <th key={d} className="px-2 py-2 font-semibold text-muted text-center whitespace-nowrap min-w-20">
                {d.slice(5)}  {/* MM-DD */}
              </th>
            ))}
            <th className="px-3 py-2 font-semibold text-muted text-right whitespace-nowrap">Total</th>
          </tr>
        </thead>
        <tbody>
          {[...groups.entries()].map(([key, { agent, pool, byDate }]) => {
            const totalMs = [...byDate.values()].reduce((s, r) => s + r.total_pause_ms, 0);
            return (
              <tr key={key} className="border-b border-border hover:bg-surface-muted transition-colors">
                <td className="px-3 py-2 text-dark font-medium truncate max-w-[200px]" title={agent}>
                  {shortAgent(agent)}
                </td>
                <td className="px-3 py-2 text-muted truncate max-w-[140px]" title={pool}>
                  {shortPool(pool)}
                </td>
                {dates.map(d => {
                  const row = byDate.get(d);
                  if (!row) return <td key={d} className="px-2 py-2 text-center text-border">—</td>;
                  const pauseMs = row.total_pause_ms;
                  const intensity = Math.min(pauseMs / (4 * 3_600_000), 1);  // cap at 4h
                  const bg = pauseMs === 0
                    ? "bg-surface-muted text-border-strong"
                    : intensity < 0.25
                      ? "bg-warning-light text-warning-text"
                      : intensity < 0.5
                        ? "bg-warning-light text-warning-text"
                        : "bg-warning text-white";
                  return (
                    <td key={d} className={`px-2 py-2 text-center font-mono tabular-nums ${bg}`}
                        title={`${row.total_pauses} pausa(s) · ${fmtDuration(pauseMs)}`}>
                      {pauseMs > 0 ? fmtDuration(pauseMs) : "—"}
                    </td>
                  );
                })}
                <td className="px-3 py-2 text-right font-semibold text-dark tabular-nums">
                  {totalMs > 0 ? fmtDuration(totalMs) : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

// ── Detail: Pausas tab ────────────────────────────────────────────────────────

const PausasTab: React.FC<{
  rows:    AvailabilityRow[];
  meta:    AvailabilityMeta | null;
  page:    number;
  onPage:  (p: number) => void;
  csvUrl:  string;
}> = ({ rows, meta, page, onPage, csvUrl }) => {
  const { t } = useTranslation("agentReports");
  // Flatten: one row per (agent, pool, date, reason)
  const flat: Array<{
    date:   string;
    agent:  string;
    pool:   string;
    reason: string;
    count:  number;
    ms:     number;
  }> = [];

  for (const r of rows) {
    if (r.reason_breakdown.length === 0) {
      flat.push({
        date: r.period_date, agent: r.agent_type_id, pool: r.pool_id,
        reason: "—", count: r.total_pauses, ms: r.total_pause_ms,
      });
    } else {
      for (const rb of r.reason_breakdown) {
        flat.push({
          date: r.period_date, agent: r.agent_type_id, pool: r.pool_id,
          reason: rb.reason_label || rb.reason_id,
          count: rb.count, ms: rb.total_ms,
        });
      }
    }
  }

  if (flat.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-2 text-muted-light text-sm">
        <span className="text-3xl">📋</span>
        <p>{t("pauses.noData")}</p>
      </div>
    );
  }

  const totalPages = meta ? Math.ceil(meta.total / (meta.page_size || 50)) : 1;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* CSV export */}
      <div className="flex items-center justify-end px-4 py-2 border-b border-border bg-surface-muted flex-shrink-0">
        <a
          href={csvUrl}
          download
          className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium
            border border-border-strong text-muted hover:bg-white hover:border-border-strong transition-colors"
        >
          ⬇ {t("pauses.exportCsv")}
        </a>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        <table className="min-w-full text-xs border-collapse">
          <thead className="sticky top-0 bg-white z-10">
            <tr className="border-b border-border">
              <th className="text-left px-3 py-2 font-semibold text-muted">Date</th>
              <th className="text-left px-3 py-2 font-semibold text-muted">Agent</th>
              <th className="text-left px-3 py-2 font-semibold text-muted">Pool</th>
              <th className="text-left px-3 py-2 font-semibold text-muted">{t("pauses.reason")}</th>
              <th className="text-right px-3 py-2 font-semibold text-muted">{t("pauses.count")}</th>
              <th className="text-right px-3 py-2 font-semibold text-muted">{t("pauses.duration")}</th>
              <th className="text-right px-3 py-2 font-semibold text-muted">Avg per pause</th>
            </tr>
          </thead>
          <tbody>
            {flat.map((r, i) => (
              <tr key={i} className="border-b border-border hover:bg-surface-muted transition-colors">
                <td className="px-3 py-2 text-muted whitespace-nowrap font-mono">{r.date}</td>
                <td className="px-3 py-2 text-dark font-medium truncate max-w-[180px]" title={r.agent}>
                  {shortAgent(r.agent)}
                </td>
                <td className="px-3 py-2 text-muted truncate max-w-[130px]" title={r.pool}>
                  {shortPool(r.pool)}
                </td>
                <td className="px-3 py-2">
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-warning-light
                    border border-warning/30 text-warning-text font-medium whitespace-nowrap">
                    {r.reason}
                  </span>
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-dark">{r.count}</td>
                <td className="px-3 py-2 text-right tabular-nums font-semibold text-dark">
                  {fmtDuration(r.ms)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-muted">
                  {r.count > 0 ? fmtDuration(Math.round(r.ms / r.count)) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {meta && totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-2 border-t border-border bg-white flex-shrink-0">
          <span className="text-xs text-muted">
            {t("pagination.results", { count: meta.total })}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => onPage(page - 1)}
              disabled={page <= 1}
              className="px-2 py-1 text-xs rounded border border-border disabled:opacity-40 hover:bg-surface-muted"
            >
              ← {t("pagination.prev")}
            </button>
            <span className="text-xs text-muted px-2">{page} / {totalPages}</span>
            <button
              onClick={() => onPage(page + 1)}
              disabled={page >= totalPages}
              className="px-2 py-1 text-xs rounded border border-border disabled:opacity-40 hover:bg-surface-muted"
            >
              {t("pagination.next")} →
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

// ── Main page ─────────────────────────────────────────────────────────────────

type TabId = "disponibilidade" | "pausas";

const AgentReportsPage: React.FC = () => {
  const { t } = useTranslation("agentReports");
  const tenantId = (import.meta as unknown as { env?: Record<string, string> }).env?.VITE_TENANT_ID ?? "tenant_demo";

  const [tab,     setTab]    = useState<TabId>("disponibilidade");
  const [fromDt,  setFromDt] = useState(iso7DaysAgo());
  const [toDt,    setToDt]   = useState(isoToday());
  const [poolId,  setPoolId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [page,    setPage]   = useState(1);

  // Reset page when filters change
  const applyFilters = useCallback(() => { setPage(1); }, []);
  useEffect(applyFilters, [fromDt, toDt, poolId, agentId, applyFilters]);

  const { data, meta, loading, error } = useAvailability({
    fromDt, toDt, poolId, agentId, page, pageSize: 50, tenantId,
  });

  const csvUrl = (() => {
    const qs = new URLSearchParams({
      tenant_id: tenantId,
      from_dt:   fromDt,
      to_dt:     toDt,
      format:    "csv",
    });
    if (poolId)  qs.set("pool_id",       poolId);
    if (agentId) qs.set("agent_type_id", agentId);
    return `/reports/agent-availability?${qs}`;
  })();

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      <PageHeader
        title={t("title")}
        breadcrumbs={[{ label: t("breadcrumbs.config") }, { label: t("title") }]}
      />

      {/* ── Filter bar ── */}
      <div className="bg-white border-b border-border px-6 py-3 flex flex-wrap items-end gap-4 flex-shrink-0">
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-muted">{t("filters.from")}</label>
          <input
            type="date"
            value={fromDt}
            max={toDt}
            onChange={e => setFromDt(e.target.value)}
            className="text-sm border border-border-strong rounded-md px-2 py-1 focus:outline-none
              focus:ring-2 focus:ring-primary/40"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-muted">{t("filters.to")}</label>
          <input
            type="date"
            value={toDt}
            min={fromDt}
            max={isoToday()}
            onChange={e => setToDt(e.target.value)}
            className="text-sm border border-border-strong rounded-md px-2 py-1 focus:outline-none
              focus:ring-2 focus:ring-primary/40"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-muted">{t("filters.pool")}</label>
          <input
            type="text"
            value={poolId}
            onChange={e => setPoolId(e.target.value.trim())}
            placeholder={t("filters.poolPlaceholder")}
            className="text-sm border border-border-strong rounded-md px-2 py-1 focus:outline-none
              focus:ring-2 focus:ring-primary/40 w-44"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-muted">{t("filters.agentType")}</label>
          <input
            type="text"
            value={agentId}
            onChange={e => setAgentId(e.target.value.trim())}
            placeholder={t("filters.agentTypePlaceholder")}
            className="text-sm border border-border-strong rounded-md px-2 py-1 focus:outline-none
              focus:ring-2 focus:ring-primary/40 w-48"
          />
        </div>
        {loading && (
          <span className="text-xs text-muted-light animate-pulse self-end pb-1.5">{t("filters.loading")}</span>
        )}
      </div>

      {/* ── Tab bar ── */}
      <div className="bg-white border-b border-border px-6 flex items-end gap-0 flex-shrink-0">
        {([
          { id: "disponibilidade", label: t("tabs.availability") },
          { id: "pausas",          label: t("tabs.pauses") },
        ] as { id: TabId; label: string }[]).map(tabDef => (
          <button
            key={tabDef.id}
            onClick={() => setTab(tabDef.id)}
            className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              tab === tabDef.id
                ? "border-primary text-primary"
                : "border-transparent text-muted hover:text-dark"
            }`}
          >
            {tabDef.label}
          </button>
        ))}
      </div>

      {/* ── Content ── */}
      <div className="flex-1 overflow-hidden bg-white flex flex-col">
        {error ? (
          <div className="flex-1 flex items-center justify-center text-sm text-red">
            {t("error.loading")}: {error}
          </div>
        ) : tab === "disponibilidade" ? (
          <DisponibilidadeTab rows={data} />
        ) : (
          <PausasTab
            rows={data}
            meta={meta}
            page={page}
            onPage={setPage}
            csvUrl={csvUrl}
          />
        )}
      </div>
    </div>
  );
};

export default AgentReportsPage;
