/**
 * ClienteTab (Cliente 360 — C1a: cadastro manual)
 *
 * Aba "Cliente" do Console: mostra o status de identificação da sessão e, quando a
 * identificação automática falhou/errou, deixa o operador BUSCAR um cadastro
 * (nome / customer_id), CRIAR um novo, e VINCULAR à sessão. Vincular grava
 * caller.customer_id no ContextStore da sessão (via /api/inject-context, o mesmo
 * write-back do ManualTagForm) e chama `onLinked`, que REFETCHA o supervisor_state e
 * re-chaveia Histórico/360. ⚠️ Não é poll: esta linha dizia *"o próximo poll do
 * supervisor_state"* e não existe poll nenhum (AUT-49) — o hook só busca no mount e em
 * evento de WS, e `inject-context` não publica evento. É o `onLinked` que fecha o laço,
 * como a prop dele já dizia. Backend: /v1/channels/webhook/identity/*.
 *
 * v1: busca + criar/vincular. Merge de cadastros e external_refs (CRM) = Fase C.
 */

import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { User, Search, UserPlus, Check, AlertCircle } from "lucide-react";
import { getAccessToken } from "../../../../auth/token-store";
import { Customer360Card } from "../Customer360Card";
import { apiFetch } from '@/api/apiFetch'

// Kinds que o operador pode digitar — os nomes do índice de identidade, nunca rótulos.
const ANCHOR_KINDS = ["phone", "email", "cpf"] as const
type AnchorKind = typeof ANCHOR_KINDS[number]

interface ClienteTabProps {
  customerId: string | null;
  contactId:  string | null;
  sessionId:  string | null;
  tenantId?:  string | null;
  /** Dispara um refresh do supervisor_state após vincular/criar — sem ele o prop
   *  customerId fica stale (o hook só refetcha em evento WS) e o selo "current"
   *  não migra para o cadastro recém-vinculado. */
  onLinked?:  () => void;
}

interface CustomerResult {
  customer_id: string;
  status:      string;
  attributes:  Record<string, unknown>;
}

const TENANT_ID = import.meta.env.VITE_TENANT_ID ?? "tenant_demo";

function attrName(attrs: Record<string, unknown> | undefined): string | null {
  const n = attrs?.["nome"] ?? attrs?.["name"];
  return typeof n === "string" && n.trim() ? n : null;
}


export const ClienteTab: React.FC<ClienteTabProps> = ({ customerId, contactId, sessionId, tenantId, onLinked }) => {
  const { t } = useTranslation('agentAssist');
  const tenant = tenantId ?? TENANT_ID;
  // "não identificado" = sem customer_id resolvido (fallback pro contactId efêmero).
  const identified = !!customerId && customerId !== contactId;

  const [query,     setQuery]     = useState("");
  const [results,   setResults]   = useState<CustomerResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched,  setSearched]  = useState(false);
  const [busyId,    setBusyId]    = useState<string | null>(null);
  const [msg,       setMsg]       = useState<{ text: string; ok: boolean } | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [cName,   setCName]   = useState("");
  // IDN-08: o valor é o KIND da âncora (inglês, como o índice espera); o rótulo é i18n.
  // Era "telefone", que não é kind — a âncora era descartada calada e o cliente nascia
  // sem ela.
  const [cKind,   setCKind]   = useState<AnchorKind>("phone");
  const [cValue,  setCValue]  = useState("");
  const [creating, setCreating] = useState(false);

  async function doSearch() {
    const q = query.trim();
    if (!q) return;
    setSearching(true); setMsg(null);
    try {
      const res = await apiFetch(`/v1/channels/webhook/identity/customers/search?${new URLSearchParams({
        tenant_id: tenant, q, limit: "20",
      })}`);
      const data = res.ok ? await res.json() : { results: [] };
      setResults(data.results ?? []);
    } catch {
      setResults([]);
    } finally {
      setSearching(false); setSearched(true);
    }
  }

  async function injectCustomerId(cid: string): Promise<{ ok: boolean; forbidden: boolean }> {
    if (!sessionId) return { ok: false, forbidden: false };
    try {
      // /api/inject-context (mcp-server) exige JWT (Bearer) e valida permissão de
      // escrita por role — 403 = sem permissão (distinto de falha genérica).
      const token = getAccessToken();
      const res = await fetch(`/api/inject-context/${sessionId}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ key: "caller.customer_id", value: cid, confidence: 1.0 }),
      });
      return { ok: res.ok, forbidden: res.status === 403 };
    } catch {
      return { ok: false, forbidden: false };
    }
  }

  function linkFailMsg(forbidden: boolean) {
    return { text: t(forbidden ? 'cliente.noPermission' : 'cliente.linkError'), ok: false };
  }

  async function linkCustomer(cid: string) {
    setBusyId(cid); setMsg(null);
    const r = await injectCustomerId(cid);
    setBusyId(null);
    setMsg(r.ok ? { text: t('cliente.linked'), ok: true } : linkFailMsg(r.forbidden));
    if (r.ok) onLinked?.();
  }

  async function createAndLink() {
    if (!cValue.trim() || !sessionId) return;
    setCreating(true); setMsg(null);
    try {
      // IDN-08: o cadastro do operador é DURÁVEL e carimba procedência `operator`, numa
      // rota com credencial (tenant do JWT, `agent_assist.atender` no pool da sessão).
      // Até aqui: `/identity/resolve` com `provision` (prospect só no Redis, sem
      // procedência) + `/attributes`, e sem identificador caía num `contact_identifier`
      // que também não é kind — cliente sem âncora nenhuma. O identificador é obrigatório.
      const rres = await apiFetch(`/v1/channels/webhook/identity/operator/register`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          anchors: [{ kind: cKind, value: cValue.trim() }],
          name: cName.trim(),
        }),
      });
      const rdata = await rres.json().catch(() => ({}));
      const cid: string = rres.ok ? (rdata.customer_id ?? "") : "";
      if (!cid) {
        const key = rres.status === 403 ? 'cliente.noPermission'
          : rdata.reason === 'ambiguous' ? 'cliente.ambiguous'
          : rdata.reason === 'anchor_owned_by_other_customer' ? 'cliente.anchorTaken'
          : rdata.reason === 'invalid_anchors' ? 'cliente.invalidAnchor'
          : 'cliente.createError';
        setMsg({ text: t(key), ok: false });
        setCreating(false);
        return;
      }
      const r = await injectCustomerId(cid);
      setShowCreate(false); setCName(""); setCValue("");
      setMsg(r.ok
        ? { text: t(rdata.outcome === 'existing' ? 'cliente.existingLinked' : 'cliente.created', { id: cid }), ok: true }
        : linkFailMsg(r.forbidden));
      if (r.ok) onLinked?.();
    } catch {
      setMsg({ text: t('cliente.createError'), ok: false });
    } finally {
      setCreating(false);
    }
  }

  if (!sessionId) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-sm text-muted-light p-4 gap-2 h-full">
        <User className="w-8 h-8" aria-hidden="true" />
        <span>{t('cliente.noSession')}</span>
      </div>
    );
  }

  return (
    <div className="absolute inset-0 overflow-y-auto p-3 space-y-3">

      {/* ── Identificação atual ── */}
      {identified ? (
        <section className="bg-green-light border border-green/30 rounded-lg p-3">
          <div className="text-2xs font-semibold text-green-text uppercase tracking-wide mb-1">
            {t('cliente.identified')}
          </div>
          <div className="font-mono text-sm text-dark break-all">{customerId}</div>
        </section>
      ) : null}

      {/* ── Card 360 (C1b): só quando identificado ── */}
      {identified && customerId && <Customer360Card customerId={customerId} />}

      {!identified && (
        <section className="bg-warning-light border border-warning/30 rounded-lg p-3 flex items-start gap-2">
          <AlertCircle className="w-4 h-4 text-warning-text shrink-0 mt-0.5" aria-hidden="true" />
          <div className="text-xs text-warning-text">{t('cliente.notIdentified')}</div>
        </section>
      )}

      {/* ── Busca de cadastro ── */}
      <section>
        <div className="text-2xs font-semibold text-muted uppercase tracking-wide mb-1.5">
          {t('cliente.searchTitle')}
        </div>
        <div className="flex items-center gap-1.5">
          <div className="relative flex-1">
            <Search className="w-3.5 h-3.5 text-muted-light absolute left-2 top-1/2 -translate-y-1/2" aria-hidden="true" />
            <input
              type="text" value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") doSearch(); }}
              placeholder={t('cliente.searchPlaceholder')}
              className="w-full text-xs border border-border-strong rounded pl-7 pr-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/40 text-dark bg-white placeholder-muted-light"
            />
          </div>
          <button onClick={doSearch} disabled={searching || !query.trim()}
            className="text-xs px-2.5 py-1.5 rounded bg-primary text-white font-medium disabled:opacity-40 hover:bg-primary-dark transition-colors">
            {searching ? "…" : t('cliente.search')}
          </button>
        </div>

        {searched && !searching && results.length === 0 && (
          <p className="text-xs text-muted-light mt-2">{t('cliente.noResults')}</p>
        )}
        {results.length > 0 && (
          <div className="flex flex-col gap-1.5 mt-2">
            {results.map(r => (
              <div key={r.customer_id} className="border border-border rounded-lg px-3 py-2 flex items-center gap-2">
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium text-dark truncate">
                    {attrName(r.attributes) ?? <span className="text-muted-light italic">{t('cliente.noName')}</span>}
                  </div>
                  <div className="font-mono text-2xs text-muted-light truncate">{r.customer_id}</div>
                </div>
                {customerId === r.customer_id ? (
                  <span className="text-2xs text-green-text inline-flex items-center gap-0.5"><Check className="w-3 h-3" />{t('cliente.current')}</span>
                ) : (
                  <button onClick={() => linkCustomer(r.customer_id)} disabled={busyId === r.customer_id}
                    className="text-2xs px-2 py-1 rounded border border-primary/40 text-primary hover:bg-primary-light transition-colors disabled:opacity-40 shrink-0">
                    {busyId === r.customer_id ? "…" : t('cliente.link')}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── Criar cadastro ── */}
      <section>
        {!showCreate ? (
          <button onClick={() => setShowCreate(true)}
            className="w-full text-xs inline-flex items-center justify-center gap-1.5 border border-dashed border-border-strong rounded-lg py-2 text-muted hover:text-dark hover:border-primary/40 transition-colors">
            <UserPlus className="w-3.5 h-3.5" aria-hidden="true" />
            {t('cliente.createToggle')}
          </button>
        ) : (
          <div className="border border-border rounded-lg p-3 space-y-2">
            <div className="text-2xs font-semibold text-muted uppercase tracking-wide">{t('cliente.createTitle')}</div>
            <input type="text" value={cName} onChange={e => setCName(e.target.value)} placeholder={t('cliente.nameLabel')}
              className="w-full text-xs border border-border-strong rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/40 text-dark bg-white placeholder-muted-light" />
            <div className="flex items-center gap-1.5">
              <select value={cKind} onChange={e => setCKind(e.target.value as AnchorKind)}
                className="text-2xs border border-border rounded px-1.5 py-1.5 bg-white text-dark focus:outline-none focus:ring-1 focus:ring-primary/40">
                {ANCHOR_KINDS.map(k => <option key={k} value={k}>{t(`cliente.kind.${k}`)}</option>)}
              </select>
              <input type="text" value={cValue} onChange={e => setCValue(e.target.value)} placeholder={t('cliente.anchorLabel')}
                className="flex-1 text-xs border border-border-strong rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/40 text-dark bg-white placeholder-muted-light" />
            </div>
            <div className="flex gap-1.5">
              <button onClick={createAndLink} disabled={creating || !cValue.trim()}
                className="flex-1 text-xs py-1.5 rounded bg-primary text-white font-medium disabled:opacity-40 hover:bg-primary-dark transition-colors">
                {creating ? t('cliente.creating') : t('cliente.createAndLink')}
              </button>
              <button onClick={() => setShowCreate(false)} className="text-xs px-2.5 py-1.5 rounded border border-border text-muted hover:text-dark transition-colors">
                {t('cliente.cancel')}
              </button>
            </div>
          </div>
        )}
      </section>

      {msg && (
        <div className={`text-xs rounded p-2 border ${msg.ok ? "bg-green-light text-green-text border-green/30" : "bg-red-light text-red-text border-red/30"}`}>
          {msg.text}
        </div>
      )}
    </div>
  );
};
