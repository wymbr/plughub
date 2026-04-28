/**
 * AgentInput
 * Text box for the human agent to compose and send messages.
 * Also has the "Encerrar" (close session) button that triggers the CloseModal.
 */

import React, { KeyboardEvent, useCallback, useRef, useState } from "react";
import { ClosePayload } from "../types";
import { CloseModal } from "./CloseModal";

interface AgentInputProps {
  onSend: (text: string) => void;
  onClose: (payload: ClosePayload) => void;
  disabled?: boolean;
  sessionClosed?: boolean;
}

export const AgentInput: React.FC<AgentInputProps> = ({
  onSend,
  onClose,
  disabled = false,
  sessionClosed = false,
}) => {
  const inputDisabled = disabled || sessionClosed;
  const [text, setText] = useState("");
  const [showModal, setShowModal] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleBlur = useCallback(
    (e: React.FocusEvent<HTMLTextAreaElement>) => {
      if (inputDisabled || showModal) return;
      const relatedTarget = e.relatedTarget as Element | null;
      if (!relatedTarget) {
        requestAnimationFrame(() => {
          if (
            !inputDisabled &&
            (document.activeElement === document.body ||
              document.activeElement === null)
          ) {
            textareaRef.current?.focus();
          }
        });
      }
    },
    [inputDisabled, showModal]
  );

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || inputDisabled) return;
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
      {sessionClosed ? (
        <div className="border-t border-amber-200 bg-amber-50 px-3 py-2 flex-shrink-0 flex items-center gap-3">
          <span className="text-amber-700 text-xs flex-1">
            ⚠️ Cliente desconectou — registre o encerramento para liberar o contato.
          </span>
          <button
            onClick={() => setShowModal(true)}
            className="px-4 py-2 rounded-lg bg-red-600 text-white text-sm font-semibold hover:bg-red-700 transition-colors shadow-sm"
          >
            Registrar encerramento
          </button>
        </div>
      ) : (
        <div className="border-t border-gray-200 bg-white px-3 py-2 flex-shrink-0 flex gap-2 items-end">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={handleBlur}
            disabled={inputDisabled}
            rows={2}
            placeholder="Digite sua mensagem… (Enter para enviar, Shift+Enter para nova linha)"
            className="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:bg-gray-50 disabled:text-gray-400"
          />
          <div className="flex flex-col gap-1.5">
            <button
              onClick={handleSend}
              disabled={inputDisabled || !text.trim()}
              className="px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Enviar
            </button>
            <button
              onClick={() => setShowModal(true)}
              disabled={inputDisabled}
              className="px-4 py-2 rounded-lg bg-white border border-red-400 text-red-600 text-sm font-medium hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Encerrar
            </button>
          </div>
        </div>
      )}

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
