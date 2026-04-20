/**
 * Dashboard App
 * Single-page app with browser-history-free navigation between three screens:
 *   Screen 1 — Pools list
 *   Screen 2 — Agents in a pool
 *   Screen 3 — Contact evaluations for an agent
 */

import React, { useState } from "react";
import { Screen } from "./types";
import { Breadcrumb } from "./components/Breadcrumb";
import { PoolsPage } from "./pages/PoolsPage";
import { AgentsPage } from "./pages/AgentsPage";
import { ContactsPage } from "./pages/ContactsPage";

const App: React.FC = () => {
  const [screen, setScreen] = useState<Screen>({ type: "pools" });

  return (
    <div className="min-h-screen bg-slate-100 flex flex-col">
      {/* Top nav */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-4 sticky top-0 z-10">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-indigo-600 flex items-center justify-center">
            <span className="text-white text-xs font-bold">P</span>
          </div>
          <span className="font-semibold text-gray-800 text-sm">
            PlugHub Dashboard
          </span>
        </div>

        <div className="w-px h-5 bg-gray-200" />

        <Breadcrumb screen={screen} onNavigate={setScreen} />
      </header>

      {/* Content */}
      <main className="flex-1 max-w-7xl w-full mx-auto">
        {screen.type === "pools" && (
          <PoolsPage onNavigate={setScreen} />
        )}
        {screen.type === "agents" && (
          <AgentsPage
            poolId={screen.poolId}
            onNavigate={setScreen}
          />
        )}
        {screen.type === "contacts" && (
          <ContactsPage
            agentId={screen.agentId}
            poolId={screen.poolId}
          />
        )}
      </main>
    </div>
  );
};

export default App;
