/**
 * AgentInput
 * Text box for the human agent to compose and send messages.
 * Also has the "Encerrar" (close session) button that triggers the CloseModal.
 */

import React, { KeyboardEvent, useState } from "react";
import { ClosePayload } from "../types";
import { CloseModal } from "./CloseModal";

interface AgentInputProps {
  onSend: (text: string) => void;
  onClose: (payload: ClosePayload) => void;
  disabled?: boolean;
}

export const AgentInput: React.FC<AgentInputProps> = ({
  onSend,
  onClose,
  disabled = false,
}) => {
  const [text, setText] = useState("");
  const [showModal, setShowModal] = useState(false);

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <>
      <div className="border-t border-gray-200 bg-white px-3 py-2 flex-shrink-0 flex gap-2 items-end">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          rows={2}
          placeholder="Digite sua mensagem… (Enter para enviar, Shift+Enter para nova linha)"
          className="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:bg-gray-50 disabled:text-gray-400"
        />
        <div className="flex flex-col gap-1.5">
          <button
            onClick={handleSend}
            disabled={disabled || !text.trim()}
            className="px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Enviar
          </button>
          <button
            onClick={() => setShowModal(true)}
            disabled={disabled}
            className="px-4 py-2 rounded-lg bg-white border border-red-400 text-red-600 text-sm font-medium hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Encerrar
          </button>
        </div>
      </div>

      {showModal && (
        <CloseModal
          onConfirm={(payload) => {
            setShowModal(false);
            onClose(payload);
          }}
          onCancel={() => setShowModal(false)}
        />
      )}
    </>
  );
};
