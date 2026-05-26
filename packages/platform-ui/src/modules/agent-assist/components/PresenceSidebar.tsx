/**
 * PresenceSidebar
 * Left panel (~160px expanded / 48px collapsed) showing all pools available
 * to the agent, with a Ready/Offline toggle per pool.
 *
 * Props:
 *   pools          — full list of pools the agent can join (from /v1/pools)
 *   activePools    — pool_ids currently connected (Ready)
 *   statuses       — Map<poolId, PoolConnectionStatus> from useMultiPoolWebSocket
 *   onToggle       — called with poolId when agent clicks the toggle
 *   collapsed      — controls expand/collapse
 *   onCollapse     — toggle collapsed state
 */

import React from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight, ChevronLeft } from "lucide-react";
import { PoolInfo, PoolConnectionStatus } from "../types";

interface PresenceSidebarProps {
  pools:       PoolInfo[];
  activePools: string[];
  statuses:    Map<string, PoolConnectionStatus>;
  onToggle:    (poolId: string) => void;
  collapsed:   boolean;
  onCollapse:  () => void;
}

// ── Status dot ────────────────────────────────────────────────────────────────
function StatusDot({ status }: { status: PoolConnectionStatus | undefined }) {
  const color =
    status === "connected"    ? "bg-green" :
    status === "connecting"   ? "bg-warning animate-pulse" :
                                "bg-muted-light";
  return <span className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${color}`} />;
}

// ── Channel icons (same as ContactList) ───────────────────────────────────────
const CHANNEL_ICON: Record<string, string> = {
  webchat:   "💬",
  whatsapp:  "📱",
  voice:     "📞",
  email:     "✉️",
  sms:       "📩",
  telegram:  "✈️",
  instagram: "📷",
  webrtc:    "🎙️",
};

function channelIcons(channelTypes: string[]): string {
  return channelTypes.slice(0, 3).map(c => CHANNEL_ICON[c] ?? "💬").join(" ");
}

// ── Pool row ──────────────────────────────────────────────────────────────────
interface PoolRowProps {
  pool:      PoolInfo;
  active:    boolean;
  status:    PoolConnectionStatus | undefined;
  onToggle:  () => void;
  collapsed: boolean;
}

const PoolRow: React.FC<PoolRowProps> = ({ pool, active, status, onToggle, collapsed }) => {
  const { t } = useTranslation('agentAssist');
  const label = pool.display_name ?? pool.pool_id;

  if (collapsed) {
    return (
      <button
        onClick={onToggle}
        title={`${label} — ${t(active ? 'presence.ready' : 'presence.off')} (${t(active ? 'presence.clickOffline' : 'presence.clickReady')})`}
        className={`w-full flex items-center justify-center py-2.5 transition-colors
          ${active
            ? "text-primary hover:bg-primary-light"
            : "text-muted-light hover:bg-surface-alt"
          }`}
      >
        <span className="text-base leading-none">
          {channelIcons(pool.channel_types) || "💬"}
        </span>
      </button>
    );
  }

  return (
    <div className="px-2 py-1.5">
      <div
        className={`flex items-center gap-2 px-2 py-2 rounded-lg transition-colors cursor-pointer
          ${active
            ? "bg-primary-light hover:bg-primary-light"
            : "hover:bg-surface-alt"
          }`}
        onClick={onToggle}
        role="button"
        tabIndex={0}
        onKeyDown={e => e.key === "Enter" && onToggle()}
        title={t(active ? 'presence.clickOffline' : 'presence.clickReady')}
      >
        {/* Status dot */}
        <StatusDot status={active ? status : undefined} />

        {/* Pool name */}
        <span
          className={`flex-1 text-xs font-medium truncate leading-snug
            ${active ? "text-dark" : "text-muted-light"}`}
          title={label}
        >
          {label}
        </span>

        {/* Toggle pill */}
        <span
          className={`text-2xs font-bold px-1.5 py-0.5 rounded-full flex-shrink-0
            ${active
              ? "bg-green-light text-green-text"
              : "bg-surface-alt text-muted-light"
            }`}
        >
          {t(active ? 'presence.ready' : 'presence.off')}
        </span>
      </div>

      {/* Channel types micro-label */}
      {active && pool.channel_types.length > 0 && (
        <p className="text-2xs text-muted-light px-2 mt-0.5 truncate">
          {pool.channel_types.join(" · ")}
        </p>
      )}
    </div>
  );
};

// ── Main component ─────────────────────────────────────────────────────────────
export const PresenceSidebar: React.FC<PresenceSidebarProps> = ({
  pools,
  activePools,
  statuses,
  onToggle,
  collapsed,
  onCollapse,
}) => {
  const { t } = useTranslation('agentAssist');
  const activeSet = new Set(activePools);
  const activeCount = activePools.length;

  return (
    <div
      className={`flex flex-col h-full border-r border-border bg-white flex-shrink-0 transition-all duration-200
        ${collapsed ? "w-12" : "w-44"}`}
    >
      {/* Header row */}
      <div className={`flex items-center border-b border-border bg-surface-muted flex-shrink-0
        ${collapsed ? "justify-center py-2.5" : "px-3 py-2 gap-1.5"}`}
      >
        {!collapsed && (
          <span className="text-xs font-semibold text-muted uppercase tracking-wide flex-1">
            {t('presence.title')}
          </span>
        )}
        {!collapsed && activeCount > 0 && (
          <span className="text-2xs text-primary font-bold bg-primary-light rounded-full px-1.5 py-0.5">
            {activeCount}
          </span>
        )}
        <button
          onClick={onCollapse}
          className="text-muted-light hover:text-muted text-sm leading-none"
          title={t(collapsed ? 'presence.expand' : 'presence.collapse')}
        >
          {collapsed
            ? <ChevronRight className="w-4 h-4" aria-hidden="true" />
            : <ChevronLeft  className="w-4 h-4" aria-hidden="true" />
          }
        </button>
      </div>

      {/* Pool list */}
      <div className="flex-1 overflow-y-auto py-1">
        {pools.length === 0 ? (
          !collapsed && (
            <p className="text-xs text-muted-light text-center px-3 py-4 leading-snug">
              {t('presence.noPools')}
            </p>
          )
        ) : (
          pools.map(pool => (
            <PoolRow
              key={pool.pool_id}
              pool={pool}
              active={activeSet.has(pool.pool_id)}
              status={statuses.get(pool.pool_id)}
              onToggle={() => onToggle(pool.pool_id)}
              collapsed={collapsed}
            />
          ))
        )}
      </div>

      {/* Footer: legend when expanded */}
      {!collapsed && (
        <div className="px-3 py-2 border-t border-border bg-surface-muted flex-shrink-0">
          <p className="text-2xs text-muted-light leading-snug">
            {t('presence.hint')}
          </p>
        </div>
      )}
    </div>
  );
};
