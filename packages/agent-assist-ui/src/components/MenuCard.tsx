/**
 * MenuCard
 * Read-only (observation mode) card that visualises a menu.render event.
 *
 * Renders all five interaction types defined by Skill Flow:
 *   text       — free-text prompt, awaiting customer reply
 *   button     — 1–3 clickable-looking chips (disabled)
 *   list       — scrollable list of labelled options (disabled)
 *   checklist  — multi-select checkboxes (disabled)
 *   form       — sequential fields preview (disabled)
 *
 * Future: substitution mode will enable actual submission on behalf of the
 * customer via POST /api/menu_submit/{sessionId} + menu_id + result.
 * All interactive elements are pre-wired with `disabled` so the transition
 * to substitution mode requires only removing that attribute + adding a
 * submit handler.
 */

import React from "react";
import { ChatMenuData } from "../types";

interface MenuCardProps {
  data: ChatMenuData;
}

// ── Icon per interaction type ─────────────────────────────────────────────────

const INTERACTION_ICONS: Record<ChatMenuData["interaction"], string> = {
  text:      "✏️",
  button:    "🔘",
  list:      "📋",
  checklist: "☑️",
  form:      "📝",
};

const INTERACTION_LABELS: Record<ChatMenuData["interaction"], string> = {
  text:      "Texto livre",
  button:    "Botões",
  list:      "Lista",
  checklist: "Seleção múltipla",
  form:      "Formulário",
};

// ── Sub-renderers ──────────────────────────────────────────────────────────────

const ButtonInteraction: React.FC<{ data: ChatMenuData }> = ({ data }) => (
  <div className="flex flex-wrap gap-1.5 mt-2">
    {(data.options ?? []).map((opt) => (
      <button
        key={opt.id}
        disabled
        className="px-3 py-1 text-xs rounded-full border border-indigo-300 bg-indigo-50 text-indigo-700
                   opacity-70 cursor-not-allowed select-none"
        title="Modo observação — apenas visualização"
      >
        {opt.label}
      </button>
    ))}
  </div>
);

const ListInteraction: React.FC<{ data: ChatMenuData }> = ({ data }) => (
  <ul className="mt-2 divide-y divide-gray-100 border border-gray-200 rounded-lg overflow-hidden text-xs">
    {(data.options ?? []).map((opt, i) => (
      <li
        key={opt.id}
        className="flex items-center gap-2 px-3 py-1.5 bg-white text-gray-700 opacity-70 select-none"
      >
        <span className="w-4 h-4 rounded-full border border-gray-300 flex items-center justify-center
                         text-[9px] text-gray-400 flex-shrink-0 font-medium">
          {i + 1}
        </span>
        {opt.label}
      </li>
    ))}
  </ul>
);

const ChecklistInteraction: React.FC<{ data: ChatMenuData }> = ({ data }) => (
  <ul className="mt-2 space-y-1 text-xs">
    {(data.options ?? []).map((opt) => (
      <li key={opt.id} className="flex items-center gap-2 text-gray-700 opacity-70 select-none">
        <input
          type="checkbox"
          disabled
          className="w-3.5 h-3.5 rounded border-gray-300 cursor-not-allowed"
        />
        <span>{opt.label}</span>
      </li>
    ))}
  </ul>
);

// Field type → HTML input type mapping (best-effort for the preview)
function inputType(fieldType: string): string {
  switch (fieldType) {
    case "email":   return "email";
    case "number":  return "number";
    case "date":    return "date";
    case "phone":   return "tel";
    default:        return "text";
  }
}

const FormInteraction: React.FC<{ data: ChatMenuData }> = ({ data }) => (
  <div className="mt-2 space-y-2 text-xs">
    {(data.fields ?? []).map((field) => (
      <div key={field.id} className="flex flex-col gap-0.5">
        <label className="text-gray-500 font-medium">{field.label}</label>
        <input
          type={inputType(field.type)}
          disabled
          placeholder={field.label}
          className="px-2 py-1 border border-gray-200 rounded text-gray-400 bg-gray-50
                     cursor-not-allowed text-xs w-full"
        />
      </div>
    ))}
  </div>
);

const TextInteraction: React.FC = () => (
  <div className="mt-2 flex items-center gap-1.5 text-xs text-gray-400 italic">
    <span className="w-3 h-3 rounded-full border border-gray-300 flex-shrink-0" />
    Aguardando resposta em texto livre do cliente…
  </div>
);

// ── MenuCard ───────────────────────────────────────────────────────────────────

export const MenuCard: React.FC<MenuCardProps> = ({ data }) => {
  const icon  = INTERACTION_ICONS[data.interaction]  ?? "❓";
  const label = INTERACTION_LABELS[data.interaction] ?? data.interaction;

  return (
    <div
      className="border border-indigo-200 rounded-xl bg-indigo-50/60 px-3 pt-2.5 pb-3
                 max-w-[90%] self-start shadow-sm"
    >
      {/* Header: interaction type badge + "IA → Cliente" */}
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="text-[10px] bg-indigo-100 text-indigo-700 rounded-full px-2 py-0.5
                         font-medium flex items-center gap-1 border border-indigo-200">
          {icon} {label}
        </span>
        <span className="text-[9px] text-gray-400 ml-auto">IA → Cliente · observação</span>
      </div>

      {/* Prompt text */}
      <p className="text-sm text-gray-800 leading-snug whitespace-pre-wrap">{data.prompt}</p>

      {/* Interaction-specific preview */}
      {data.interaction === "text"      && <TextInteraction />}
      {data.interaction === "button"    && <ButtonInteraction    data={data} />}
      {data.interaction === "list"      && <ListInteraction      data={data} />}
      {data.interaction === "checklist" && <ChecklistInteraction data={data} />}
      {data.interaction === "form"      && <FormInteraction      data={data} />}
    </div>
  );
};
