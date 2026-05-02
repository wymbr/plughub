/**
 * CannedPhrasesPalette
 *
 * Floating palette triggered by the "/" button (or typing "/") in AgentInput.
 * Two sections:
 *   1. "Frases rápidas" — filterable pre-written response templates
 *   2. "Especialistas"  — @mention commands to activate specialist agents
 *
 * Selecting a phrase calls onSelect(text) and the palette closes.
 * Escape or click-outside also closes the palette.
 */

import React, { useEffect, useRef, useState, useCallback } from "react";
import { SupervisorCapabilities } from "../types";

// ── Types ─────────────────────────────────────────────────────────────────────
export interface CannedPhrase {
  id:    string;
  label: string;   // Short name shown in the list
  text:  string;   // Full text inserted into the textarea
}

export interface SpecialistEntry {
  alias:       string;   // e.g. "retencao"  (used as @retencao)
  label:       string;   // Display name
  description: string;   // Short description
}

// ── Default phrases ────────────────────────────────────────────────────────────
export const DEFAULT_PHRASES: CannedPhrase[] = [
  {
    id: "saudacao",
    label: "Saudação",
    text: "Olá! Bem-vindo ao suporte PlugHub. Como posso ajudá-lo hoje?",
  },
  {
    id: "aguarde",
    label: "Aguarde um momento",
    text: "Um momento, por favor. Vou verificar essa informação para você.",
  },
  {
    id: "entendido",
    label: "Entendido, vou resolver",
    text: "Entendido! Deixa comigo — vou resolver isso agora mesmo.",
  },
  {
    id: "desculpa",
    label: "Pedir desculpas",
    text: "Peço desculpas pelo inconveniente causado. Vou solucionar isso imediatamente.",
  },
  {
    id: "transferencia",
    label: "Aviso de transferência",
    text: "Vou transferi-lo para o especialista responsável por este assunto. Um momento.",
  },
  {
    id: "encerramento",
    label: "Encerramento",
    text: "Obrigado pelo contato! Foi um prazer ajudá-lo. Tenha um ótimo dia! 😊",
  },
  {
    id: "confirmar_dados",
    label: "Confirmar dados",
    text: "Para que eu possa prosseguir, poderia confirmar seu CPF ou número de conta?",
  },
  {
    id: "prazo",
    label: "Informar prazo",
    text: "Seu pedido será processado em até 2 dias úteis. Enviaremos uma confirmação por e-mail.",
  },
];

// ── Props ─────────────────────────────────────────────────────────────────────
interface CannedPhrasesPaletteProps {
  phrases?:      CannedPhrase[];
  capabilities?: SupervisorCapabilities | null;
  onSelect:      (text: string) => void;
  onClose:       () => void;
}

// ── Component ─────────────────────────────────────────────────────────────────
export const CannedPhrasesPalette: React.FC<CannedPhrasesPaletteProps> = ({
  phrases = DEFAULT_PHRASES,
  capabilities,
  onSelect,
  onClose,
}) => {
  const [query,     setQuery]     = useState("");
  const [activeIdx, setActiveIdx] = useState<number>(-1);
  const searchRef   = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Build specialist list from capabilities, or use defaults
  const specialists: SpecialistEntry[] = React.useMemo(() => {
    const fromCaps = capabilities?.suggested_agents?.map(a => ({
      alias:       a.agent_type_id.replace(/agente_|_v\d+/gi, "").replace(/_/g, ""),
      label:       a.agent_type_id,
      description: a.reason ?? "Especialista disponível",
    })) ?? [];
    if (fromCaps.length > 0) return fromCaps;
    // Fallback defaults for when capabilities aren't loaded
    return [
      { alias: "retencao",  label: "Retenção",     description: "Especialista em retenção de clientes" },
      { alias: "cobranca",  label: "Cobrança",      description: "Dívidas, faturas e negociações" },
      { alias: "tecnico",   label: "Suporte Téc.",  description: "Problemas técnicos e integrações" },
      { alias: "comercial", label: "Comercial",     description: "Vendas, planos e upgrades" },
    ];
  }, [capabilities]);

  // Focus search on mount
  useEffect(() => {
    searchRef.current?.focus();
  }, []);

  // Close on Escape, navigate with arrows
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
  }, [onClose]);

  // Click-outside closes
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  const q = query.toLowerCase().trim();

  const filteredPhrases = phrases.filter(p =>
    !q || p.label.toLowerCase().includes(q) || p.text.toLowerCase().includes(q)
  );

  const filteredSpecialists = specialists.filter(s =>
    !q || s.alias.includes(q) || s.label.toLowerCase().includes(q) || s.description.toLowerCase().includes(q)
  );

  const totalItems = filteredPhrases.length + filteredSpecialists.length;

  // Keyboard navigation
  const handleSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx(prev => Math.min(prev + 1, totalItems - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx(prev => Math.max(prev - 1, 0));
    } else if (e.key === "Enter" && activeIdx >= 0) {
      e.preventDefault();
      if (activeIdx < filteredPhrases.length) {
        onSelect(filteredPhrases[activeIdx].text);
      } else {
        const sp = filteredSpecialists[activeIdx - filteredPhrases.length];
        if (sp) onSelect(`@${sp.alias} `);
      }
      onClose();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  return (
    <div
      ref={containerRef}
      onKeyDown={handleKeyDown}
      className="absolute bottom-full left-0 right-0 mb-1 mx-2 z-50
        bg-white rounded-xl border border-gray-200 shadow-xl
        flex flex-col overflow-hidden"
      style={{ maxHeight: "360px" }}
    >
      {/* ── Search bar ── */}
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-gray-100 flex-shrink-0">
        <span className="text-sm font-mono text-indigo-600 font-bold select-none">/</span>
        <input
          ref={searchRef}
          type="text"
          value={query}
          onChange={e => { setQuery(e.target.value); setActiveIdx(-1); }}
          onKeyDown={handleSearchKeyDown}
          placeholder="Buscar frases ou @especialista…"
          className="flex-1 text-sm outline-none placeholder-gray-400 bg-transparent"
          autoComplete="off"
        />
        <kbd
          onClick={onClose}
          className="text-[10px] text-gray-400 border border-gray-200 rounded px-1 py-0.5
            cursor-pointer hover:bg-gray-50 font-mono select-none"
        >
          ESC
        </kbd>
      </div>

      {/* ── Results ── */}
      <div className="overflow-y-auto flex-1">

        {/* Frases rápidas */}
        {filteredPhrases.length > 0 && (
          <div>
            <p className="px-3 pt-2.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
              Frases rápidas
            </p>
            {filteredPhrases.map((phrase, i) => (
              <button
                key={phrase.id}
                onClick={() => { onSelect(phrase.text); onClose(); }}
                className={`w-full text-left px-3 py-2 transition-colors flex flex-col gap-0.5
                  ${activeIdx === i ? "bg-indigo-50" : "hover:bg-gray-50"}`}
              >
                <span className="text-xs font-semibold text-gray-700">{phrase.label}</span>
                <span className="text-[11px] text-gray-500 truncate">{phrase.text}</span>
              </button>
            ))}
          </div>
        )}

        {/* Especialistas */}
        {filteredSpecialists.length > 0 && (
          <div>
            <p className="px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400
              border-t border-gray-100 mt-1">
              Especialistas <span className="text-gray-300">(@mention)</span>
            </p>
            {filteredSpecialists.map((sp, i) => {
              const globalIdx = filteredPhrases.length + i;
              return (
                <button
                  key={sp.alias}
                  onClick={() => { onSelect(`@${sp.alias} `); onClose(); }}
                  className={`w-full text-left px-3 py-2 transition-colors flex items-center gap-3
                    ${activeIdx === globalIdx ? "bg-indigo-50" : "hover:bg-gray-50"}`}
                >
                  <span className="flex-shrink-0 w-6 h-6 rounded-full bg-indigo-100
                    flex items-center justify-center text-[11px] font-bold text-indigo-600">
                    @
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-gray-700">{sp.label}</span>
                      <code className="text-[10px] text-indigo-500 font-mono">@{sp.alias}</code>
                    </div>
                    <span className="text-[11px] text-gray-400 truncate block">{sp.description}</span>
                  </div>
                </button>
              );
            })}
          </div>
        )}

        {/* Empty state */}
        {filteredPhrases.length === 0 && filteredSpecialists.length === 0 && (
          <div className="py-8 text-center text-xs text-gray-400">
            Nenhum resultado para "<span className="font-mono">{query}</span>"
          </div>
        )}
      </div>
    </div>
  );
};
