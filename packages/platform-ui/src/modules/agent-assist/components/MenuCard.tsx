/**
 * MenuCard
 * Renders a menu.render event in two modes:
 *
 *   observation mode  (substitutionMode=false, default)
 *     All interaction types are disabled — read-only preview for the agent.
 *
 *   substitution mode (substitutionMode=true)
 *     The supervisor can answer the menu on behalf of the customer.
 *     Calls onSubmit(result) when the supervisor submits.
 *     Result shape per interaction type:
 *       button    → string (option id)
 *       list      → string (option id)
 *       checklist → string[] (selected option ids)
 *       form      → Record<string, string> (fieldId → value)
 *       text      → string (free text)
 */

import React, { useState } from "react";
import { ChatMenuData } from "../types";

export type SubmitResult = string | string[] | Record<string, string>;

interface MenuCardProps {
  data:              ChatMenuData;
  substitutionMode?: boolean;
  onSubmit?:         (result: SubmitResult) => void;
}

interface InteractionProps {
  data:             ChatMenuData;
  substitutionMode: boolean;
  onSubmit:         (result: SubmitResult) => void;
}

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

// ---------------------------------------------------------------------------
// Button
// ---------------------------------------------------------------------------
const ButtonInteraction: React.FC<InteractionProps> = ({ data, substitutionMode, onSubmit }) => (
  <div className="flex flex-wrap gap-1.5 mt-2">
    {(data.options ?? []).map((opt) => (
      <button
        key={opt.id}
        disabled={!substitutionMode}
        onClick={substitutionMode ? () => onSubmit(opt.id) : undefined}
        className={[
          "px-3 py-1 text-xs rounded-full border transition-colors",
          substitutionMode
            ? "border-amber-400 bg-amber-50 text-amber-800 hover:bg-amber-100 cursor-pointer font-medium"
            : "border-indigo-300 bg-indigo-50 text-indigo-700 opacity-70 cursor-not-allowed select-none",
        ].join(" ")}
        title={substitutionMode ? `Responder: ${opt.label}` : "Modo observação — apenas visualização"}
      >
        {opt.label}
      </button>
    ))}
  </div>
);

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------
const ListInteraction: React.FC<InteractionProps> = ({ data, substitutionMode, onSubmit }) => (
  <ul className="mt-2 divide-y divide-gray-100 border border-gray-200 rounded-lg overflow-hidden text-xs">
    {(data.options ?? []).map((opt, i) => (
      <li
        key={opt.id}
        onClick={substitutionMode ? () => onSubmit(opt.id) : undefined}
        className={[
          "flex items-center gap-2 px-3 py-1.5 bg-white text-gray-700 transition-colors",
          substitutionMode
            ? "cursor-pointer hover:bg-amber-50 hover:text-amber-900 font-medium"
            : "opacity-70 select-none",
        ].join(" ")}
        title={substitutionMode ? `Selecionar: ${opt.label}` : undefined}
      >
        <span
          className={[
            "w-4 h-4 rounded-full border flex items-center justify-center text-[9px] flex-shrink-0 font-medium",
            substitutionMode
              ? "border-amber-400 text-amber-700"
              : "border-gray-300 text-gray-400",
          ].join(" ")}
        >
          {i + 1}
        </span>
        {opt.label}
      </li>
    ))}
  </ul>
);

// ---------------------------------------------------------------------------
// Checklist
// ---------------------------------------------------------------------------
const ChecklistInteraction: React.FC<InteractionProps> = ({ data, substitutionMode, onSubmit }) => {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  return (
    <div className="mt-2 text-xs">
      <ul className="space-y-1">
        {(data.options ?? []).map((opt) => (
          <li
            key={opt.id}
            className={[
              "flex items-center gap-2 text-gray-700",
              substitutionMode ? "cursor-pointer select-none" : "opacity-70 select-none",
            ].join(" ")}
            onClick={substitutionMode ? () => toggle(opt.id) : undefined}
          >
            <input
              type="checkbox"
              disabled={!substitutionMode}
              checked={selected.has(opt.id)}
              readOnly={!substitutionMode}
              onChange={() => {}} /* controlled via li onClick above */
              className={[
                "w-3.5 h-3.5 rounded border-gray-300 pointer-events-none",
                substitutionMode ? "accent-amber-500" : "cursor-not-allowed",
              ].join(" ")}
            />
            <span>{opt.label}</span>
          </li>
        ))}
      </ul>
      {substitutionMode && (
        <button
          onClick={() => onSubmit(Array.from(selected))}
          disabled={selected.size === 0}
          className="mt-2 px-3 py-1 rounded-md text-xs font-medium bg-amber-500 text-white
                     hover:bg-amber-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Confirmar seleção ({selected.size})
        </button>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Form
// ---------------------------------------------------------------------------

function inputType(fieldType: string): string {
  switch (fieldType) {
    case "email":  return "email";
    case "number": return "number";
    case "date":   return "date";
    case "phone":  return "tel";
    default:       return "text";
  }
}

const FormInteraction: React.FC<InteractionProps> = ({ data, substitutionMode, onSubmit }) => {
  const [values, setValues] = useState<Record<string, string>>({});

  const handleChange = (fieldId: string, value: string) =>
    setValues((prev) => ({ ...prev, [fieldId]: value }));

  const allFilled = (data.fields ?? []).every((f) => (values[f.id] ?? "").trim() !== "");

  return (
    <div className="mt-2 space-y-2 text-xs">
      {(data.fields ?? []).map((field) => (
        <div key={field.id} className="flex flex-col gap-0.5">
          <label className={substitutionMode ? "text-amber-700 font-medium" : "text-gray-500 font-medium"}>
            {field.label}
          </label>
          <input
            type={inputType(field.type)}
            disabled={!substitutionMode}
            value={values[field.id] ?? ""}
            onChange={(e) => handleChange(field.id, e.target.value)}
            placeholder={field.label}
            className={[
              "px-2 py-1 border rounded text-xs w-full transition-colors",
              substitutionMode
                ? "border-amber-300 bg-white text-gray-800 focus:outline-none focus:ring-1 focus:ring-amber-400"
                : "border-gray-200 text-gray-400 bg-gray-50 cursor-not-allowed",
            ].join(" ")}
          />
        </div>
      ))}
      {substitutionMode && (
        <button
          onClick={() => onSubmit(values)}
          disabled={!allFilled}
          className="mt-1 px-3 py-1 rounded-md text-xs font-medium bg-amber-500 text-white
                     hover:bg-amber-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Enviar formulário
        </button>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Text (free-form)
// ---------------------------------------------------------------------------
const TextInteraction: React.FC<Omit<InteractionProps, "data">> = ({ substitutionMode, onSubmit }) => {
  const [text, setText] = useState("");

  if (!substitutionMode) {
    return (
      <div className="mt-2 flex items-center gap-1.5 text-xs text-gray-400 italic">
        <span className="w-3 h-3 rounded-full border border-gray-300 flex-shrink-0" />
        Aguardando resposta em texto livre do cliente…
      </div>
    );
  }

  return (
    <div className="mt-2 flex flex-col gap-1.5">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={2}
        placeholder="Digite a resposta do cliente…"
        className="px-2 py-1.5 border border-amber-300 rounded text-xs w-full resize-none
                   focus:outline-none focus:ring-1 focus:ring-amber-400 text-gray-800 bg-white"
      />
      <button
        onClick={() => onSubmit(text)}
        disabled={text.trim() === ""}
        className="self-start px-3 py-1 rounded-md text-xs font-medium bg-amber-500 text-white
                   hover:bg-amber-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        Enviar resposta
      </button>
    </div>
  );
};

// ---------------------------------------------------------------------------
// MenuCard — container
// ---------------------------------------------------------------------------
export const MenuCard: React.FC<MenuCardProps> = ({
  data,
  substitutionMode = false,
  onSubmit,
}) => {
  const icon  = INTERACTION_ICONS[data.interaction]  ?? "❓";
  const label = INTERACTION_LABELS[data.interaction] ?? data.interaction;

  const handleSubmit = (result: SubmitResult) => {
    onSubmit?.(result);
  };

  const interactionProps: InteractionProps = {
    data,
    substitutionMode,
    onSubmit: handleSubmit,
  };

  return (
    <div
      className={[
        "rounded-xl px-3 pt-2.5 pb-3 max-w-[90%] self-start shadow-sm border transition-colors",
        substitutionMode
          ? "border-amber-300 bg-amber-50/70"
          : "border-indigo-200 bg-indigo-50/60",
      ].join(" ")}
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        <span
          className={[
            "text-[10px] rounded-full px-2 py-0.5 font-medium flex items-center gap-1 border",
            substitutionMode
              ? "bg-amber-100 text-amber-800 border-amber-300"
              : "bg-indigo-100 text-indigo-700 border-indigo-200",
          ].join(" ")}
        >
          {icon} {label}
        </span>
        <span className="text-[9px] text-gray-400 ml-auto">
          {substitutionMode ? "IA → Cliente · substituição" : "IA → Cliente · observação"}
        </span>
      </div>

      <p className="text-sm text-gray-800 leading-snug whitespace-pre-wrap">{data.prompt}</p>

      {data.interaction === "text"      && <TextInteraction      substitutionMode={substitutionMode} onSubmit={handleSubmit} />}
      {data.interaction === "button"    && <ButtonInteraction    {...interactionProps} />}
      {data.interaction === "list"      && <ListInteraction      {...interactionProps} />}
      {data.interaction === "checklist" && <ChecklistInteraction {...interactionProps} />}
      {data.interaction === "form"      && <FormInteraction      {...interactionProps} />}
    </div>
  );
};
